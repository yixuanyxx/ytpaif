# AI Thematic Analysis for Investment Research
## User Stories & Acceptance Criteria

**Project:** IS4002 — AI Thematic Exposure Engine  
**Sponsor:** Schroders  
**Version:** v0 (Theme Lab)  
**Last updated:** October 2026  
**Target audience:** Internal team — sprint planning and development handoff  
**Jira:** YTP board — each story heading is tagged with its Jira issue key

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
Define theme → Match companies → Find/upload filings → Run AI analysis
    → Manual review → Import portfolio → View dashboard → Export
```

Explicitly out of scope for the presentation:
- Specialist feedback routing channel
- Exposure change monitoring / alerts
- Portfolio Exposure Log (historical dashboard snapshots)
- Multi-user authentication
- Automated benchmark data feeds
- Indirect beneficiary quantification (value-chain exposure)

---

## Epic YTP-6 — Theme Definition

### YTP-8 · Define a theme

**As a** Portfolio Manager,
**I want to** define an investment theme with structured scope fields,
**so that** all downstream analysis is anchored to a precise, versioned definition.

**Acceptance Criteria:**

1. Required field: `theme_name`.
2. At least one of the following pairs must be filled: {`theme_description`, `included_activities`} or {`positive_examples`}.
3. Non-required fields: `excluded_activities`, `negative_examples`, `additional_information`.
4. Submitting with a blank `theme_name` returns an error; no partial record is created.
5. The UI requires all required fields before submission and shows inline validation errors.
6. On success the theme is assigned a unique `theme_id` and `version = 1`.
7. Editing an approved theme creates a new record with `version + 1` sharing the same `theme_id`; the original is not modified.

---

### YTP-14 · Generate a sub-theme taxonomy with AI

**As a** Portfolio Manager,
**I want to** generate a sub-theme taxonomy via AI from the theme definition,
**so that** I have a structured classification hierarchy without drafting it manually.

**Acceptance Criteria:**

1. The system sends the theme scope to the LLM and returns a proposed node list.
2. Each node has: `node_id`, `parent_id` (null for roots), `name`, `definition`, `examples[]`.
3. The hierarchy contains two subtheme levels beneath the main theme: theme → Tier-1 subtheme → Tier-2 subtheme. No deeper nesting is generated.
4. Before generation, the PM can select "Let AI decide" or specify the number of Tier-1 subthemes and the number of Tier-2 subthemes per Tier-1 node. Specified counts must be positive integers and are followed by the generator. These settings are saved with the generation run.
5. If counts are not specified, the AI proposes an appropriate number of Tier-1 and Tier-2 subthemes based on the theme definition, avoiding redundant categories and unnecessary splitting.
6. The PM can manually edit the proposed taxonomy or request AI regeneration for a selected node and its descendants before approval. The PM can provide instructions for regeneration. Changes are presented for review before being applied; unrelated branches remain unchanged. Regenerated content must remain within the theme definition and the two-subtheme-level limit.
7. The returned taxonomy has `needs_review` status — it is never auto-approved.
8. If `LLM_API_KEY` or `LLM_MODEL` is not configured, the system returns a clear error; manual taxonomy editing is still available.
9. If generation fails or the response cannot be validated as a complete, structurally valid taxonomy, the system shows a clear error and allows the PM to retry. The failed attempt does not replace any existing taxonomy.
10. Repeating generation with the same theme definition, requested category counts, model and prompt version produces materially consistent categories and hierarchy. Consistency is assessed by category meaning and parent-child relationships, not exact wording. The acceptance threshold will be defined in the project's validation criteria.

**Mentor notes (6 Oct):** 2 levels to subthemes. AI may combine company language and market consensus. GICS or Bloomberg Industry Classification System (BICS) can be a starting anchor but is not required.

**Future enhancement:** Configurable hierarchy depth beyond two subtheme levels.

---

### YTP-15 · Review and approve a taxonomy

**As a** Portfolio Manager,
**I want to** review the proposed taxonomy and approve it before it is used in any analysis,
**so that** I control what counts as thematic exposure.

**Acceptance Criteria:**

1. The PM can manually edit by adding, removing, or editing nodes across all layers, or request AI regeneration for a selected node and its descendants before approval. The PM can provide instructions for regeneration. Changes are presented for review before being applied; unrelated branches remain unchanged. Regenerated content must remain within the theme definition and the two-subtheme-level limit.
2. Only Tier-2 subthemes (level-2 nodes) are valid classification targets.
3. Before approval, the system validates that: required node fields are completed, node IDs are unique, parent references are valid, no cycles exist, and the hierarchy does not exceed two subtheme levels. Each Tier-1 subtheme must contain at least one Tier-2 subtheme before approval. Invalid structures block approval and show clear errors. Drafts that fail validation can be saved but cannot be approved.
4. Approving a taxonomy locks that version. Further edits create a new draft under the same theme. The existing approved taxonomy remains in use until the new draft is approved; after that the newest approved taxonomy is used for new analyses. Previous taxonomy versions are retained unchanged; previous analyses remain linked to the exact version used.
5. Only approved taxonomy versions can be used for company classification. Each analysis records the taxonomy version used.
6. The UI clearly distinguishes `needs_review` drafts from `approved` versions.

**Future enhancement:** Browse and select a previous taxonomy version for a new analysis run.

**Next step:** YTP-23

---

## Epic YTP-7 — Company & Source Management

### YTP-16 · Create and match company records

**As a** Portfolio Manager,
**I want** the system to create or match company records with securities from my imported portfolio data,
**so that** documents and thematic analyses are linked to the correct companies and securities without manually registering each one.

**Acceptance Criteria:**

1. For each imported portfolio row, the system creates a security record if its SEDOL or Bloomberg ticker is not already registered; otherwise it reuses the existing security record. Each security is linked to its company record, creating a new company record where necessary.
2. Conflicting security identifiers or ambiguous security-to-company matches are flagged for PM review. Companies are not automatically merged based solely on similar names.
3. Multiple securities belonging to the same company can be linked to one company record following PM confirmation. Their portfolio and benchmark weights remain separate in the imported data.
4. Portfolio weights, benchmark weights, and dates remain associated with the relevant portfolio import, not stored as permanent attributes of the company or security.
5. The PM can manually register an additional company and security by providing the company name and at least one security identifier.
6. Once identity conflicts are resolved, company records are available for source collection and thematic analysis.

---

### YTP-17 · Find company source documents

**As a** Portfolio Manager,
**I want** the system to find relevant public documents for companies in my imported portfolio,
**so that** thematic analysis can use supporting evidence without requiring me to collect every document manually.

**Acceptance Criteria:**

1. The system searches for source documents for selected portfolio companies using their company names and available identifiers.
2. The PM can upload documents or provide source URLs before or after an automated search. Automated searching is optional when sufficient documents have already been provided. All sources undergo the same validation, company-matching, cutoff, and deduplication checks. The PM can review and exclude sources before analysis.
3. Before searching, the PM can select the source types to search for. Annual reports and regulatory filings are selected by default. Additional supported options include: investor presentations, company website pages, earnings-call transcripts, sustainability reports, news and company announcements. The PM can specify additional source types. If a requested source cannot be accessed or processed, the system explains the limitation and allows the PM to provide an alternative URL or upload. Selected source types and the resulting sources (labelled with source type) are saved with the search run.
4. The system prioritises official company websites, investor relations pages, and regulatory or stock-exchange sources.
5. Before source collection or analysis, the PM can specify the reporting period and evidence cutoff date. These settings apply to both manually provided and automatically retrieved sources and are saved with the analysis run.
6. For each document found, the system records: title, associated company, source URL, source type, publication date, and reporting period where applicable. Missing metadata is explicitly identified rather than guessed.
7. Documents published after the selected cutoff date are excluded from analysis, with the exclusion reason shown. Documents with unknown publication dates are flagged for review before use.
8. The system checks that each document relates to the intended company. Ambiguous company matches are flagged for PM review and are not used until resolved.
9. The PM can view the collected documents and exclude unsuitable sources.
10. If no suitable documents are found or a document cannot be retrieved, the system identifies the affected company and the reason where known, and allows retry or manual upload. Failures for one company do not prevent collection for other companies.
11. Successfully retrieved documents are passed to document ingestion for file validation, deduplication, and text extraction.
12. Additional sources may support classification and explain business activities. Revenue figures used in exposure calculations must remain traceable to supporting disclosures; estimates are clearly distinguished from reported figures.
13. The system retains the source list and document versions used for each analysis. A fresh search does not silently replace evidence used in a previous analysis.

**Mentor notes (6 Oct):** Primary sources are SEC filings via EDGAR (10-K, 20-F, 40-F). Lagging (published) information only — no forward projections.

---

### YTP-18 · Enforce evidence cutoff

**As an** Analyst,
**I want** sources published after a theme's cutoff date to be automatically excluded from analysis,
**so that** no future-dated disclosure contaminates the assessment.

**Acceptance Criteria:**

1. When evidence is retrieved for a company/theme pair, only sources where `publication_date ≤ theme.cutoff` are included.
2. A future-dated source uploads successfully but its evidence IDs are absent from the valid evidence set for any theme with an earlier cutoff.
3. No error is raised at upload time; exclusion is applied silently at retrieval.

---

## Epic YTP-9 — AI-Assisted Analysis

### YTP-19 · Run AI analysis on selected passages

**As an** Analyst,
**I want to** verify that the AI fact extraction and classification output is accurate,
**so that** the system produces a draft exposure case ready for review.

**Acceptance Criteria:**

1. The analyst selects one or more `evidence_id` values from cutoff-eligible sources for a given company and theme.
2. Only valid evidence IDs (within the approved evidence set for that theme) are accepted; invalid IDs are rejected.
3. Total character count of selected passages must not exceed 60,000.
4. **Stage 1 — Fact extraction:** the LLM returns `total_revenue`, `denominator_evidence_ids`, `period`, `currency`, `scale`, `units[]`, `unresolved_questions[]`. Each unit has `unit_id`, `activity`, `revenue`, `period`, `currency`, `scale`, `external: true`, `evidence_ids[]`.
5. **Stage 2 — Classification:** the LLM classifies each extracted unit against approved Tier-2 taxonomy nodes, returning `relationship_type` (one of `direct`, `indirect`, `excluded`, `insufficient_evidence`), `rationale`, `scope_check`, `confidence_band` (`high`/`medium`/`low`), and `confidence_basis`.
6. The AI must not invent revenue figures; all numbers must be traceable to selected passages.
7. Every extracted unit appears exactly once in the classification output.
8. `direct` classifications require at least one valid Tier-2 node ID and at least one cited evidence ID.
9. `excluded` and `indirect` units contribute zero qualifying revenue.
10. Units with ambiguous allocation retain a range (`lower < upper`, `point = null`).
11. A malformed or schema-violating LLM response marks the run `failed`; no case is created.
12. Run status progresses: `extracting → classifying → validating → needs_review` on success; `failed` on any error.

**Mentor notes (6 Oct):** Companies that discuss a theme but cannot tie it to a revenue line → `insufficient_evidence`, not a guess. Mapping qualitative language to estimated ranges is acceptable.

---

### YTP-20 · Control AI usage with a call cap

**As a** Portfolio Manager,
**I want** AI calls to be capped by an explicit environment variable,
**so that** I never incur unexpected API charges.

**Acceptance Criteria:**

1. `LLM_MAX_CALLS` defaults to `0` (AI disabled).
2. Any call when the cap is reached returns: "AI call cap reached. Set LLM_MAX_CALLS explicitly before running paid calls."
3. The cap is persistent across restarts (tracked by Supabase model-call records).
4. Identical requests (same payload, model, endpoint, version) are served from cache without consuming the cap.
5. Each company analysis consumes 2 calls (extraction + classification); taxonomy generation consumes 1.

---

## Epic YTP-10 — Manual Review Workflow

### YTP-21 · Enter a manual revenue case

**As an** Analyst,
**I want to** manually enter a structured revenue case,
**so that** I can classify exposure without AI or override an AI draft.

**Acceptance Criteria:**

1. Required: `company_id`, `theme_id`, `total_revenue` (positive finite number), `denominator_evidence_ids[]`, `period`, `currency`, `scale`, `units[]`, `classification_confidence` (ratio), `exposure_quality`, `confidence_basis` (non-empty text).
2. Each unit must be `external: true`, `revenue ≥ 0`, match denominator `period`/`currency`/`scale`, reference valid `evidence_ids[]`, have a non-empty `rationale`.
3. Duplicate `unit_id` values in one case are rejected.
4. Two units sharing an `overlap_group` are rejected (prevents double-counting).
5. Sum of unit revenues must not exceed `total_revenue` (tolerance 1e-8).
6. An unallocated residual is flagged in `issues[]`; cases with `issues[]` cannot be approved until resolved.
7. All evidence IDs must belong to sources whose `period` matches the case's declared revenue period.

---

### YTP-22 · Approve or reject a case

**As a** Portfolio Manager,
**I want to** approve or reject a submitted case with a documented reason,
**so that** only verified results reach the portfolio dashboard.

**Acceptance Criteria:**

1. Only cases with `review_status = needs_review` can be reviewed; re-reviewing a decided case is rejected.
2. Required: `decision` (`approved` or `rejected`), non-empty `reviewer`, non-empty `reason`.
3. On approval: `review_status → approved`; associated run → `completed`.
4. On rejection: `review_status → rejected`; analyst can create a revised case version.
5. An audit record is stored containing: original case value, revised case value, reviewer, reason, and UTC timestamp.
6. Approved cases are immutable; revisions create a new case record, not an edit.

---

## Epic YTP-11 — Portfolio Dashboard

### YTP-23 · Import portfolio and benchmark holdings data

**As a** Portfolio Manager,
**I want to** import portfolio holdings and their corresponding benchmark weights,
**so that** the system can calculate portfolio thematic exposure and active thematic contribution across the companies held in the portfolio.

**Working assumption:** Analysis covers only companies in the uploaded portfolio. Active thematic contribution is calculated as the sum of `(portfolio weight − reference weight) × company thematic exposure` across these companies. Benchmark-only companies are excluded; the result is not presented as full portfolio-minus-benchmark thematic exposure.

**Acceptance Criteria:**

1. The PM can upload an Excel file following the Schroders sample format with columns: Company, Bloomberg ticker, Country, Sector, SEDOL, Portfolio wt, Reference wt, Active wt.
2. Required per row: company name, at least one security identifier (Bloomberg ticker or SEDOL), portfolio weight, and reference weight. The import upload timestamp is recorded separately.
3. Optional: Country and Sector are retained when provided. Portfolio holdings date, benchmark name, and benchmark holdings date can also be uploaded or filled in. If unavailable, they are recorded as "Not provided". If both holdings dates are provided and differ, the system flags the mismatch for PM confirmation before proceeding.
4. The system reads Excel percentage-formatted weights correctly and stores them internally as decimal fractions.
5. Missing required information, invalid numeric weights, and duplicate security identifiers are flagged for correction before import is completed. Missing weights are not silently treated as zero.
6. Active weight is calculated as portfolio weight minus reference weight. If an uploaded Active wt differs beyond an agreed rounding tolerance, the discrepancy is flagged.
7. The system displays total portfolio and reference weights. Portfolio weights are expected to total 100%, subject to an agreed rounding tolerance. Reference weights are not required to total 100% because only portfolio holdings are included.
8. Successfully imported holdings are available for company analysis and subsequent exposure calculations.

**Mentor notes (6 Oct):** Equities only. Benchmark is MSCI Asia ex Japan for the pilot. Exposure formula: Σ (portfolio weight × company revenue exposure). Cost exposure is not considered.

**Further enhancement:** Allow the PM to upload updated benchmark companies and weights or retrieve them through an authorised data source. Support importing all benchmark companies including those not held in the portfolio to calculate full benchmark and active thematic exposures.

**Next step:** YTP-16

---

### YTP-24 · View the portfolio exposure dashboard

**As a** Portfolio Manager,
**I want to** see portfolio, benchmark, and active thematic exposures with company-level breakdown,
**so that** I understand how the portfolio is positioned and can explain each exposure.

**Acceptance Criteria:**

1. The dashboard shows `portfolio`, `benchmark`, and `active` (portfolio − benchmark) exposures with `lower`, `upper`, and `point` values.
2. Companies are sorted by absolute active contribution (largest contributors first).
3. A coverage metric shows the percentage of portfolio and benchmark weight with approved estimates.
4. Each company row shows its exposure and the classification rationale — supporting explainability.
5. Sub-theme breakdown is shown to explain drivers of overall theme exposure.
6. The same inputs always produce the same dashboard output (consistency).

**Mentor notes (6 Oct):** Non-negotiable — show exposure and explain why. "Why does this portfolio have X% exposure?" must be answerable from the UI.

---

### YTP-25 · Export the research package

**As an** Analyst,
**I want to** export all analysis artefacts as a ZIP archive,
**so that** the research is reproducible, auditable, and shareable with the team.

**Acceptance Criteria:**

1. The export ZIP contains: `exposure_results.csv`, `evidence.csv`, `run_manifest.json`, `company_cases.json`, `model_outputs.json`.
2. `exposure_results.csv` includes per-case: `case_id`, `company_id`, `lower`, `upper`, `point`, `period`, `classification_confidence`, `exposure_quality`, `confidence_basis`, `review_status`.
3. `run_manifest.json` includes the theme definition, all runs, all reviews, and the dashboard state at export time.
4. Re-exporting the same approved data produces equivalent content (deterministic).

---

## Epic YTP-13 — Backend / System

### YTP-29 · Persist themes as versioned, immutable records

**As the** system,
**I need to** write every theme as an immutable record and create new versions on edit,
**so that** past analyses always reference the exact scope definition they were run against.

**Acceptance Criteria:**

1. A new theme with no `previous_id` gets `version = 1` and a fresh `theme_id`.
2. A theme with a valid `previous_id` inherits the same `theme_id` and receives `version = previous.version + 1`; the prior record is not modified.
3. `engine.taxonomy(nodes)` is called before any write; structural errors (duplicate IDs, cycles, missing fields, empty list) abort the write.
4. All records are persisted in Supabase; the backend uses `(kind, id)` keys for generic research records.

---

### YTP-30 · Ingest source documents with deduplication and passage extraction

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

### YTP-31 · Manage the AI run through a strict state machine

**As the** system,
**I need to** transition each AI run through defined statuses and record failures with stage and reason,
**so that** every failure is traceable and no partial output reaches the case store.

**Acceptance Criteria:**

1. `analyze()` creates a run at `status = extracting` before any LLM call.
2. Successful Stage 1 → `classifying`; successful Stage 2 + engine validation → `needs_review`; case record written only at this point.
3. Any exception at any stage: `run.status = failed`, `run.errors[]` appended with `{stage, reason}`, run persisted, exception re-raised, no case record created.
4. Raw provider responses are stored in `provider_outputs` before schema validation; validated model outputs are stored in `model_outputs` with stage label.
5. LLM responses are validated for required keys and types before use; a missing key or wrong type is treated as a failure, not silently ignored.

---

### YTP-32 · Cache LLM responses for determinism

**As the** system,
**I need to** cache validated LLM responses by a hash of (task, payload, model, endpoint, versions),
**so that** re-running the same analysis on the same data returns identical results without extra API calls.

**Acceptance Criteria:**

1. Before each LLM call, compute `SHA-256(json([task, payload, model, endpoint, VERSIONS]))` and check the `cache` table.
2. A cache hit returns the stored response immediately; the call cap counter is not incremented.
3. A cache miss on a successful response writes the response to the `cache` table before returning.
4. `VERSIONS` includes the component version strings; a version bump invalidates prior cache entries for that component.

---

### YTP-33 · Calculate exposure deterministically with no LLM involvement

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

### YTP-34 · Gate case approval on resolved reconciliation

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

### YTP-35 · Aggregate portfolio exposure using the latest approved case per company

**As the** system,
**I need to** select exactly one approved case per company per theme for the dashboard,
**so that** the portfolio view is always consistent and never uses a superseded result.

**Acceptance Criteria:**

1. `selected_estimates(theme_id)` returns a `{company_id: estimate}` dict using only `review_status = approved` cases; where multiple exist for a company, the most recently inserted record wins.
2. Companies present in the snapshot but absent from estimates: `lower=0, upper=1, point=null` (unknown, not zero).
3. Negative active weight (underweight position) reverses `lower`/`upper` bounds.
4. `active.point` is suppressed to null if any company with nonzero active weight has `point = null`.

---

### YTP-36 · Enforce same-origin and local-only access

**As the** system,
**I need to** reject write requests from any origin other than the local application,
**so that** a page from any external site cannot trigger data modifications.

**Acceptance Criteria:**

1. All POST handlers check `Origin == 'http://' + Host`; mismatch returns HTTP 403.
2. All GET handlers check that `Host` is `127.0.0.1` or `localhost`; other hosts return HTTP 403.
3. A request with `Origin: https://untrusted.example` to any write endpoint returns HTTP 403 without executing any business logic.

---

## Appendix: Prioritised Story Map

| Priority | Jira IDs | Why |
|---|---|---|
| **Must have** | YTP-8, YTP-14, YTP-15 | Theme + taxonomy — everything downstream depends on this |
| **Must have** | YTP-16, YTP-17, YTP-18 | Company matching + source collection |
| **Must have** | YTP-19, YTP-20, YTP-21, YTP-22 | AI analysis + manual review — core classification workflow |
| **Must have** | YTP-23, YTP-24, YTP-25 | Portfolio dashboard — the primary deliverable |
| **Must have** | YTP-29–YTP-36 | Backend contracts underpinning all of the above |

---

## Appendix: Core Business Rules

| Rule | Behaviour |
|---|---|
| `theme_name` required; other fields partially required | See YTP-8 for field requirements |
| Two-tier taxonomy (Tier-1 → Tier-2) | Max depth 2 levels below theme; only Tier-2 nodes are classification targets |
| Tier-1: 5 nodes (target), Tier-2: 3 per parent (target, max 4) | LLM constrained via prompt; hard ceiling enforced by validation |
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
| Confidence is ordinal, not probabilistic | high/medium/low; never a percentage |
| Consistency | Same inputs → same output on every run |
| Explainability | Every exposure entry shows its evidence and rationale |
