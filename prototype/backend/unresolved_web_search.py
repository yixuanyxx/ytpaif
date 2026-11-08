"""Find new, cited theme revenue evidence after an unresolved exposure run.

Discovery uses Tavily's ordinary web index and public source documents. It
does not invoke Gemini or any managed search grounding tool. Search hits alone
are never evidence: a bounded, approved document read must support a numeric
theme revenue statement before a source is returned for another analysis.
"""
import hashlib
import json
import math
import re
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse
from urllib.request import Request, build_opener

import analysis_library
import engine
import financial_research
import source_discovery
import web_source_index
from approved_collector import AGENT, allowed, explicit_dates, robots_permits
from exchange_filings import domains as exchange_domains


MAX_QUERIES = 2
MAX_DOCUMENTS = 4
CACHE_HOURS = 24
_PERCENT = re.compile(r'\b\d+(?:\.\d+)?\s*(?:%|per\s*cent|percent(?:age)?)\b|'
                      r'\b(?:low|mid|high)[\s-]*(?:single|double|teens|twenties|thirties|forties)'
                      r'(?:[\s-]*digits)?\s*(?:%|per\s*cent|percent(?:age)?)\b', re.I)
_NUMERIC_PERCENT = re.compile(r'\b\d+(?:\.\d+)?\s*(?:%|per\s*cent|percent(?:age)?)\b', re.I)
_MONEY = re.compile(r'(?:[$€£¥₹]|NT\$|US\$)\s*\d[\d,.]*(?:\s*(?:million|billion|trillion|bn|mn))?'
                    r'|\b\d+(?:\.\d+)?\s*(?:million|billion|trillion|bn|mn)\b', re.I)
_REVENUE = re.compile(r'\brevenue\b|\bnet sales\b|\bturnover\b', re.I)
_SHARE = re.compile(r'\b(?:accounted for|represents?|represented|contributed|comprises?|made up|share of|'
                    r'of (?:our|the|total|group|consolidated) (?:net )?revenue)\b', re.I)
_GROWTH_ONLY = re.compile(r'\b(?:grew|growth|increased?|rise|rose|cagr|year.over.year)\b', re.I)
_NON_REVENUE_MONEY = re.compile(r'\b(?:capex|capital expenditure|capital spending|investment|spending|'
                                r'guidance|forecast|projected|projection|outlook|expect(?:ed|s)?)\b', re.I)
_FORWARD_LOOKING = re.compile(r'\b(?:guidance|forecast|projected|projection|outlook|'
                              r'expect(?:ed|s)?|estimated?|estimate|target|would|could|will)\b', re.I)
_NON_ATTRIBUTION = re.compile(r'\b(?:demand|boost(?:ed)?|driv(?:e|en|ing)|growth|guidance|forecast|'
                              r'capex|capital|investment|spending)\b', re.I)
_GENERIC_AI = re.compile(r'\bAI\b|artificial intelligence|machine learning', re.I)


def _year(value):
    match = re.search(r'20\d{2}', str(value or ''))
    return match.group() if match else ''


def _ticker(company):
    raw = str(company.get('bloomberg_ticker') or company.get('ticker') or '').strip()
    return raw.split()[0][:24] if raw else ''


def _search_name(company):
    abbreviations = {'MFG': 'Manufacturing', 'CO': 'Company', 'ELEC': 'Electronics',
                     'INTL': 'International', 'SEMICON': 'Semiconductor'}
    words = str(company.get('name') or '').replace('"', ' ').split()
    return ' '.join(abbreviations.get(word.upper().strip('.,'), word) for word in words)[:90]


def _issuer_brand(company):
    for field in ('website', 'website_url', 'investor_relations_url'):
        website = str(company.get(field) or '')
        if not website:
            continue
        host = urlparse(website if '://' in website else 'https://' + website).hostname or ''
        labels = host.lower().removeprefix('www.').split('.')
        brand = next((label for label in labels if label not in ('investor', 'investors', 'ir')), '')
        if brand and len(brand) >= 3:
            return brand[:30]
    return ''


def _terms(theme):
    """Select short query phrases from the nested taxonomy JSON."""
    nodes = engine.flatten_taxonomy(theme.get('nodes') or [])
    leaves = engine.taxonomy(theme['nodes']) if nodes else set()
    terms = []
    for node in nodes:
        if node.get('node_id') not in leaves:
            continue
        terms.extend([node.get('name'), *(node.get('keywords') or []), *(node.get('examples') or [])])
    terms.append(theme.get('theme_name'))
    clean = []
    for term in terms:
        if not isinstance(term, str):
            continue
        term = ' '.join(term.replace('"', ' ').split()).strip(' ,;:')[:60]
        if len(term) < 3 or term.lower() in {value.lower() for value in clean}:
            continue
        clean.append(term)
    return clean


def search_queries(company, theme, year):
    """At most two focused searches; no full taxonomy JSON in the query."""
    name = _search_name(company)
    ticker = _ticker(company)
    brand = _issuer_brand(company)
    terms = _terms(theme)
    if not name or not year or not terms:
        raise ValueError('Company name, reporting year and named taxonomy leaves are required for unresolved web search')
    identity = f'"{name}" {ticker} {year}'.strip()
    focused = ' '.join(f'"{term}"' for term in terms[:2])
    theme_name = str(theme.get('theme_name') or terms[0]).strip()[:60]
    broad_identity = f'{name} {brand} {ticker} {year}'.strip()
    return [f'{identity} {focused} revenue share percent',
            f'{broad_identity} "{theme_name}" revenue earnings transcript'][:MAX_QUERIES]


def _indexed_date(value):
    if not value:
        return ''
    dates = explicit_dates(str(value))
    if len(dates) == 1:
        return dates[0]
    try:
        return parsedate_to_datetime(str(value)).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ''


def _document_date(text, indexed):
    """Require a date in the document; the index date alone cannot establish it."""
    observed = explicit_dates(text[:3500])
    if indexed and indexed in observed:
        return indexed
    if len(observed) == 1:
        return observed[0]
    return ''


def _html_publication_date(url, company, timeout):
    """Read a small approved HTML header for an explicit publication date."""
    hosts = source_discovery.approved_domains(company)
    if timeout <= 0 or not robots_permits(url, hosts, source_discovery.public_url, min(3, timeout)):
        return ''
    source_discovery.public_url(url)
    with build_opener(source_discovery.PublicRedirect(company)).open(
            Request(url, headers={'User-Agent': AGENT, 'Range': 'bytes=0-131071'}), timeout=timeout) as response:
        final_url = response.geturl()
        if not source_discovery.approved_source_url(final_url, company):
            return ''
        html = response.read(131072).decode('utf-8', errors='replace')
    class PublicationMetadata(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            label = (attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').lower()
            if tag == 'meta' and label in ('article:published_time', 'datepublished'):
                self.values.append(attrs.get('content') or '')
            elif tag == 'time' and label == 'datepublished':
                self.values.append(attrs.get('datetime') or '')

    metadata = PublicationMetadata()
    metadata.feed(html)
    values = [*metadata.values,
              *re.findall(r'"datePublished"\s*:\s*"(20\d{2}-\d{2}-\d{2})', html)]
    dates = sorted({day for value in values for day in explicit_dates(value[:80])})
    return dates[0] if len(dates) == 1 else ''


def _issuer_matches(company, title, text, official):
    if official:
        return True
    generic = {'company', 'corporation', 'corp', 'limited', 'ltd', 'group', 'holdings',
               'holding', 'inc', 'incorporated', 'plc', 'co', 'the', 'international'}
    words = [word.lower() for word in re.findall(r'[A-Za-z0-9]+', company.get('name', ''))
             if len(word) >= 3 and word.lower() not in generic]
    hay = set(re.findall(r'[a-z0-9]+', (title + ' ' + text[:2500]).lower()))
    aliases = {'mfg': 'manufacturing', 'semi': 'semiconductor', 'elec': 'electronics'}
    matched = sum(word in hay or aliases.get(word) in hay for word in words)
    ticker = _ticker(company).lower()
    return bool(words and (matched >= max(1, (len(words) + 1) // 2)
                           or (ticker and ticker in hay and matched >= 1)))


def _thematic_pattern(theme):
    is_ai = bool(_GENERIC_AI.search(str(theme.get('theme_name') or '')))
    terms = [term for term in _terms(theme) if len(term) >= 3]
    if is_ai:
        return _GENERIC_AI
    return re.compile('|'.join(r'(?<!\w)' + re.escape(term) + r'(?!\w)' for term in terms[:12]), re.I)


def _theme_revenue_link(sentence, thematic):
    """Require the theme to describe the revenue being quantified."""
    for revenue in _REVENUE.finditer(sentence):
        for topic in thematic.finditer(sentence):
            if topic.end() <= revenue.start():
                gap = sentence[topic.end():revenue.start()]
                if len(gap) <= 65 and not _NON_ATTRIBUTION.search(gap) and not re.search(r'[.;:]', gap):
                    return revenue
            elif revenue.end() <= topic.start():
                gap = sentence[revenue.end():topic.start()]
                if (len(gap) <= 45 and re.match(r'\s*(?:from|of|for|attributable to)\b', gap, re.I)
                        and not _NON_ATTRIBUTION.search(gap) and not re.search(r'[.;:]', gap)):
                    return revenue
    return None


def _attributed_money(sentence, revenue):
    """Reject unrelated CapEx/guidance dollars in a theme discussion."""
    if _NON_REVENUE_MONEY.search(sentence) or _GROWTH_ONLY.search(sentence):
        return False
    for amount in _MONEY.finditer(sentence):
        gap = sentence[min(amount.end(), revenue.end()):max(amount.start(), revenue.start())]
        if (len(gap) <= 90 and not _NON_ATTRIBUTION.search(gap)
                and not re.search(r'\b(?:profit|margin|cash flow|assets|debt)\b', gap, re.I)):
            return True
    return False


def _revenue_excerpt(text, theme):
    """Return a contiguous, bounded excerpt with an attributable theme figure.

    This filter only decides whether a document is worth another model call.
    It never calculates exposure or converts an approximate share to a point.
    """
    clean = re.sub(r'\s+', ' ', text)
    thematic = _thematic_pattern(theme)
    candidates = []
    for sentence in re.finditer(r'[^.!?]{20,600}(?:[.!?]|$)', clean):
        value = sentence.group().strip()
        if not thematic.search(value) or not _REVENUE.search(value):
            continue
        revenue = _theme_revenue_link(value, thematic)
        if not revenue:
            continue
        share = _PERCENT.search(value) and _SHARE.search(value) and not _FORWARD_LOOKING.search(value)
        amount = _attributed_money(value, revenue)
        if not (share or amount):
            continue
        kind = ('reported_share' if share and _NUMERIC_PERCENT.search(value)
                else 'reported_amount' if amount else 'approximate_share')
        candidates.append((kind, sentence.start(), sentence.end()))
    if candidates:
        priority = {'reported_share': 0, 'reported_amount': 1, 'approximate_share': 2}
        _, at, end_at = min(candidates, key=lambda item: (priority[item[0]], item[1]))
        start = max(0, at - 180)
        end = min(len(clean), end_at + 180)
        return clean[start:end][:1200]
    return ''


def _evidence_kind(excerpt, theme):
    """Describe the observed figure without inferring an exact share."""
    thematic = _thematic_pattern(theme)
    approximate = False
    for sentence in re.split(r'[.!?]', excerpt):
        revenue = _theme_revenue_link(sentence, thematic)
        if revenue and _SHARE.search(sentence) and not _FORWARD_LOOKING.search(sentence):
            if _NUMERIC_PERCENT.search(sentence):
                return 'reported_share'
            if _PERCENT.search(sentence):
                approximate = True
        if revenue and _attributed_money(sentence, revenue):
            return 'reported_amount'
    return 'approximate_share' if approximate else 'reported_amount'


def _cache_key(company, theme, year, cutoff):
    scope = analysis_library.scope_key(theme)
    identity = json.dumps([company.get('id'), scope, year, cutoff], sort_keys=True)
    return 'webfallback_' + hashlib.sha256(identity.encode()).hexdigest()[:40]


def _cached(app, key):
    finder = getattr(app, 'find', None)
    if not callable(finder):
        return None
    try:
        record = finder('cache', key)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    try:
        created = datetime.fromisoformat(record['created_at'].replace('Z', '+00:00'))
    except (KeyError, TypeError, ValueError):
        return None
    if (datetime.now(timezone.utc) - created).total_seconds() > CACHE_HOURS * 3600:
        return None
    result = record.get('response')
    return result if isinstance(result, dict) and isinstance(result.get('sources'), list) else None


def _hit_score(hit):
    try:
        value = float(hit.get('score') or 0)
        return value if math.isfinite(value) else 0
    except (TypeError, ValueError):
        return 0


def _candidate_priority(hit, year):
    """Prioritize year-end transcript evidence over older or generic reports."""
    path = unquote(urlparse(hit.get('url') or '').path).lower()
    label = (str(hit.get('title') or '') + ' ' + path).lower()
    short_year = year[-2:]
    q4 = bool(re.search(r'\b(?:4q\s*[\'’]?\s*' + short_year + r'|q4\s*' + year
                        + r'|fourth quarter\s*' + year + r')\b', label))
    transcript = bool(re.search(r'transcript|earnings call|conference call', label))
    english = '/english/' in path or '/en/' in path
    return (int(q4 and transcript), int(english), int(transcript), int(q4),
            int(year in label), _hit_score(hit))


def _mirror_key(hit, year):
    """Group language mirrors of the same quarter's earnings transcript."""
    path = unquote(urlparse(hit.get('url') or '').path).lower()
    label = (str(hit.get('title') or '') + ' ' + path).lower()
    if not re.search(r'transcript|earnings call|conference call', label):
        return ''
    for quarter in range(1, 5):
        if re.search(r'\b(?:' + str(quarter) + r'q\s*[\'’]?\s*' + year[-2:]
                     + r'|q' + str(quarter) + r'\s*' + year + r')\b', label):
            return f'earnings:{year}:q{quarter}'
    return ''


def _unique_then_mirrors(pairs, year):
    primary, mirrors, seen = [], [], set()
    for pair in pairs:
        key = _mirror_key(pair[0], year)
        if key and key in seen:
            mirrors.append(pair)
        else:
            primary.append(pair)
            if key:
                seen.add(key)
    return [*primary, *mirrors]


def discover(app, company, theme, existing_sources=(), *, timeout=50):
    """Return newly verified sources containing numeric theme revenue evidence.

    The result contains source records and search diagnostics. The caller owns
    persistence and decides whether an additional exposure analysis is needed.
    """
    year = _year(theme.get('reporting_period')) or max(
        (_year(source.get('period')) for source in existing_sources), default='')
    if not year:
        raise ValueError('Set the theme reporting period before unresolved web search')
    cutoff = str(theme.get('cutoff') or date.today().isoformat())
    key = _cache_key(company, theme, year, cutoff)
    cached = _cached(app, key)
    if cached is not None:
        return {**cached, 'diagnostics': {**cached.get('diagnostics', {}), 'cache_hit': True}}
    started = time.monotonic()
    try:
        website, _ = source_discovery.resolve_issuer_website(company)
    except (OSError, ValueError):
        website = ''
    if website and not any(company.get(field) for field in ('website', 'website_url', 'investor_relations_url')):
        company = {**company, 'website': website}
    queries = search_queries(company, theme, year)
    domains = list(dict.fromkeys([*source_discovery.approved_domains(company), *exchange_domains(company)]))
    official_hosts = [urlparse(str(company.get(field) or '') if '://' in str(company.get(field) or '')
                               else 'https://' + str(company.get(field) or '')).hostname
                      for field in ('website', 'website_url', 'investor_relations_url')
                      if company.get(field)]
    official_hosts = list(dict.fromkeys(host.removeprefix('www.') for host in official_hosts if host))
    existing = {source_discovery.normalized_url(source.get('url')) for source in existing_sources}
    existing.update(source_discovery.normalized_url(source.get('discovery_url')) for source in existing_sources)
    hits, searched, rejections = [], set(), []
    query_calls = 0
    for index, query in enumerate(queries):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 1:
            break
        try:
            query_domains = official_hosts if index == 0 and official_hosts else domains
            query_calls += 1
            results = web_source_index.search(query, query_domains, remaining, max_results=5)
        except web_source_index.WebIndexError as error:
            if not hits:
                raise ValueError(str(error)) from None
            rejections.append({'stage': 'web search', 'reason': str(error)})
            break
        for hit in results:
            url = source_discovery.normalized_url(hit.get('url'))
            if url and url not in searched and url not in existing:
                hits.append((hit, query))
                searched.add(url)
    hits.sort(key=lambda pair: _candidate_priority(pair[0], year), reverse=True)
    official_hits = _unique_then_mirrors(
        [pair for pair in hits if any(allowed(pair[0]['url'], [host]) for host in official_hosts)], year)
    other_hits = _unique_then_mirrors([pair for pair in hits if pair not in official_hits], year)
    # A robots-blocked issuer PDF must not consume the entire inspection budget.
    inspected = [pair for pair in (official_hits[:1] + other_hits[:1]
                                   + official_hits[1:2] + other_hits[1:2])]
    inspected.extend(pair for pair in [*official_hits[2:], *other_hits[2:]] if pair not in inspected)
    inspected = inspected[:MAX_DOCUMENTS]
    sources = []
    for hit, query in inspected:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 2:
            rejections.append({'url': hit['url'], 'stage': 'document', 'reason': 'Search time limit reached'})
            break
        url = hit['url']
        if not allowed(url, domains) or not source_discovery.approved_source_url(url, company):
            rejections.append({'url': url, 'stage': 'domain', 'reason': 'Search hit is outside approved source domains'})
            continue
        indexed = _indexed_date(hit.get('published_date'))
        if indexed and indexed > cutoff:
            rejections.append({'url': url, 'stage': 'date', 'reason': 'Index date is after theme cutoff'})
            continue
        official = any(allowed(url, [host]) for host in official_hosts)
        title = str(hit.get('title') or '')[:300]
        if not source_discovery.specific_document_url(url):
            rejections.append({'url': url, 'stage': 'document', 'reason': 'Search hit is a generic listing'})
            continue
        try:
            verified_url, mime = source_discovery.verify_document_link(
                {'url': url, 'title': title, 'source_type': 'other'},
                timeout=min(8, remaining), company=company)
            if not allowed(verified_url, domains) or not source_discovery.approved_source_url(verified_url, company):
                raise ValueError('Verified document redirected outside approved source domains')
            try:
                text = financial_research._read_document(
                    verified_url, min(20, timeout - (time.monotonic() - started)))
            except ValueError as error:
                # The robots fetch can time out transiently. Repeat the same
                # access check once; never fetch the document without it.
                if 'disallows automated access' not in str(error) or timeout - (time.monotonic() - started) <= 5:
                    raise
                text = financial_research._read_document(
                    verified_url, min(20, timeout - (time.monotonic() - started)))
        except (OSError, ValueError) as error:
            rejections.append({'url': url, 'stage': 'document', 'reason': str(error)[:200]})
            continue
        if not _issuer_matches(company, title, text, official):
            rejections.append({'url': url, 'stage': 'issuer', 'reason': 'Document does not identify the selected company'})
            continue
        published = _document_date(text, indexed)
        if not published and mime.startswith('text/html'):
            try:
                remaining = timeout - (time.monotonic() - started)
                published = _html_publication_date(verified_url, company, min(5, remaining))
            except (OSError, ValueError):
                published = ''
        if not published or published > cutoff:
            rejections.append({'url': url, 'stage': 'date', 'reason': 'Document lacks an eligible verified publication date'})
            continue
        if year not in title + ' ' + text:
            rejections.append({'url': url, 'stage': 'period', 'reason': 'Document does not identify the reporting year'})
            continue
        excerpt = _revenue_excerpt(text, theme)
        if not excerpt:
            rejections.append({'url': url, 'stage': 'theme evidence',
                               'reason': 'No AI-specific revenue amount or attributable revenue share found'})
            continue
        source_type = ('earnings_call' if re.search(r'earnings call|transcript', title + ' ' + verified_url, re.I)
                       else 'other')
        evidence_kind = _evidence_kind(excerpt, theme)
        source_id = 'source_' + hashlib.sha256(
            json.dumps([company['id'], verified_url, year], sort_keys=True).encode()).hexdigest()[:32]
        digest = hashlib.sha256(text.encode()).hexdigest()
        evidence_id = f'{source_id}_exposure_{hashlib.sha256(excerpt.encode()).hexdigest()[:16]}'
        timestamp = app.now() if hasattr(app, 'now') else datetime.now(timezone.utc).isoformat()
        sources.append({
            'id': source_id, 'company_id': company['id'], 'company_name': company['name'],
            'bloomberg_ticker': company.get('bloomberg_ticker', ''), 'sedol': company.get('sedol', ''),
            'theme_id': theme.get('id') if source_type == 'other' else None,
            'source_type': source_type, 'title': title or verified_url,
            'filename': title or urlparse(verified_url).path.rsplit('/', 1)[-1],
            'url': verified_url, 'discovery_url': url, 'publication_date': published,
            'publication_date_source_url': verified_url, 'source_date_basis': 'source document text',
            'period': year, 'cutoff': cutoff, 'retrieved_at': timestamp,
            'extension': '.pdf' if mime == 'application/pdf' else '.html',
            'sha256': digest, 'storage_path': None, 'storage_mode': 'url_only',
            'link_status': 'verified', 'link_verified_at': timestamp,
            'content_type': mime, 'search_provider': 'tavily_web_index', 'search_query': query,
            'theme_numeric_evidence': True,
            'theme_numeric_evidence_kind': evidence_kind,
            'evidence': [{'evidence_id': evidence_id, 'source_id': source_id,
                          'page': None, 'passage': excerpt}],
            'exposure_text_scope': 'numeric_theme_excerpt',
            'exposure_reader_version': financial_research.DOCUMENT_TEXT_VERSION,
            'evidence_selection': 'Verified source excerpt with an explicit theme revenue amount or share',
            'extraction_status': 'not_requested', 'warnings': [],
        })
        if len(sources) >= 2:
            break
    diagnostics = {'provider': 'tavily_web_index', 'queries_count': query_calls,
                   'candidate_count': len(hits), 'inspected_count': len(inspected),
                   'qualified_count': len(sources), 'rejections': rejections[:12],
                   'cache_hit': False, 'gemini_search_calls': 0}
    result = {'sources': sources, 'queries': queries, 'diagnostics': diagnostics}
    if hasattr(app, 'put'):
        app.put('cache', {'id': key, 'response': result,
                          'created_at': app.now() if hasattr(app, 'now') else datetime.now(timezone.utc).isoformat()})
    return result
