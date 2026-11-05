import unittest
from unittest.mock import patch

import exchange_filings


class ExchangeFilingsTest(unittest.TestCase):
    def test_every_supported_asian_market_has_an_official_filing_authority(self):
        # Japan is explicitly outside this product's geographic scope.  The
        # catalogue covers every Asian exchange code accepted by the importer.
        expected = {
            'TT', 'KS', 'KQ', 'HK', 'CH', 'SP', 'IN', 'IJ', 'MK', 'TB',
            'PM', 'VN', 'BD', 'PA', 'SL',
        }
        self.assertTrue(expected.issubset(exchange_filings.OFFICIAL_FILING_AUTHORITIES))
        self.assertNotIn('JP', exchange_filings.OFFICIAL_FILING_AUTHORITIES)
        for code in expected:
            authority = exchange_filings.OFFICIAL_FILING_AUTHORITIES[code]
            self.assertTrue(authority['name'])
            self.assertTrue(authority['domains'])
            self.assertTrue(authority['search_url'].startswith('https://'))

    def test_authority_metadata_is_exchange_specific(self):
        taiwan = exchange_filings.authority_metadata({'bloomberg_ticker': '3023 TT Equity'})
        korea = exchange_filings.authority_metadata({'bloomberg_ticker': '357780 KS Equity'})
        self.assertEqual(taiwan['registry'], 'MOPS / TWSE')
        self.assertEqual(korea['registry'], 'DART (Financial Supervisory Service)')
        self.assertEqual(korea['authority_country'], 'South Korea')

    def test_dart_uses_receipt_dates_and_reporting_periods_from_the_same_row(self):
        company = {'name': 'Soulbrain', 'bloomberg_ticker': '357780 KS Equity'}
        html = '''
        <tr><td><a href="?rcpNo=20260316001567">사업보고서 (2025.12)</a>
        <img onclick="openXbrlViewerNew('20260316001567')"></td><td>2026.03.16</td></tr>
        <tr><td><a href="?rcpNo=20250317000913">사업보고서 (2024.12)</a>
        <img onclick="openXbrlViewerNew('20250317000913')"></td><td>2025.03.17</td></tr>
        '''
        with patch.object(exchange_filings, 'read', return_value=html) as read:
            rows = exchange_filings.collect(company, ['annual_report'], '2025', '2026-11-05', str, 10)
            self.assertEqual(read.call_count, 2)
            self.assertIn(b'textCrpNm=357780', read.call_args.kwargs['data'])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['publication_date'], '2026-03-16')
            self.assertEqual(rows[0]['ai_confidence'], 95)
            self.assertEqual(rows[0]['registry'], 'DART (Financial Supervisory Service)')
            self.assertEqual(exchange_filings.collect(company, ['annual_report'], '2025', '2026-03-15', str, 10), [])

    def test_twse_resolves_the_exact_issuer_and_checks_the_cutoff(self):
        company = {'name': 'SINBON', 'bloomberg_ticker': '3023 TT Equity'}
        with patch.object(exchange_filings, 'twse_directory', return_value={
            '3023': {'網址': 'www.sinbon.com'},
            '3024': {'網址': 'www.other.test'},
        }), patch.object(exchange_filings, 'singapore_today', return_value='2026-11-05'):
            website = exchange_filings.issuer_website(company, str)
            self.assertEqual(website, 'https://www.sinbon.com')
            rows = exchange_filings.collect(company, ['company_website'], '2026', '2026-11-05', str, 10, website)
            self.assertEqual(rows[0]['source_date_basis'], 'observed')
            self.assertEqual(exchange_filings.collect(company, ['company_website'], '2026', '2026-11-04', str, 10, website), [])
