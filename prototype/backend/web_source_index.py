"""Find candidate crawl entry points using a non-LLM web index."""
import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from approved_collector import allowed

ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def discover(company, types, period, domains, timeout):
    key = os.environ.get('BRAVE_SEARCH_API_KEY', '').strip()
    if not key or not domains or timeout <= 0:
        return []
    name = str(company.get('name') or '').replace('"', ' ')[:120]
    if not name:
        return []
    categories = ' OR '.join('"' + kind.replace('_', ' ') + '"' for kind in types)
    sites = ' OR '.join('site:' + domain for domain in domains)
    query = f'"{name}" {period} ({categories}) ({sites})'
    request = Request(ENDPOINT + '?' + urlencode({'q': query, 'count': 10, 'result_filter': 'web'}),
                      headers={'Accept': 'application/json', 'X-Subscription-Token': key})
    try:
        with build_opener(NoRedirect()).open(request, timeout=min(5, timeout)) as response:
            results = json.loads(response.read(262144)).get('web', {}).get('results', [])
    except (OSError, ValueError):
        return []
    urls = []
    for result in results:
        url = result.get('url')
        if isinstance(url, str) and allowed(url, domains) and url not in urls:
            if not urlsplit(url).path.lower().endswith(('.pdf', '.zip')):
                urls.append(url)
    return urls[:6]
