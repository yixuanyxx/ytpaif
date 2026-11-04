"""Find candidate crawl entry points using Tavily's web index."""
import json
import os
from urllib.parse import urldefrag, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from approved_collector import allowed

ENDPOINT = 'https://api.tavily.com/search'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
