"""Deterministic, review-gated thematic revenue calculations."""
import math

VERSION = '1.0.0'

def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return float(value)

def fraction(value, name):
    n = number(value, name)
    if not 0 <= n <= 1:
        raise ValueError(f'{name} must be between 0 and 1')
    return n

MAX_TIERS = 3  # Theme → Tier-1 → Tier-2 → Tier-3 (Tier-3 only via manual "break down")

# Maximum number of children allowed at each tier depth.
#   Tier-1: up to 5 top-level areas
#   Tier-2: up to 3 activities under each Tier-1 (produced by "generate taxonomy")
#   Tier-3: up to 4 sub-nodes under a Tier-2 (only via manual "break down")
MAX_CHILDREN = {1: 5, 2: 3, 3: 4}

def taxonomy(nodes, require_leaves=False):
    """Validate the taxonomy and return the set of leaf node IDs.

    Required node fields: node_id, name, definition, examples.
    parent_id is null for Tier-1 nodes and a valid node_id otherwise.
    Hierarchy must not exceed MAX_TIERS levels below the theme. Leaves are any
    nodes that are not themselves a parent of another node (so they may sit at
    Tier-2 or Tier-3 depending on how far the taxonomy was broken down).
    """
    if not isinstance(nodes, list) or not nodes:
        raise ValueError('At least one taxonomy node is required')
    if any(not isinstance(n, dict) or not isinstance(n.get('node_id'), str) or not n['node_id'].strip() for n in nodes):
        raise ValueError('Node IDs must be nonempty strings')
    ids = [n['node_id'] for n in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate taxonomy node IDs')
    parents = {n['node_id']: n.get('parent_id') for n in nodes}
    for n in nodes:
        for key in ('node_id', 'name', 'definition', 'examples'):
            if key not in n or (key != 'examples' and not str(n[key]).strip()):
                raise ValueError(f'Taxonomy node requires {key}')
        if not isinstance(n['examples'], list):
            raise ValueError('Node examples must be a list')
        pid = n.get('parent_id')
        if pid is not None and pid not in parents:
            raise ValueError('Unknown parent ID')
        # Walk the ancestor chain to detect cycles AND measure depth.
        seen = set()
        depth = 1  # this node is at least Tier-1
        current = n['node_id']
        while current:
            if current in seen:
                raise ValueError('Taxonomy contains a cycle')
            if current not in parents:
                raise ValueError('Unknown parent ID')
            seen.add(current)
            parent = parents[current]
            if parent is not None:
                depth += 1
            current = parent
        if depth > MAX_TIERS:
            raise ValueError(f'Hierarchy must not exceed {MAX_TIERS} subtheme levels')

    # Enforce the per-tier child-count caps (Tier-1: 5, Tier-2: 3, Tier-3: 4).
    def _depth(nid):
        d, cur, seen = 0, nid, set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            d += 1
            cur = parents.get(cur)
        return d
    tier1_count = sum(1 for p in parents.values() if p is None)
    if tier1_count > MAX_CHILDREN[1]:
        raise ValueError(f'A theme may have at most {MAX_CHILDREN[1]} Tier-1 areas')
    child_counts = {}
    for n in nodes:
        pid = n.get('parent_id')
        if pid is not None:
            child_counts[pid] = child_counts.get(pid, 0) + 1
    for pid, count in child_counts.items():
        child_tier = _depth(pid) + 1
        cap = MAX_CHILDREN.get(child_tier)
        if cap is not None and count > cap:
            raise ValueError(f'A Tier-{child_tier - 1} node may have at most {cap} sub-nodes')

    if require_leaves:
        tier1_ids = {n['node_id'] for n in nodes if n.get('parent_id') is None}
        parent_ids_with_children = {p for p in parents.values() if p}
        for t1 in tier1_ids:
            if t1 not in parent_ids_with_children:
                raise ValueError('Each Tier-1 node must have at least one Tier-2 subtheme')
    leaf_ids = set(ids) - {p for p in parents.values() if p}
    return leaf_ids

def estimate(case, nodes, evidence):
    leaves = taxonomy(nodes)
    total = number(case['total_revenue'], 'Total revenue')
    if total <= 0:
        raise ValueError('Consolidated external revenue must be positive')
    if not case.get('period') or not case.get('currency') or not case.get('scale'):
        raise ValueError('Period, currency and scale are required')
    if not case.get('denominator_evidence_ids') or not set(case['denominator_evidence_ids']) <= evidence:
        raise ValueError('Denominator requires valid source evidence')
    units = case.get('units', [])
    seen, groups, issues = set(), set(), []
    lower = upper = point = accounted = 0.0
    has_point = True
    sub = {n: {'lower': 0.0, 'upper': 0.0, 'point': 0.0} for n in leaves}
    for u in units:
        uid = u['unit_id']
        if uid in seen:
            raise ValueError('Duplicate revenue unit')
        seen.add(uid)
        if u.get('overlap_group'):
            if u['overlap_group'] in groups:
                raise ValueError('Overlapping revenue units cannot be added')
            groups.add(u['overlap_group'])
        if u.get('period') != case['period'] or u.get('currency') != case['currency'] or u.get('scale') != case['scale']:
            raise ValueError('Revenue units must use the denominator period, currency and scale')
        if u.get('external') is not True:
            raise ValueError('Remove intersegment sales before calculating')
        revenue = number(u['revenue'], 'Revenue')
        if revenue < 0:
            raise ValueError('Negative revenue requires review')
        accounted += revenue
        if not u.get('evidence_ids') or not set(u['evidence_ids']) <= evidence:
            raise ValueError('Every revenue unit requires valid evidence IDs')
        if not set(u.get('classification_evidence_ids', [])) <= evidence:
            raise ValueError('Classification evidence IDs must exist')
        if not set(u.get('node_ids', [])) <= leaves:
            raise ValueError('Classifications must map to approved leaves')
        relationship = u['relationship_type']
        if relationship not in ('direct', 'indirect', 'excluded', 'insufficient_evidence'):
            raise ValueError('Unknown relationship type')
        if not u.get('rationale'):
            raise ValueError('Classification requires a rationale')
        if relationship == 'excluded' and not u.get('scope_check'):
            raise ValueError('Zero exposure requires a documented scope check')
        lo, hi = fraction(u['lower'], 'Lower allocation'), fraction(u['upper'], 'Upper allocation')
        p = u.get('point')
        if lo > hi or (p is not None and not lo <= fraction(p, 'Point allocation') <= hi):
            raise ValueError('Allocation bounds are inconsistent')
        if relationship in ('excluded', 'indirect') and (lo or hi or p not in (None, 0)):
            raise ValueError('Excluded and indirect activity cannot count as qualifying revenue')
        if u.get('method') not in ('disclosed', 'derived', 'estimated', 'insufficient_evidence'):
            raise ValueError('Unknown estimation method')
        if u['method'] == 'estimated' and not u.get('assumption'):
            raise ValueError('Estimated allocation requires a documented assumption')
        if u['method'] == 'insufficient_evidence' and p is not None:
            raise ValueError('Insufficient evidence cannot have a point estimate')
        allocations = u.get('allocations', {})
        if not set(allocations) <= leaves:
            raise ValueError('Allocations must refer to approved leaves')
        shares = {k: fraction(v, 'Leaf share') for k, v in allocations.items()}
        if relationship == 'direct' and not shares and not u.get('node_ids'):
            raise ValueError('Direct activity requires an approved leaf classification; unsupported leaf splits may remain unallocated')
        if sum(shares.values()) > 1 + 1e-10:
            raise ValueError('Leaf shares double count revenue')
        lower += revenue * lo
        upper += revenue * hi
        if p is None:
            has_point = False
        else:
            point += revenue * p
        for node, share in shares.items():
            sub[node]['lower'] += revenue * lo * share / total
            sub[node]['upper'] += revenue * hi * share / total
            if p is None and share:
                sub[node]['point'] = None
            elif sub[node]['point'] is not None:
                sub[node]['point'] += revenue * (p or 0) * share / total
    if accounted > total + total * 1e-8:
        raise ValueError('Revenue units exceed consolidated external revenue')
    residual = max(0.0, total - accounted)
    if residual > total * 1e-8:
        issues.append('Unallocated residual revenue retained as unknown; reviewer must resolve reconciliation')
        upper += residual
        has_point = False
    if not units:
        issues.append('No revenue units assessed')
    lo, hi = lower / total, upper / total
    sub_lo = sum(v['lower'] for v in sub.values())
    sub_hi = sum(v['upper'] for v in sub.values())
    return {'lower': lo, 'upper': hi, 'point': point / total if has_point else None,
            'residual_revenue': residual, 'issues': issues, 'subthemes': sub,
            'unresolved_subtheme': {'lower': lo-sub_lo, 'upper': hi-sub_hi},
            'calculation_version': VERSION}

def holdings(rows):
    if not rows:
        raise ValueError('Both snapshots require holdings')
    dates, snapshots, securities, result = set(), set(), set(), {}
    total = 0
    for r in rows:
        for field in ('snapshot_id', 'as_of_date', 'security_id', 'company_id'):
            if not r.get(field):
                raise ValueError(f'Holdings require {field}')
        dates.add(r['as_of_date']); snapshots.add(r['snapshot_id'])
        if r['security_id'] in securities:
            raise ValueError('Duplicate security in snapshot')
        securities.add(r['security_id'])
        weight = fraction(float(r['weight']), 'Weight')
        total += weight
        cid = r['company_id']
        result[cid] = result.get(cid, 0) + weight
    if len(dates) != 1 or len(snapshots) != 1 or abs(total-1) > 1e-6:
        raise ValueError('Snapshot must have one date, one ID and weights summing to 1 (tolerance 0.000001)')
    return result

def aggregate(portfolio, benchmark, estimates):
    p, b = holdings(portfolio), holdings(benchmark)
    if portfolio[0]['as_of_date'] != benchmark[0]['as_of_date']:
        raise ValueError('Portfolio and benchmark dates must match')
    summary = {k: {'lower': 0.0, 'upper': 0.0, 'point': 0.0} for k in ('portfolio', 'benchmark', 'active')}
    coverage = {'portfolio': 0.0, 'benchmark': 0.0}
    rows = []
    cash = {'portfolio': p.get('CASH', 0), 'benchmark': b.get('CASH', 0)}
    for cid in sorted(set(p) | set(b)):
        e = estimates.get(cid)
        assessed = bool(e and e.get('review_status') == 'approved')
        lo, hi, point = (e['lower'], e['upper'], e['point']) if assessed else (0, 1, None)
        if cid == 'CASH':
            lo = hi = point = 0
            assessed = True
        weights = {'portfolio': p.get(cid, 0), 'benchmark': b.get(cid, 0), 'active': p.get(cid, 0)-b.get(cid, 0)}
        contribution = {}
        for name, w in weights.items():
            a, z = (w*lo, w*hi) if w >= 0 else (w*hi, w*lo)
            val = w*point if point is not None else (0 if w == 0 else None)
            contribution[name] = {'lower': a, 'upper': z, 'point': val}
            summary[name]['lower'] += a; summary[name]['upper'] += z
            if val is None:
                summary[name]['point'] = None
            elif summary[name]['point'] is not None:
                summary[name]['point'] += val
        if assessed and cid != 'CASH':
            for name in coverage:
                coverage[name] += weights[name]
        rows.append({'company_id': cid, 'assessed': assessed, 'weights': weights, 'exposure': {'lower': lo, 'upper': hi, 'point': point}, 'contributions': contribution})
    rows.sort(key=lambda r: abs(r['contributions']['active']['point'] or r['contributions']['active']['upper']), reverse=True)
    return {'summary': summary, 'coverage': coverage, 'cash': cash, 'companies': rows}
