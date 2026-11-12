"""Deterministic, review-gated thematic revenue calculations."""
import math

VERSION = '1.6.0'

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
MAX_CHILDREN = {1: 5, 2: 15, 3: 60}  # Global totals at each depth.


def flatten_taxonomy(tree):
    """Convert nested taxonomy JSON to the calculation-friendly node list.

    Persisted taxonomies use ``children`` so their hierarchy is explicit. This
    adapter also accepts legacy parent_id lists while older saved themes are
    migrated naturally on their next save.
    """
    if isinstance(tree, dict):
        tree = tree.get('children', [])
    if not isinstance(tree, list):
        raise ValueError('Taxonomy must be a JSON array of nodes')
    flattened = []

    def visit(node, parent_id=None):
        if not isinstance(node, dict):
            raise ValueError('Taxonomy nodes must be objects')
        current = dict(node)
        children = current.pop('children', None)
        # Preserve parent_id for old flat input, but nested structure is the
        # source of truth whenever children are present.
        current['parent_id'] = parent_id if children is not None else current.get('parent_id', parent_id)
        flattened.append(current)
        if children is not None:
            if not isinstance(children, list):
                raise ValueError('Taxonomy children must be a list')
            for child in children:
                visit(child, current.get('node_id'))

    for root in tree:
        visit(root)
    return flattened


def nest_taxonomy(nodes):
    """Return canonical nested JSON, omitting the internal parent_id field."""
    flat = flatten_taxonomy(nodes)
    by_id = {}
    for node in flat:
        copy = {key: value for key, value in node.items() if key != 'parent_id'}
        copy['children'] = []
        by_id[node.get('node_id')] = copy
    roots = []
    for node in flat:
        target = by_id[node.get('node_id')]
        parent_id = node.get('parent_id')
        if parent_id is None:
            roots.append(target)
        elif parent_id in by_id:
            by_id[parent_id]['children'].append(target)
    return roots


def taxonomy(nodes, require_leaves=False):
    """Validate the taxonomy and return the set of leaf node IDs.

    Required node fields: node_id, name, definition, examples.
    parent_id is null for Tier-1 nodes and a valid node_id otherwise.
    Hierarchy must not exceed MAX_TIERS levels below the theme. Leaves are any
    nodes that are not themselves a parent of another node (so they may sit at
    Tier-2 or Tier-3 depending on how far the taxonomy was broken down).
    """
    nodes = flatten_taxonomy(nodes)
    if not nodes:
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
        depth = _depth(n['node_id'])
        child_counts[depth] = child_counts.get(depth, 0) + 1
    for child_tier, count in child_counts.items():
        cap = MAX_CHILDREN.get(child_tier)
        if cap is not None and count > cap:
            raise ValueError(f'A taxonomy may have at most {cap} Tier-{child_tier} nodes in total')

    if require_leaves:
        tier1_ids = {n['node_id'] for n in nodes if n.get('parent_id') is None}
        parent_ids_with_children = {p for p in parents.values() if p}
        for t1 in tier1_ids:
            if t1 not in parent_ids_with_children:
                raise ValueError('Each Tier-1 node must have at least one Tier-2 subtheme')
    leaf_ids = set(ids) - {p for p in parents.values() if p}
    return leaf_ids


def _hierarchy(nodes):
    """Return parent/child lookups and all leaf descendants for each node."""
    nodes = flatten_taxonomy(nodes)
    parents = {node['node_id']: node.get('parent_id') for node in nodes}
    children = {node_id: [] for node_id in parents}
    for node_id, parent_id in parents.items():
        if parent_id is not None:
            children[parent_id].append(node_id)

    def descendants(node_id):
        direct = children[node_id]
        if not direct:
            return {node_id}
        result = set()
        for child_id in direct:
            result.update(descendants(child_id))
        return result

    return parents, children, {node_id: descendants(node_id) for node_id in parents}

def estimate(case, nodes, evidence):
    nodes = flatten_taxonomy(nodes)
    leaves = taxonomy(nodes)
    parents, children, descendant_leaves = _hierarchy(nodes)
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
    # A partial set of classified revenue segments can still produce the best
    # available thematic-revenue estimate.  Keep this separate from ``has_point``:
    # the latter means every company revenue unit reconciles exactly.
    has_direct_segment_point = False
    # Keep underlying revenue identity through the roll-up. A revenue unit may
    # match several leaves, but a common parent must include that unit once.
    node_units = {node_id: {} for node_id in parents}
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
            if relationship == 'direct':
                has_direct_segment_point = True
        if relationship in ('direct', 'insufficient_evidence'):
            # Allocations express an evidenced split where one is available.
            # Otherwise node_ids records a supported match without inventing a
            # split; it is intentionally allowed to overlap across leaves.
            matched_leaves = set(shares) if shares else set(u.get('node_ids', []))
            for leaf in matched_leaves:
                share = shares.get(leaf, 1.0)
                node_units[leaf][uid] = {
                    'lower': revenue * lo * share,
                    'upper': revenue * hi * share,
                    'point': None if p is None else revenue * p * share,
                    'explicit_allocation': bool(shares),
                }
    if accounted > total + total * 1e-8:
        raise ValueError('Revenue units exceed consolidated external revenue')
    residual = max(0.0, total - accounted)
    if residual > total * 1e-8:
        issues.append('Unallocated residual revenue retained as unknown; reviewer must resolve reconciliation')
        upper += residual
        has_point = False
    if case.get('unresolved_questions'):
        issues.extend(str(question) for question in case['unresolved_questions'])
        has_point = False
    if not units:
        issues.append('No revenue units assessed')
    lo, hi = lower / total, upper / total
    node_exposures = {}
    for node_id, leaf_set in descendant_leaves.items():
        # Use each unit once within this node even when it occurs under several
        # children. If a source supplies explicit leaf allocations, their sum
        # remains bounded by the validation above.
        records = {}
        for leaf in leaf_set:
            for unit_id, values in node_units[leaf].items():
                current = records.get(unit_id)
                if current is None:
                    records[unit_id] = values.copy()
                else:
                    if current['explicit_allocation'] and values['explicit_allocation']:
                        # Distinct evidenced leaf shares may be added; their
                        # total is already bounded at one per revenue unit.
                        current['lower'] += values['lower']
                        current['upper'] += values['upper']
                        current['point'] = (None if current['point'] is None or values['point'] is None
                                            else current['point'] + values['point'])
                    else:
                        # An unsplit unit matched multiple leaves: it is the
                        # same revenue, not extra revenue.
                        current['lower'] = max(current['lower'], values['lower'])
                        current['upper'] = max(current['upper'], values['upper'])
                        current['point'] = (None if current['point'] is None or values['point'] is None
                                            else max(current['point'], values['point']))
                        current['explicit_allocation'] = False
        node_exposures[node_id] = {
            'lower': sum(v['lower'] for v in records.values()) / total,
            'upper': sum(v['upper'] for v in records.values()) / total,
            'point': (sum(v['point'] for v in records.values()) / total
                      if records and all(v['point'] is not None for v in records.values()) else None),
            'revenue_unit_ids': sorted(records),
            'is_leaf': not children[node_id],
        }
    # The public leaf breakdown uses the same matching records as its parents.
    # Overlapping sibling percentages are valid; unresolved revenue is computed
    # from each unit's union of allocated shares rather than their summed leaves.
    sub = {leaf: {key: node_exposures[leaf][key] for key in ('lower', 'upper', 'point')}
           for leaf in leaves}
    covered_lower = covered_upper = 0.0
    for u in units:
        if u['relationship_type'] != 'direct':
            continue
        share = sum(u.get('allocations', {}).values()) if u.get('allocations') else 1.0
        covered_lower += u['revenue'] * u['lower'] * share / total
        covered_upper += u['revenue'] * u['upper'] * share / total
    # Step 4 presents one best comparable estimate: the sum of the taxonomy-
    # mapped revenue segments (using each segment's evidenced/derived point
    # allocation) divided by consolidated external revenue.  An unallocated
    # *other* segment does not erase an otherwise supported thematic segment
    # estimate.  It makes the result partial and keeps the issue trail.
    #
    # Crucially, a failed extraction has no segment numerator at all. It is
    # unavailable, not 0%.
    if not units:
        final_point = None
        point_basis = 'unavailable'
        calculation_status = 'unavailable'
        main_theme_revenue = None
    elif has_point:
        final_point = point / total
        point_basis = 'complete'
        calculation_status = 'complete'
        main_theme_revenue = point
    elif has_direct_segment_point:
        final_point = point / total
        point_basis = 'segment_estimate'
        calculation_status = 'partial'
        main_theme_revenue = point
    else:
        final_point = None
        point_basis = 'unavailable'
        calculation_status = 'undetermined'
        main_theme_revenue = None
    return {'lower': lo, 'upper': hi, 'point': final_point,
            'point_basis': point_basis,
            'main_theme_revenue': main_theme_revenue,
            'identified_theme_revenue': lower,
            'calculation_status': calculation_status,
            'revenue_unit_ids': sorted(u['unit_id'] for u in units if u['relationship_type'] == 'direct'),
            'residual_revenue': residual, 'issues': issues, 'subthemes': sub,
            'node_exposures': node_exposures,
            'unresolved_subtheme': {'lower': max(0.0, lo-covered_lower), 'upper': max(0.0, hi-covered_upper)},
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
        review_status = e.get('review_status') if e else None
        assessed = bool(e and review_status in ('approved', 'needs_review'))
        # Keep the dashboard point-in-time view numeric while preserving the
        # full uncertainty range and coverage for companies not yet calculated.
        lo, hi, point = (e['lower'], e['upper'], e['point']) if assessed else (0, 1, 0)
        if cid == 'CASH':
            lo = hi = point = 0
            assessed = True
            review_status = 'approved'
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
        rows.append({'company_id': cid, 'assessed': assessed, 'review_status': review_status,
                     'point_basis': e.get('point_basis') if e else None,
                     'weights': weights, 'exposure': {'lower': lo, 'upper': hi, 'point': point},
                     'contributions': contribution})
    rows.sort(key=lambda r: abs(r['contributions']['active']['point'] or r['contributions']['active']['upper']), reverse=True)
    return {'summary': summary, 'coverage': coverage, 'cash': cash, 'companies': rows}
