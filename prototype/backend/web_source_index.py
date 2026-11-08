"""Find candidate crawl entry points using Tavily's web index."""
import json
import os
from urllib.error import HTTPError
from urllib.parse import urldefrag, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from approved_collector import allowed

ENDPOINT = 'https://api.tavily.com/search'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebIndexError(ValueError):
    """A bounded ordinary-web search failed without exposing the API key."""


def search(query, domains, timeout, *, max_results=5):
    """Return approved Tavily hits with index metadata, never an AI answer.

    Search snippets, dates and titles are candidates only. Callers must read
    and verify the source itself before turning any hit into evidence.
    """
    key = os.environ.get('TAVILY_API_KEY', '').strip()
    if not key:
        raise WebIndexError('Configure TAVILY_API_KEY for unresolved web search')
    if not query or not domains or timeout <= 0:
        return []
    payload = {
        'query': str(query)[:380],
        'include_domains': list(dict.fromkeys(domains)),
        'include_domains_mode': 'restrict',
        'search_depth': 'basic',
        'auto_parameters': False,
        'include_answer': False,
        'include_raw_content': False,
        'include_published_date': True,
        'max_results': max(1, min(int(max_results), 5)),
    }
    request = Request(ENDPOINT, data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=min(5, timeout)) as response:
            body = json.loads(response.read(262144))
    except HTTPError as error:
        if error.code == 429:
            raise WebIndexError('Web search quota reached; try again after the Tavily limit resets') from None
        raise WebIndexError(f'Web search failed (HTTP {error.code})') from None
    except (OSError, ValueError):
        raise WebIndexError('Web search is unavailable; try again later') from None
    if not isinstance(body, dict) or not isinstance(body.get('results'), list):
        raise WebIndexError('Web search returned an invalid response')
    rows, seen = [], set()
    for hit in body['results']:
        if not isinstance(hit, dict) or not isinstance(hit.get('url'), str):
            continue
        url = urldefrag(hit['url'])[0]
        if not allowed(url, domains) or url in seen:
            continue
        seen.add(url)
        rows.append({key: hit.get(key) for key in ('title', 'content', 'published_date', 'score')}
                    | {'url': url})
        if len(rows) >= payload['max_results']:
            break
    return rows


def discover(company, types, period, domains, timeout, pdf_domains=(), reject=None, theme_terms=()):
    key = os.environ.get('TAVILY_API_KEY', '').strip()
    if not key or not domains or not types or timeout <= 0:
        return []
    name = str(company.get('name') or '').replace('"', ' ').strip()[:120]
    if not name:
        return []
    labels = {'company_website': 'company profile', 'earnings_call': 'earnings call transcript'}
    categories = ' OR '.join('"' + labels.get(kind, kind.replace('_', ' ')) + '"' for kind in types)
    if theme_terms:
        categories = ' OR '.join('"' + term.replace('"', ' ') + '"' for term in theme_terms)
    payload = {
        'query': f'"{name}" {period} ({categories})'[:380],
        'include_domains': list(domains),
        'search_depth': 'basic',
        'auto_parameters': False,
        'include_answer': False,
        'include_raw_content': False,
        'max_results': 10,
    }
    request = Request(ENDPOINT, data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=min(5, timeout)) as response:
            payload = json.loads(response.read(262144))
    except (OSError, ValueError):
        return []
    results = payload.get('results') if isinstance(payload, dict) else None
    urls = []
    for result in results if isinstance(results, list) else []:
        url = result.get('url') if isinstance(result, dict) else None
        if not isinstance(url, str):
            continue
        if not allowed(url, domains):
            if reject:
                try:
                    parsed = urlsplit(url)
                    display_url = (parsed.hostname or 'Unknown host') + parsed.path
                except ValueError:
                    display_url = 'Malformed URL'
                reject(display_url, 'web index', 'URL is outside the approved HTTPS domains')
            continue
        url = urldefrag(url)[0]
        path = urlsplit(url).path.lower()
        if url in urls:
            continue
        if path.endswith('.zip') or (path.endswith('.pdf') and not allowed(url, pdf_domains)):
            if reject:
                reject(url, 'web index', 'Direct files are accepted only as PDFs from approved exchanges')
            continue
        if len(urls) == 6:
            if reject:
                reject(url, 'web index', 'Skipped after reaching the web index result limit')
            continue
        urls.append(url)
    return urls
