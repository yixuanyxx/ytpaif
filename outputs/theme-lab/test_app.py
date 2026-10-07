import base64
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

class AppFlow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        os.environ['THEME_DATA_DIR']=self.temp.name
        import app
        self.app=importlib.reload(app)
        self.theme=self.app.demo()

    def tearDown(self):
        self.temp.cleanup()

    def test_demo_reproducible_and_export(self):
        d=self.app.dashboard(self.theme['id'])
        self.assertAlmostEqual(d['summary']['portfolio']['point'],.18)
        self.assertAlmostEqual(d['summary']['benchmark']['point'],.14)
        self.assertAlmostEqual(d['summary']['active']['point'],.04)
        self.assertEqual(self.app.demo()['id'],self.theme['id'])
        data=self.app.export(self.theme['id'])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('evidence.csv',z.namelist())
            manifest=json.loads(z.read('run_manifest.json'))
            self.assertEqual(manifest['theme']['id'],self.theme['id'])
            self.assertEqual(len(manifest['reviews']),3)

    def test_review_immutable_and_reconciliation_gate(self):
        c=self.app.all_records('cases')[0]
        with self.assertRaises(ValueError):self.app.review({'case_id':c['id'],'decision':'rejected','reviewer':'test','reason':'test'})
        body={'company_id':c['company_id'],'theme_id':self.theme['id'],'case':c['case'],'classification_confidence':'high','exposure_quality':'high','confidence_basis':'Clear source'}
        body['case']['units']=body['case']['units'][:1]
        new=self.app.save_case(body)
        with self.assertRaises(ValueError):self.app.review({'case_id':new['id'],'decision':'approved','reviewer':'test','reason':'test'})

    def test_cutoff_excludes_future_sources(self):
        source=self.app.source_ingest({'company_id':'DEMO_A','filename':'future.txt','content':base64.b64encode(b'Future disclosure').decode(),'publication_date':'2099-01-01','period':'FY2098','url':''})
        _, ids=self.app.evidence_for('DEMO_A',self.theme['id'])
        self.assertNotIn(source['evidence'][0]['evidence_id'],ids)

    def test_versions_are_immutable(self):
        d={**self.theme,'previous_id':self.theme['id'],'description':'Revised scope'}
        revised=self.app.theme_create(d)
        self.assertEqual(revised['version'],2)
        self.assertEqual(self.app.get('themes',self.theme['id'])['version'],1)
        self.assertEqual(revised['theme_id'],self.theme['theme_id'])

    def test_failed_model_does_not_create_zero_case(self):
        evidence=self.app.all_records('sources')[0]['evidence'][0]['evidence_id']
        before=len(self.app.all_records('cases'))
        with patch.dict(os.environ,{'LLM_API_KEY':'','LLM_MODEL':''}):
            with self.assertRaises(ValueError):self.app.analyze({'theme_id':self.theme['id'],'company_id':'DEMO_A','evidence_ids':[evidence]})
        self.assertEqual(before,len(self.app.all_records('cases')))
        self.assertEqual(self.app.all_records('runs')[-1]['status'],'failed')

    def test_provider_call_cap(self):
        with patch.dict(os.environ,{'LLM_API_KEY':'test','LLM_MODEL':'test','LLM_MAX_CALLS':'0'}):
            with self.assertRaisesRegex(ValueError,'cap'):self.app.llm('test',{}, {'usage':[]})

    def test_security_company_mapping(self):
        p=[dict(company_id='DEMO_A',security_id='one',weight=1,snapshot_id='p',as_of_date='2026-10-03')]
        b=[dict(company_id='DEMO_B',security_id='one',weight=1,snapshot_id='b',as_of_date='2026-10-03')]
        with self.assertRaises(ValueError):self.app.dispatch('/api/snapshot',{'portfolio':p,'benchmark':b})

    def test_ai_pipeline_preserves_unknown_allocation(self):
        source=self.app.all_records('sources')[0]
        evidence=source['evidence'][0]['evidence_id']
        facts={'total_revenue':100,'denominator_evidence_ids':[evidence],'period':'FY2025','currency':'USD','scale':'millions',
               'units':[{'unit_id':'u1','activity':'AI hardware','revenue':100,'period':'FY2025','currency':'USD','scale':'millions','external':True,'evidence_ids':[evidence],'overlap_group':None}], 'unresolved_questions':[]}
        classification={'classifications':[{'unit_id':'u1','node_ids':['compute'],'relationship_type':'direct','rationale':'Source describes AI hardware','scope_check':'','evidence_ids':[evidence],'unresolved_questions':[]}],'confidence_band':'high','confidence_basis':'Clear activity evidence'}
        with patch.object(self.app,'llm',side_effect=[facts,classification]):
            c=self.app.analyze({'theme_id':self.theme['id'],'company_id':'DEMO_A','evidence_ids':[evidence]})
        self.assertIsNone(c['estimate']['point'])
        self.assertEqual(c['review_status'],'needs_review')
        self.assertEqual(c['classification_score'],3)
        self.assertEqual(self.app.get('runs',c['run_id'])['status'],'needs_review')
        self.assertEqual(len(self.app.all_records('model_outputs')),2)

    def test_scanned_pdf_warns_instead_of_zero(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=612,height=792);stream=io.BytesIO();writer.write(stream)
        source=self.app.source_ingest({'company_id':'DEMO_A','filename':'scan.pdf','content':base64.b64encode(stream.getvalue()).decode(),'publication_date':'2026-01-01','period':'FY2025','url':''})
        self.assertTrue(source['warnings'])
        self.assertFalse(source['evidence'])

if __name__=='__main__':unittest.main()
