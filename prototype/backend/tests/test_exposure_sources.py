import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import exposure_sources


class ExposureSourcesTest(unittest.TestCase):
    def app(self, sources):
        records = {source['id']: copy.deepcopy(source) for source in sources}
        def evidence_for(company_id, theme_id):
            rows = [source for source in records.values()
                    if source['company_id'] == company_id and source['publication_date'] <= '2026-11-08']
            return rows, {row['evidence_id'] for source in rows for row in source.get('evidence', [])}
        return SimpleNamespace(evidence_for=evidence_for, now=lambda: '2026-11-08T00:00:00Z',
                               put=Mock(side_effect=lambda kind, source: records.update({source['id']: source}))), records

    def source(self, source_id, **updates):
        return {'id': source_id, 'company_id': 'company', 'publication_date': '2026-01-01',
                'period': 'FY2025', 'url': f'https://issuer.test/{source_id}.pdf',
                'link_status': 'verified', 'storage_mode': 'url_only', 'evidence': [], **updates}

    def test_saved_links_produce_persisted_citations_and_existing_evidence_is_reused(self):
        existing = self.source('existing', evidence=[{'evidence_id': 'original', 'passage': 'Original revenue'}])
        app, records = self.app([self.source('linked'), existing])
        with patch.object(exposure_sources.financial_research, '_read_document', return_value='Issuer\nPage 12\nRevenue USD 100 million.') as read:
            sources, valid, errors = exposure_sources.prepare_sources(app, 'company', 'theme')
            again = exposure_sources.prepare_sources(app, 'company', 'theme')
        read.assert_called_once()
        self.assertEqual(errors, [])
        self.assertEqual(valid, again[1])
        self.assertIn('original', valid)
        self.assertEqual(records['linked']['period'], 'FY2025')
        self.assertEqual(records['linked']['url'], 'https://issuer.test/linked.pdf')
        self.assertTrue(any(row['page'] == 12 for row in records['linked']['evidence']))
        self.assertIn('not a complete', records['linked']['evidence_selection'])

    def test_cutoff_and_verified_link_requirements_are_enforced(self):
        app, _ = self.app([self.source('future', publication_date='2099-01-01'),
                           self.source('unverified', link_status='unresolved')])
        with patch.object(exposure_sources.financial_research, '_read_document') as read:
            _, valid, errors = exposure_sources.prepare_sources(app, 'company', 'theme')
        read.assert_not_called()
        self.assertEqual(valid, set())
        self.assertEqual(errors, [])

    def test_read_failure_does_not_discard_another_successful_source(self):
        app, records = self.app([self.source('bad'), self.source('good')])
        def read(url, timeout):
            if '/bad.' in url:
                raise ValueError('Document site disallows automated access')
            return 'Page 3\nSales USD 20 million'
        with patch.object(exposure_sources.financial_research, '_read_document', side_effect=read):
            _, valid, errors = exposure_sources.prepare_sources(app, 'company', 'theme')
        self.assertTrue(valid)
        self.assertTrue(records['good']['evidence'])
        self.assertEqual(errors[0]['source_id'], 'bad')
        self.assertEqual(records['bad']['evidence'], [])

    def test_large_source_library_selects_denominator_and_leaf_revenue_within_budget(self):
        theme = {'nodes': [{'node_id': 'solar', 'name': 'Solar', 'definition': 'Photovoltaic equipment',
                            'examples': [], 'children': []}]}
        sources = [self.source(name, evidence=[{'evidence_id': name, 'passage': text.ljust(30000, 'x')}])
                   for name, text in [('irrelevant', 'Company history'), ('total', 'Consolidated revenue USD 1000 million.'),
                                      ('leaf', 'Solar revenue USD 200 million.'), ('other', 'Governance and administration')]]
        selected, notes = exposure_sources.select_evidence(sources, theme)
        self.assertEqual(set(selected), {'total', 'leaf'})
        self.assertTrue(notes)

    def test_old_generated_excerpts_are_refreshed_without_replacing_uploaded_evidence(self):
        old = self.source('old', exposure_text_scope='financial_excerpt', exposure_reader_version='1',
                          evidence=[{'evidence_id': 'bad_excerpt', 'passage': 'Hidden filing metadata'}])
        uploaded = self.source('upload', evidence=[{'evidence_id': 'user_evidence', 'passage': 'Uploaded report'}])
        app, records = self.app([old, uploaded])
        with patch.object(exposure_sources.financial_research, '_read_document',
                          return_value='Page 2\nConsolidated revenue USD 100 million.') as read:
            _, valid, errors = exposure_sources.prepare_sources(app, 'company', 'theme')
        read.assert_called_once()
        self.assertNotIn('bad_excerpt', valid)
        self.assertIn('user_evidence', valid)
        self.assertEqual(errors, [])
        self.assertEqual(records['old']['exposure_reader_version'], exposure_sources.financial_research.DOCUMENT_TEXT_VERSION)


if __name__ == '__main__':
    unittest.main()
