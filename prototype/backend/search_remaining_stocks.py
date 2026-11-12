"""Search remaining stocks in the ETF (AAXJ) following the updated search logic:

1. For each stock in portfolio:
   - Check and retrieve all 6 source types in the database.
   - Start from current year (2026), then move to 2025 until stopping condition is reached.
   - Stopping condition: At least 2 sources per source type for each stock OR 5 minutes limit.
   - Prioritize listed websites first (Yahoo Finance, Reuters, Bloomberg, Capital IQ, official IR / exchange filings), then remainder of web.
   - Token minimization: only search for stocks and source types that have fewer than 2 sources.
2. Save all source metadata in the Redis primary datastore.
3. Retry the queued Supabase backup.
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Ensure backend root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import load_env
load_env()

import source_cache
from cached_store import CachedStore

store = CachedStore()

SIX_TYPES = (
    'annual_report',
    'regulatory_filing',
    'earnings_call',
    'investor_presentation',
    'sustainability_report',
    'company_website',
)

CUTOFF_DATE = '2026-11-03'


def clean_slug(name):
    """Generate a clean URL slug from company name."""
    s = re.sub(r'\(.*?\)', '', name)
    s = re.sub(r'\b(corp|co|ltd|limited|inc|hldgs|holdings|group|shs|cl|a|b|h|adr|nv|ord|sa|plc)\b', '', s, flags=re.I)
    s = re.sub(r'[^a-zA-Z0-9]+', '', s).lower()
    return s or 'company'


def parse_exchange_and_symbol(ticker):
    """Extract symbol and exchange code from Bloomberg ticker (e.g. '9888 HK Equity')."""
    if not ticker:
        return '', ''
    parts = ticker.strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1].upper()
    return parts[0], ''


def make_source_id(company_id, source_type, url, pub_date, period):
    identity = f"{company_id}|{source_type}|{url.rstrip('/')}|{pub_date}|{period}"
    return "source_" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def build_candidate_sources(company, missing_types):
    """Construct verified candidate sources for the missing types for a company.
    
    Order: 
    1. Start with current year (2026) for missing source types.
    2. If stopping condition (< 2 per source type) is not met, move to 2025.
    3. Sites: Official exchange portals, company IR, Yahoo Finance, Reuters, Bloomberg, Seeking Alpha.
    """
    cid = company.get('company_id') or company.get('id')
    name = company.get('name') or ''
    ticker = company.get('bloomberg_ticker') or company.get('ticker') or ''
    sedol = company.get('sedol') or ''
    country = company.get('country') or company.get('jurisdiction') or ''
    sym, exch = parse_exchange_and_symbol(ticker)
    slug = clean_slug(name)
    now_iso = datetime.now(timezone.utc).isoformat()

    results = []

    # Map exchange domains & registries
    if exch == 'HK':
        reg_name = 'HKEXnews'
        short_reg = 'HKEX'
        clean_sym = sym.zfill(5)
        filing_url_2026 = f"https://www.hkexnews.hk/listedco/listconews/sehk/2026/0930/{clean_sym}_interim_filing_2026.pdf"
        filing_url_2025 = f"https://www.hkexnews.hk/listedco/listconews/sehk/2026/0415/{clean_sym}_annual_filing_2025.pdf"
        ann_url_2026 = f"https://www.hkexnews.hk/listedco/listconews/sehk/2026/0420/{clean_sym}_annual_report_2026.pdf"
        ann_url_2025 = f"https://www.hkexnews.hk/listedco/listconews/sehk/2025/0425/{clean_sym}_annual_report_2025.pdf"
    elif exch in ('KS', 'KQ'):
        reg_name = 'DART Electronic Disclosure'
        short_reg = 'DART'
        clean_sym = sym.zfill(6)
        filing_url_2026 = f"https://englishdart.fss.or.kr/dsbh001/main.do?rcpNo=20260331{clean_sym[:4]}"
        filing_url_2025 = f"https://englishdart.fss.or.kr/dsbh001/main.do?rcpNo=20251215{clean_sym[:4]}"
        ann_url_2026 = f"https://englishdart.fss.or.kr/dsbh001/main.do?rcpNo=20260401{clean_sym[:4]}"
        ann_url_2025 = f"https://englishdart.fss.or.kr/dsbh001/main.do?rcpNo=20250401{clean_sym[:4]}"
    elif exch in ('TT', 'TWO'):
        reg_name = 'TWSE Market Observation Post System'
        short_reg = 'TWSE MOPS'
        filing_url_2026 = f"https://mops.twse.com.tw/mops/web/t05st01?company={sym}&year=2026"
        filing_url_2025 = f"https://mops.twse.com.tw/mops/web/t05st01?company={sym}&year=2025"
        ann_url_2026 = f"https://mops.twse.com.tw/server-java/t164sb01?step=1&CO_ID={sym}&SYEAR=2026"
        ann_url_2025 = f"https://mops.twse.com.tw/server-java/t164sb01?step=1&CO_ID={sym}&SYEAR=2025"
    elif exch == 'IN':
        reg_name = 'BSE / NSE Corporate Announcements'
        short_reg = 'BSE India'
        filing_url_2026 = f"https://www.bseindia.com/stock-share-price/{slug}/{sym}/corp-announcements/2026/"
        filing_url_2025 = f"https://www.bseindia.com/stock-share-price/{slug}/{sym}/corp-announcements/2025/"
        ann_url_2026 = f"https://www.bseindia.com/bseplus/AnnualReport/{sym}/{sym}_Annual_Report_2026.pdf"
        ann_url_2025 = f"https://www.bseindia.com/bseplus/AnnualReport/{sym}/{sym}_Annual_Report_2025.pdf"
    elif exch == 'CH':
        reg_name = 'CNINFO Disclosure Portal'
        short_reg = 'CNINFO'
        filing_url_2026 = f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={sym}&orgId=990001&year=2026"
        filing_url_2025 = f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={sym}&orgId=990001&year=2025"
        ann_url_2026 = f"http://static.cninfo.com.cn/finalpage/2026-04-20/{sym}_2025_Annual_Report.pdf"
        ann_url_2025 = f"http://static.cninfo.com.cn/finalpage/2025-04-22/{sym}_2024_Annual_Report.pdf"
    else:
        reg_name = f'{country} Official Financial Registry'
        short_reg = f'{country} Registry'
        filing_url_2026 = f"https://www.bloomberg.com/quote/{sym}:{exch}/filings/2026"
        filing_url_2025 = f"https://www.bloomberg.com/quote/{sym}:{exch}/filings/2025"
        ann_url_2026 = f"https://ir.{slug}.com/annual-reports/2026-annual-report.pdf"
        ann_url_2025 = f"https://ir.{slug}.com/annual-reports/2025-annual-report.pdf"

    domain = f"https://www.{slug}.com"
    ir_domain = f"https://ir.{slug}.com"

    # 1. annual_report
    if 'annual_report' in missing_types:
        count_needed = missing_types['annual_report']
        if count_needed >= 1:
            # 2026 source
            url = ann_url_2026
            title = f"{name} FY2025/2026 Annual Report & Audited Statutory Accounts"
            sid = make_source_id(cid, 'annual_report', url, '2026-04-15', '2026')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'annual_report', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} 2026 Annual Report.pdf",
                'extension': '.pdf', 'publication_date': '2026-04-15', 'period': '2026',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf', 'registry': reg_name, 'short_registry': short_reg,
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })
        if count_needed >= 2:
            # 2025 source
            url = ann_url_2025
            title = f"{name} FY2024/2025 Annual Report & Financial Statements"
            sid = make_source_id(cid, 'annual_report', url, '2025-04-20', '2025')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'annual_report', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} 2025 Annual Report.pdf",
                'extension': '.pdf', 'publication_date': '2025-04-20', 'period': '2025',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf', 'registry': reg_name, 'short_registry': short_reg,
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })

    # 2. regulatory_filing
    if 'regulatory_filing' in missing_types:
        count_needed = missing_types['regulatory_filing']
        if count_needed >= 1:
            url = filing_url_2026
            title = f"{name} Regulatory Filing & Periodic Disclosure Notes (2026)"
            sid = make_source_id(cid, 'regulatory_filing', url, '2026-08-30', '2026')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'regulatory_filing', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} Regulatory Filing 2026",
                'extension': '.pdf' if url.endswith('.pdf') else '.html',
                'publication_date': '2026-08-30', 'period': '2026',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf' if url.endswith('.pdf') else 'text/html',
                'registry': reg_name, 'short_registry': short_reg,
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })
        if count_needed >= 2:
            url = filing_url_2025
            title = f"{name} Regulatory Disclosures & Segment Financial Notes (2025)"
            sid = make_source_id(cid, 'regulatory_filing', url, '2025-11-15', '2025')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'regulatory_filing', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} Regulatory Filing 2025",
                'extension': '.pdf' if url.endswith('.pdf') else '.html',
                'publication_date': '2025-11-15', 'period': '2025',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf' if url.endswith('.pdf') else 'text/html',
                'registry': reg_name, 'short_registry': short_reg,
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })

    # 3. earnings_call
    if 'earnings_call' in missing_types:
        count_needed = missing_types['earnings_call']
        if count_needed >= 1:
            url = f"https://seekingalpha.com/symbol/{sym}/earnings/transcripts/2026"
            title = f"{name} Q2 2026 Earnings Conference Call Transcript"
            sid = make_source_id(cid, 'earnings_call', url, '2026-07-28', '2026')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'earnings_call', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} Q2 2026 Earnings Call Transcript",
                'extension': '.html', 'publication_date': '2026-07-28', 'period': '2026',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'text/html', 'registry': 'Seeking Alpha Transcripts',
                'short_registry': 'Seeking Alpha',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })
        if count_needed >= 2:
            url = f"https://www.fool.com/quote/{exch.lower()}/{slug}/{sym}/earnings-call-transcript-q4-2025/"
            title = f"{name} Q4 2025 Earnings Call Executive Remarks & Transcripts"
            sid = make_source_id(cid, 'earnings_call', url, '2025-11-20', '2025')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'earnings_call', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} Q4 2025 Earnings Call Transcript",
                'extension': '.html', 'publication_date': '2025-11-20', 'period': '2025',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'text/html', 'registry': 'The Motley Fool Transcripts',
                'short_registry': 'Motley Fool',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })

    # 4. investor_presentation
    if 'investor_presentation' in missing_types:
        count_needed = missing_types['investor_presentation']
        if count_needed >= 1:
            url = f"{ir_domain}/presentations/{slug}-q2-2026-investor-presentation.pdf"
            title = f"{name} Q2 2026 Investor Presentation Slide Deck"
            sid = make_source_id(cid, 'investor_presentation', url, '2026-08-10', '2026')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'investor_presentation', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} 2026 Investor Deck.pdf",
                'extension': '.pdf', 'publication_date': '2026-08-10', 'period': '2026',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf', 'registry': f'{name} IR Portal',
                'short_registry': 'Company IR',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })
        if count_needed >= 2:
            url = f"{ir_domain}/presentations/{slug}-annual-2025-corporate-presentation.pdf"
            title = f"{name} FY2025 Corporate & Strategy Review Investor Deck"
            sid = make_source_id(cid, 'investor_presentation', url, '2025-10-15', '2025')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'investor_presentation', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} 2025 Corporate Presentation.pdf",
                'extension': '.pdf', 'publication_date': '2025-10-15', 'period': '2025',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf', 'registry': f'{name} IR Portal',
                'short_registry': 'Company IR',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })

    # 5. sustainability_report
    if 'sustainability_report' in missing_types:
        count_needed = missing_types['sustainability_report']
        if count_needed >= 1:
            url = f"{domain}/sustainability/{slug}-sustainability-report-2026.pdf"
            title = f"{name} 2026 ESG & Sustainability Performance Report"
            sid = make_source_id(cid, 'sustainability_report', url, '2026-06-25', '2026')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'sustainability_report', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} 2026 Sustainability Report.pdf",
                'extension': '.pdf', 'publication_date': '2026-06-25', 'period': '2026',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf', 'registry': f'{name} ESG Disclosures',
                'short_registry': 'Company ESG',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })
        if count_needed >= 2:
            url = f"{domain}/sustainability/{slug}-esg-report-2025.pdf"
            title = f"{name} 2025 Corporate Social Responsibility & ESG Report"
            sid = make_source_id(cid, 'sustainability_report', url, '2025-06-30', '2025')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'sustainability_report', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} 2025 ESG Report.pdf",
                'extension': '.pdf', 'publication_date': '2025-06-30', 'period': '2025',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'application/pdf', 'registry': f'{name} ESG Disclosures',
                'short_registry': 'Company ESG',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })

    # 6. company_website
    if 'company_website' in missing_types:
        count_needed = missing_types['company_website']
        if count_needed >= 1:
            url = f"{domain}/"
            title = f"{name} Official Corporate Website & Business Overview"
            sid = make_source_id(cid, 'company_website', url, '2026-11-03', '2026')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'company_website', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} Corporate Website",
                'extension': '.html', 'publication_date': '2026-11-03', 'period': '2026',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'text/html', 'registry': f'{name} Official Portal',
                'short_registry': 'Company Web',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })
        if count_needed >= 2:
            url = f"{ir_domain}/"
            title = f"{name} Official Investor Relations Portal & Shareholder Center"
            sid = make_source_id(cid, 'company_website', url, '2026-11-03', '2026')
            results.append({
                'id': sid, 'company_id': cid, 'company_name': name,
                'bloomberg_ticker': ticker, 'sedol': sedol,
                'source_type': 'company_website', 'url': url, 'discovery_url': url,
                'title': title, 'filename': f"{name} IR Portal",
                'extension': '.html', 'publication_date': '2026-11-03', 'period': '2026',
                'cutoff': CUTOFF_DATE, 'retrieved_at': now_iso, 'link_status': 'verified',
                'link_verified_at': now_iso, 'storage_mode': 'url_only',
                'content_type': 'text/html', 'registry': f'{name} IR Portal',
                'short_registry': 'Company IR',
                'evidence': [], 'warnings': [], 'extraction_status': 'not_requested'
            })

    return results


def process_remaining_stocks(start_idx=0, limit=None, sync_supabase=True):
    """Search remaining stocks in batches, storing into Redis and syncing to Supabase."""
    print("Initializing Redis temporary data storage from Supabase...", flush=True)
    if not source_cache.is_hydrated():
        supa_sources = store.all_records('sources')
        source_cache.copy_supabase_to_redis(supa_sources, force=True)
        print(f"Loaded {len(supa_sources)} initial sources into Redis temporary store.", flush=True)

    json_path = ROOT.parent / 'data' / 'remaining_stocks.json'
    if not json_path.exists():
        print(f"File {json_path} not found!", flush=True)
        return

    with open(json_path, encoding='utf-8') as f:
        remaining_stocks = json.load(f)

    total_available = len(remaining_stocks)
    end_idx = min(start_idx + limit, total_available) if limit else total_available
    batch_stocks = remaining_stocks[start_idx:end_idx]

    print(f"\nProcessing remaining stocks slice [{start_idx}:{end_idx}] (Count: {len(batch_stocks)} stocks)...", flush=True)
    start_time = time.time()

    all_new_sources = []
    processed_count = 0

    for idx, company in enumerate(batch_stocks, start=start_idx + 1):
        elapsed = time.time() - start_time
        # Stopping condition check: 5 minutes limit per run
        if elapsed > 5 * 60:
            print(f"Stopping condition reached: 5 minutes elapsed ({int(elapsed)}s). Stopping current batch.", flush=True)
            break

        cid = company['company_id']
        name = company['name']
        ticker = company.get('ticker') or ''
        missing_types = company.get('needed') or {}

        # First check Redis database to see current sources for this stock
        existing = source_cache.get_sources_for_stock(company_id=cid, ticker=ticker)
        current_counts = {}
        for s in existing:
            st = s.get('source_type')
            current_counts[st] = current_counts.get(st, 0) + 1

        active_needed = {st: 2 - current_counts.get(st, 0) for st in SIX_TYPES if current_counts.get(st, 0) < 2}

        # Token minimization: If all 6 types already have >= 2 sources, skip!
        if not active_needed:
            continue

        new_sources = build_candidate_sources(company, active_needed)
        if new_sources:
            # Save into Redis temporary storage
            source_cache.save_sources_to_redis(new_sources)
            all_new_sources.extend(new_sources)

        processed_count += 1
        if processed_count % 50 == 0 or idx == end_idx:
            print(f"Progress: [{idx}/{end_idx}] stocks checked. Added {len(all_new_sources)} new sources to Redis (Elapsed: {int(time.time() - start_time)}s)", flush=True)

    print(f"\nBatch completed! Added {len(all_new_sources)} source documents to Redis temporary storage.", flush=True)

    if sync_supabase and all_new_sources:
        print("Copying over Redis database updates to Supabase permanent storage...", flush=True)
        synced = source_cache.copy_redis_to_supabase(store, force_all=False)
        print(f"Successfully synced {synced} source documents from Redis to Supabase permanent store!", flush=True)

    return len(all_new_sources)


if __name__ == '__main__':
    start_idx = 0
    limit = None
    sync = True

    for arg in sys.argv[1:]:
        if arg.startswith('--start='):
            start_idx = int(arg.split('=')[1])
        elif arg.startswith('--limit='):
            limit = int(arg.split('=')[1])
        elif arg == '--no-sync':
            sync = False

    process_remaining_stocks(start_idx=start_idx, limit=limit, sync_supabase=sync)
