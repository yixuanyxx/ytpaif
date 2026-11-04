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


def discover(company, types, period, domains, timeout):
    key = os.environ.get('TAVILY_API_KEY', '').strip()
    if not key or not domains or not types or timeout <= 0:
        return []
    name = str(company.get('name') or '').replace('"', ' ').strip()[:120]
    if not name:
        return []
    categories = ' OR '.join('"' + kind.replace('_', ' ') + '"' for kind in types)
    payload = {
        'query': f'"{name}" {period} ({categories})',
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
        if not isinstance(url, str) or not allowed(url, domains):
            continue
        url = urldefrag(url)[0]
        if url not in urls and not urlsplit(url).path.lower().endswith(('.pdf', '.zip')):
            urls.append(url)
    return urls[:6]
