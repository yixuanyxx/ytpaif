"""Issuer lookup through public Korean and Taiwanese market registries."""
import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from approved_collector import AGENT, ApprovedRedirect, allowed, company_matches, explicit_dates, robots_permits

TWSE_DIRECTORY = 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'
DART_SEARCH = 'https://dart.fss.or.kr/dsab001/search.ax'
DART_VIEWER = 'https://opendart.fss.or.kr/xbrl/viewer/main.do'
MARKET_DOMAINS = {
    'SP': ('sgx.com',), 'IN': ('nseindia.com', 'bseindia.com'),
    'HK': ('hkex.com.hk', 'hkexnews.hk'), 'CH': ('sse.com.cn', 'szse.cn', 'cninfo.com.cn', 'bse.cn'),
    'TT': ('twse.com.tw', 'tpex.org.tw'), 'KS': ('opendart.fss.or.kr', 'kind.krx.co.kr'),
    'KQ': ('opendart.fss.or.kr', 'kind.krx.co.kr'), 'MK': ('bursamalaysia.com',),
    'IJ': ('idx.co.id',), 'TB': ('set.or.th',), 'PM': ('pse.com.ph',),
    'VN': ('hsx.vn', 'hnx.vn'), 'AU': ('asx.com.au',), 'NZ': ('nzx.com',),
    'BD': ('dsebd.org', 'cse.com.bd'), 'PA': ('psx.com.pk',), 'SL': ('cse.lk',),
}


def listing(company):
    match = re.fullmatch(r'(\d{4,6})\s+(TT|KS|KQ)(?:\s+EQUITY)?',
                         (company.get('bloomberg_ticker') or '').strip(), re.I)
    return (match[1], match[2].upper()) if match else ('', '')


def domains(company):
    parts = (company.get('bloomberg_ticker') or '').upper().split()
    return list(MARKET_DOMAINS.get(parts[1], ())) if len(parts) >= 2 else []


@lru_cache(maxsize=256)
def indexed_issuer_website(name, ticker, day, public_url):
    """Use indexed exchange profiles to corroborate an issuer's website."""
    from scrapy import Selector
    from issuer_directory import name_words
    from web_source_index import discover

    company = {'name': name, 'bloomberg_ticker': ticker}
    hosts = domains(company)
    if not hosts or not name_words(name):
        return ''
    deadline = time.monotonic() + 8
    websites = set()
    for page in discover(company, ['company_website'], '', hosts, 3)[:3]:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        try:
            profile = Selector(text=read(page, public_url, min(remaining, 3)))
        except (OSError, ValueError):
            continue
        headings = profile.css('h1::text, h2::text, [itemprop="legalName"]::text').getall()
        if not any(name_words(heading) == name_words(name) for heading in headings):
            continue
        for anchor in profile.css('a[href]'):
            label = ' '.join(anchor.xpath('.//text()').getall()).strip()
            cell = anchor.xpath('ancestor::td[1]/preceding-sibling::*[1]')
            field = ' '.join(cell.xpath('.//text()').getall()).strip()
            if not any(re.fullmatch(r'(?:company\s+|official\s+|corporate\s+)?web\s*site\s*:?', text, re.I)
                       for text in (label, field)):
                continue
            try:
                website = urljoin(page, anchor.attrib['href'])
                if not allowed(website, hosts):
                    websites.add(public_url(website).rstrip('/'))
            except ValueError:
                continue
    return next(iter(websites)) if len(websites) == 1 else ''


def pdf_evidence(url, company, public_url, timeout):
    """Read a bounded exchange PDF and require issuer and dated filing-cover evidence."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    from web_source_index import NoRedirect

    hosts = domains(company)
    if not allowed(url, hosts) or not urlsplit(url).path.lower().endswith('.pdf'):
        return None
    limit = 4 * 1024 * 1024
    public_url(url)
    if not robots_permits(url, hosts, public_url, min(timeout, 3)):
        raise OSError('Exchange PDF could not be checked under the site access rules')
    opener = build_opener(NoRedirect())
    with opener.open(Request(url, headers={'User-Agent': AGENT}), timeout=timeout) as response:
        content = response.read(limit + 1)
    try:
        if len(content) > limit or not content.startswith(b'%PDF-'):
            return None
        pages = PdfReader(io.BytesIO(content)).pages
        cover = (pages[0].extract_text() or '')[:12000]
        subject = re.search(r'^\s*Subject\s*[:–-]+\s*([^\n]+)', cover, re.I | re.M)
        if not subject or not company_matches(cover, company):
            return None
        dates = explicit_dates(cover[:subject.start()])
        if len(dates) != 1:
            return None
        title = ' '.join(subject[1].split())
        next_page = (pages[1].extract_text() or '')[:2500] if len(pages) > 1 else ''
        return title, dates[0], next_page
    except (ValueError, IndexError, PyPdfError):
        return None


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
