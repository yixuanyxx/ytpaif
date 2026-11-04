"""Bounded page downloads for the existing background source worker."""
import asyncio
import ipaddress
import ssl
import sys
import threading
import time
from concurrent.futures import Future, TimeoutError

import scrapy
from scrapy.crawler import CrawlerRunner
from scrapy.resolver import CachingThreadedResolver


class WindowsTrustPolicy:
    """Use the same Windows trust store as Python's other source downloads."""
    @classmethod
    def from_crawler(cls, crawler, method=None):
        from twisted.internet.ssl import Certificate, trustRootFromCertificates
        from twisted.web.client import BrowserLikePolicyForHTTPS

        roots = [Certificate.load(cert) for cert in ssl.create_default_context().get_ca_certs(binary_form=True)]
        return BrowserLikePolicyForHTTPS(trustRootFromCertificates(roots))


class PublicResolver(CachingThreadedResolver):
    def getHostByName(self, name, timeout=()):
        def require_public(address):
            if not ipaddress.ip_address(address).is_global:
                raise ValueError('Non-public crawl address rejected')
            return address

        return super().getHostByName(name, timeout).addCallback(require_public)


class PageBatch(scrapy.Spider):
    name = 'approved_source_pages'

    async def start(self):
        for url in self.urls:
            yield scrapy.Request(url, callback=self.capture, meta={'handle_httpstatus_all': True})

    def capture(self, response):
        request_url = response.request.url
        location = response.headers.get(b'Location', b'').decode('latin-1')
        content_type = response.headers.get(b'Content-Type', b'').decode('latin-1')
        body = response.text if isinstance(response, scrapy.http.TextResponse) and any(
            kind in content_type.lower() for kind in ('html', 'xml')) else ''
        self.pages[request_url] = (response.status, location, body)


_start_lock = threading.Lock()
_runtime = Future()


def _run():
    try:
        from twisted.internet import asyncioreactor
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        asyncioreactor.install(loop)
        from twisted.internet import reactor
        PublicResolver(reactor, cache_size=128, timeout=4).install_on_reactor()
        runner = CrawlerRunner({
            'CONCURRENT_REQUESTS': 4,
            'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
            'AUTOTHROTTLE_ENABLED': True,
            'AUTOTHROTTLE_START_DELAY': 0.15,
            'AUTOTHROTTLE_MAX_DELAY': 1,
            'RETRY_TIMES': 1,
            'REDIRECT_ENABLED': False,
            'METAREFRESH_ENABLED': False,
            'COOKIES_ENABLED': False,
            'TELNETCONSOLE_ENABLED': False,
            'LOG_ENABLED': False,
            'HTTPPROXY_ENABLED': False,
            'DOWNLOAD_VERIFY_CERTIFICATES': True,
            **({'DOWNLOADER_CLIENTCONTEXTFACTORY': WindowsTrustPolicy} if sys.platform == 'win32' else {}),
        })
    except Exception as error:
        _runtime.set_exception(error)
        return
    reactor.callWhenRunning(_runtime.set_result, (reactor, runner))
    reactor.run(installSignalHandlers=False)


def download(urls, timeout, max_bytes, agent):
    """Return responses received before the crawl deadline, including redirects."""
    deadline = time.monotonic() + timeout
    with _start_lock:
        if not _runtime.running() and not _runtime.done():
            _runtime.set_running_or_notify_cancel()
            threading.Thread(target=_run, name='source-scrapy', daemon=True).start()
    reactor, runner = _runtime.result(timeout=timeout)
    complete = Future()
    pages = {}
    crawling = {}

    def start():
        try:
            crawler = runner.create_crawler(PageBatch)
            crawler.settings.set('USER_AGENT', agent, priority='cmdline')
            crawler.settings.set('DOWNLOAD_TIMEOUT', min(5, timeout), priority='cmdline')
            crawler.settings.set('DOWNLOAD_MAXSIZE', max_bytes, priority='cmdline')
            crawler.settings.set('DOWNLOAD_WARNSIZE', 0, priority='cmdline')
            crawling['crawler'] = crawler
            deferred = runner.crawl(crawler, urls=urls, pages=pages)
            deferred.addCallbacks(lambda _: complete.set_result(None),
                                  lambda failure: complete.set_exception(failure.value))
        except Exception as error:
            complete.set_exception(error)

    def stop():
        if 'crawler' in crawling:
            crawling['crawler'].stop()

    reactor.callFromThread(start)
    try:
        complete.result(timeout=max(0, deadline - time.monotonic()))
    except TimeoutError:
        reactor.callFromThread(stop)
    # Scrapy may percent-encode or normalize request URLs. Match against the
    # same Request normalization used by the spider.
    return {url: pages.get(scrapy.Request(url).url) for url in urls}
