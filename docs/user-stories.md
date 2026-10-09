# AI Thematic Analysis for Investment Research
## User Stories & Acceptance Criteria

**Project:** IS4002 — AI Thematic Exposure Engine  
**Sponsor:** Schroders  
**Version:** v0 (Theme Lab)  
**Last updated:** October 2026  
**Target audience:** Internal team — sprint planning and development handoff  
**Jira:** [YTP board](https://is212-g5-t2.atlassian.net/jira/software/projects/YTP/boards/3/backlog) — each story heading below is tagged with its Jira ID (e.g. `YTP-8`)

---

## Actors

| Actor | Description |
|---|---|
| **Portfolio Manager (PM)** | Defines themes, reviews classifications, monitors portfolio exposure. |
| **Analyst** | Uploads source documents, runs AI analysis, enters manual cases. |
| **System** | The backend — `app.py` + `engine.py`. Written from the system's perspective in backend stories. |

---

## Scope boundary

The MVP delivers this end-to-end flow and nothing else:

```
Define theme → Add companies → Upload filings → Run AI analysis
    → Manual review → Import portfolio → View dashboard → Export
```

The following are explicitly out of scope for the presentation:
- Specialist feedback routing channel
- Exposure change monitoring / alerts
- Portfolio Exposure Log (historical dashboard snapshots)
- Multi-user authentication
- Automated benchmark data feeds
- Indirect beneficiary quantification (value-chain exposure)

---

## Epic 1 — Theme Definition

### YTP-8 · Define a theme 

**As a** Portfolio Manager,  
**I want to** define an investment theme with structured scope fields,  
**so that** all downstream analysis is anchored to a precise, versioned definition.

**Acceptance Criteria:**

1. Required fields: `name`, `description`, `economic_driver`, `inclusions`, `exclusions`, `examples`, `geographic_scope`, `cutoff` (ISO date `YYYY-MM-DD`), `exposure_basis`.
2. Submitting with any blank field returns an error naming the missing field; no partial record is created.
3. `exposure_basis` must be `qualifying_revenue`; any other value is rejected.
4. On success the theme is assigned a unique `id`, a stable `theme_id`, and `version = 1`.
5. Editing an approved theme creates a new record with `version + 1` sharing the same `theme_id`; the original is not modified.
6. The UI requires all fields before submission and shows inline validation errors.

**Mentor notes (6 Oct):** All fields required — no optional fields. Geographic scope is MSCI Asia ex Japan for the pilot. Assume the analyst knows what they're doing; AI refinement of the definition is optional.

---

### YTP-9 · Generate a sub-theme taxonomy with AI 

**As a** Portfolio Manager,  
**I want to** generate a sub-theme taxonomy via AI from the theme definition,  
**so that** I have a structured classification hierarchy without drafting it manually.

**Acceptance Criteria:**

1. The system sends the theme scope to the LLM and returns a proposed node list.
2. Each node has: `node_id`, `parent_id` (null for roots), `name`, `definition`, `inclusion_rule`, `exclusion_rule`, `examples[]`, `rationale`.
3. Hierarchy is exactly **two levels deep** (theme → subtheme); deeper nesting is not generated.
4. The number of subthemes is capped at **5–10** per theme.
5. The returned taxonomy has `needs_review` status — it is never auto-approved.
6. If `LLM_API_KEY` or `LLM_MODEL` is not configured, the system returns a clear error; manual taxonomy editing is still available.
7. A failed or malformed LLM response does not produce a partial node list; the run is marked `failed`.

**Mentor notes (6 Oct):** Theme → subtheme only (2 levels). Keywords from both company language and market consensus are acceptable; AI combines both. GICS can be a starting anchor but is not required.

---

### YTP-10 · Review and approve a taxonomy 

**As a** Portfolio Manager,  
**I want to** review the proposed taxonomy and approve it before it is used in any analysis,  
**so that** I control what counts as thematic exposure.

**Acceptance Criteria:**

1. Taxonomy nodes can be edited (add, remove, update `inclusion_rule`, `exclusion_rule`, `definition`) before approving.
2. Only **leaf nodes** (nodes with no children) are valid classification targets.
3. Approving a theme version locks the taxonomy; further edits create a new theme version.
4. The UI clearly distinguishes `needs_review` drafts from `approved` versions.

---

## Epic 2 — Company & Source Management

### YTP-11 · Register a company 

**As an** Analyst,  
**I want to** register a company with a stable ID and legal name,  
**so that** all uploaded sources and cases are linked to the correct entity.

**Acceptance Criteria:**

1. Required fields: `id` (stable, analyst-assigned string), `name` (legal name), optional `jurisdiction`.
2. `id` must not be `CASH` (reserved for cash holdings).
3. A duplicate `id` is rejected with a clear error.
4. The registered company is immediately available for source uploads and analysis.

---

### YTP-12 · Upload a company source document 

**As an** Analyst,  
**I want to** upload an annual report or regulatory filing,  
**so that** the system can extract text passages for AI analysis.

**Acceptance Criteria:**

1. Accepted formats: PDF and UTF-8 TXT only; other types are rejected.
2. File size: 1 byte – 20 MB; outside this range is rejected.
3. PDFs are validated by magic bytes (`%PDF-`); invalid PDFs are rejected.
4. PDFs are capped at 500 pages.
5. Required metadata on upload: `company_id`, `publication_date` (ISO date), `period` (e.g. `FY2025`), `source_type`.
6. Optional: `url` (must be HTTP/HTTPS if provided).
7. The system deduplicates by SHA-256 hash + company + period + date; re-uploading the same file returns the existing record without creating a duplicate.
8. Text is split into passages of up to 5,000 characters, each tagged with `evidence_id`, `source_id`, and `page` number.
9. Pages with fewer than 80 characters of extracted text are flagged with a warning; they are not silently dropped.

**Mentor notes (6 Oct):** Primary sources are SEC filings via EDGAR (10-K, 20-F, 40-F). Earnings transcripts are acceptable supplements. Lagging (published) information only — no forward projections.

---

### YTP-13 · Enforce evidence cutoff 

**As an** Analyst,  
**I want** sources published after a theme's cutoff date to be automatically excluded from analysis,  
**so that** no future-dated disclosure contaminates the assessment.

**Acceptance Criteria:**

1. When evidence is retrieved for a company/theme pair, only sources where `publication_date ≤ theme.cutoff` are included.
2. A future-dated source uploads successfully but its evidence IDs are absent from the valid evidence set for any theme with an earlier cutoff.
3. No error is raised at upload time; exclusion is applied silently at retrieval.

---

## Epic 3 — AI-Assisted Analysis

### YTP-14 · Run AI analysis on selected passages 

**As an** Analyst,  
**I want to** select evidence passages and trigger AI fact extraction and classification in one step,  
**so that** the system produces a draft exposure case ready for review.

**Acceptance Criteria:**

1. The analyst selects one or more `evidence_id` values from cutoff-eligible sources for a given company and theme.
2. Only valid evidence IDs (within the approved evidence set for that theme) are accepted; invalid IDs are rejected.
3. Total character count of selected passages must not exceed 60,000.
4. **Stage 1 — Fact extraction:** the LLM returns `total_revenue`, `denominator_evidence_ids`, `period`, `currency`, `scale`, `units[]`, `unresolved_questions[]`. Each unit has `unit_id`, `activity`, `revenue`, `period`, `currency`, `scale`, `external: true`, `evidence_ids[]`.
5. **Stage 2 — Classification:** the LLM classifies each extracted unit against approved taxonomy leaf nodes, returning `relationship_type` (one of `direct`, `indirect`, `excluded`, `insufficient_evidence`), `rationale`, `scope_check`, `confidence_band` (`high`/`medium`/`low`), and `confidence_basis`.
6. The AI must not invent revenue figures; all numbers must be traceable to selected passages.
7. Every extracted unit appears exactly once in the classification output.
8. `direct` classifications require at least one valid leaf node ID and at least one cited evidence ID.
9. `excluded` and `indirect` units contribute zero qualifying revenue.
10. Units with ambiguous allocation retain a range (`lower < upper`, `point = null`).
11. A malformed or schema-violating LLM response marks the run `failed`; no case is created.
12. Run status progresses: `extracting → classifying → validating → needs_review` on success; `failed` on any error.

**Mentor notes (6 Oct):** Companies that discuss a theme but cannot tie it to a revenue line → `insufficient_evidence`, not a guess. Mapping qualitative language to estimated ranges is acceptable.

---

### YTP-15 · Control AI usage with a call cap 

**As a** Portfolio Manager,  
**I want** AI calls to be capped by an explicit environment variable,  
**so that** I never incur unexpected API charges.

**Acceptance Criteria:**

1. `LLM_MAX_CALLS` defaults to `0` (AI disabled).
2. Any call when the cap is reached returns: "AI call cap reached. Set LLM_MAX_CALLS explicitly before running paid calls."
3. The cap is persistent across restarts (tracked in SQLite).
4. Identical requests (same payload, model, endpoint, version) are served from cache without consuming the cap.
5. Each company analysis consumes 2 calls (extraction + classification); taxonomy generation consumes 1.

---

## Epic 4 — Manual Review Workflow

### YTP-16 · Enter a manual revenue case 

**As an** Analyst,  
**I want to** manually enter a structured revenue case,  
**so that** I can classify exposure without AI or override an AI draft.

**Acceptance Criteria:**

1. Required: `company_id`, `theme_id`, `total_revenue` (positive finite number), `denominator_evidence_ids[]`, `period`, `currency`, `scale`, `units[]`, `classification_confidence` (`high`/`medium`/`low`), `exposure_quality`, `confidence_basis` (non-empty text).
2. Each unit must be `external: true`, `revenue ≥ 0`, match denominator `period`/`currency`/`scale`, reference valid `evidence_ids[]`, have a non-empty `rationale`.
3. `excluded` and `indirect` units must have `lower = upper = point = 0`.
4. `estimated` method units require a non-empty `assumption`.
5. `insufficient_evidence` method units cannot have a `point` estimate.
6. Duplicate `unit_id` values in one case are rejected.
7. Two units sharing an `overlap_group` are rejected (prevents double-counting).
8. Sum of unit revenues must not exceed `total_revenue` (tolerance 1e-8).
9. An unallocated residual (gap between unit sum and total) is flagged in `issues[]`; cases with `issues[]` cannot be approved until resolved.
10. All evidence IDs must belong to sources whose `period` matches the case's declared revenue period.

---

### YTP-17 · Approve or reject a case 

**As a** Portfolio Manager,  
**I want to** approve or reject a submitted case with a documented reason,  
**so that** only verified results reach the portfolio dashboard.

**Acceptance Criteria:**

1. Only cases with `review_status = needs_review` can be reviewed; re-reviewing a decided case is rejected.
2. Required: `decision` (`approved` or `rejected`), non-empty `reviewer`, non-empty `reason`.
3. Cases with non-empty `issues[]` on the estimate **cannot** be approved.
4. On approval: `review_status → approved`; associated run → `completed`.
5. On rejection: run → `needs_review`; analyst can create a revised case version.
6. An audit record is stored containing: original case value, revised case value, reviewer, reason, and UTC timestamp.
7. Approved cases are immutable; revisions create a new case record, not an edit.

---

## Epic 5 — Portfolio Dashboard

### YTP-18 · Import a portfolio and benchmark snapshot 

**As a** Portfolio Manager,  
**I want to** import a portfolio and benchmark holdings snapshot,  
**so that** the system can aggregate company-level exposures into a portfolio view.

**Acceptance Criteria:**

1. Both `portfolio` and `benchmark` arrays are required.
2. Each row requires: `snapshot_id`, `as_of_date`, `security_id`, `company_id`, `weight` (decimal 0–1).
3. Weights within each snapshot must sum to 1.0 (tolerance 0.000001).
4. `as_of_date` must be identical for portfolio and benchmark.
5. A `security_id` must map to the same `company_id` in both snapshots; conflicting mappings are rejected.
6. Duplicate `security_id` within a single snapshot is rejected.
7. Multiple securities may share a `company_id` (multiple share classes); their weights are aggregated to company level.
8. `company_id = CASH` is accepted and treated as zero thematic exposure.

**Mentor notes (6 Oct):** Equities only. Benchmark is MSCI Asia ex Japan for the pilot. Exposure formula: Σ (portfolio weight × company revenue exposure). Cost exposure is not considered.

---

### YTP-19 · View the portfolio exposure dashboard 

**As a** Portfolio Manager,  
**I want to** see portfolio, benchmark, and active thematic exposures with company-level breakdown,  
**so that** I understand how the portfolio is positioned and can explain each exposure.

**Acceptance Criteria:**

1. The dashboard shows `portfolio`, `benchmark`, and `active` (portfolio − benchmark) exposures with `lower`, `upper`, and `point` values.
2. Companies without an approved case contribute `lower = 0`, `upper = 1`, `point = null` (unknown, not zero).
3. Cash holdings show zero exposure and are reported separately from equity coverage.
4. Negative active weights (underweight positions) correctly reverse `lower`/`upper` bounds.
5. When any company with a nonzero active weight has no point estimate, `active.point` is shown as null/unavailable — not a false zero.
6. Holdings weights are never renormalised.
7. Companies are sorted by absolute active contribution (largest contributors first).
8. A coverage metric shows the percentage of portfolio and benchmark weight with approved estimates.
9. Each company row shows its exposure range, point estimate, and the classification rationale — supporting explainability.
10. Sub-theme breakdown is shown to explain drivers of overall theme exposure.
11. The same inputs always produce the same dashboard output (consistency).

**Mentor notes (6 Oct):** Non-negotiable: show exposure *and* explain why. "Why does this portfolio have X% exposure?" must be answerable from the UI.

---

### YTP-20 · Export the research package 

**As an** Analyst,  
**I want to** export all analysis artefacts as a ZIP archive,  
**so that** the research is reproducible, auditable, and shareable with the team.

**Acceptance Criteria:**

1. The export ZIP contains: `exposure_results.csv`, `evidence.csv`, `run_manifest.json`, `company_cases.json`, `model_outputs.json`.
2. `exposure_results.csv` includes per-case: `case_id`, `company_id`, `lower`, `upper`, `point`, `period`, `classification_confidence`, `exposure_quality`, `confidence_basis`, `review_status`.
3. `run_manifest.json` includes the theme definition, all runs, all reviews, and the dashboard state at export time.
4. Re-exporting the same approved data produces equivalent content (deterministic).

---

## Epic 6 — Validation & Evaluation

### YTP-21 · Evaluate classification accuracy against a labelled set 

**As an** Analyst,  
**I want to** run `evaluate.py` against a labelled reference set,  
**so that** I can report classification accuracy, exposure error, and confidence calibration for the presentation.

**Acceptance Criteria:**

1. `python evaluate.py labelled_examples.json` produces a JSON report without error.
2. Report includes: `examples`, `abstention_rate`, `answered_accuracy`, per-label `precision`/`recall`/`F1`, per-rubric-band `observed_accuracy`, `exposure_mean_absolute_error_pp`, `reference_range_overlap_rate`.
3. Rubric band accuracy is labelled as non-probabilistic.
4. Development and held-out splits are kept separate; no accuracy claim is made from the synthetic demo.

**Mentor notes (6 Oct):** Target gold set: 20 companies (8 dev / 12 held-out). Acceptable bar: mean exposure error ≤ 10 pp on held-out, beating sector-label and keyword baselines. 15–20 well-validated companies beats 30 poorly-validated ones.

---

## Backend Stories (Epic 7)

> System-level behaviour: persistence, state transitions, calculation invariants, security. These are implementation acceptance criteria for the backend.

---

### YTP-22 · Persist themes as versioned, immutable records 

**As the** system,  
**I need to** write every theme as an immutable record and create new versions on edit,  
**so that** past analyses always reference the exact scope definition they were run against.

**Acceptance Criteria:**

1. A new theme with no `previous_id` gets `version = 1` and a fresh `theme_id`.
2. A theme with a valid `previous_id` inherits the same `theme_id` and receives `version = previous.version + 1`; the prior record is not modified.
3. `engine.taxonomy(nodes)` is called before any write; structural errors (duplicate IDs, cycles, missing fields, empty list) abort the write.
4. All records are persisted via `INSERT OR REPLACE INTO records` with `(kind, id)` as primary key; SQLite WAL mode handles concurrent reads.

---

### YTP-23 · Ingest source documents with deduplication and passage extraction 

**As the** system,  
**I need to** hash each uploaded file, detect duplicates, and split the text into page-linked passages,  
**so that** evidence IDs are stable and the same filing is never ingested twice.

**Acceptance Criteria:**

1. SHA-256 hash is computed before any write; an existing record with the same hash + company + period + date is returned immediately with no new file written.
2. PDF magic bytes (`%PDF-`) are checked; invalid PDFs are rejected.
3. Each page is chunked at 5,000-character boundaries; each chunk gets a deterministic `evidence_id` of the form `<source_id>_p<page>_<chunk>`.
4. Pages with fewer than 80 characters of text produce a warning entry but no evidence passage — they are not silently empty.
5. Evidence retrieval (`evidence_for`) filters by `publication_date ≤ theme.cutoff`; evidence IDs from excluded sources are not in the valid set.

---

### YTP-24 · Manage the AI run through a strict state machine 

**As the** system,  
**I need to** transition each AI run through defined statuses and record failures with stage and reason,  
**so that** every failure is traceable and no partial output reaches the case store.

**Acceptance Criteria:**

1. `analyze()` creates a run at `status = extracting` before any LLM call.
2. Successful Stage 1 → `classifying`; successful Stage 2 + engine validation → `needs_review`; case record written only at this point.
3. Any exception at any stage: `run.status = failed`, `run.errors[]` appended with `{stage, reason}`, run persisted, exception re-raised, **no case record created**.
4. Raw provider responses are stored in `provider_outputs` before schema validation; validated model outputs are stored in `model_outputs` with stage label.
5. LLM responses are validated for required keys and types before use; a missing key or wrong type is treated as a failure, not silently ignored.

---

### YTP-25 · Cache LLM responses for determinism 

**As the** system,  
**I need to** cache validated LLM responses by a hash of (task, payload, model, endpoint, versions),  
**so that** re-running the same analysis on the same data returns identical results without extra API calls.

**Acceptance Criteria:**

1. Before each LLM call, compute `SHA-256(json([task, payload, model, endpoint, VERSIONS]))` and check the `cache` table.
2. A cache hit returns the stored response immediately; the call cap counter is not incremented.
3. A cache miss on a successful response writes the response to the `cache` table before returning.
4. `VERSIONS` includes the component version strings; a version bump invalidates prior cache entries for that component.

---

### YTP-26 · Calculate exposure deterministically with no LLM involvement 

**As the** system,  
**I need to** compute all exposure figures in `engine.estimate()` as a pure function,  
**so that** the same case always produces the same numerical result regardless of model or provider.

**Acceptance Criteria:**

1. `engine.estimate(case, nodes, evidence)` performs no I/O and has no side effects.
2. Core formula: `exposure = Σ(unit.revenue × unit.allocation) / total_revenue` for qualifying units.
3. Unallocated residual (`total_revenue − Σ unit.revenue > 1e-8`): added to `upper`, `point` suppressed to null, issue appended.
4. `excluded` and `indirect` units have allocations forced to zero; any nonzero allocation is rejected.
5. `insufficient_evidence` units cannot have a `point` allocation.
6. Duplicate `unit_id`, shared `overlap_group`, unit revenue sum exceeding total, allocation fractions > 1: all rejected with specific errors.
7. Return includes `lower`, `upper`, `point` (null if any unit is unresolved), `residual_revenue`, `issues[]`, `subthemes{}`, `calculation_version`.

---

### YTP-27 · Gate case approval on resolved reconciliation 

**As the** system,  
**I need to** block approval of any case with a non-empty `issues[]` on the estimate,  
**so that** the dashboard is never fed a result with an unresolved revenue gap.

**Acceptance Criteria:**

1. `review()` checks `case.estimate.issues`; if non-empty and `decision = approved`, raises an error before any write.
2. `rejected` decisions are permitted even with unresolved issues.
3. On approval: `case.review_status → approved`, `run.status → completed`.
4. On rejection: `run.status → needs_review`.
5. An audit record (`reviews` table) is written containing the deep-copied original case, the revised case, reviewer, reason, and UTC timestamp — written before the case update.

---

### YTP-28 · Aggregate portfolio exposure using the latest approved case per company 

**As the** system,  
**I need to** select exactly one approved case per company per theme for the dashboard,  
**so that** the portfolio view is always consistent and never uses a superseded result.

**Acceptance Criteria:**

1. `selected_estimates(theme_id)` returns a `{company_id: estimate}` dict using only `review_status = approved` cases; where multiple exist for a company, the most recently inserted record wins.
2. Companies present in the snapshot but absent from estimates: `lower=0, upper=1, point=null` (unknown, not zero).
3. Negative active weight (underweight position) reverses `lower`/`upper` bounds.
4. `active.point` is suppressed to null if any company with nonzero active weight has `point = null`.
5. `CASH` holdings are always `lower=upper=point=0`.
6. Snapshot weights are never renormalised.

---

### YTP-29 · Enforce same-origin and local-only access 

**As the** system,  
**I need to** reject write requests from any origin other than the local application,  
**so that** a page from any external site cannot trigger data modifications.

**Acceptance Criteria:**

1. All POST handlers check `Origin == 'http://' + Host`; mismatch returns HTTP 403.
2. All GET handlers check that `Host` is `127.0.0.1` or `localhost`; other hosts return HTTP 403.
3. A request with `Origin: https://untrusted.example` to any write endpoint returns HTTP 403 without executing any business logic.

---

## Appendix: Prioritised Story Map

| Priority | Stories | Why |
|---|---|---|
| **Must have** | US-01 (YTP-8), US-02 (YTP-9), US-03 (YTP-10), US-04 (YTP-11), US-05 (YTP-12), US-06 (YTP-13) | Theme + source pipeline — everything depends on this |
| **Must have** | US-07 (YTP-14), US-08 (YTP-15), US-09 (YTP-16), US-10 (YTP-17) | Analysis + review — core classification workflow |
| **Must have** | US-11 (YTP-18), US-12 (YTP-19), US-13 (YTP-20) | Portfolio dashboard — the primary deliverable |
| **Must have** | US-B01–B08 (YTP-22–YTP-29) | Backend contracts that make the above work correctly |
| **Should have** | US-14 (YTP-21) | Evaluation for presentation evidence |
| **Cut** | Specialist feedback (US-11 old), Exposure monitoring (US-15/16 old), Indirect beneficiary quantification | Not achievable in under 1 month |

---

## Appendix: Core Business Rules

| Rule | Behaviour |
|---|---|
| All theme fields required | Reject with field name if any blank |
| Theme → Subtheme only (2 levels) | Max depth 2; leaf nodes only for classification |
| 5–10 subthemes per theme | LLM constrained at generation time |
| Equities only | No bonds, derivatives, or other instruments |
| Revenue exposure, not cost | Cost lines are not used in the formula |
| MSCI Asia ex Japan benchmark | Pilot universe |
| Lagging data only | SEC filings; no forward-looking projections |
| No invented figures | AI must cite source evidence for all numbers |
| Insufficient data → flag | `insufficient_evidence` type, not a guessed number |
| Residual blocks approval | Unallocated revenue must be resolved first |
| Immutable approved themes | Edits create a new version |
| Immutable approved cases | Revisions create a new case record |
| AI cap defaults to 0 | Must be set explicitly before any paid calls |
| Confidence is ordinal, not probabilistic | high/medium/low = 3/2/1; never a percentage |
| Consistency | Same inputs → same output on every run |
| Explainability | Every exposure entry shows its evidence and rationale |
