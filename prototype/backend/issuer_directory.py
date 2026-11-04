"""Resolve missing issuer website seeds without relying on an LLM."""
import json
import re
from functools import lru_cache
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from approved_collector import AGENT, ApprovedRedirect

WIKIDATA_API = 'https://www.wikidata.org/w/api.php'
_NAME_SUFFIX = re.compile(r'\b(?:incorporated|corporation|limited|company|holdings|group|corp|ltd|inc|plc|co|pte|berhad|bhd|the)\b', re.I)


def name_words(name):
    name = re.sub(r'\([^)]*\)', '', name).strip()
    name = re.sub(r'\b(holdings|group) li$', r'\1', name, flags=re.I)
    return re.findall(r'[^\W_]+', _NAME_SUFFIX.sub(' ', name).lower())


def ticker_key(ticker):
    ticker = ticker.strip().upper()
    return ticker.lstrip('0') if ticker.isascii() and ticker.isdecimal() else ticker


def statements(entity, prop):
    return [claim for claim in entity.get('claims', {}).get(prop, [])
            if claim.get('rank') != 'deprecated' and not claim.get('qualifiers', {}).get('P582')]


def claim_value(snak):
    return snak.get('datavalue', {}).get('value')


def matching_websites(entities, company_name, ticker):
    company_words = name_words(company_name)
    if not company_words or not ticker:
        return set()
    matches = set()
    for entity in entities.values():
        label = entity.get('labels', {}).get('en', {}).get('value', '')
        aliases = [alias.get('value', '') for alias in entity.get('aliases', {}).get('en', [])]
        if not any(name_words(name) == company_words for name in [label, *aliases]):
            continue
        symbols = [claim_value(claim.get('mainsnak', {})) for claim in statements(entity, 'P249')]
        symbols += [claim_value(snak) for claim in statements(entity, 'P414')
                    for snak in claim.get('qualifiers', {}).get('P249', [])]
        if not any(isinstance(value, str) and ticker_key(value) == ticker for value in symbols):
            continue
        websites = statements(entity, 'P856')
        preferred = [item for item in websites if item.get('rank') == 'preferred']
        for item in preferred or websites:
            url = claim_value(item.get('mainsnak', {}))
            if not isinstance(url, str):
                continue
            parsed = urlsplit(url)
            if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 443):
                matches.add(parsed._replace(scheme='https', fragment='').geturl().rstrip('/'))
    return matches


@lru_cache(maxsize=256)
def lookup(company_name, bloomberg_ticker, day, public_url):
    ticker_parts = bloomberg_ticker.split()
    if not ticker_parts or not name_words(company_name):
        return ''
    domains = ['www.wikidata.org']
    agent = AGENT + '/1.0 (+https://is4002.com)'
    opener = build_opener(ApprovedRedirect(domains, public_url))

    def fetch(params):
        # Use the documented data API. Website crawls still check robots.txt.
        url = WIKIDATA_API + '?' + urlencode({'format': 'json', 'maxlag': 10, **params})
        public_url(url)
        with opener.open(Request(url, headers={'User-Agent': agent}), timeout=4) as response:
            return json.loads(response.read(1_048_576))

    try:
        # Try the original spelling first. Bloomberg labels often omit the
        # legal suffix or include a market note in parentheses.
        name = re.sub(r'\([^)]*\)', '', company_name).strip()
        matches = fetch({'action': 'wbsearchentities', 'search': name, 'language': 'en', 'limit': 8})
        ids = [item['id'] for item in matches.get('search', []) if re.fullmatch(r'Q\d+', item.get('id', ''))]
        if not ids:
            matches = fetch({'action': 'wbsearchentities', 'search': ' '.join(name_words(name)), 'language': 'en', 'limit': 8})
            ids = [item['id'] for item in matches.get('search', []) if re.fullmatch(r'Q\d+', item.get('id', ''))]
        if not ids:
            return ''
        response = fetch({'action': 'wbgetentities', 'ids': '|'.join(ids), 'props': 'labels|aliases|claims', 'languages': 'en'})
        urls = matching_websites(response.get('entities', {}), company_name, ticker_key(ticker_parts[0]))
        if len(urls) != 1:
            return ''
        website = next(iter(urls))
        public_url(website)
        return website
    except (OSError, ValueError, KeyError, TypeError):
        return ''
