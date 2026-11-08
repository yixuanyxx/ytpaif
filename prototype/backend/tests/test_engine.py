import copy
import unittest
import engine

NODES = [
    {'node_id': 'hardware', 'parent_id': None, 'name': 'Hardware',
     'definition': 'Dedicated AI computing hardware', 'examples': ['AI accelerators']},
    {'node_id': 'chips', 'parent_id': 'hardware', 'name': 'AI Chips',
     'definition': 'Custom silicon for training and inference', 'examples': ['GPUs', 'TPUs']}
]

def unit(uid, revenue, lo, hi, point, relationship='direct', leaf='chips'):
    return {
        'unit_id': uid, 'revenue': revenue, 'period': 'FY2025', 'currency': 'USD', 'scale': 'millions',
        'external': True, 'evidence_ids': ['e1'], 'relationship_type': relationship,
        'rationale': 'Source disclosure', 'scope_check': 'Outside AI scope' if relationship == 'excluded' else '',
        'lower': lo, 'upper': hi, 'point': point,
        'method': 'disclosed' if point is not None else ('insufficient_evidence' if relationship != 'excluded' else 'derived'),
        'allocations': {leaf: 1} if relationship == 'direct' and leaf else {}
    }

def case(units):
    return {
        'total_revenue': 100, 'denominator_evidence_ids': ['e1'],
        'period': 'FY2025', 'currency': 'USD', 'scale': 'millions',
        'units': units
    }

def holding(cid, w, sid=None):
    return {
        'company_id': cid, 'weight': w, 'security_id': sid or cid,
        'snapshot_id': 's1', 'as_of_date': '2026-10-03'
    }

class EngineCalculations(unittest.TestCase):
    def test_spec_mixed_business_has_no_point(self):
        e = engine.estimate(case([
            unit('ai', 20, 1, 1, 1),
            unit('mixed', 40, 0, 1, None),
            unit('other', 40, 0, 0, 0, 'excluded', leaf=None)
        ]), NODES, {'e1'})
        self.assertAlmostEqual(e['lower'], 0.2)
        self.assertAlmostEqual(e['upper'], 0.6)
        self.assertIsNone(e['point'])

    def test_spec_portfolio_example(self):
        estimates = {
            cid: {'lower': p, 'upper': p, 'point': p, 'review_status': 'approved'}
            for cid, p in [('a', 0.5), ('b', 0.1)]
        }
        d = engine.aggregate([holding('a', 0.2), holding('b', 0.8)],
                             [holding('a', 0.1), holding('b', 0.9)], estimates)
        self.assertAlmostEqual(d['summary']['portfolio']['point'], 0.18)
        self.assertAlmostEqual(d['summary']['benchmark']['point'], 0.14)
        self.assertAlmostEqual(d['summary']['active']['point'], 0.04)

    def test_negative_active_weight_reverses_bounds(self):
        d = engine.aggregate(
            [holding('a', 0.2), holding('CASH', 0.8)],
            [holding('a', 0.6), holding('CASH', 0.4)],
            {'a': {'lower': 0.2, 'upper': 0.6, 'point': None, 'review_status': 'approved'}}
        )
        self.assertAlmostEqual(d['summary']['active']['lower'], -0.24)
        self.assertAlmostEqual(d['summary']['active']['upper'], -0.08)
        self.assertIsNone(d['summary']['active']['point'])

    def test_missing_keeps_uncertainty_while_dashboard_point_starts_at_zero(self):
        d = engine.aggregate([holding('a', 1)], [holding('a', 1)], {})
        self.assertEqual(d['coverage']['portfolio'], 0)
        self.assertEqual(d['summary']['portfolio']['upper'], 1)
        self.assertEqual(d['summary']['portfolio']['point'], 0)
        self.assertEqual(d['summary']['active']['point'], 0)

    def test_unreviewed_calculated_result_is_included_provisionally(self):
        d = engine.aggregate([holding('a', 1)], [holding('a', 1)],
                             {'a': {'lower': 0.2, 'upper': 0.6, 'point': 0.6,
                                    'review_status': 'needs_review', 'point_basis': 'upper'}})
        self.assertEqual(d['summary']['portfolio']['point'], 0.6)
        self.assertTrue(d['companies'][0]['assessed'])
        self.assertEqual(d['companies'][0]['review_status'], 'needs_review')
        self.assertEqual(d['companies'][0]['point_basis'], 'upper')

    def test_multiple_share_classes(self):
        d = engine.aggregate([holding('a', 0.3, 'a1'), holding('a', 0.7, 'a2')],
                             [holding('a', 1)],
                             {'a': {'lower': 0.5, 'upper': 0.5, 'point': 0.5, 'review_status': 'approved'}})
        self.assertEqual(len(d['companies']), 1)
        self.assertAlmostEqual(d['summary']['portfolio']['point'], 0.5)

    def test_revenue_double_count_rejected(self):
        for mutation in ('duplicate', 'overlap', 'exceeds', 'leaves'):
            a = unit('a', 50, 1, 1, 1)
            b = unit('b', 50, 1, 1, 1)
            if mutation == 'duplicate':
                b['unit_id'] = 'a'
            if mutation == 'overlap':
                a['overlap_group'] = b['overlap_group'] = 'same_revenue'
            if mutation == 'exceeds':
                b['revenue'] = 60
            if mutation == 'leaves':
                a['allocations'] = {'chips': 1.1}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                engine.estimate(case([a, b]), NODES, {'e1'})

    def test_invalid_denominator_period_source_and_zero(self):
        for key, value in [('total_revenue', 0), ('total_revenue', -1),
                           ('total_revenue', float('nan')), ('denominator_evidence_ids', ['fake'])]:
            c = case([unit('a', 100, 1, 1, 1)])
            c[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                engine.estimate(c, NODES, {'e1'})
        for key, value in [('period', 'FY2024'), ('external', False), ('currency', 'EUR'),
                           ('evidence_ids', []), ('scope_check', '')]:
            u = unit('a', 100, 0, 0, 0, 'excluded', leaf=None)
            u[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                engine.estimate(case([u]), NODES, {'e1'})

    def test_residual_never_silently_zero(self):
        e = engine.estimate(case([unit('a', 20, 1, 1, 1)]), NODES, {'e1'})
        self.assertEqual(e['lower'], 0.2)
        self.assertEqual(e['upper'], 1.0)
        self.assertIsNone(e['point'])
        self.assertTrue(e['issues'])

    def test_indirect_does_not_enter_revenue(self):
        with self.assertRaises(ValueError):
            engine.estimate(case([unit('a', 100, 1, 1, 1, 'indirect')]), NODES, {'e1'})

    def test_leaf_matches_roll_up_without_double_counting(self):
        nodes = [
            {'node_id': 'theme', 'parent_id': None, 'name': 'Theme', 'definition': 'Theme', 'examples': []},
            {'node_id': 'solar', 'parent_id': 'theme', 'name': 'Solar', 'definition': 'Solar', 'examples': []},
            {'node_id': 'storage', 'parent_id': 'theme', 'name': 'Storage', 'definition': 'Storage', 'examples': []},
        ]
        shared = unit('integrated-system', 50, 1, 1, 1, leaf='solar')
        shared['node_ids'] = ['solar', 'storage']
        shared['allocations'] = {}
        standalone = unit('battery', 20, 1, 1, 1, leaf='storage')
        result = engine.estimate(case([shared, standalone]), nodes, {'e1'})
        self.assertEqual(result['node_exposures']['solar']['revenue_unit_ids'], ['integrated-system'])
        self.assertEqual(result['node_exposures']['storage']['revenue_unit_ids'], ['battery', 'integrated-system'])
        self.assertEqual(result['node_exposures']['theme']['revenue_unit_ids'], ['battery', 'integrated-system'])
        self.assertAlmostEqual(result['node_exposures']['theme']['point'], .7)

    def test_evidenced_leaf_splits_add_once_at_parent(self):
        nodes = [
            {'node_id': 'theme', 'parent_id': None, 'name': 'Theme', 'definition': 'Theme', 'examples': []},
            {'node_id': 'a', 'parent_id': 'theme', 'name': 'A', 'definition': 'A', 'examples': []},
            {'node_id': 'b', 'parent_id': 'theme', 'name': 'B', 'definition': 'B', 'examples': []},
        ]
        mixed = unit('mixed', 80, 1, 1, 1, leaf='a')
        mixed['allocations'] = {'a': .25, 'b': .75}
        result = engine.estimate(case([mixed]), nodes, {'e1'})
        self.assertAlmostEqual(result['node_exposures']['a']['point'], .2)
        self.assertAlmostEqual(result['node_exposures']['b']['point'], .6)
        self.assertAlmostEqual(result['node_exposures']['theme']['point'], .8)

    def test_nested_taxonomy_is_accepted_and_preserves_hierarchy(self):
        nested = [{
            'node_id': 'theme', 'name': 'Theme', 'definition': 'Theme', 'examples': [],
            'children': [{
                'node_id': 'leaf', 'name': 'Leaf', 'definition': 'Leaf', 'examples': [], 'children': []
            }],
        }]
        self.assertEqual(engine.taxonomy(nested), {'leaf'})
        self.assertEqual(engine.nest_taxonomy(nested), nested)

    def test_taxonomy_cycles_and_nesting_limits(self):
        # Cycle
        n_cycle = copy.deepcopy(NODES)
        n_cycle[0]['parent_id'] = 'hardware'
        with self.assertRaises(ValueError):
            engine.taxonomy(n_cycle)
        # Three tiers are supported; a fourth tier exceeds the limit.
        n_deep = copy.deepcopy(NODES)
        n_deep.append({'node_id': 'subchip', 'parent_id': 'chips', 'name': 'Subchip',
                       'definition': 'Third tier', 'examples': []})
        self.assertEqual(engine.taxonomy(n_deep), {'subchip'})
        n_deep.append({'node_id': 'fourth', 'parent_id': 'subchip', 'name': 'Fourth',
                       'definition': 'Too deep', 'examples': []})
        with self.assertRaises(ValueError):
            engine.taxonomy(n_deep)

    def test_weight_validation(self):
        for rows in ([holding('a', -0.1), holding('b', 1.1)],
                     [holding('a', 0.9)],
                     [holding('a', 0.5), holding('a', 0.5)],
                     [holding('a', float('nan'))]):
            with self.assertRaises(ValueError):
                engine.holdings(rows)

if __name__ == '__main__':
    unittest.main()
