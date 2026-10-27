"""Synthetic fixtures restricted to isolated automated tests."""
import base64

def demo(app):
    existing = [t for t in app.all_records('themes') if t.get('theme_name') == 'AI Infrastructure' and t.get('theme_description', '').startswith('Synthetic worked examples')]
    if existing:
        return existing[0]
    nodes = [
        {'node_id': 'compute',      'parent_id': None,      'name': 'Compute Hardware',
         'definition': 'Dedicated AI chips and systems', 'examples': ['AI accelerators']},
        {'node_id': 'compute_design','parent_id': 'compute', 'name': 'AI Chip Design',
         'definition': 'Companies designing AI-specific chips', 'examples': ['GPU designers']},
        {'node_id': 'compute_fab',  'parent_id': 'compute', 'name': 'AI Chip Fabrication',
         'definition': 'Foundries manufacturing AI chips', 'examples': ['TSMC AI orders']},
        {'node_id': 'datacenter',   'parent_id': None,      'name': 'Data Centre Infrastructure',
         'definition': 'Infrastructure explicitly serving AI workloads', 'examples': ['AI server racks']},
        {'node_id': 'dc_power',     'parent_id': 'datacenter','name': 'Power & Cooling',
         'definition': 'Power delivery and cooling for AI data centres', 'examples': ['Liquid cooling systems']},
        {'node_id': 'dc_server',    'parent_id': 'datacenter','name': 'Server Hardware',
         'definition': 'AI-optimised server platforms', 'examples': ['AI inference servers']},
    ]
    theme = app.theme_create({
        'theme_name': 'AI Infrastructure',
        'theme_description': 'Synthetic worked examples from the specification; no real-company investment conclusions.',
        'included_activities': 'Dedicated AI hardware and supported AI infrastructure',
        'excluded_activities': 'General enterprise computing; indirect beneficiaries',
        'positive_examples': 'AI accelerators / dedicated AI server revenue qualifies',
        'negative_examples': 'General cloud revenue without an AI allocation does not qualify',
        'nodes': nodes
    })
    for cid, exposure in [('DEMO_A', .5), ('DEMO_B', .1), ('DEMO_MIXED', None)]:
        app.put('companies', {
            'id': cid,
            'name': {'DEMO_A': 'Company A', 'DEMO_B': 'Company B', 'DEMO_MIXED': 'Mixed business'}[cid] + ' (synthetic)',
            'bloomberg_ticker': cid, 'sedol': '', 'jurisdiction': 'Example', 'created_at': app.now()
        })
        text = 'SYNTHETIC DEMONSTRATION. Consolidated external FY2025 revenue is USD 100 million. '
        if exposure is not None:
            text += f'Qualifying AI hardware revenue is USD {exposure*100:g} million; remaining revenue is unrelated consumer services.'
        else:
            text += 'AI hardware revenue 20 million; mixed data centre revenue 40 million with undisclosed AI share; excluded consumer services revenue 40 million.'
        source = app.source_ingest({
            'company_id': cid, 'publication_date': '2026-01-01', 'period': 'FY2025',
            'filename': cid + '.txt',
            'content': base64.b64encode(text.encode()).decode(),
            'url': '', 'source_type': 'synthetic_example'
        })
        eid = source['evidence'][0]['evidence_id']
        def unit(uid, revenue, lo, hi, point, relationship, leaf=None):
            return {
                'unit_id': uid, 'activity': uid, 'revenue': revenue,
                'period': 'FY2025', 'currency': 'USD', 'scale': 'millions', 'external': True,
                'evidence_ids': [eid], 'overlap_group': None,
                'lower': lo, 'upper': hi, 'point': point,
                'relationship_type': relationship,
                'rationale': 'Synthetic source explicitly describes activity',
                'scope_check': 'Consumer services fall outside approved AI infrastructure scope' if relationship == 'excluded' else '',
                'method': 'disclosed' if point is not None else 'insufficient_evidence',
                'assumption': '', 'allocations': {leaf: 1} if leaf else {}
            }
        if exposure is not None:
            units = [unit('hardware', exposure*100, 1, 1, 1, 'direct', 'compute_fab'),
                     unit('other', (1-exposure)*100, 0, 0, 0, 'excluded')]
        else:
            units = [unit('hardware', 20, 1, 1, 1, 'direct', 'compute_design'),
                     unit('mixed', 40, 0, 1, None, 'insufficient_evidence', 'dc_server'),
                     unit('other', 40, 0, 0, 0, 'excluded')]
        case = app.save_case({
            'company_id': cid, 'theme_id': theme['id'],
            'case': {'total_revenue': 100, 'denominator_evidence_ids': [eid],
                     'period': 'FY2025', 'currency': 'USD', 'scale': 'millions', 'units': units},
            'classification_confidence': 'high',
            'exposure_quality': 'high' if exposure is not None else 'low',
            'confidence_basis': 'Synthetic disclosed figures; mixed business retains an unknown allocation'
        })
        app.review({'case_id': case['id'], 'decision': 'approved',
                'reviewer': 'Demo fixture', 'reason': 'Verified synthetic worked example'})

    def row(cid, w, label):
        return {'snapshot_id': label, 'as_of_date': '2026-10-03',
                'company_id': cid, 'security_id': cid + '_ORD', 'weight': w}
    app.snapshot_create({
        'name': 'Specification example · synthetic',
        'portfolio': [row('DEMO_A', .2, 'demo_p'), row('DEMO_B', .8, 'demo_p')],
        'benchmark': [row('DEMO_A', .1, 'demo_b'), row('DEMO_B', .9, 'demo_b')]
    })
    return theme

