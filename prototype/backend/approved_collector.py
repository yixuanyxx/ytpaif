"""Bounded, robots-aware collection from explicitly approved HTTPS hosts."""
import re
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urldefrag, parse_qs, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

AGENT = 'ThemeLabResearchBot'
MAX_PAGES = 24
MAX_BYTES = 1048576
COFORGE_IR = 'https://investors.coforge.com'
COFORGE_REPORTS = COFORGE_IR + '/annual-report'
COFORGE_DISCLOSURES = COFORGE_IR + '/disclosure-under-listing-regulations'


def allowed(url, domains):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and any(host == d or host.endswith('.' + d) for d in domains))
    except ValueError:
        return False


def sitemap_location(value):
    value = value.strip()
    return 'https://' + value[7:] if value.startswith('http://') else value


class ApprovedRedirect(HTTPRedirectHandler):
    def __init__(self, domains, public_url):
        self.domains, self.public_url = domains, public_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl, self.domains):
            raise ValueError('Redirect to unapproved domain blocked')
        self.public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def robots_permits(url, domains, public_url, timeout):
    if not allowed(url, domains):
        return False
    robots_url = 'https://' + urlparse(url).netloc + '/robots.txt'
    try:
        public_url(robots_url)
        opener = build_opener(ApprovedRedirect(domains, public_url))
        with opener.open(Request(robots_url, headers={'User-Agent': AGENT}), timeout=timeout) as response:
            data = response.read(MAX_BYTES).decode('utf-8', errors='replace')
        parser = RobotFileParser(); parser.parse(data.splitlines())
        return parser.can_fetch(AGENT, url)
    except HTTPError as error:
        return error.code in (404, 410)
    except (OSError, ValueError, URLError):
        return False


def page_links(selector):
    """Extract links with dates scoped to their own anchor or a single-link row."""
    visible = './/text()[not(ancestor::script or ancestor::style or ancestor::noscript)]'
    for anchor in selector.css('a[href]'):
        href = anchor.attrib.get('href')
        title = anchor.attrib.get('title') or ' '.join(anchor.xpath(visible + ' | .//img/@alt').getall())
        title = ' '.join(title.split())[:300]
        row = anchor.xpath('ancestor::tr[1]')
        evidence = title
        if row and len(row.css('a[href]')) == 1:
            evidence += ' ' + ' '.join(' '.join(row.xpath(visible).getall()).split())
        yield href, title, evidence


def company_matches(text, company):
    """Match distinctive company-name words, never generic legal suffixes."""
    generic = {'company', 'corporation', 'corp', 'limited', 'ltd', 'group', 'holdings',
               'holding', 'inc', 'incorporated', 'plc', 'co', 'the', 'international'}
    words = [w.lower() for w in re.findall(r'[A-Za-z0-9]+', company.get('name', ''))
             if len(w) >= 3 and w.lower() not in generic]
    haystack = set(re.findall(r'[a-z0-9]+', text.lower()))
    return bool(words and all(word in haystack for word in words))


def explicit_dates(text):
    dates = set()
    for match in re.findall(r'\b20\d{2}-\d{2}-\d{2}\b', text):
        try:
            dates.add(datetime.fromisoformat(match).date().isoformat())
        except ValueError:
            continue
    for year, month, day in re.findall(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text):
        try:
            dates.add(datetime(int(year), int(month), int(day)).date().isoformat())
        except ValueError:
            continue
    for match in re.finditer(r'\b\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2}\b', text):
        for fmt in ('%d %b %Y', '%d %B %Y'):
            try:
                dates.add(datetime.strptime(match.group(), fmt).date().isoformat())
                break
            except ValueError:
                continue
    for match in re.finditer(r'\b[A-Za-z]{3,9}\s+\d{1,2},\s+20\d{2}\b', text):
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                dates.add(datetime.strptime(match.group(), fmt).date().isoformat())
                break
            except ValueError:
                continue
    return sorted(dates)


def annual_report_year(label):
    """Read an explicit fiscal period, excluding the date after a disclosure label."""
    label = label.split('|', 1)[0]
    match = re.search(r'\b(?:FY\s*|annual report(?:\s+for)?\s+)(20\d{2})(?:\s*[-–/]\s*(20\d{2}|\d{2}))?\b', label, re.I)
    if not match:
        return None
    start, end = match.groups()
    if not end:
        return start
    year = str(int(start) + 1)
    return year if year.endswith(end) else None


def official_annual_reports(company, reports, disclosures):
    """Pair issuer-hosted report PDFs with the same issuer's dated release notices."""
    notices = {}
    for link, title in disclosures:
        if not (re.search(r'\bannual report\b', title, re.I)
                and re.search(r'\b(?:intimation|submission|publication|published|release|filed|filing)\b', title, re.I)):
            continue
        year = annual_report_year(title)
        dates = explicit_dates(title)
        if year and len(dates) == 1 and 0 <= int(dates[0][:4]) - int(year) <= 1:
            notices.setdefault(year, {}).setdefault(dates[0], link)
    for report in reports:
        year = annual_report_year(report['title'])
        evidence = notices.get(year, {})
        if len(evidence) != 1:
            continue
        path = unquote(urlparse(report['url']).path)
        if not path.lower().endswith('.pdf'):
            continue
        if not (re.search(r'\bannual report\b', report['title'], re.I)
                or re.search(r'(?:annual[-_ ]*report|[-_ ]ar)[-_ ]*' + year + r'\.pdf$', path, re.I)):
            continue
        publication, disclosure_url = next(iter(evidence.items()))
        if report['url'] == disclosure_url:
            continue
        name = company['name']
        yield {**report, 'title': f'{name} FY{year} Annual Report',
               'context': f'{name} annual report FY{year}, published {publication}.',
               'publication_dates': [publication],
               'official_annual_report': {'publication_date': publication, 'period': year,
                                          'publication_date_source_url': disclosure_url}}


def collect(company, domains, public_url, timeout, indexed_pages=(), exchange_domains=(), reject=None):
    """Return observed links and whether the crawl had restricted/incomplete coverage."""
    from scrapy import Selector
    from scrapy_pages import download
    if reject is None:
        reject = lambda url, stage, reason: None

    deadline = time.monotonic() + max(1, timeout)
    opener = build_opener(ApprovedRedirect(domains, public_url))
    robots = {}
    incomplete = False
    candidates, queue, seen = [], [], set()
    official_reports, official_disclosures = [], []
    sitemap_urls = set()
    for field in ('investor_relations_url', 'website_url', 'website'):
        seed = str(company.get(field) or '').strip()
        if seed:
            seed = seed if '://' in seed else 'https://' + seed
            if allowed(seed, domains):
                queue.append((seed, 0))
                if field in ('website', 'website_url'):
                    root = 'https://' + urlparse(seed).netloc
                    queue.extend([(root + '/investors', 0), (root + '/investor-relations', 0)])
    queue[0:0] = [(url, 0) for url in indexed_pages if allowed(url, domains)]
    if company.get('name', '').strip().upper() in ('COFORGE LTD', 'COFORGE LIMITED'):
        queue[0:0] = [(COFORGE_REPORTS, 0), (COFORGE_DISCLOSURES, 0),
                      (COFORGE_IR + '/quarter-reports', 0), (COFORGE_IR + '/', 0)]
    official_domains = [domain for domain in domains[7:] if domain not in exchange_domains]
    # Company IR pages tend to contain the most direct filings, so visit them
    # before the large publishers when an official domain is recorded.
    for domain in [*official_domains, *domains[:7]]:
        queue.append((f'https://{domain}/', 0))
    keywords = re.compile(
        r'investor|financial|annual|report|filing|earnings|results|presentation|sustainab|esg|transcript|disclosure'
        r'|年報|年报|年度報告|年度报告|財務|财务|投資者|投资者|永續|可持續|사업보고서|투자정보|지속가능'
        r'|laporan|pemodal|keberlanjutan|báo cáo|nhà đầu tư|รายงาน|นักลงทุน', re.I)

    def can_fetch(url):
        nonlocal incomplete
        host = urlparse(url).netloc
        if host not in robots:
            robots_url = 'https://' + host + '/robots.txt'
            try:
                public_url(robots_url)
                with opener.open(Request(robots_url, headers={'User-Agent': AGENT}), timeout=min(3, max(.1, deadline-time.monotonic()))) as response:
                    raw = response.read(MAX_BYTES).decode('utf-8', errors='replace')
                parser = RobotFileParser()
                parser.parse(raw.splitlines())
                robots[host] = parser
                if allowed(url, official_domains):
                    for value in (parser.site_maps() or [])[:2]:
                        sitemap = sitemap_location(urljoin(robots_url, value))
                        if allowed(sitemap, official_domains) and sitemap not in sitemap_urls:
                            sitemap_urls.add(sitemap)
                            queue.insert(0, (sitemap, 0))
            except HTTPError as error:
                if error.code in (404, 410):
                    parser = RobotFileParser(); parser.parse([]); robots[host] = parser
                else:
                    robots[host] = None; incomplete = True
            except (OSError, ValueError, URLError):
                robots[host] = None; incomplete = True
        return robots[host] is not None and robots[host].can_fetch(AGENT, url)

    indexed_pages = set(indexed_pages)

    def crawl_pages():
        nonlocal incomplete
        while queue and len(seen) < MAX_PAGES and time.monotonic() < deadline:
            batch = {}
            while queue and len(batch) < 4 and len(seen) < MAX_PAGES and time.monotonic() < deadline:
                url, depth = queue.pop(0)
                if url in seen or not allowed(url, domains):
                    continue
                seen.add(url)
                try:
                    public_url(url)
                    if can_fetch(url):
                        batch[url] = depth
                    else:
                        incomplete = True
                        reason = ('Could not read the site access rules' if robots.get(urlparse(url).netloc) is None
                                  else 'Blocked by the site access rules')
                        reject(url, 'crawl', reason)
                except (OSError, ValueError) as error:
                    incomplete = True
                    reject(url, 'crawl', str(error) if type(error) is ValueError else type(error).__name__)
            if not batch or time.monotonic() >= deadline:
                continue
            pages = download(batch, deadline - time.monotonic(), MAX_BYTES, AGENT)
            for url, depth in batch.items():
                result = pages.get(url)
                if result is None:
                    incomplete = True
                    reject(url, 'crawl', 'Page fetch timed out or failed')
                    continue
                status, location, body = result
                if status in (301, 302, 303, 307, 308) and location:
                    try:
                        target = urldefrag(urljoin(url, location))[0]
                    except ValueError:
                        incomplete = True
                        reject(url, 'crawl', 'Invalid redirect URL')
                        continue
                    if allowed(target, domains):
                        queue.insert(0, (target, depth))
                        if url in indexed_pages:
                            indexed_pages.add(target)
                        if url in sitemap_urls:
                            sitemap_urls.add(target)
                    else:
                        incomplete = True
                        reject(url, 'crawl', 'Redirect points outside approved domains')
                elif 200 <= status < 300 and body:
                    yield url, depth, body
                elif status >= 400:
                    incomplete = True
                    reject(url, 'crawl', f'Website returned HTTP {status}')
                else:
                    reject(url, 'crawl', f'Website returned no usable page (HTTP {status})')

    for page_url, depth, page in crawl_pages():
        url = page_url
        if url in sitemap_urls:
            sitemap = Selector(text=page, type='xml')
            children = sitemap.xpath('//*[local-name()="sitemap"]/*[local-name()="loc"]/text()').getall()
            children = dict.fromkeys(link for link in map(sitemap_location, children)
                                     if link not in sitemap_urls and allowed(link, official_domains))
            prioritized = sorted(children, key=lambda link: not bool(
                re.search(r'main|investor|about|tentang|corporate|financial', urlparse(link).path, re.I)))
            prioritized = prioritized[:max(0, 4 - len(sitemap_urls))]
            sitemap_urls.update(prioritized)
            queue[0:0] = [(child, 0) for child in prioritized]
            links = sitemap.xpath('//*[local-name()="url"]/*[local-name()="loc"]/text()').getall()
            relevant = [link for link in map(sitemap_location, links) if allowed(link, official_domains)
                        and keywords.search(unquote(urlparse(link).path))]
            prioritized = sorted(dict.fromkeys(relevant), key=lambda link: (
                    not bool(re.search(r'annual|tahunan|sustainab|keberlanjutan|financial|disclosure', link, re.I)),
                    len(link)))[:8]
            queue[0:0] = [(link, 0) for link in prioritized if link not in seen]
            continue
        selector = Selector(text=page)
        title = selector.css('title::text').get(default='').strip()
        raw_date = selector.css('meta[property="article:published_time"]::attr(content), meta[name="datePublished"]::attr(content)').get(default='')
        published = explicit_dates(raw_date[:10])
        dated_disclosure = (
            allowed(page_url, official_domains) and len(published) == 1
            and re.search(r'\b(?:financial statements|financial results|quarterly results|yearly results|financial announcement)\b', title, re.I)
            and not re.search(r'\b(?:archive|library)\b', title, re.I)
        )
        if url in indexed_pages and title and company_matches(title, company):
            if len(published) == 1:
                candidates.append({'url': page_url, 'title': title[:300],
                                   'context': f'{title[:300]} Published {published[0]}',
                                   'publication_dates': published, 'issuer_hosted': allowed(page_url, official_domains)})
        for href, title, evidence in page_links(selector):
            try:
                link = urldefrag(urljoin(page_url, href))[0]
            except ValueError:
                continue
            # Some IR sites embed the actual approved document in a tracking
            # URL. Extract it without requesting the unapproved tracker host.
            if not allowed(link, domains):
                redirect = parse_qs(urlparse(link).query).get('redirect', [])
                if len(redirect) == 1 and allowed(redirect[0], domains):
                    link = urldefrag(redirect[0])[0]
            if not allowed(link, domains):
                continue
            if keywords.search(title + ' ' + link):
                if depth < 2 and not urlparse(link).path.lower().endswith(('.pdf', '.zip')):
                    if allowed(link, official_domains):
                        position = next((i for i, (seed, _) in enumerate(queue)
                                         if not allowed(seed, official_domains)), len(queue))
                        queue.insert(position, (link, depth + 1))
                    else:
                        queue.append((link, depth + 1))
                official = allowed(page_url, official_domains) and allowed(link, official_domains)
                if title and (company_matches(title + ' ' + link, company) or official):
                    context = f'{evidence[:600]} URL: {link}'
                    if official:
                        context += ' Official investor-relations site for ' + company.get('name', '')
                    dates = explicit_dates(evidence)
                    candidate = {'url': link, 'title': title, 'context': context, 'publication_dates': dates,
                                 'issuer_hosted': official}
                    if official and dated_disclosure and not dates and urlparse(link).path.lower().endswith('.pdf'):
                        # Use the disclosure's date only for a labelled financial attachment.
                        attachment = re.sub(r'[_/-]+', ' ', title + ' ' + unquote(urlparse(link).path).rsplit('/', 1)[-1])
                        if (re.search(r'\b(?:FY\s*20\d{2}|20\d{2}\s+(?:results|investor))\b', attachment, re.I)
                                and re.search(r'\b(?:investor deck|investor presentation|results presentation|results announcement)\b', attachment, re.I)):
                            candidate['publication_dates'] = published
                            candidate['publication_date_source_url'] = page_url
                    candidates.append(candidate)
                    if official:
                        if re.search(r'\b(?:intimation|submission|publication|published|release|filed|filing)\b', title, re.I):
                            official_disclosures.append((link, evidence))
                        elif re.search(r'annual[-_ /]*reports?|financial[-_ /]*reports?', title + ' ' + page_url, re.I):
                            official_reports.append(candidate)
    if queue or time.monotonic() >= deadline:
        incomplete = True
    # A bounded crawl cannot prove that the full site has no other documents.
    unique = {}
    for item in candidates:
        previous = unique.get(item['url'])
        # Keep descriptive titles and dated context when the same filing is
        # linked from several places (e.g. an image, a menu and a report list).
        if previous is None or (len(item['publication_dates']), len(item['title'])) > (
                len(previous['publication_dates']), len(previous['title'])):
            unique[item['url']] = item
    ranked = sorted(unique.values(), key=lambda item: (
        bool(item['publication_dates']),
        bool(re.search(r'20\d{2}', item['title'] + ' ' + item['url'])),
    ), reverse=True)
    verified = {report['url']: report for report in official_annual_reports(company, official_reports, official_disclosures)}
    combined = list(verified.values()) + [item for item in ranked if item['url'] not in verified]
    return combined[:80], incomplete or len(combined) > 80
