"""Durable portfolio financial lookups using the shared detached job queue."""
import os

import financial_research
import source_jobs


def start(app, data):
    if not os.environ.get('FINANCIALS_LLM_API_KEY', '').strip():
        raise ValueError('Configure FINANCIALS_LLM_API_KEY before populating financial data')
    portfolio_id = data.get('portfolio_id')
    if not portfolio_id:
        raise ValueError('Select a portfolio before populating financial data')
    portfolio = app.get('portfolio_imports', portfolio_id)
    company_ids = list(dict.fromkeys(row['company_id'] for row in portfolio.get('rows', [])
                                    if row.get('company_id') and row['company_id'] != 'CASH' and not row.get('excluded')))
    companies = {company['id']: company for company in app.all_records('companies')}
    selected = [companies[company_id] for company_id in company_ids if company_id in companies]
    if not selected:
        raise ValueError('The portfolio has no matched companies')
    run = {'id': app.ident('financial'), 'job_type': 'financials', 'status': 'running', 'phase': 'queued',
           'portfolio_id': portfolio_id, 'portfolio_name': portfolio.get('name', ''),
           'company_ids': [company['id'] for company in selected], 'companies_completed': [],
           'created_at': app.now(), 'cutoff': app.now()[:10], 'results': {}, 'outcomes': {},
           'updated': 0, 'failed': 0, 'unresolved': 0, 'results_count': 0, 'errors': []}
    run = source_jobs.Queue().enqueue(run, selected)
    source_jobs.ensure_worker()
    return {'financial_run': run}


def cancel(app, data):
    """Cancel a durable financial lookup without allowing a worker to revive it."""
    run_id = data.get('id')
    if not run_id:
        raise ValueError('Financial lookup run ID is required')
    queue = source_jobs.Queue()
    try:
        queued = queue.job(run_id)
        # A queue-volume reset from an older worker can surface as ``None``
        # rather than the current Queue.job() ValueError contract. Treat both
        # shapes as a missing local entry and resolve the durable mirror below.
        run = queued[0] if queued else None
        if run is None:
            raise ValueError('Job not found')
    except (TypeError, ValueError):
        # The browser can see an old remote/search_runs mirror after a worker
        # restart or a queue-volume loss.  With no local queue entry there is
        # no worker the queue can cancel; mark the matching financial mirror
        # terminal so it stops presenting a permanently-running cancel button.
        run = next((record for record in app.all_records('search_runs')
                    if record.get('id') == run_id), None)
        if not run or run.get('job_type') != 'financials':
            raise ValueError('Financial lookup run not found') from None
        if run.get('status') in ('completed', 'incomplete', 'failed', 'cancelled'):
            return {'financial_run': run}
        run = {
            **run,
            'status': 'cancelled',
            'phase': 'finished',
            'finished_at': app.now(),
            'cancel_reason': 'Financial lookup cancelled by user (the local job queue entry was unavailable).',
        }
        app.put('search_runs', run)
        return {'financial_run': run}
    if run.get('job_type') != 'financials':
        raise ValueError('Financial lookup run not found')
    run = queue.cancel(run_id, 'Financial lookup cancelled by user.')
    # Queue storage is durable and takes precedence over its remote mirror in
    # state(). A slow/unavailable remote store must not delay cancellation.
    source_jobs.ensure_worker()
    return {'financial_run': run}


def apply_result(app, company_id, result):
    if result.get('error'):
        return 'failed'
    market_cap, revenue = result.get('market_cap'), result.get('net_revenue')
    if not market_cap and not revenue:
        return 'unresolved'
    company = dict(app.get('companies', company_id))
    for field, fact, missing in (('market_cap', market_cap, 'No sourced market capitalisation'),
                                 ('net_revenue', revenue, 'No sourced net revenue')):
        company[field] = fact['value'] if fact else None
        company[field + '_citation'] = (f"{fact['supporting_sources']} / {fact['evaluated_sources']} sources · {fact['year']}"
                                        if fact else missing)
    company['financials_updated_at'] = app.now()
    app.put('companies', company)
    return 'updated'


def worker(app, run, companies):
    run.setdefault('results', {})
    run.setdefault('outcomes', {})
    run['phase'] = 'researching'

    def cancelled():
        checker = getattr(app, 'is_cancelled', None)
        return bool(checker and checker(run['id']))

    def checkpoint():
        run['companies_completed'] = list(run['outcomes'])
        for outcome in ('updated', 'failed', 'unresolved'):
            run[outcome] = sum(value == outcome for value in run['outcomes'].values())
        run['results_count'] = run['updated']
        app.put('search_runs', run)

    def save_result(company_id, result):
        if cancelled():
            return
        run['results'][company_id] = result
        checkpoint()  # Preserve the AI result before writing company data.
        if cancelled():
            return
        run['outcomes'][company_id] = apply_result(app, company_id, result)
        checkpoint()

    try:
        checkpoint()
        if cancelled():
            return
        for company in companies:
            if cancelled():
                return
            company_id = company['id']
            if company_id in run['results'] and company_id not in run['outcomes']:
                save_result(company_id, run['results'][company_id])
        pending = [company for company in companies if company['id'] not in run['outcomes']]
        if pending:
            # Newly discovered links are in Redis, uploaded evidence stays in Supabase.
            sources = {source['id']: source for source in app.store.all_records('sources')}
            for source in app.source_link_records():
                sources[source['id']] = {**sources.get(source['id'], {}), **source}
            by_company = {company['id']: [] for company in pending}
            for source in sources.values():
                if source.get('company_id') in by_company:
                    by_company[source['company_id']].append(source)
            results = financial_research.iter_results(pending, by_company, should_cancel=cancelled)
            try:
                for company_id, result in results:
                    if cancelled():
                        return
                    save_result(company_id, result)
                    if cancelled():
                        return
            finally:
                close = getattr(results, 'close', None)
                if close:
                    close()
        if cancelled():
            return
        run['status'] = 'completed'
        if run['failed'] or run['unresolved']:
            run['status'] = 'failed' if run['failed'] == len(companies) else 'incomplete'
    except Exception as error:
        run['status'] = 'failed'
        run['errors'].append({'reason': type(error).__name__})
    run['phase'] = 'finished'
    run['finished_at'] = app.now()
    checkpoint()
