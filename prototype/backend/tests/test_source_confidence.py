"""Evidence-grounded Claude source traffic lights."""
import unittest
from unittest.mock import patch

import source_confidence


def source(source_id, url, evidence_ids, source_type='annual_report', **extra):
    return {'id': source_id, 'url': url, 'source_type': source_type,
            'link_status': 'verified', 'period': 'FY2025',
            'evidence': [{'evidence_id': eid, 'passage': 'FY2025 revenue USD 100 million.'}
                         for eid in evidence_ids], **extra}


class SourceConfidenceTest(unittest.TestCase):
    def setUp(self):
        self.company = {'name': 'Example Semiconductors', 'bloomberg_ticker': 'EX TT Equity'}
        self.sources = [
            source('filing', 'https://www.sec.gov/Archives/example/report', ['e1']),
            source('reuters', 'https://www.reuters.com/companies/example', ['e2'], 'other'),
            source('yahoo', 'https://finance.yahoo.com/quote/EX', ['e3'], 'other'),
        ]
        self.facts = {'period': 'FY2025', 'currency': 'USD', 'total_revenue': 100,
                      'denominator_evidence_ids': ['e1', 'e2', 'e3'],
                      'units': [{'unit_id': 'u1', 'activity': 'AI hardware', 'revenue': 100,
                                 'evidence_ids': ['e1', 'e2', 'e3']}]}
        self.passages = [{'source_id': row['id'], **e}
                         for row in self.sources for e in row['evidence']]

    def assess(self, light, agreeing, divergent=(), basis='Sources report the same FY2025 revenue.'):
        return ({'light': light, 'basis': basis, 'agreeing_source_ids': list(agreeing),
                 'divergent_source_ids': list(divergent)}, 'claude-test')

    def test_three_independent_approved_cited_sources_can_be_green(self):
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('green', ['filing', 'reuters', 'yahoo'])) as model:
            result = source_confidence.evaluate(self.sources, self.company, self.facts, self.passages)
        self.assertEqual(result['source_confidence_light'], 'green')
        self.assertEqual(result['source_confidence_source_ids'], ['filing', 'reuters', 'yahoo'])
        self.assertEqual(len(model.call_args.args[0]['sources']), 3)

    def test_credible_divergent_reporting_schedules_are_yellow(self):
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('yellow', ['filing', 'reuters'], ['yahoo'],
                                                     'Yahoo uses a different reporting schedule.')):
            result = source_confidence.evaluate(self.sources, self.company, self.facts, self.passages)
        self.assertEqual(result['source_confidence_light'], 'yellow')
        self.assertIn('reporting schedules', result['source_confidence_basis'])

    def test_different_reporting_period_cannot_count_for_green(self):
        self.sources[2]['period'] = 'Q3 2025'
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('green', ['filing', 'reuters', 'yahoo'])):
            result = source_confidence.evaluate(self.sources, self.company, self.facts, self.passages)
        self.assertEqual(result['source_confidence_light'], 'yellow')

    def test_unapproved_third_party_cited_for_revenue_is_red(self):
        self.sources.append(source('blog', 'https://market-rumours.test/article', ['e4'], 'other'))
        self.facts['units'][0]['evidence_ids'].append('e4')
        self.passages.append({'source_id': 'blog', **self.sources[-1]['evidence'][0]})
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('green', ['filing', 'reuters', 'yahoo'])):
            result = source_confidence.evaluate(self.sources, self.company, self.facts, self.passages)
        self.assertEqual(result['source_confidence_light'], 'red')
        self.assertIn('market-rumours.test', result['source_confidence_basis'])

    def test_uncited_unapproved_source_does_not_turn_result_red(self):
        self.sources.append(source('blog', 'https://market-rumours.test/article', ['e4'], 'other'))
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('green', ['filing', 'reuters', 'yahoo'])):
            result = source_confidence.evaluate(self.sources, self.company, self.facts, self.passages)
        self.assertEqual(result['source_confidence_light'], 'green')

    def test_three_passages_in_one_document_do_not_count_as_three_sources(self):
        one = [source('filing', 'https://www.sec.gov/report', ['e1', 'e2', 'e3'])]
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('green', ['filing'])):
            result = source_confidence.evaluate(one, self.company, self.facts,
                [{'source_id': 'filing', **e} for e in one[0]['evidence']])
        self.assertEqual(result['source_confidence_light'], 'yellow')
        self.assertIn('needs at least three', result['source_confidence_basis'])

    def test_duplicate_urls_are_one_document_for_green_threshold(self):
        duplicates = [source('filing_a', 'https://www.sec.gov/report?ref=1', ['e1']),
                      source('filing_b', 'https://www.sec.gov/report?ref=2', ['e2']),
                      source('filing_c', 'https://www.sec.gov/report#page=3', ['e3'])]
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('green', ['filing_a', 'filing_b', 'filing_c'])):
            result = source_confidence.evaluate(duplicates, self.company, self.facts,
                [{'source_id': row['id'], **e} for row in duplicates for e in row['evidence']])
        self.assertEqual(result['source_confidence_light'], 'yellow')

    def test_model_cannot_claim_unprovided_source_id(self):
        with patch.object(source_confidence, 'claude_assess',
                          return_value=self.assess('green', ['filing', 'reuters', 'invented'])):
            with self.assertRaisesRegex(ValueError, 'invalid source ID'):
                source_confidence.evaluate(self.sources, self.company, self.facts, self.passages)

    def test_filing_type_on_unknown_host_is_not_approved(self):
        row = source('fake', 'https://filings.example.test/report', ['e1'], 'annual_report')
        self.assertEqual(source_confidence.provenance(row, self.company), 'unverified_third_party')

    def test_verified_issuer_website_proves_investor_subdomain(self):
        company = {'name': 'TAIWAN SEMICONDUCTOR MFG', 'bloomberg_ticker': '2330 TT Equity'}
        website = source('site', 'https://www.tsmc.com/', ['e1'], 'company_website')
        presentation = source('deck', 'https://investor.tsmc.com/quarterly-deck.pdf', ['e2'],
                              'investor_presentation')
        third_party = source('blog', 'https://www.fool.com/earnings', ['e3'], 'earnings_call')
        roots = source_confidence._issuer_roots([website, presentation, third_party], company)
        self.assertEqual(roots, {'tsmc.com'})
        self.assertEqual(source_confidence.provenance(presentation, company, roots), 'issuer_filing')
        self.assertEqual(source_confidence.provenance(third_party, company, roots), 'unverified_third_party')

    def test_issuer_root_keeps_company_label_on_country_suffixes(self):
        self.assertEqual(source_confidence._site_root('investor.samsung.com.au'), 'samsung.com.au')
        self.assertEqual(source_confidence._site_root('ir.samsung.co.kr'), 'samsung.co.kr')
        company = {'name': 'SAMSUNG ELECTRONICS CO', 'bloomberg_ticker': '005930 KS Equity'}
        website = source('site', 'https://www.samsung.co.kr/', ['e1'], 'company_website')
        filing = source('filing', 'https://ir.samsung.co.kr/report.pdf', ['e2'], 'annual_report')
        roots = source_confidence._issuer_roots([website, filing], company)
        self.assertEqual(roots, {'samsung.co.kr'})
        self.assertEqual(source_confidence.provenance(filing, company, roots), 'issuer_filing')


if __name__ == '__main__':
    unittest.main()
