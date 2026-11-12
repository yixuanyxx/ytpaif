# Theme Lab v1 prototype

Local prototype of the AI Thematic Exposure Engine for IS4002 / Schroders. All development belongs in `prototype/`.

## Run locally

From `prototype/backend/`, using Python 3.11 or newer:

```sh
cd prototype/backend
python -m venv .venv
# On Windows: .venv\Scripts\activate  (On macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8766. Set `PORT` in `prototype/backend/.env` to override the port.
Before starting the app, configure Supabase and run the schema and migration steps below.

## Directory Structure

```text
prototype/
├── frontend/       # Web UI, bundled Excel parser, upload template and tests/
└── backend/        # Python server, engine, Supabase schema/migrations, tests/ and config
```

## Workflow

1. Define a theme and its two-tier taxonomy. The taxonomy appears as a full-width, top-down mind map from the theme through Tier-1 areas to Tier-2 activities. Click nodes to open definitions and examples in a dialog. Theme scope and JSON editing are collapsible to keep the map compact. The preview updates when you edit the theme name, edit JSON, or generate a taxonomy with AI. Provide a theme name and either a description plus included activities, or positive examples. Save or update a theme from the taxonomy card; updates edit the saved theme in place.
2. Import a portfolio in Portfolio Import. Select or drop an `.xlsx`, `.xls`, or `.csv` file, enter a portfolio name, and click **Load & preview data**.
3. Review the loaded holdings table. Edit company names, identifiers, country, sector, portfolio weights, and reference weights inline. Active weights are calculated automatically. Search, add rows, exclude rows, and resolve identity conflicts before clicking **Confirm & import**. The table remains available for further editing during the session.
4. Upload company PDF or UTF-8 TXT evidence, with publication date and fiscal period. PDFs are limited to 500 pages; files are limited to 20 MB.
5. Select evidence for AI analysis or enter a manual revenue case. Review and approve cases before they feed the dashboard.
6. View portfolio and benchmark exposure and export the analysis ZIP.

### Portfolio file format

The first worksheet must start with a header row containing `Company`, `Portfolio wt`, `Reference wt`, and at least one of `Bloomberg ticker` or `SEDOL`. Optional columns: `Country`, `Sector`, `Active wt`.

Weights support decimals such as `0.013` and explicit percentages such as `1.3%`. Numeric values greater than 1.5 in magnitude are interpreted as percentage values. Excel percentage-formatted numeric cells are read as decimals.

Excel parsing runs in the browser using the bundled SheetJS CE 0.20.3 library in `prototype/frontend/js/xlsx.full.min.js`; no CDN connection or `openpyxl` installation is required.

## Implementation

- `backend/app.py`: local Python HTTP server, API, Supabase persistence, source extraction, AI integration, review records, and ZIP exports.
- `backend/engine.py`: deterministic taxonomy validation, revenue exposure calculations, and portfolio aggregation.
- `frontend/index.html`, `frontend/app.js`, `frontend/style.css`: frontend and workflow views.

Runtime records and uploaded source files are stored in Supabase. The backend requires `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (or `SUPABASE_SERVICE_ROLE_KEY`) in `prototype/backend/.env`; credentials stay server-side. Exposure bounds describe evidence and assumptions; rubric scores are non-probabilistic.
Login sessions and login rate limits also require a reachable shared Redis server. Configure `REDIS_URL` or run Redis on localhost before starting the backend; otherwise login returns 503 even if the username and password are correct.

Dashboard records also have a Redis read cache. Successful Supabase reads are reused for 60 seconds to reduce repeated downloads. On quota/billing restrictions (HTTP 402), rate limits, server failures or connection failures, the last successfully cached result remains available with a visible cached-data notice. Redis copies have no application expiry, but Redis eviction or restart can still remove them. An uncached dataset cannot be reconstructed during an outage; restore Supabase access and open the app once to populate it. Writes and file uploads still require Supabase success, so the app never claims that an unsaved edit is permanent. Permission and schema errors do not use stale fallback data.

## Official filing authorities

Annual reports and regulatory filings are authority-first. The discovery service maps each supported Bloomberg exchange code to its responsible regulator or exchange, rather than treating vendor pages as filings. The catalogue covers Taiwan (MOPS/TWSE), South Korea (DART), Hong Kong (HKEXnews), China (CNINFO/SSE/SZSE), Singapore (SGXNet), India (NSE/BSE), Indonesia (IDX), Malaysia (Bursa Malaysia), Thailand (SET), Philippines (PSE EDGE), Vietnam (HOSE/HNX), Bangladesh (DSE/CSE), Pakistan (PSX), and Sri Lanka (CSE). Australia (ASX) and New Zealand (NZX) remain supported for the portfolio's Australasia scope. Japan is intentionally excluded.

An authority portal is used only to locate filings: a source is saved only after the system has verified a direct, issuer-matched document URL, publication date, reporting period, and site-access rules. If an authority provides a dynamic viewer or blocks automated reading, the source stays visibly unresolved and the PM can upload the official PDF/TXT file.

## Prototype limitations

- Discovery is authority-first for annual reports and regulatory filings. A filing is only saved after its direct document link, issuer, date, reporting period and access rules are verified; upload the official PDF/TXT when an authority's viewer cannot be read automatically.
- Confirming a portfolio saves companies and a portfolio/benchmark snapshot, including the benchmark name. Both sets of weights must total 100%. Editable draft rows remain in browser memory during the session; saved snapshots persist in Supabase.
- Country and sector are saved with company records and portfolio imports.
- No multi-user authentication or encrypted storage.

## Working dataset

Use `docs/Example 80 Stock Portfolio.xlsx` as the working portfolio. Demo loading is unavailable. Upload actual company documents before analysis when the relevant authority blocks automated document reading. Synthetic examples exist only in isolated automated test fixtures.

## Enable AI

```sh
cp .env.example .env
# Set FINANCIALS_LLM_API_KEY and TAVILY_API_KEY in .env.
python app.py
```

Exposure extraction and classification default to Gemini (`gemini-3.8-flash`). The app does not impose a daily Gemini call cap. Google AI Studio's provider quota and rate limits still apply; a provider rejection is reported with the affected companies, and the remaining batch is deferred rather than retried repeatedly. Cached model responses use no provider request.

## Workflow additions

- Step 1 offers a standalone SVG mind-map export. The Excel holdings template is available in Step 2.
- Step 2 provides a portfolio-only Excel template with the expected headers on its first worksheet, ready to fill and import.
- Step 3 groups sources by company in a table. Expand a company to see all six source categories, edit document names/URLs/categories, and view source-derived market cap and revenue with document/page citations. Extraction recognises explicit currency-qualified market-cap and total/consolidated/group revenue statements; ambiguous tables and missing figures remain unresolved. Weight is aggregated by company from the latest saved portfolio snapshot. Exposure arithmetic still uses reviewed revenue evidence.
- Step 4 provides batch and individual company exposure calculation. Each completed company's exposure and AI confidence are saved in the shared company/theme analysis library in Supabase (`analysis_cases`). Other portfolios holding the same company under the same theme automatically show the saved result without another AI call. If the result is unresolved, a bounded Tavily web search uses the company name, ticker, reporting year and nested taxonomy leaf terms to look for approved, dated revenue evidence. It runs at most two queries, checks up to four candidate documents, and reuses recent search results for 24 hours. This fallback's search and document reading use no Gemini grounding; only a new, source-backed exact numeric finding may trigger one more exposure analysis. Approximate language is retained as evidence without fabricating a point estimate. Analyse All searches at most three unresolved companies per run by default (`UNRESOLVED_SEARCH_BATCH_LIMIT`). The separate Source Documents AI search still uses its existing Gemini-grounded path. Changed theme scope, reporting period, source documents or calculation version requires recalculation. Approval is still required before results feed portfolio aggregates.
- Dashboard sub-theme buttons show classified companies in the current holdings universe using approved cases for the selected theme. The reference button opens benchmark constituents and uses the saved benchmark name where available.

## Supabase setup

1. Configure `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (or `SUPABASE_SERVICE_ROLE_KEY`) in `prototype/backend/.env`. Never put the secret key in frontend code.
2. Ensure the existing `themes`, `portfolios`, and `source_documents` tables and private `source-documents` / `portfolio-files` buckets are configured.
3. If a database error reports `42P01` for `public.audit_events`, remove any remaining public-schema trigger that references the obsolete audit table. This does not remove analyst reviews or the AI Checker feature.

The app uses Supabase for company, source and analysis records. The local SQLite source-job queue must be kept on persistent storage when running on a server. New portfolio imports keep their original Excel file in the private `portfolio-files` bucket and store its filename, storage path, size, content type, and checksum in `portfolios`.

## Tests

From the repository root:

```sh
PYTHONPATH=prototype/backend python -m unittest discover -s prototype/backend/tests -v
node --test prototype/frontend/tests/workflow.test.cjs
```

On Windows, set `PYTHONPATH=prototype/backend` before running the unittest command. Run `start_server.bat` from the repository root for the Windows launcher; the backend shortcut delegates to the same launcher.
