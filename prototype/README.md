# Theme Lab v1 prototype

Local prototype of the AI Thematic Exposure Engine for IS4002 / Schroders. All v1 development belongs in `prototype/`; the repository's `outputs/` and `work/` folders contain v0.

## Run locally

From this folder, using Python 3.11 or newer:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8766. Set `PORT` to override the port. V0 uses port 8765.

## Workflow

1. Define a theme and its two-tier taxonomy. Provide a theme name and either a description plus included activities, or positive examples. Editing an approved definition creates a new version.
2. Import a portfolio in Portfolio Import. Select or drop an `.xlsx`, `.xls`, or `.csv` file, enter a portfolio name, and click **Load & preview data**.
3. Review the loaded holdings table. Edit company names, identifiers, country, sector, portfolio weights, and reference weights inline. Active weights are calculated automatically. Search, add rows, exclude rows, and resolve identity conflicts before clicking **Confirm & import**. The table remains available for further editing during the session.
4. Upload company PDF or UTF-8 TXT evidence, with publication date and fiscal period. PDFs are limited to 500 pages; files are limited to 20 MB.
5. Select evidence for AI analysis or enter a manual revenue case. Review and approve cases before they feed the dashboard.
6. View portfolio and benchmark exposure, export the analysis ZIP, or evaluate independently labelled JSON examples.

### Portfolio file format

The first worksheet must start with a header row containing `Company`, `Portfolio wt`, `Reference wt`, and at least one of `Bloomberg ticker` or `SEDOL`. Optional columns: `Country`, `Sector`, `Active wt`.

Weights support decimals such as `0.013` and explicit percentages such as `1.3%`. Numeric values greater than 1.5 in magnitude are interpreted as percentage values. Excel percentage-formatted numeric cells are read as decimals.

Excel parsing runs in the browser using the bundled SheetJS CE 0.20.3 library in `static/js/xlsx.full.min.js`; no CDN connection or `openpyxl` installation is required.

## Implementation

- `app.py`: local Python HTTP server, API, SQLite persistence, source extraction, AI integration, review records, and ZIP exports.
- `engine.py`: deterministic taxonomy validation, revenue exposure calculations, and portfolio aggregation.
- `static/index.html`, `static/app.js`, `static/style.css`: frontend and workflow views.
- `evaluate.py`: classification metrics, abstention, exposure error, and reference-range overlap.

Data is stored in `data/research.sqlite` and uploaded sources in `data/sources/`. Set `THEME_DATA_DIR` to use another location. Exposure bounds describe evidence and assumptions; rubric scores are non-probabilistic.

## Prototype limitations

- Automated document search currently displays a simulated success message; it does not retrieve or ingest documents. Use manual uploads.
- Confirming a portfolio registers companies. Edited holdings and file metadata remain in browser memory for the current session; this import flow does not yet save a portfolio/benchmark snapshot. Dashboard calculations require a saved snapshot, available through the snapshot API or demo data.
- Country is saved as company jurisdiction; sector remains in the editable session data.
- No multi-user authentication or encrypted storage.

## Demo data

Click **Load demo data** on Overview. The synthetic worked example produces 18% portfolio exposure, 14% benchmark exposure, and +4 percentage points active exposure. It writes demo records and a holdings snapshot to the local database.

## Enable AI

```sh
cp .env.example .env
# Set LLM_API_KEY, LLM_MODEL, and an explicit LLM_MAX_CALLS budget.
python app.py
```

The adapter supports a configurable `LLM_ENDPOINT`, caches responses, and records usage. AI proposes extracted facts and classifications; Python validates evidence references and computes exposure. Manual analysis remains available without AI configuration.

## Evaluation CLI

```sh
python evaluate.py labelled_examples.json
```

Use independently reviewed development and held-out examples. UI sample evaluation data is synthetic and randomly generated.
