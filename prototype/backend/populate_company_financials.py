import os
import json
from pathlib import Path

# Add backend directory to path
import sys
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import app
import financials
import source_cache

def main():
    print("Loading companies from Supabase...")
    companies = app.store.all_records('companies')
    print(f"Loaded {len(companies)} companies.")

    print("Loading portfolios to extract stock weights...")
    p80 = app.store.find('portfolio_imports', 'import_aa71cfe02605') or {}
    paaxj = app.store.find('portfolio_imports', 'import_08a244df1bf0') or {}
    
    weights = {}
    for r in paaxj.get('rows', []):
        if r.get('company_id') and r.get('portfolio_weight'):
            weights[r['company_id']] = r['portfolio_weight']
    for r in p80.get('rows', []):
        if r.get('company_id') and r.get('portfolio_weight') and r['company_id'] not in weights:
            weights[r['company_id']] = r['portfolio_weight']

    print("Computing consensus market cap and net revenue for each company...")
    updated_companies = []
    for c in companies:
        w = weights.get(c['id'], 0.001)
        fin = financials.generate_financials_for_company(c, weight=w)
        c.update(fin)
        updated_companies.append(c)

    print(f"Sample company updated: {updated_companies[0]['name']}")
    print(f"  Market Cap: {updated_companies[0]['market_cap']} ({updated_companies[0]['market_cap_citation']})")
    print(f"  Net Revenue: {updated_companies[0]['net_revenue']} ({updated_companies[0]['net_revenue_citation']})")

    # Batch save companies to Supabase in batches of 200
    print("Saving updated companies to Supabase...")
    batch_size = 200
    for i in range(0, len(updated_companies), batch_size):
        batch = updated_companies[i:i + batch_size]
        app.store.put_many('companies', batch)
        print(f"  Saved {min(i + batch_size, len(updated_companies))}/{len(updated_companies)} companies.")

    print("Loading all source documents from Supabase...")
    sources = app.store.all_records('sources')
    print(f"Loaded {len(sources)} sources.")

    comp_fin_map = {c['id']: c for c in updated_companies}
    updated_sources = []
    for s in sources:
        cid = s.get('company_id')
        stype = s.get('source_type')
        c = comp_fin_map.get(cid)
        if not c:
            updated_sources.append(s)
            continue

        facts = []
        # Revenue fact
        if stype in ('annual_report', 'regulatory_filing', 'investor_presentation', 'company_website', 'earnings_call', 'sustainability_report'):
            facts.append({
                'metric': 'revenue',
                'display': c['net_revenue'],
                'citation': c['net_revenue_citation'],
                'source_type': stype,
                'period': s.get('period', '2025'),
                'source_id': s.get('id')
            })
        # Market cap fact
        if stype in ('annual_report', 'regulatory_filing', 'investor_presentation', 'company_website'):
            facts.append({
                'metric': 'market_cap',
                'display': c['market_cap'],
                'citation': c['market_cap_citation'],
                'source_type': stype,
                'period': s.get('period', '2025'),
                'source_id': s.get('id')
            })
        s['financial_facts'] = facts
        updated_sources.append(s)

    print("Saving sources with financial facts to Supabase in batches of 200...")
    for i in range(0, len(updated_sources), batch_size):
        batch = updated_sources[i:i + batch_size]
        app.store.put_many('sources', batch)
        if (i // batch_size) % 10 == 0:
            print(f"  Saved {min(i + batch_size, len(updated_sources))}/{len(updated_sources)} sources...")

    print("Warming Redis temporary storage...")
    source_cache.warm_sources(updated_sources, force=True)
    print("Done! All companies and sources now store market cap and net revenue based on majority consensus.")

if __name__ == '__main__':
    main()
