import unittest
from unittest.mock import patch

import issuer_directory
import source_discovery
from approved_collector import Links, explicit_dates


def snak(value):
    return {'datavalue': {'value': value}}


def entity(name, symbol, site):
    return {'labels': {'en': {'value': name}}, 'claims': {
        'P414': [{'qualifiers': {'P249': [snak(symbol)]}}],
        'P856': [{'mainsnak': snak(site)}],
    }}


class IssuerDirectoryTest(unittest.TestCase):
    def test_requires_both_matching_issuer_name_and_ticker(self):
        records = {'Q1': entity('Example Holdings Limited', '0700', 'https://issuer.test/'),
                   'Q2': entity('Other Company', '700', 'https://wrong.test/'),
                   'Q3': entity('Example Holdings Limited', '701', 'https://unrelated.test/')}
        self.assertEqual(issuer_directory.matching_websites(records, 'EXAMPLE HOLDINGS LTD (HK)', '700'),
                         {'https://issuer.test'})

    def test_uses_directory_site_as_crawl_seed_and_scores_matching_pdf(self):
        company = {'name': 'Example Industries', 'bloomberg_ticker': 'EXAMPLE MK Equity'}
        candidate = {'url': 'https://issuer.test/annual-report-2025.pdf', 'title': 'FY2025 Annual Report',
                     'context': 'FY2025 Annual Report, published 2026-04-01',
                     'publication_dates': ['2026-04-01'], 'issuer_hosted': True}
        with patch.object(issuer_directory, 'lookup', return_value='https://issuer.test'), \
                patch('approved_collector.collect', return_value=([candidate], True)) as collect, \
                patch.object(source_discovery, 'urlopen') as provider:
            rows = source_discovery.grounded_search(company, ['annual_report'], '2025', '2026-11-05', False, 20)
        provider.assert_not_called()
        self.assertIn('issuer.test', collect.call_args.args[1])
        self.assertEqual(rows[0]['publication_date'], '2026-04-01')
        self.assertEqual(rows[0]['ai_confidence_basis'], 'issuer-hosted document listing')

    def test_crawler_accepts_missing_image_alt_and_localized_dates(self):
        parser = Links()
        parser.feed('<a href="/report.pdf"><img alt>2025年度報告（2026年4月2日）</a>')
        self.assertEqual(parser.links[0][0], '/report.pdf')
        self.assertEqual(explicit_dates(parser.links[0][1]), ['2026-04-02'])
