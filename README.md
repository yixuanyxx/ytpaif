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

## Directory Structure

```text
prototype/
├── frontend/       # Web UI, bundled Excel parser, upload template and tests/
└── backend/        # Python server, engine, audit sync, tests/ and config
    └── database/   # Local SQLite database and uploaded sources/
```

## Workflow

1. Define a theme and its two-tier taxonomy. The taxonomy appears as a full-width, top-down mind map from the theme through Tier-1 areas to Tier-2 activities. Click nodes to open definitions and examples in a dialog. Theme scope and JSON editing are collapsible to keep the map compact. The preview updates when you edit the theme name, edit JSON, or generate a taxonomy with AI. Provide a theme name and either a description plus included activities, or positive examples. Save or update a theme from the taxonomy card; updates edit the saved theme in place.
2. Import a portfolio in Portfolio Import. Select or drop an `.xlsx`, `.xls`, or `.csv` file, enter a portfolio name, and click **Load & preview data**.
3. Review the loaded holdings table. Edit company names, identifiers, country, sector, portfolio weights, and reference weights inline. Active weights are calculated automatically. Search, add rows, exclude rows, and resolve identity conflicts before clicking **Confirm & import**. The table remains available for further editing during the session.
4. Upload company PDF or UTF-8 TXT evidence, with publication date and fiscal period. PDFs are limited to 500 pages; files are limited to 20 MB.
5. Select evidence for AI analysis or enter a manual revenue case. Review and approve cases before they feed the dashboard.
6. View portfolio and benchmark exposure, export the analysis ZIP, or evaluate independently labelled JSON examples.

### Portfolio file format

The first worksheet must start with a header row containing `Company`, `Portfolio wt`, `Reference wt`, and at least one of `Bloomberg ticker` or `SEDOL`. Optional columns: `Country`, `Sector`, `Active wt`.

Weights support decimals such as `0.013` and explicit percentages such as `1.3%`. Numeric values greater than 1.5 in magnitude are interpreted as percentage values. Excel percentage-formatted numeric cells are read as decimals.

Excel parsing runs in the browser using the bundled SheetJS CE 0.20.3 library in `prototype/frontend/js/xlsx.full.min.js`; no CDN connection or `openpyxl` installation is required.

## Implementation

- `backend/app.py`: local Python HTTP server, API, SQLite persistence, source extraction, AI integration, review records, and ZIP exports.
- `backend/engine.py`: deterministic taxonomy validation, revenue exposure calculations, and portfolio aggregation.
- `frontend/index.html`, `frontend/app.js`, `frontend/style.css`: frontend and workflow views.
- `backend/evaluate.py`: classification metrics, abstention, exposure error, and reference-range overlap.

Data is stored in `prototype/backend/database/research.sqlite` and uploaded sources in `prototype/backend/database/sources/` by default. An existing `prototype/database/` is also supported. Set `THEME_DATA_DIR` in `.env` to use another location. Exposure bounds describe evidence and assumptions; rubric scores are non-probabilistic.

## Prototype limitations

- Document search filters uploaded documents only. Use manual uploads for actual filings.
- Confirming a portfolio saves companies and a portfolio/benchmark snapshot, including the benchmark name. Both sets of weights must total 100%. Editable draft rows remain in browser memory during the session; saved snapshots persist in SQLite.
- Country and sector are saved with company records and portfolio imports.
- No multi-user authentication or encrypted storage.

## Working dataset

Use `docs/Example 80 Stock Portfolio.xlsx` as the working portfolio. Demo loading and generated evaluation samples are unavailable. Upload actual company documents before analysis; source search filters uploaded documents and does not retrieve filings from the web. Synthetic examples exist only in isolated automated test fixtures.

## Enable AI

```sh
cp .env.example .env
# Set LLM_API_KEY in .env (endpoint, model, and call budget have built-in defaults).
python app.py
```

The adapter defaults to Google AI Studio Gemini (`gemini-3.5-flash-lite`) with a 100-call budget, and supports configurable overrides via `LLM_ENDPOINT`, `LLM_MODEL`, and `LLM_MAX_CALLS`. Manual analysis remains available without AI configuration.

## Evaluation CLI

```sh
python evaluate.py labelled_examples.json
```

Use independently reviewed development and held-out examples. The UI requires an uploaded labelled JSON dataset; generated sample evaluation data is disabled.

## Workflow additions

- Step 1 offers a standalone SVG mind-map export. The Excel holdings template is available in Step 2.
- Step 2 provides a portfolio-only Excel template with the expected headers on its first worksheet, ready to fill and import.
- Step 3 groups sources by company in a table. Expand a company to see all six source categories, edit document names/URLs/categories, and view source-derived market cap and revenue with document/page citations. Extraction recognises explicit currency-qualified market-cap and total/consolidated/group revenue statements; ambiguous tables and missing figures remain unresolved. Weight is aggregated by company from the latest saved portfolio snapshot. Exposure arithmetic still uses reviewed revenue evidence.
- Step 4 combines Analysis and Review tabs. Analyze all processes companies with eligible sources sequentially under the configured AI call cap. Companies with over 60,000 evidence characters require manual passage selection; failures are reported per company.
- Dashboard sub-theme buttons show classified companies in the current holdings universe using approved cases for the selected theme. The reference button opens benchmark constituents and uses the saved benchmark name where available.

## Supabase audit copy

SQLite remains the working database. Optional Supabase replication records submitted inputs, outputs, model calls and record changes using a durable local queue. Configure `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (or the legacy `SUPABASE_SERVICE_ROLE_KEY`) in `prototype/backend/.env`. Keep credentials in backend configuration.

The sync worker writes directly to `overview`, `theme_definition`, `portfolio_import`, `source_documents`, `analysis_review` and `evaluate`. Each table contains input/output event rows linked by `request_id`; Dashboard is excluded. The Supabase tables must already exist with the matching audit-event columns. Save/Update theme attempts an immediate sync; other queued events retry in the background.

From `prototype/backend/`, run `python sync_audit.py --backfill` to queue existing records as current-state baselines, or `python sync_audit.py --replay` to resend retained events without duplicating event IDs.

## Tests

From the repository root:

```sh
PYTHONPATH=prototype/backend python -m unittest discover -s prototype/backend/tests -v
node --test prototype/frontend/tests/workflow.test.cjs
```

On Windows, set `PYTHONPATH=prototype/backend` before running the unittest command. Run `start_server.bat` from the repository root for the Windows launcher; the backend shortcut delegates to the same launcher.
