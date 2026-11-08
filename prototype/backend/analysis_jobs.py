"""Durable thematic-exposure analysis jobs.

The normal ``analyze_all`` function remains useful for one company, but a
portfolio can require several long-running document reads and model calls.  A
job records the request in the same durable queue as source and financial
work, so closing the browser does not abandon an in-progress portfolio run.
"""
import hashlib
import json
import os

import analysis_library
import source_jobs


def _selected_companies(app, data):
    theme_id = str(data.get('theme_id') or '').strip()
    portfolio_id = str(data.get('portfolio_id') or '').strip()
    if not theme_id:
        raise ValueError('Select a theme before starting analysis')
    if not portfolio_id:
        raise ValueError('Select a portfolio before starting analysis')
    # Validate both records before the detached worker is launched.
    theme = app.get('themes', theme_id)
    portfolio = app.get('portfolio_imports', portfolio_id)

    portfolio_ids = list(dict.fromkeys(
        row.get('company_id') for row in portfolio.get('rows', [])
        if row.get('company_id') not in (None, '', 'CASH') and not row.get('excluded')
    ))
    requested = data.get('company_ids')
    if requested is not None:
        if (not isinstance(requested, list) or not requested
                or any(not isinstance(company_id, str) or not company_id for company_id in requested)):
            raise ValueError('company_ids must be a nonempty list of company IDs')
        requested = list(dict.fromkeys(requested))
        if not set(requested) <= set(portfolio_ids):
            raise ValueError('Requested companies must belong to the selected portfolio')
        company_ids = requested
    else:
        # Match ``analyze_all``'s normal bulk behavior: a portfolio-wide run
        # should not turn every source-less holding into an avoidable failure.
        source_loader = getattr(app, 'source_link_records', None)
        sources = source_loader() if callable(source_loader) else app.all_records('sources')
        reusable = {
            case.get('company_id')
            for case in analysis_library.current_results(app.all_records('cases'), [theme], sources)
        }
        sourced = {source.get('company_id') for source in sources if source.get('company_id')}
        company_ids = [company_id for company_id in portfolio_ids
                       if company_id in reusable or company_id in sourced]

    companies = {company.get('id'): company for company in app.all_records('companies')}
    selected = [companies[company_id] for company_id in company_ids if company_id in companies]
    if not selected:
        raise ValueError('The portfolio has no matched companies to analyse')
    return theme_id, portfolio, selected


def _request_key(theme_id, portfolio_id, companies, force_recalculate, allow_unresolved_web_search):
    """Stable identity used to collapse duplicate browser clicks atomically."""
    value = {
        'theme_id': theme_id,
        'portfolio_id': portfolio_id,
        'company_ids': sorted(company['id'] for company in companies),
        'force_recalculate': force_recalculate,
        'allow_unresolved_web_search': allow_unresolved_web_search,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _preflight_provider_configuration():
    """Fail once at queue time instead of repeating a missing-key error 80 times."""
    if not os.environ.get('FINANCIALS_LLM_API_KEY', '').strip():
        raise ValueError('Configure FINANCIALS_LLM_API_KEY before starting exposure analysis')
    provider = os.environ.get('EXPOSURE_CONFIDENCE_PROVIDER', 'claude').strip().lower()
    if provider == 'claude':
        names = ('CHECKER_LLM_API_KEY', 'CLAUDE_API_KEY', 'ANTHROPIC_API_KEY')
        if not any(os.environ.get(name, '').strip() for name in names):
            raise ValueError('Configure CHECKER_LLM_API_KEY (Claude) before starting source-confidence analysis')
    elif provider == 'gemini':
        names = ('EXPOSURE_CONFIDENCE_GEMINI_API_KEY', 'GEMINI_API_KEY',
                 'FINANCIALS_LLM_API_KEY', 'LLM_API_KEY')
        if not any(os.environ.get(name, '').strip() for name in names):
            raise ValueError('Configure GEMINI_API_KEY before testing source-confidence analysis')
    else:
        raise ValueError('EXPOSURE_CONFIDENCE_PROVIDER must be claude or gemini')


def start(app, data):
    """Queue a portfolio analysis and return its durable status immediately."""
    _preflight_provider_configuration()
    theme_id, portfolio, companies = _selected_companies(app, data)
    force_recalculate = data.get('force_recalculate') is True
    allow_unresolved_web_search = data.get('allow_unresolved_web_search') is not False
    request_key = _request_key(theme_id, portfolio['id'], companies, force_recalculate,
                               allow_unresolved_web_search)
    run = {
        'id': app.ident('analysis'),
        'job_type': 'analysis',
        # Queue rows use ``queued`` until a worker claims them, while this
        # status remains ``running`` so the existing supervisor can execute it.
        'status': 'running',
        'phase': 'queued',
        'theme_id': theme_id,
        'portfolio_id': portfolio['id'],
        'portfolio_name': portfolio.get('name', ''),
        'company_ids': [company['id'] for company in companies],
        'companies_completed': [],
        'outcomes': {},
        # Keep compact per-company summaries rather than full cases so state
        # polling remains fast for an 80-stock portfolio.
        'results': {},
        'errors': [],
        'analysed_count': 0,
        'calculated_count': 0,
        'reused_count': 0,
        'failed_count': 0,
        'deferred_company_ids': [],
        'fallback_searches': 0,
        'force_recalculate': force_recalculate,
        'allow_unresolved_web_search': allow_unresolved_web_search,
        'request_key': request_key,
        'created_at': app.now(),
    }
    queue = source_jobs.Queue()
    # Only the small fields needed by the worker are serialized. ``analyze_all``
    # reloads the current company data from the durable store for each company.
    queued_companies = [{'id': company['id'], 'name': company.get('name', company['id'])}
                        for company in companies]
    run = queue.enqueue(run, queued_companies)
    source_jobs.ensure_worker()
    return {'analysis_run': run}


def status(data):
    run_id = str(data.get('id') or '').strip()
    if not run_id:
        raise ValueError('Analysis run ID is required')
    try:
        run, _ = source_jobs.Queue().job(run_id)
    except (TypeError, ValueError):
        raise ValueError('Analysis run not found') from None
    if run.get('job_type') != 'analysis':
        raise ValueError('Analysis run not found')
    return {'analysis_run': run}


def _quota_error(value):
    """Recognise provider quota failures which should halt an 80-row run."""
    text = str(value or '').lower()
    return ('http 429' in text or 'resource_exhausted' in text
            or 'quota exceeded' in text or 'rate limit' in text)


def _summary(company, result):
    case = result.get('case') if isinstance(result, dict) else {}
    case = case if isinstance(case, dict) else {}
    return {
        'company_id': company['id'],
        'company_name': company.get('name', company['id']),
        'status': result.get('status', 'success'),
        'reused': result.get('reused') is True,
        'case_id': result.get('case_id') or case.get('id'),
        'fallback_status': result.get('fallback_status', ''),
        'fallback_message': result.get('fallback_message', ''),
    }


def _inflight_case(app, run, company):
    """Recover a case saved just before a worker crashed at its checkpoint."""
    if run.get('inflight_company_id') != company['id']:
        return None
    candidates = [case for case in app.all_records('cases')
                  if case.get('analysis_job_id') == run['id']
                  and case.get('company_id') == company['id']
                  and case.get('theme_id') == run['theme_id']]
    if not candidates:
        return None
    case = max(candidates, key=lambda row: row.get('created_at') or '')
    return {
        'company_id': company['id'],
        'company_name': company.get('name', company['id']),
        'status': 'success',
        'reused': False,
        'case_id': case.get('id'),
        'fallback_status': (case.get('fallback_web_search') or {}).get('status', ''),
        'fallback_message': (case.get('fallback_web_search') or {}).get('message', ''),
    }


def worker(app, run, companies):
    """Run one company at a time and checkpoint after every terminal outcome."""
    run.setdefault('results', {})
    run.setdefault('outcomes', {})
    run.setdefault('errors', [])
    run.setdefault('deferred_company_ids', [])
    try:
        run['fallback_searches'] = max(0, int(run.get('fallback_searches', 0)))
    except (TypeError, ValueError):
        run['fallback_searches'] = 0
    limit_getter = getattr(app, 'unresolved_search_batch_limit', None)
    try:
        fallback_search_limit = max(0, int(limit_getter() if callable(limit_getter) else 3))
    except (TypeError, ValueError):
        fallback_search_limit = 3
    run['fallback_search_limit'] = fallback_search_limit
    run['phase'] = 'analysing'

    def cancelled():
        checker = getattr(app, 'is_cancelled', None)
        return bool(checker and checker(run['id']))

    def checkpoint():
        run['companies_completed'] = list(run['outcomes'])
        run['analysed_count'] = len(run['outcomes'])
        statuses = [value.get('status') if isinstance(value, dict) else value
                    for value in run['outcomes'].values()]
        run['calculated_count'] = sum(value == 'success' for value in statuses)
        run['reused_count'] = sum(value == 'reused' for value in statuses)
        run['failed_count'] = sum(value == 'failed' for value in statuses)
        run['results_count'] = len(run['results'])
        app.put('search_runs', run)

    def defer_remaining(index, reason):
        run['deferred_company_ids'] = [company['id'] for company in companies[index:]
                                       if company['id'] not in run['outcomes']]
        run['completion_reason'] = reason
        run['status'] = 'incomplete'

    def clear_inflight(company_id):
        if run.get('inflight_company_id') == company_id:
            run.pop('inflight_company_id', None)
            run.pop('inflight_started_at', None)

    try:
        checkpoint()
        for index, company in enumerate(companies):
            company_id = company['id']
            if cancelled():
                run['status'] = 'cancelled'
                break
            if company_id in run['outcomes']:
                continue
            recovered = _inflight_case(app, run, company)
            if recovered:
                run['results'][company_id] = recovered
                run['outcomes'][company_id] = recovered
                clear_inflight(company_id)
                checkpoint()
                continue
            # Persist this marker before the LLM operation. If the process
            # exits after the case write but before the next checkpoint, the
            # next worker can identify that case by analysis_job_id.
            run['inflight_company_id'] = company_id
            run['inflight_started_at'] = app.now()
            checkpoint()
            search_permitted = (run.get('allow_unresolved_web_search') is not False
                                and run['fallback_searches'] < fallback_search_limit)
            payload = {
                'theme_id': run['theme_id'],
                'portfolio_id': run['portfolio_id'],
                'company_ids': [company_id],
                'force_recalculate': run.get('force_recalculate') is True,
                'allow_unresolved_web_search': search_permitted,
                'analysis_job_id': run['id'],
            }
            try:
                response = app.analyze_all(payload)
                completed = next((row for row in response.get('results', [])
                                  if row.get('company_id') == company_id), None)
                failure = next((row for row in response.get('errors', [])
                                if row.get('company_id') == company_id), None)
                if completed:
                    summary = _summary(company, completed)
                    followup_quota = _quota_error(summary['fallback_message'])
                    if followup_quota:
                        # The first case may be saved, but its web-evidence
                        # follow-up could not complete. Show Retry and stop
                        # before 79 more companies hit the same provider limit.
                        summary['status'] = 'deferred'
                        summary['error'] = summary['fallback_message']
                        run['errors'].append({'company_id': company_id,
                                              'company_name': company.get('name', company_id),
                                              'reason': summary['error']})
                    run['results'][company_id] = summary
                    run['outcomes'][company_id] = summary
                    if search_permitted and completed.get('fallback_search_attempted'):
                        run['fallback_searches'] += 1
                    if followup_quota:
                        defer_remaining(index + 1, 'provider_quota')
                        run['deferred_company_ids'].insert(0, company_id)
                else:
                    reason = str((failure or {}).get('error') or 'The company analysis returned no result')
                    summary = {
                        'company_id': company_id,
                        'company_name': company.get('name', company_id),
                        'status': 'failed',
                        'error': reason,
                    }
                    run['results'][company_id] = summary
                    run['outcomes'][company_id] = summary
                    run['errors'].append({'company_id': company_id,
                                          'company_name': company.get('name', company_id),
                                          'reason': reason})
                    # The exact failure stage is not returned by analyze_all.
                    # Count a permitted failed attempt conservatively so an
                    # opaque fallback failure cannot unlock 79 more searches.
                    if search_permitted:
                        run['fallback_searches'] += 1
                    if _quota_error(reason):
                        defer_remaining(index + 1, 'provider_quota')
                clear_inflight(company_id)
                checkpoint()
                if run.get('completion_reason') == 'provider_quota':
                    break
            except Exception as error:
                reason = str(error) or type(error).__name__
                summary = {
                    'company_id': company_id,
                    'company_name': company.get('name', company_id),
                    'status': 'failed',
                    'error': reason,
                }
                run['results'][company_id] = summary
                run['outcomes'][company_id] = summary
                run['errors'].append({'company_id': company_id,
                                      'company_name': company.get('name', company_id),
                                      'reason': reason})
                if search_permitted:
                    run['fallback_searches'] += 1
                if _quota_error(reason):
                    defer_remaining(index + 1, 'provider_quota')
                clear_inflight(company_id)
                checkpoint()
                if run.get('completion_reason') == 'provider_quota':
                    break
        if run.get('status') == 'running':
            if run.get('completion_reason') == 'provider_quota':
                run['status'] = 'incomplete'
            elif run['failed_count']:
                run['status'] = 'failed' if run['failed_count'] == len(companies) else 'incomplete'
            else:
                run['status'] = 'completed'
    except Exception as error:
        run['status'] = 'failed'
        run['errors'].append({'reason': type(error).__name__})
    finally:
        if cancelled():
            run['status'] = 'cancelled'
        run['phase'] = 'finished' if run.get('status') != 'running' else run.get('phase', 'analysing')
        if run.get('status') != 'running':
            run['finished_at'] = app.now()
        checkpoint()
