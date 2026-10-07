import copy
import unittest
import engine

NODES = [{'node_id':'hardware','parent_id':None,'name':'Hardware','definition':'AI chips','inclusion_rule':'AI-specific','exclusion_rule':'General chips','examples':[],'rationale':'Direct compute'}]

def unit(uid, revenue, lo, hi, point, relationship='direct'):
    return dict(unit_id=uid,revenue=revenue,period='FY2025',currency='USD',scale='millions',external=True,evidence_ids=['e1'],
                relationship_type=relationship,rationale='Source disclosure',scope_check='Outside AI scope',lower=lo,upper=hi,point=point,
                method='disclosed' if point is not None else 'insufficient_evidence',allocations={'hardware':1} if relationship=='direct' else {})

def case(units):
    return dict(total_revenue=100,denominator_evidence_ids=['e1'],period='FY2025',currency='USD',scale='millions',units=units)

def holding(cid,w,sid=None):
    return dict(company_id=cid,weight=w,security_id=sid or cid,snapshot_id='s1',as_of_date='2026-10-03')

class Calculations(unittest.TestCase):
    def test_spec_mixed_business_has_no_point(self):
        e=engine.estimate(case([unit('ai',20,1,1,1),unit('mixed',40,0,1,None),unit('other',40,0,0,0,'excluded')]),NODES,{'e1'})
        self.assertAlmostEqual(e['lower'],.2);self.assertAlmostEqual(e['upper'],.6);self.assertIsNone(e['point'])

    def test_spec_portfolio_example(self):
        estimates={cid:dict(lower=p,upper=p,point=p,review_status='approved') for cid,p in [('a',.5),('b',.1)]}
        d=engine.aggregate([holding('a',.2),holding('b',.8)],[holding('a',.1),holding('b',.9)],estimates)
        self.assertAlmostEqual(d['summary']['portfolio']['point'],.18)
        self.assertAlmostEqual(d['summary']['benchmark']['point'],.14)
        self.assertAlmostEqual(d['summary']['active']['point'],.04)

    def test_negative_active_weight_reverses_bounds(self):
        d=engine.aggregate([holding('a',.2),holding('CASH',.8)],[holding('a',.6),holding('CASH',.4)],{'a':dict(lower=.2,upper=.6,point=None,review_status='approved')})
        self.assertAlmostEqual(d['summary']['active']['lower'],-.24)
        self.assertAlmostEqual(d['summary']['active']['upper'],-.08)
        self.assertIsNone(d['summary']['active']['point'])

    def test_missing_is_unknown_not_zero(self):
        d=engine.aggregate([holding('a',1)],[holding('a',1)],{})
        self.assertEqual(d['coverage']['portfolio'],0)
        self.assertEqual(d['summary']['portfolio']['upper'],1)
        self.assertIsNone(d['summary']['portfolio']['point'])
        self.assertEqual(d['summary']['active']['point'],0)

    def test_unreviewed_result_is_unknown(self):
        d=engine.aggregate([holding('a',1)],[holding('a',1)],{'a':dict(lower=0,upper=0,point=0,review_status='needs_review')})
        self.assertEqual(d['summary']['portfolio']['upper'],1)

    def test_multiple_share_classes(self):
        d=engine.aggregate([holding('a',.3,'a1'),holding('a',.7,'a2')],[holding('a',1)],{'a':dict(lower=.5,upper=.5,point=.5,review_status='approved')})
        self.assertEqual(len(d['companies']),1);self.assertEqual(d['summary']['portfolio']['point'],.5)

    def test_revenue_double_count_rejected(self):
        for mutation in ('duplicate','overlap','exceeds','leaves'):
            a=unit('a',50,1,1,1);b=unit('b',50,1,1,1)
            if mutation=='duplicate':b['unit_id']='a'
            if mutation=='overlap':a['overlap_group']=b['overlap_group']='same_revenue'
            if mutation=='exceeds':b['revenue']=60
            if mutation=='leaves':a['allocations']={'hardware':1.1}
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):engine.estimate(case([a,b]),NODES,{'e1'})

    def test_invalid_denominator_period_source_and_zero(self):
        for key,value in [('total_revenue',0),('total_revenue',-1),('total_revenue',float('nan')),('denominator_evidence_ids',['fake'])]:
            c=case([unit('a',100,1,1,1)]);c[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):engine.estimate(c,NODES,{'e1'})
        for key,value in [('period','FY2024'),('external',False),('currency','EUR'),('evidence_ids',[]),('scope_check','')]:
            u=unit('a',100,0,0,0,'excluded');u[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):engine.estimate(case([u]),NODES,{'e1'})

    def test_residual_never_silently_zero(self):
        e=engine.estimate(case([unit('a',20,1,1,1)]),NODES,{'e1'})
        self.assertEqual(e['lower'],.2);self.assertEqual(e['upper'],1);self.assertIsNone(e['point']);self.assertTrue(e['issues'])

    def test_indirect_does_not_enter_revenue(self):
        with self.assertRaises(ValueError):engine.estimate(case([unit('a',100,1,1,1,'indirect')]),NODES,{'e1'})

    def test_taxonomy_cycles_missing_parents(self):
        for parent in ('hardware','missing'):
            n=copy.deepcopy(NODES);n[0]['parent_id']=parent
            with self.assertRaises(ValueError):engine.taxonomy(n)

    def test_weight_validation(self):
        for rows in ([holding('a',-.1),holding('b',1.1)],[holding('a',.9)],[holding('a',.5),holding('a',.5)],[holding('a',float('nan'))]):
            with self.assertRaises(ValueError):engine.holdings(rows)

if __name__=='__main__':unittest.main()
