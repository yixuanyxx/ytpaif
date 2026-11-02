Source discovery runs through a local SQLite queue and a detached supervisor.
Starting `python app.py` starts the supervisor automatically. It also starts when
a search is submitted. Closing the browser or restarting app.py leaves it running.
The operating system releases its singleton lock if it crashes; the next app
startup starts a replacement that recovers queued/running jobs from disk.

The default database is backend/data/source-jobs.sqlite3 (ignored by git).
SOURCE_JOB_DB can select another absolute path on persistent local disk. Use a
separate database per environment and retain it across releases. This is a
single-host queue; it does not coordinate workers across multiple servers.

There is no overall job time limit. Existing queue databases are migrated to
remove the former deadline column while preserving jobs. One job runs at a time.
Recovery resumes saved progress without expiring the job because of elapsed time.
Individual Gemini and link-validation requests still have network timeouts (90 and 20
seconds respectively), so a failed request can be handled independently.

Four company searches and eight link-validation tasks can run concurrently.
The first pass uses gemini-3.5-flash-lite; follow-ups for missing coverage use
gemini-3.8-flash. Both current models use the same GEMINI_API_KEY (or LLM_API_KEY fallback), require
Google Search grounding and accept only citation-backed URLs. Each provider response records its
non-sensitive diagnostics: search-call count, citation count, candidate count and response step types.
Uncited or ungrounded suggestions are never saved. General AI
tasks keep LLM_MODEL=gemini-3.8-flash without the search tool; verification keeps
CHECKER_LLM_MODEL=claude-sonnet-5 using its separate Claude API credential, with no
silent fallback to another model. New API keys per Gemini model are not required.
Available Gemini text models require a valid project credential and the required Google Search
access. Code cannot grant quota, enable billing or substitute ungrounded/invented URLs.

AI discovery stores direct online document URLs and metadata only. It does not
download full documents, upload files to Storage, extract text or generate passages.
Existing stored files and manual uploads are left intact. URLs are checked using
HEAD and, for HTML articles/viewers, at most 128 KiB of transient page metadata.
Redirect targets must remain public HTTPS and document-specific. Obvious homepages,
IR indexes, search/quote/login pages and unverifiable pages are rejected. A PDF is
checked by content type without reading its bytes. HTML must match the cited title
and identify a specific article, report viewer or relevant company content page.
These checks reduce landing-page results; they cannot guarantee future availability.
Resolved document URLs, validation status and timestamps are kept in metadata.
No Supabase schema or bucket policy changes are required.

Source records are loaded once per round, cached in memory, and new metadata is
upserted in batches of up to 20 (or after about five seconds while work is running).
Progress is checkpointed locally on each batch and at phase boundaries; remote
search-run progress is mirrored at most once per ten seconds, plus final status.
Each verified link's pending metadata is journaled locally until the bulk upsert
and progress checkpoint succeed. Deterministic source IDs permit safe replay.

Each search snapshots the selected source types and derives all included, matched,
non-cash stocks from the selected portfolio on the backend. Empty or invalid type
selections and unmatched holdings are rejected before queueing. The preferred-site
pass covers all selected types for every stock, even if saved coverage already
meets the target. All first-pass searches, link checks and metadata commits must
finish before a successful completion or gap-only follow-up is allowed. All
qualifying first-pass results are saved, without capping at two. Once that barrier
is reached, broader passes only target stock/type pairs with fewer than two
qualifying documents. The first-pass completion flag survives restarts.
Verified qualifying document links count toward the target. Distinct URLs count
separately, including different websites hosting the same filing; repeated records
or URL fragments do not. Uploaded files without a verified direct document URL do
not satisfy a URL-only search. Documents must match the chosen period (unless latest mode) and cutoff.

Only the selected categories are searched and saved. Provider responses containing
other categories are discarded. Preferred websites are Yahoo Finance, Reuters,
Bloomberg, Capital IQ, and official filings/IR pages; additional public sites are
searched once for remaining gaps. The preferred-site ordering is an AI instruction,
not separate authenticated integrations with those sites.

Successful completion requires at least two distinct qualifying documents per
selected category for every included portfolio stock. If gaps remain after the
preferred and broader-public-web phases, the job finishes as `incomplete` and
preserves the gaps for a later user-initiated retry. It never reports incomplete
coverage as successful or spins forever on uncited/no-result responses. The
original source URLs are retained.

Checkpoints are committed locally before mirroring to Supabase. The local queue
is authoritative for status on this installation when Supabase is unavailable.
Verified links are deduplicated by company/category/URL on recovery. An interrupted
metadata write may have reached Supabase before its response was received; stable
IDs allow safe replay without creating duplicate source records.

Provider quota/authentication/configuration errors stop a search with an actionable
message. Temporary provider failures stop after three consecutive attempts.
Account quota or billing must be resolved with the provider before retrying.
