"""Issuer lookup through public Korean and Taiwanese market registries."""
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from approved_collector import AGENT, ApprovedRedirect, robots_permits

TWSE_DIRECTORY = 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'
DART_SEARCH = 'https://dart.fss.or.kr/dsab001/search.ax'
DART_VIEWER = 'https://opendart.fss.or.kr/xbrl/viewer/main.do'


def listing(company):
    match = re.fullmatch(r'(\d{4,6})\s+(TT|KS|KQ)(?:\s+EQUITY)?',
                         (company.get('bloomberg_ticker') or '').strip(), re.I)
    return (match[1], match[2].upper()) if match else ('', '')


def domains(company):
    _, market = listing(company)
    return ['opendart.fss.or.kr'] if market in ('KS', 'KQ') else []


def read(url, public_url, timeout, data=None, limit=1_048_576):
    hosts = [urlsplit(url).hostname]
    public_url(url)
    if not robots_permits(url, hosts, public_url, min(timeout, 4)):
        raise ValueError('Registry disallows automated access')
    opener = build_opener(ApprovedRedirect(hosts, public_url))
    with opener.open(Request(url, data=data, headers={'User-Agent': AGENT}), timeout=timeout) as response:
        return response.read(limit).decode(response.headers.get_content_charset() or 'utf-8', errors='replace')


@lru_cache(maxsize=2)
def twse_directory(public_url, day):
    return {r['公司代號']: r for r in json.loads(read(TWSE_DIRECTORY, public_url, 6, limit=4_194_304))}


def singapore_today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def issuer_website(company, public_url):
    code, market = listing(company)
    if market != 'TT':
        return ''
    try:
        record = twse_directory(public_url, singapore_today()).get(code, {})
        website = (record.get('網址') or '').strip()
        if not website:
            return ''
        url = 'https://' + re.sub(r'^https?://', '', website, flags=re.I)
        parsed = urlsplit(url)
        if not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            return ''
        return url
    except (OSError, ValueError, KeyError):
        return ''


def dart_filings(html, company, period):
    """Match a filing type, period and receipt date in the same disclosure row."""
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.I | re.S):
        receipt = re.search(r'rcpNo=(\d{14})', row)
        report = re.search(r'(사업보고서|반기보고서|분기보고서)\s*\((' + period + r')\.(\d{2})\)', row)
        filed = re.search(r'<td[^>]*>\s*(20\d{2}\.\d{2}\.\d{2})\s*</td>', row)
        if not (receipt and report and filed and 'openXbrlViewerNew' in row):
            continue
        published = filed[1].replace('.', '-')
        try:
            date.fromisoformat(published)
        except ValueError:
            continue
        if published.replace('-', '') != receipt[1][:8]:
            continue
        annual = report[1] == '사업보고서'
        kind = 'annual_report' if annual else 'regulatory_filing'
        url = DART_VIEWER + '?' + urlencode({'lang': 'en', 'rcpNo': receipt[1]})
        title = f'FY{period} Annual Report' if annual else f'{period}-{report[3]} Periodic Financial Filing'
        yield {'url': url, 'title': f'{company["name"]} {title}', 'source_type': kind,
               'period': period, 'publication_date': published, 'publication_date_source_url': url,
               'ai_confidence': 95, 'ai_confidence_basis': 'official regulator filing',
               'ai_confidence_reason': 'DART lists the issuer-specific filing, reporting period and receipt date.'}


def collect(company, types, period, cutoff, public_url, timeout, website=''):
    code, market = listing(company)
    if not code or not re.fullmatch(r'20\d{2}', period or ''):
        return []
    if market == 'TT':
        observed = singapore_today()
        if not website or 'company_website' not in types or period != observed[:4] or observed > cutoff:
            return []
        return [{'url': website, 'title': f'{company["name"]} Official Website', 'source_type': 'company_website',
                 'period': period, 'publication_date': observed, 'source_date_basis': 'observed',
                 'publication_date_source_url': TWSE_DIRECTORY, 'ai_confidence': 95,
                 'ai_confidence_basis': 'official exchange issuer directory',
                 'ai_confidence_reason': 'TWSE identifies this website for the issuer ticker. The date records when it was checked.'}]
    if not set(types) & {'annual_report', 'regulatory_filing'}:
        return []
    deadline = time.monotonic() + timeout
    results = {}
    # Annual reports usually arrive in the following calendar year. Query
    # each year separately to keep busy issuers from crowding out older rows.
    for year in (str(int(period) + 1), period):
        end = min(cutoff, year + '-12-31')
        remaining = deadline - time.monotonic()
        if end < year + '-01-01' or remaining <= 1:
            continue
        data = urlencode({'textCrpNm': code, 'startDate': year + '0101', 'endDate': end.replace('-', ''),
                          'pageIndex': 1, 'maxResults': 100}).encode()
        try:
            html = read(DART_SEARCH, public_url, min(remaining, 6), data=data)
        except (OSError, ValueError):
            continue
        for row in dart_filings(html, company, period):
            if row['source_type'] in types and row['publication_date'] <= cutoff:
                results[row['url']] = row
    return sorted(results.values(), key=lambda row: row['publication_date'], reverse=True)
