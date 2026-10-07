# Theme Lab

Local implementation of the AI Thematic Analysis technical specification v0.1. Python 3.11+ serves a browser dashboard and stores research records in SQLite. Deterministic calculations are independent of the model provider. The UI and manual analysis use Python's standard library; PDF extraction requires the pinned pypdf dependency.

## Start

On this computer, right-click `start.ps1` and run with PowerShell, or run:

```powershell
.\start.ps1
```

Open http://127.0.0.1:8765. Stop with Ctrl+C. The launcher uses the available bundled Python on this computer. On another computer:

```sh
python -m venv .venv
# Activate the environment, then:
python -m pip install -r requirements.txt
python app.py
```

Load the worked example to reproduce the specification's 18% portfolio, 14% benchmark and +4 percentage points active exposure. All demo companies and evidence are synthetic. A separate mixed company reproduces the 20%–60% range with no point estimate.

## Research workflow

1. Define a theme, economic driver, inclusion/exclusion scope, examples, geography and evidence cutoff. Edit taxonomy JSON or request an AI proposal. Review the scope and approve an immutable version. Editing a theme creates a new version; it never changes existing analyses.
2. Add stable company IDs. Upload English public annual reports as PDF or UTF-8 TXT, with fiscal period, publication date and public URL if available. Text is split into exact page-linked passages. Scanned pages are flagged; supply verified text manually rather than assume successful extraction.
3. Select relevant passages and run fact extraction and classification, or enter a manual revenue case in Analyst review. AI receives company evidence and approved scope, never portfolio weights. Every numerical fact requires existing source evidence. AI classifications do not invent revenue allocation fractions; ambiguous revenue retains a range until an analyst supplies a justified allocation.
4. Inspect source passages alongside classifications. Edit by saving a revised case, then approve/reject with reviewer name and reason. Original AI outputs, case versions and review events remain retained. Cases with unallocated reconciliation residuals cannot be approved; represent the residual as a sourced unknown revenue unit or resolve it first.
5. Import portfolio and benchmark CSVs. Match dates, supply snapshot/company/security IDs and decimal weights, and total each snapshot to one (tolerance 0.000001). Use company ID `CASH` for cash. Multiple securities can share a company analysis.
6. Review exposure, bounds, coverage, signed active contributions and additive subtheme allocations. Export the ZIP of company results, evidence, cases, model outputs and run manifest.

## Optional AI configuration

The provider adapter supports an OpenAI-compatible Chat Completions endpoint with JSON-object responses. Use a provider/model supporting this contract. No model or paid call is selected automatically. API secrets are read only from environment variables, never saved in SQLite or exports.

```powershell
$env:LLM_ENDPOINT = 'https://api.openai.com/v1/chat/completions'
$env:LLM_MODEL = 'YOUR_COMPATIBLE_MODEL'
$env:LLM_API_KEY = 'YOUR_KEY'
$env:LLM_MAX_CALLS = '6'
.\start.ps1
```

`LLM_MAX_CALLS` defaults to zero. This is a persistent workspace call cap including retry attempts, not a dollar spending cap. Each company uses two calls; taxonomy generation uses one. Confirm provider pricing, data handling and permitted material before enabling it. Token usage is retained where returned; monetary cost is explicitly unknown. Calls have 90-second timeouts and at most one retry for transient HTTP failures. Bad JSON and invalid schema/reference outputs fail visibly and never produce a zero result. Unchanged valid provider responses cache by payload/model/endpoint/version hash.

## Storage and calculations

`data/research.sqlite` retains typed JSON records for themes, companies, sources, cases, runs, reviews, model outputs, calls, caches and snapshots. `data/sources/` retains original uploaded bytes by generated source ID. Hashes are SHA-256; timestamps are UTC. Source publication cutoff and per-case fiscal periods are checked. Set `THEME_DATA_DIR` to relocate storage; set `PORT` to change the default port.

Revenue amount × qualifying allocation is summed and divided by consolidated external revenue. Each revenue unit must be external, nonnegative and compatible with the denominator period/currency/scale. Overlap groups, duplicate units, invalid fractions, incompatible source IDs and revenue totals above the denominator are rejected. Units must be a single non-overlapping breakdown; analysts are responsible for verifying that differently labelled disclosures are not overlapping.

An absent or unapproved estimate has lower 0, upper 1 and point null. Covered holdings are never renormalised. Portfolio and benchmark share a single latest approved case version per company/theme version. Negative active weights reverse bounds. Exact active points are suppressed whenever a nonzero active weight has no company point; zero active weight cancels uncertainty. Indirect economic beneficiaries are retained as classifications with no qualifying revenue. Cash has zero thematic revenue and is reported separately from assessed equity weight.

Rubric confidence uses high/medium/low and is not a calibrated probability. The UI reports classification confidence separately from exposure evidence quality. Bounds are assumption-based limits, not statistical confidence intervals.

Rubric scores are ordinal 1 (low), 2 (medium), 3 (high), stored and exported separately for classification and exposure quality. The score is not a percentage probability.

## Validation

```sh
python -m unittest discover -v
```

Tests cover both specification fixtures, uncertain allocations, missing estimates, review gating, negative active weights, cash, multiple share classes, taxonomy errors, revenue overlap, denominator/source/period errors, immutable definitions, cutoff dates, exports and provider failures/call caps.

`python evaluate.py labelled_examples.json` computes per-label precision/recall/F1, abstention rate, answered accuracy, observed accuracy by rubric band, exposure error in percentage points and reference-range overlap. Supply a JSON list with `actual`, optional `predicted` (null means abstention), `confidence_band`, and optional `point`, `reference_point`, `lower`, `upper`, `reference_lower`, `reference_upper`. Labels may be taxonomy leaf IDs. Keep development and held-out examples separate; no evaluation accuracy is claimed from the synthetic demo.

## Pilot limits and further work

This is a local pilot application, not a hosted production service. It binds to loopback, checks Host/Origin for writes, escapes displayed document text, limits uploads, does not serve storage files and has no external write tools. It has no multi-user authentication, backup automation or hosting. Keep the data directory restricted to the project team and back it up deliberately.

PDF text extraction does not reconstruct complex merged financial tables or provide automatic OCR. Verify extracted figures against source reports. Cross-company reporting-period comparability and old disclosures remain analyst responsibilities. Provider calls have not been validated with a real API key. Probability calibration, independently labelled held-out evaluation, dollar budgets, benchmark data feeds, automated refreshes and qualitative causal-link analysis remain future work. No performance or accuracy claim is made.

Provider request contract: [official Chat Completions API reference](https://platform.openai.com/docs/api-reference/chat/create).
