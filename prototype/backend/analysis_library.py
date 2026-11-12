"""Reusable company/theme results backed by the persisted analysis cases.

Portfolio weights do not belong to a company exposure calculation. A saved
case therefore serves every portfolio containing that company under the same
theme scope, reporting period and calculation version.
"""
import hashlib
import json
import re
from datetime import date

import engine


def scope_key(theme):
    fields = ('theme_name', 'theme_description', 'included_activities', 'excluded_activities',
              'positive_examples', 'negative_examples', 'additional_information',
              'cutoff', 'geographic_scope', 'exposure_basis', 'reporting_period')
    scope = {field: theme.get(field) for field in fields}
    scope['nodes'] = engine.nest_taxonomy(theme.get('nodes', []))
    return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def display_point(estimate):
    """Return the comparable calculated exposure, never an upper bound."""
    if estimate.get('calculation_status') in ('undetermined', 'unavailable'):
        return None, 'unavailable'
    point = estimate.get('point')
    if isinstance(point, (int, float)) and 0 <= point <= 1:
        return point, 'point'
    return None, 'unavailable'


def result_fields(estimate, confidence_percent=None):
    if confidence_percent is not None:
        confidence_percent = engine.number(confidence_percent, 'AI confidence percentage')
        if not 0 <= confidence_percent <= 100:
            raise ValueError('AI confidence percentage must be between 0 and 100')
    point, _ = display_point(estimate)
    return {'exposure_percent': None if point is None else point * 100,
            'confidence_percent': confidence_percent}


def source_signature(sources):
    fields = ('id', 'url', 'period', 'publication_date', 'sha256')
    inventory = sorted(({**{field: source.get(field) for field in fields},
                         'period': source.get('period') or source.get('reporting_period') or ''}
                        for source in sources),
                       key=lambda row: row.get('id') or '')
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def is_compatible(case, theme):
    """Whether a saved result still describes the selected theme scope.

    Compatibility deliberately ignores the calculation version. Dashboards
    may display a compatible prior result while a newer methodology reruns it;
    only ``is_current`` results are reusable by the analysis pipeline.
    """
    if case.get('theme_id') != theme.get('id') or case.get('review_status') == 'rejected':
        return False
    if case.get('analysis_scope_key'):
        if case['analysis_scope_key'] != scope_key(theme):
            return False
    elif case.get('taxonomy_updated_at') != theme.get('updated_at', theme.get('created_at')):
        return False
    target = str(theme.get('reporting_period') or '').strip()
    if target:
        normalise = lambda value: re.sub(r'[^a-z0-9]', '', str(value).lower()).removeprefix('fy')
        if normalise(case.get('case', {}).get('period', '')) != normalise(target):
            return False
    return True


def is_current(case, theme, sources=None):
    if not is_compatible(case, theme):
        return False
    if case.get('estimate', {}).get('calculation_version') != engine.VERSION:
        return False
    target = str(theme.get('reporting_period') or '').strip()
    if sources is not None:
        cutoff = theme.get('cutoff') or date.today().isoformat()
        eligible = [source for source in sources if source.get('company_id') == case.get('company_id')
                    and source.get('publication_date') and source['publication_date'] <= cutoff]
        if eligible and case.get('source_signature'):
            if case['source_signature'] != source_signature(eligible):
                return False
        elif eligible and not target:
            source_years = [year for source in eligible for year in re.findall(r'20\d{2}', str(source.get('period', '')))]
            case_years = re.findall(r'20\d{2}', str(case.get('case', {}).get('period', '')))
            if source_years and case_years and max(source_years) > max(case_years):
                return False
    return True


def display_results(cases, themes):
    """Latest scope-compatible result per company, including stale versions."""
    by_theme = {theme['id']: theme for theme in themes}
    latest = {}
    for case in sorted(cases, key=lambda row: row.get('created_at') or ''):
        theme = by_theme.get(case.get('theme_id'))
        if theme and is_compatible(case, theme):
            latest[(case.get('company_id'), case.get('theme_id'))] = case
    return [{**case,
             **result_fields(case['estimate'], case.get('confidence_percent')),
             'analysis_reusable': is_current(case, by_theme[case['theme_id']]),
             'calculation_stale': case.get('estimate', {}).get('calculation_version') != engine.VERSION}
            for case in latest.values()]


def current_results(cases, themes, sources=None):
    by_theme = {theme['id']: theme for theme in themes}
    latest = {}
    # Storage returns cases by creation time; sort as well for callers using an
    # in-memory store so persistence/reload cannot select a different revision.
    for case in sorted(cases, key=lambda row: row.get('created_at') or ''):
        latest[(case.get('company_id'), case.get('theme_id'))] = case
    result = []
    for case in latest.values():
        theme = by_theme.get(case.get('theme_id'))
        if theme and is_current(case, theme, sources):
            result.append({**case, **result_fields(case['estimate'], case.get('confidence_percent')),
                           'analysis_reusable': True})
    return result
