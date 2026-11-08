"""Prepare saved company documents for thematic exposure analysis.

Source discovery stores verified links. Reading those saved links here gives
the exposure pipeline cited passages without performing another source search.
"""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import financial_research
import engine


def _evidence(source_id, document_text):
    """Retain page markers from the bounded reader and stable citation IDs."""
    chunks = re.split(r'(?m)^Page (\d+)\s*\n', document_text)
    pages = [(None, chunks[0])]
    pages.extend((int(chunks[index]), chunks[index + 1])
                 for index in range(1, len(chunks), 2))
    evidence = []
    for page, text in pages:
        for offset in range(0, len(text), 5000):
            passage = text[offset:offset + 5000].strip()
            if not passage:
                continue
            digest = hashlib.sha256(f'{source_id}:{page}:{offset}:{passage}'.encode()).hexdigest()[:16]
            evidence.append({'evidence_id': f'{source_id}_exposure_{digest}',
                             'source_id': source_id, 'page': page, 'passage': passage})
    return evidence


def prepare_sources(app, company_id, theme_id, timeout=25):
    """Return eligible sources, valid evidence IDs and per-source read errors.

    Existing passages are reused. Verified saved URLs are read concurrently;
    successful text reads are persisted before analysis, so the saved case's
    evidence IDs continue to resolve after refresh. The shared reader enforces
    public addresses, same-site redirects, robots rules, 20 MB and 500 pages.
    Its 24,000-character financial-page selection is recorded as a limitation.
    """
    sources, _ = app.evidence_for(company_id, theme_id)
    reader_version = getattr(financial_research, 'DOCUMENT_TEXT_VERSION', '1')
    pending = [source for source in sources
               if (not any(row.get('passage') for row in (source.get('evidence') or []))
                   or (source.get('exposure_text_scope') == 'financial_excerpt'
                       and source.get('exposure_reader_version') != reader_version))
               and source.get('url') and source.get('link_status') == 'verified']
    deadline = time.monotonic() + timeout

    def read(source):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return source, '', 'Document reading deadline reached'
        try:
            text = financial_research._read_document(source['url'], min(10, remaining))
            return source, text, '' if text.strip() else 'No readable text found'
        except (OSError, ValueError) as error:
            message = f'HTTP {error.code}' if isinstance(error, HTTPError) else str(error)
            return source, '', message or type(error).__name__

    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for source, text, error in pool.map(read, pending):
            if error:
                errors.append({'source_id': source['id'],
                               'title': source.get('title') or source.get('filename', ''), 'reason': error})
                continue
            saved = dict(source)
            saved['evidence'] = _evidence(source['id'], text)
            saved['exposure_text_scope'] = 'financial_excerpt'
            saved['exposure_reader_version'] = reader_version
            saved['evidence_selection'] = 'Bounded financial-page text (up to 24,000 characters); not a complete document read'
            saved['exposure_read_at'] = app.now()
            app.put('sources', saved)
    sources, valid = app.evidence_for(company_id, theme_id)
    return sources, valid, errors


def select_evidence(sources, theme, limit=60000):
    """Select whole cited passages within the model budget for large libraries.

    Prefer the requested/newest fiscal period, consolidated revenue, and leaf
    activity terms. Never truncate a passage silently or substitute missing text.
    """
    rows = [(source, passage) for source in sources for passage in (source.get('evidence') or [])
            if passage.get('passage') and passage.get('evidence_id')]
    if sum(len(passage['passage']) for _, passage in rows) <= limit:
        return sorted({passage['evidence_id'] for _, passage in rows}), []
    leaves = engine.taxonomy(theme['nodes'])
    leaf_nodes = [node for node in engine.flatten_taxonomy(theme['nodes']) if node['node_id'] in leaves]
    terms = set()
    for node in leaf_nodes:
        text = ' '.join([node['name'], node['definition'], *node.get('keywords', []), *node.get('examples', [])])
        terms.update(term.lower() for term in re.findall(r'\w{3,}', text))
    target = str(theme.get('reporting_period') or '')

    def rank(row):
        source, passage = row
        text = passage['passage'].lower()
        period = str(source.get('period') or '')
        years = re.findall(r'20\d{2}', period)
        year = max(years, default='0000')
        score = 30 * bool(re.search(r'(?:total|consolidated|group|external)\s+(?:net\s+)?(?:revenue|sales)', text))
        score += 5 * len(re.findall(r'revenue|sales|營收|营业收入|매출', text))
        score += sum(3 for term in terms if term in text)
        return (bool(target and period == target), year, score)

    selected, used = [], 0
    for _, passage in sorted(rows, key=rank, reverse=True):
        size = len(passage['passage'])
        if used + size <= limit:
            selected.append(passage['evidence_id'])
            used += size
    if not selected:
        raise ValueError('Evidence exceeds 60,000 characters per passage; select passages manually.')
    return sorted(set(selected)), ['Automatically selected relevant revenue and leaf-activity passages within the 60,000-character limit.']
