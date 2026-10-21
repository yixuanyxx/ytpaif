"""Conservative extraction of explicitly stated financial figures, with citations.

Ambiguous tables, currencies, and company/segment scope are left unresolved.
No market capitalisation is inferred from revenue or historic share counts.
"""
import re

AMOUNT = r'(?P<currency>USD|SGD|HKD|EUR|GBP|JPY|CNY|RMB|TWD|INR|AUD|CAD|US\$|S\$|HK\$)\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>trillion|billion|million|thousand|bn|mn)?\b'
PATTERNS = {
    'market_cap': re.compile(r'\bmarket\s+(?:capitali[sz]ation|cap)\s*(?:was|is|of|at|:|=|stood at|amounted to)?\s*' + AMOUNT, re.I),
    'revenue': re.compile(r'\b(?:total|consolidated|group)(?:\s+external)?(?:\s+(?:FY\s*)?\d{4})?\s+revenues?\s*(?:was|is|of|:|=|amounted to)?\s*' + AMOUNT, re.I),
}


def extract(source):
    facts = []
    for evidence in source.get('evidence', []):
        text = evidence.get('passage', '')
        for metric, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                amount = match.group('amount')
                # Reject malformed grouping rather than silently changing the value.
                if ',' in amount and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?', amount):
                    continue
                facts.append({
                    'metric': metric,
                    'display': ' '.join(filter(None, [match.group('currency').upper(), amount, match.group('scale')])),
                    'source_id': source['id'], 'evidence_id': evidence['evidence_id'],
                    'page': evidence['page'], 'quote': match.group(0),
                    'period': source.get('period', ''),
                    'publication_date': source.get('publication_date', ''),
                })
    return facts
