"""Bounded, robots-aware collection from explicitly approved HTTPS hosts."""
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urldefrag, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

AGENT = 'ThemeLabResearchBot'
MAX_PAGES = 24
MAX_BYTES = 1048576


def allowed(url, domains):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower().rstrip('.')
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and any(host == d or host.endswith('.' + d) for d in domains))


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


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.href = None
        self.text = []
        self.links = []
        self.page_text = []
        self.text_offset = 0
        self.anchor_start = 0
        self.hidden = 0
        self.anchor_title = ''
        self.row_start = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1
        if tag == 'tr' and self.row_start is None:
            self.row_start = self.text_offset
        if tag == 'a':
            attributes = dict(attrs)
            self.href = attributes.get('href')
            self.anchor_title = attributes.get('title', '')
            self.text = []
            self.anchor_start = self.text_offset
        if tag == 'img' and self.href is not None:
            self.text.append(dict(attrs).get('alt', ''))

    def handle_data(self, data):
        if self.hidden:
            return
        clean = ' '.join(data.split())
        if clean:
            self.page_text.append(clean + ' ')
            self.text_offset += len(clean) + 1
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.hidden:
            self.hidden -= 1
        if tag == 'tr' and self.row_start is not None:
            self.rows.append((self.row_start, self.text_offset))
            self.row_start = None
        if tag == 'a' and self.href is not None:
            label = self.anchor_title or ' '.join(' '.join(self.text).split())
            self.links.append((self.href, label[:300], self.anchor_start, self.text_offset))
            self.href = None


def company_matches(text, company):
    """Match distinctive company-name words, never generic legal suffixes."""
    generic = {'company', 'corporation', 'corp', 'limited', 'ltd', 'group', 'holdings',
               'holding', 'inc', 'incorporated', 'plc', 'co', 'the', 'international'}
    words = [w.lower() for w in re.findall(r'[A-Za-z0-9]+', company.get('name', ''))
             if len(w) >= 3 and w.lower() not in generic]
    haystack = set(re.findall(r'[a-z0-9]+', text.lower()))
    return bool(words and all(word in haystack for word in words))


def explicit_dates(text):
    dates = set(re.findall(r'\b20\d{2}-\d{2}-\d{2}\b', text))
    for match in re.finditer(r'\b\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2}\b', text):
        for fmt in ('%d %b %Y', '%d %B %Y'):
            try:
                dates.add(datetime.strptime(match.group(), fmt).date().isoformat())
                break
            except ValueError:
                continue
    return sorted(dates)


def collect(company, domains, public_url, timeout):
    """Return observed links and whether the crawl had restricted/incomplete coverage."""
    deadline = time.monotonic() + max(1, timeout)
    opener = build_opener(ApprovedRedirect(domains, public_url))
    robots = {}
    incomplete = False
    candidates, queue, seen = [], [], set()
    for field in ('investor_relations_url', 'website_url', 'website'):
        seed = str(company.get(field) or '').strip()
        if seed:
            seed = seed if '://' in seed else 'https://' + seed
            if allowed(seed, domains):
                queue.append((seed, 0))
                if field in ('website', 'website_url'):
                    root = 'https://' + urlparse(seed).netloc
                    queue.extend([(root + '/investors', 0), (root + '/investor-relations', 0)])
    if company.get('name', '').strip().upper() in ('COFORGE LTD', 'COFORGE LIMITED'):
        queue[0:0] = [('https://investors.coforge.com/annual-report', 0),
                      ('https://investors.coforge.com/quarter-reports', 0),
                      ('https://investors.coforge.com/', 0)]
    # Company IR pages tend to contain the most direct filings, so visit them
    # before the large publishers when an official domain is recorded.
    for domain in [*domains[7:], *domains[:7]]:
        queue.append((f'https://{domain}/', 0))
    keywords = re.compile(r'investor|financial|annual|report|filing|earnings|results|presentation|sustainab|esg|transcript|disclosure', re.I)

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
            except HTTPError as error:
                if error.code in (404, 410):
                    parser = RobotFileParser(); parser.parse([]); robots[host] = parser
                else:
                    robots[host] = None; incomplete = True
            except (OSError, ValueError, URLError):
                robots[host] = None; incomplete = True
        return robots[host] is not None and robots[host].can_fetch(AGENT, url)

    while queue and len(seen) < MAX_PAGES and time.monotonic() < deadline:
        url, depth = queue.pop(0)
        if url in seen or not allowed(url, domains):
            continue
        seen.add(url)
        if not can_fetch(url):
            incomplete = True
            continue
        try:
            public_url(url)
            with opener.open(Request(url, headers={'User-Agent': AGENT, 'Accept': 'text/html'}), timeout=min(5, max(.1, deadline-time.monotonic()))) as response:
                if 'html' not in response.headers.get('Content-Type', '').lower():
                    continue
                page_url = response.geturl()
                page = response.read(MAX_BYTES).decode('utf-8', errors='replace')
        except (OSError, ValueError, URLError):
            incomplete = True
            continue
        parser = Links(); parser.feed(page)
        page_text = ''.join(parser.page_text)
        for href, title, start, end in parser.links:
            link = urldefrag(urljoin(page_url, href))[0]
            # Some IR sites embed the actual approved document in a tracking
            # URL. Extract it without requesting the unapproved tracker host.
            if not allowed(link, domains):
                redirect = parse_qs(urlparse(link).query).get('redirect', [])
                if len(redirect) == 1 and allowed(redirect[0], domains):
                    link = urldefrag(redirect[0])[0]
            if not allowed(link, domains):
                continue
            row_bounds = next(((a, b) for a, b in parser.rows if a <= start <= b), None)
            nearby = page_text[row_bounds[0]:row_bounds[1]] if row_bounds else page_text[max(0, start - 80):end + 80]
            context = title + ' ' + link + ' ' + nearby
            if keywords.search(title + ' ' + link):
                if depth < 2 and not urlparse(link).path.lower().endswith(('.pdf', '.zip')):
                    if urlparse(page_url).hostname in domains[7:]:
                        queue.insert(0, (link, depth + 1))
                    else:
                        queue.append((link, depth + 1))
                company_host = urlparse(page_url).hostname or ''
                official = any(company_host == d or company_host.endswith('.' + d) for d in domains[7:])
                if title and (company_matches(title + ' ' + link, company) or official):
                    evidence = context[:600]
                    if official:
                        evidence += ' Official investor-relations site for ' + company.get('name', '')
                    dates = explicit_dates(evidence)
                    if dates:
                        evidence += ' Explicit publication-date candidates: ' + ', '.join(dates)
                    candidates.append({'url': link, 'title': title, 'context': evidence})
    if queue or time.monotonic() >= deadline:
        incomplete = True
    # A bounded crawl cannot prove that the full site has no other documents.
    unique = {}
    for item in candidates:
        previous = unique.get(item['url'])
        # Keep descriptive titles and dated context when the same filing is
        # linked from several places (e.g. an image, a menu and a report list).
        if previous is None or (len(explicit_dates(item['context'])), len(item['title'])) > (
                len(explicit_dates(previous['context'])), len(previous['title'])):
            unique[item['url']] = item
    ranked = sorted(unique.values(), key=lambda item: (
        bool(explicit_dates(item['context'])),
        bool(re.search(r'20\d{2}', item['title'] + ' ' + item['url'])),
    ), reverse=True)
    return ranked[:80], True
