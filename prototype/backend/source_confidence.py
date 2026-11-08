"""LLM-assisted source-provenance traffic light for a calculated company case.

The model compares the actual cited documents. Local checks keep an invented
source ID or an unapproved website from producing a green light. The approved
publisher list is deliberately small until the research team finalises it.
"""
import json
import os
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import exchange_filings
import gemini_fallback


APPROVED_PUBLISHERS = ('finance.yahoo.com', 'reuters.com', 'bloomberg.com')
OFFICIAL_FILINGS = ('sec.gov',)
ISSUER_BASIS = ('official regulator filing', 'official exchange issuer directory',
                'official exchange issuer profile', 'issuer-hosted document listing',
                'issuer name and ticker directory match')
FILING_TYPES = ('annual_report', 'regulatory_filing', 'investor_presentation',
                'earnings_call', 'company_website', 'sustainability_report')
MULTIPART_SUFFIXES = ('com.au', 'co.kr', 'com.tw', 'co.in', 'com.hk', 'com.sg',
                      'co.nz', 'com.cn', 'co.id', 'com.my', 'co.th', 'com.ph',
                      'com.vn', 'com.bd', 'com.pk', 'co.uk')


def _matches(host, domain):
    domain = domain.lower().strip().lstrip('.')
    return bool(domain and (host == domain or host.endswith('.' + domain)))


def _host(url):
    parsed = urlparse(str(url or ''))
    return (parsed.hostname or '').lower().rstrip('.') if parsed.scheme == 'https' else ''


def _document_key(source):
    digest = str(source.get('sha256') or '').strip().lower()
    if digest:
        return 'sha256:' + digest
    url = str(source.get('url') or '')
    if url:
        parsed = urlparse(url)
        return 'url:' + parsed._replace(netloc=parsed.netloc.lower(), query='', fragment='').geturl().rstrip('/')
    return 'source:' + str(source['id'])


def _period_key(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower()).removeprefix('fy')


def _site_root(host):
    labels = host.split('.')
    if len(labels) >= 3 and any(host.endswith('.' + suffix) for suffix in MULTIPART_SUFFIXES):
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:]) if len(labels) >= 2 else host


def _issuer_roots(sources, company):
    """Infer a verified issuer site from its company-website source and name."""
    tokens = [re.sub(r'[^a-z0-9]', '', token.lower())
              for token in str(company.get('name') or '').split()]
    tokens = [token for token in tokens if token and token not in
              ('co', 'corp', 'corporation', 'ltd', 'limited', 'inc', 'the', 'group')]
    initials = ''.join(token[0] for token in tokens)
    aliases = {token for token in tokens if len(token) >= 5 and token not in
               ('taiwan', 'china', 'korea', 'india', 'holdings', 'semiconductor', 'electronics')}
    roots = set()
    for source in sources:
        if source.get('source_type') != 'company_website' or source.get('link_status') != 'verified':
            continue
        host = _host(source.get('url'))
        root = _site_root(host)
        label = root.split('.')[0]
        if (not host or root.endswith(('.test', '.example', '.invalid'))
                or any(_matches(host, domain) for domain in (*APPROVED_PUBLISHERS, *OFFICIAL_FILINGS))):
            continue
        if label in aliases or (len(initials) >= 3 and label.startswith(initials)
                                and len(label) <= len(initials) + 2):
            roots.add(root)
    return roots


def provenance(source, company, issuer_roots=()):
    """Classify source origin without trusting an editable source-type label."""
    url = str(source.get('url') or '')
    host = _host(url)
    if not url:
        # An uploaded filing may be credible, but its origin is not verified
        # enough to count towards the three-source green threshold.
        return 'uploaded_unverified_origin'
    if not host:
        return 'unverified_third_party'
    extra = tuple(x.strip() for x in os.environ.get('EXPOSURE_CONFIDENCE_APPROVED_DOMAINS', '').split(',') if x.strip())
    if any(_matches(host, domain) for domain in (*APPROVED_PUBLISHERS, *extra)):
        return 'approved_publisher'
    if (source.get('source_type') in FILING_TYPES
            and any(_matches(host, domain) for domain in (*OFFICIAL_FILINGS, *exchange_filings.domains(company)))):
        return 'official_filing'
    for field in ('website', 'website_url', 'investor_relations_url'):
        company_host = _host(company.get(field))
        if source.get('source_type') in FILING_TYPES and company_host and _matches(host, company_host):
            return 'issuer_filing'
    if (source.get('source_type') in FILING_TYPES
            and source.get('link_status') == 'verified'
            and any(_matches(host, root) for root in issuer_roots)):
        return 'issuer_filing'
    # Discovery's broader website allowlist also includes research vendors.
    # Count a verified issuer document only when discovery recorded its issuer
    # provenance; the source-type alone is insufficient.
    if (source.get('source_type') in FILING_TYPES and source.get('link_status') == 'verified'
            and source.get('ai_confidence_basis') in ISSUER_BASIS):
        return 'issuer_filing'
    return 'unverified_third_party'


def cited_sources(sources, company, facts, passages):
    cited = set(facts.get('denominator_evidence_ids') or [])
    for unit in facts.get('units') or []:
        cited.update(unit.get('evidence_ids') or [])
    evidence_to_source = {row['evidence_id']: source['id']
                          for source in sources for row in (source.get('evidence') or [])
                          if row.get('evidence_id')}
    if not cited or not cited <= evidence_to_source.keys():
        raise ValueError('Source confidence requires valid cited evidence IDs')
    used_ids = {evidence_to_source[eid] for eid in cited}
    issuer_roots = _issuer_roots(sources, company)
    selected = []
    for source in sources:
        if source['id'] not in used_ids:
            continue
        selected.append({'id': source['id'], 'title': source.get('title') or source.get('filename'),
                         'url': source.get('url'), 'host': _host(source.get('url')),
                         'document_key': _document_key(source),
                         'source_type': source.get('source_type'), 'period': source.get('period'),
                         'publication_date': source.get('publication_date'),
                         'provenance': provenance(source, company, issuer_roots),
                         'cited_evidence_ids': sorted(eid for eid in cited if evidence_to_source[eid] == source['id']),
                         'quoted_passages': [row['passage'][:1500] for row in passages
                                             if row.get('source_id') == source['id']
                                             and row.get('evidence_id') in cited][:4]})
    return selected


def claude_assess(payload):
    """Request a JSON source assessment from the configured Claude checker."""
    endpoint = os.environ.get('CHECKER_LLM_ENDPOINT') or 'https://api.anthropic.com/v1/messages'
    key = (os.environ.get('CHECKER_LLM_API_KEY') or os.environ.get('CLAUDE_API_KEY')
           or os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    model = os.environ.get('CHECKER_LLM_MODEL') or 'claude-sonnet-5'
    if not key:
        raise ValueError('Configure CHECKER_LLM_API_KEY (Claude) to assess source confidence')
    parsed = urlparse(endpoint)
    if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('Claude endpoint must use HTTPS')
    anthropic_api = _matches((parsed.hostname or '').lower(), 'anthropic.com')
    prompt = (
        'Assess source confidence for the extracted company revenue and thematic classification. '
        'Return JSON only: {"light":"green|yellow|red","basis":"specific concise reason",'
        '"agreeing_source_ids":[],"divergent_source_ids":[]}. '
        'Green only if at least three independent APPROVED sources agree on the material revenue numbers '
        'for the same reporting period. Approved means Yahoo Finance, Reuters, Bloomberg or verified company '
        'filings from the issuer/official regulator. Three passages from one document count as ONE source. '
        'Yellow (amber) when credible approved sources disagree, their reporting schedules differ, or fewer '
        'than three independent approved sources corroborate the relevant figures. Red when extracted data '
        'relies on an unverified third-party website outside the provisional approved list. '
        'Only list source IDs supplied in sources; do not infer agreement just because sources share a publisher '
        'or cite a company. Compare fiscal periods, currencies and actual material figures. If agreement is '
        'not demonstrated, return yellow rather than green. The exposure range and its model confidence are '
        'different from this source-provenance traffic light.\n' + json.dumps(payload, ensure_ascii=False))
    if anthropic_api:
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'}
        body = {'model': model, 'max_tokens': 1000,
                'messages': [{'role': 'user', 'content': prompt}]}
    else:
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        body = {'model': model, 'response_format': {'type': 'json_object'},
                'messages': [{'role': 'user', 'content': prompt}]}
    request = Request(endpoint, data=json.dumps(body).encode(), headers=headers)
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    if anthropic_api:
        raw = ''.join(block.get('text', '') for block in result.get('content', []) if block.get('type') == 'text')
    else:
        raw = result['choices'][0]['message']['content']
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError('Claude source confidence response was not JSON')
    return json.loads(match.group(0)), model


def gemini_assess(payload):
    """Request the same constrained assessment using Gemini during testing only."""
    endpoint = (os.environ.get('EXPOSURE_CONFIDENCE_GEMINI_ENDPOINT')
                or 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
    key = (os.environ.get('EXPOSURE_CONFIDENCE_GEMINI_API_KEY')
           or os.environ.get('GEMINI_API_KEY') or os.environ.get('FINANCIALS_LLM_API_KEY')
           or os.environ.get('LLM_API_KEY') or '').strip()
    model = os.environ.get('EXPOSURE_CONFIDENCE_GEMINI_MODEL') or 'gemini-3.8-flash'
    if not key:
        raise ValueError('Configure GEMINI_API_KEY to test source confidence with Gemini')
    parsed = urlparse(endpoint)
    if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('Gemini endpoint must use HTTPS')
    prompt = (
        'Assess source confidence for the extracted company revenue and thematic classification. '
        'Return JSON only: {"light":"green|yellow|red","basis":"specific concise reason",'
        '"agreeing_source_ids":[],"divergent_source_ids":[]}. '
        'Green only if at least three independent APPROVED sources agree on the material revenue numbers '
        'for the same reporting period. Approved means Yahoo Finance, Reuters, Bloomberg or verified company '
        'filings from the issuer/official regulator. Three passages from one document count as ONE source. '
        'Yellow (amber) when credible approved sources disagree, their reporting schedules differ, or fewer '
        'than three independent approved sources corroborate the relevant figures. Red when extracted data '
        'relies on an unverified third-party website outside the provisional approved list. '
        'Only list source IDs supplied in sources; do not infer agreement just because sources share a publisher '
        'or cite a company. Compare fiscal periods, currencies and actual material figures. If agreement is '
        'not demonstrated, return yellow rather than green. The exposure range and its model confidence are '
        'different from this source-provenance traffic light.\n' + json.dumps(payload, ensure_ascii=False))
    body = {'model': model, 'response_format': {'type': 'json_object'},
            'messages': [{'role': 'user', 'content': prompt}]}
    request = Request(endpoint, data=json.dumps(body).encode(),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    result, model_info = gemini_fallback.request_json(request, 60, open_request=urlopen)
    raw = result['choices'][0]['message']['content']
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError('Gemini source confidence response was not JSON')
    return json.loads(match.group(0)), model_info['model']


def assess(payload):
    """Use Gemini only when the explicit test-only provider switch is enabled."""
    provider = os.environ.get('EXPOSURE_CONFIDENCE_PROVIDER', 'claude').strip().lower()
    if provider == 'gemini':
        return gemini_assess(payload)
    if provider != 'claude':
        raise ValueError('EXPOSURE_CONFIDENCE_PROVIDER must be claude or gemini')
    return claude_assess(payload)


def evaluate(sources, company, facts, passages):
    """Return a provider assessment constrained by cited-source provenance."""
    used = cited_sources(sources, company, facts, passages)
    payload = {'company': company.get('name'), 'period': facts.get('period'),
               'currency': facts.get('currency'), 'total_revenue': facts.get('total_revenue'),
               'units': [{'unit_id': unit.get('unit_id'), 'activity': unit.get('activity'),
                          'revenue': unit.get('revenue'), 'evidence_ids': unit.get('evidence_ids')}
                         for unit in facts.get('units') or []],
               'sources': used}
    assessment, model = assess(payload)
    source_ids = {source['id'] for source in used}
    for field in ('agreeing_source_ids', 'divergent_source_ids'):
        ids = assessment.get(field)
        if not isinstance(ids, list) or any(not isinstance(sid, str) or sid not in source_ids for sid in ids):
            raise ValueError('Claude source confidence cited an invalid source ID')
    if assessment.get('light') not in ('green', 'yellow', 'red') or not str(assessment.get('basis') or '').strip():
        raise ValueError('Claude source confidence requires a traffic light and reason')
    trusted = {source['id'] for source in used if source['provenance'] in
               ('approved_publisher', 'official_filing', 'issuer_filing')}
    unapproved = [source for source in used if source['provenance'] == 'unverified_third_party']
    target_period = _period_key(facts.get('period'))
    agreeing = {source['id'] for source in used
                if source['id'] in set(assessment['agreeing_source_ids']) & trusted
                and target_period and _period_key(source['period']) == target_period}
    agreeing_documents = {source['document_key'] for source in used if source['id'] in agreeing}
    divergent = set(assessment['divergent_source_ids'])
    if unapproved:
        light = 'red'
        hosts = ', '.join(sorted({source['host'] or 'unverified URL' for source in unapproved}))
        basis = f'Cited revenue relies on unapproved third-party source(s): {hosts}. ' + assessment['basis']
    elif (assessment['light'] == 'green' and len(agreeing_documents) >= 3 and not divergent):
        light = 'green'
        basis = f'{len(agreeing_documents)} independent approved cited documents agree on the material figures. ' + assessment['basis']
    else:
        light = 'yellow'
        if divergent:
            reason = 'Credible cited sources diverge or use different reporting schedules. '
        elif len(agreeing_documents) < 3:
            reason = f'Only {len(agreeing_documents)} independent approved cited document(s) were confirmed to agree; green needs at least three. '
        else:
            reason = 'Claude did not confirm a green agreement across the approved cited sources. '
        basis = reason + assessment['basis']
    return {'source_confidence_light': light, 'source_confidence_basis': basis.strip(),
            'source_confidence_model': model, 'source_confidence_source_ids': sorted(source_ids),
            'source_confidence_assessment': assessment}
