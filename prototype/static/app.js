'use strict';

/* ====================================================
   Theme Lab v1 — Frontend Prototype
   All views in one file for easy serving.
   ==================================================== */

// ── State ──────────────────────────────────────────
let state = null;
let currentView = 'overview';

// ── Helpers ────────────────────────────────────────
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c =>
  ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
const pct = (v, signed = false, dec = 1) =>
  v === null || v === undefined ? '—' :
  `${signed && v > 0 ? '+' : ''}${(v * 100).toFixed(dec)}%`;
const pctRange = e => `${pct(e.lower)} – ${pct(e.upper)}`;

function badge(text, cls = 'neutral') {
  return `<span class="badge badge-${cls}">${esc(text)}</span>`;
}
function statusDot(color, label) {
  return `<span class="status-indicator"><span class="status-dot-sm dot-${color}"></span>${esc(label)}</span>`;
}
function reviewStatusBadge(s) {
  if (s === 'approved')    return badge('Approved', 'green');
  if (s === 'rejected')    return badge('Rejected', 'red');
  if (s === 'needs_review')return badge('Awaiting review', 'amber');
  return badge(s, 'neutral');
}

// ── API ────────────────────────────────────────────
async function api(path, data) {
  const r = await fetch('/api/' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  const json = await r.json();
  if (!r.ok) throw new Error(json.error || 'Request failed');
  return json;
}

async function apiGet(path) {
  const r = await fetch('/api/' + path);
  if (!r.ok) throw new Error('Fetch failed');
  return r.json();
}

// ── Loading / Notice ──────────────────────────────
function showLoading(msg = 'Working…') {
  $('#loading-text').textContent = msg;
  $('#loading-overlay').hidden = false;
}
function hideLoading() { $('#loading-overlay').hidden = true; }

function notice(msg, type = 'success') {
  const icons = { success: '✓', warning: '⚠', error: '✕', info: 'ℹ' };
  $('#notice-area').innerHTML = `
    <div class="notice ${type}">
      <span class="notice-icon">${icons[type]}</span>
      <div>${esc(msg)}</div>
    </div>`;
  if (type === 'success') setTimeout(() => { $('#notice-area').innerHTML = ''; }, 5000);
}
function clearNotice() { $('#notice-area').innerHTML = ''; }

// ── Task runner ────────────────────────────────────
async function run(fn, loadingMsg) {
  showLoading(loadingMsg);
  clearNotice();
  try {
    await fn();
  } catch (e) {
    notice(e.message, 'error');
  } finally {
    hideLoading();
  }
}

// ── State refresh ──────────────────────────────────
async function refresh() {
  state = await apiGet('state');
  updateSidebarBadges();
  updateAiStatus();
}

function updateSidebarBadges() {
  if (!state) return;
  const pending = (state.cases || []).filter(c => c.review_status === 'needs_review').length;
  const missingDocs = (state.companies || []).filter(c =>
    !(state.sources || []).some(s => s.company_id === c.id)
  ).length;
  const rb = $('#badge-review');
  const sb = $('#badge-sources');
  if (rb) { rb.textContent = pending; rb.hidden = pending === 0; }
  if (sb) { sb.textContent = missingDocs; sb.hidden = missingDocs === 0; }
}

function updateAiStatus() {
  const el = $('#ai-status-label');
  if (!el || !state) return;
  el.textContent = state.ai_configured
    ? `AI: ${state.ai_model} (${state.calls_used}/${state.call_cap} calls)`
    : 'AI: not configured';
}

// ── Navigation ─────────────────────────────────────
function setView(view) {
  currentView = view;
  $$('.nav-step').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  render();
}

document.addEventListener('DOMContentLoaded', async () => {
  await run(async () => {
    await refresh();
    render();
  }, 'Loading workspace…');

  // Sidebar nav
  $$('.nav-step').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));

  // Global delegation for data-view buttons inside content
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-goto]');
    if (el) { e.preventDefault(); setView(el.dataset.goto); }
  });
});

// ── Router ─────────────────────────────────────────
async function render() {
  const views = {
    overview:  viewOverview,
    theme:     viewTheme,
    portfolio: viewPortfolio,
    sources:   viewSources,
    analysis:  viewAnalysis,
    review:    viewReview,
    dashboard: viewDashboard,
    evaluate:  viewEvaluate,
  };
  const fn = views[currentView] || viewOverview;
  await fn();
}

// ══════════════════════════════════════════════════════
// VIEW: OVERVIEW
// ══════════════════════════════════════════════════════
async function viewOverview() {
  setPageHeader('THEMATIC EXPOSURE ENGINE', 'Overview', 'End-to-end workflow status');
  $('#page-actions').innerHTML = `
    <button class="btn btn-primary" id="btn-demo">Load demo data</button>
    <button class="btn" id="btn-export">↓ Export analysis</button>`;

  if (!state) { $('#page-content').innerHTML = '<p>Loading…</p>'; return; }

  const theme    = (state.themes || []).find(t => t.approved);
  const snapshot = (state.snapshots || []).at(-1);
  const cases    = state.cases || [];
  const approved = cases.filter(c => c.review_status === 'approved');
  const pending  = cases.filter(c => c.review_status === 'needs_review');
  const companies = state.companies || [];
  const sources   = state.sources || [];

  // Workflow status tiles
  const steps = [
    { label: 'Theme defined',     done: !!theme,             goto: 'theme',     hint: theme ? theme.theme_name : 'Not yet defined' },
    { label: 'Portfolio imported', done: companies.length>0,  goto: 'portfolio', hint: `${companies.length} companies` },
    { label: 'Sources collected', done: sources.length>0,     goto: 'sources',   hint: `${sources.length} documents` },
    { label: 'Cases analysed',    done: cases.length>0,       goto: 'analysis',  hint: `${cases.length} total` },
    { label: 'Cases approved',    done: approved.length>0,    goto: 'review',    hint: `${approved.length} approved, ${pending.length} pending` },
    { label: 'Dashboard ready',   done: !!snapshot,           goto: 'dashboard', hint: snapshot ? snapshot.name : 'No snapshot yet' },
  ];

  const stepsHtml = steps.map((s, i) => `
    <div class="card" style="padding:18px 20px;cursor:pointer" data-goto="${s.goto}">
      <div class="flex items-center gap-12">
        <div style="width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;
          background:${s.done ? 'var(--teal-100)' : 'var(--slate-100)'};
          color:${s.done ? 'var(--teal-700)' : 'var(--slate-400)'};font-weight:700;font-size:13px;flex-shrink:0">
          ${s.done ? '✓' : i+1}
        </div>
        <div class="flex-1">
          <div style="font-weight:600;font-size:13px">${esc(s.label)}</div>
          <div class="text-muted text-small mt-4">${esc(s.hint)}</div>
        </div>
        <div style="font-size:18px;color:var(--slate-200)">›</div>
      </div>
    </div>`).join('');

  // Dashboard snapshot if available
  let dashHtml = '';
  if (theme && snapshot) {
    try {
      const d = await api('dashboard', { theme_id: theme.id, snapshot_id: snapshot.id });
      if (d) {
        const s = d.summary;
        dashHtml = `
          <div class="metric-grid" style="grid-template-columns:repeat(3,1fr)">
            ${metricTile('Portfolio exposure', pct(s.portfolio.point), pctRange(s.portfolio), '')}
            ${metricTile('Benchmark exposure', pct(s.benchmark.point), pctRange(s.benchmark), '')}
            ${metricTile('Active exposure', pct(s.active.point, true), pctRange(s.active), 'accent')}
          </div>
          <div class="panel">
            <div class="panel-title">Top active contributors</div>
            <div class="panel-subtitle">Sorted by absolute active contribution</div>
            ${waterfallChart(d.companies.slice(0,7), state)}
          </div>`;
      }
    } catch (_) {}
  }

  $('#page-content').innerHTML = `
    <div class="grid-2" style="align-items:start">
      <div>
        <div class="panel-title mb-12">Workflow progress</div>
        <div style="display:flex;flex-direction:column;gap:10px">${stepsHtml}</div>
        ${!theme ? `
          <div class="notice info mt-16">
            <span class="notice-icon">ℹ</span>
            <div>Start by defining your investment theme and approving a taxonomy.
              <a href="#" data-goto="theme" style="margin-left:8px;font-weight:600">Define theme →</a>
            </div>
          </div>` : ''}
      </div>
      <div>
        ${dashHtml || `
          <div class="empty-state" style="background:white;border:1px solid var(--slate-100);border-radius:var(--radius-lg)">
            <div class="empty-icon">◫</div>
            <h3>No dashboard yet</h3>
            <p>Complete the workflow steps on the left to see portfolio thematic exposure here.</p>
          </div>`}
      </div>
    </div>`;

  $('#btn-demo').onclick = () => run(async () => {
    await api('demo', {});
    await refresh();
    notice('Demo data loaded. Explore the workflow with synthetic AI Infrastructure data.');
    render();
  }, 'Loading demo…');

  $('#btn-export').onclick = () => run(async () => {
    if (!theme) throw new Error('Approve a theme before exporting');
    const r = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme_id: theme.id, snapshot_id: snapshot?.id || null })
    });
    if (!r.ok) throw new Error((await r.json()).error);
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'theme-analysis.zip';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    notice('Export downloaded.');
  }, 'Exporting…');
}

function metricTile(label, value, meta, accent) {
  return `<div class="metric-tile ${accent}">
    <div class="label">${esc(label)}</div>
    <div class="value">${esc(value)}</div>
    <div class="meta">${esc(meta)}</div>
  </div>`;
}

function waterfallChart(companies, state) {
  const pts = companies.map(r => Math.abs(r.contributions?.active?.point ?? 0));
  const max = Math.max(...pts, 0.001);
  return `<div class="waterfall">
    ${companies.map(r => {
      const v = r.contributions?.active?.point ?? null;
      if (v === null) return '';
      const w = Math.abs(v) / max * 47;
      const name = (state.companies || []).find(c => c.id === r.company_id)?.name || r.company_id;
      return `<div class="wf-row">
        <div class="wf-label" title="${esc(name)}">${esc(name.length > 22 ? name.slice(0,20)+'…' : name)}</div>
        <div class="wf-track">
          <div class="wf-bar ${v >= 0 ? 'pos' : 'neg'}"
            style="left:${v>=0?50:50-w}%;width:${w}%"></div>
        </div>
        <div class="wf-value ${v>=0?'pos':'neg'}">${pct(v, true)}</div>
      </div>`;
    }).join('')}
  </div>`;
}

// ══════════════════════════════════════════════════════
// VIEW: THEME DEFINITION  (YTP-8, YTP-14, YTP-15)
// ══════════════════════════════════════════════════════
async function viewTheme() {
  const theme = (state.themes || []).filter(t => t.approved).at(-1);
  setPageHeader('STEP 1', 'Theme Definition',
    'Define the investment theme and approve a taxonomy');
  $('#page-actions').innerHTML = theme
    ? `<button class="btn btn-sm" id="btn-new-version">Create new version</button>`
    : '';

  const themeFormHtml = `
    <form id="theme-form">
      <div class="form-grid">
        <div class="form-group span-2">
          <label class="field-label">Theme name <span class="required">*</span></label>
          <input type="text" name="theme_name" placeholder="e.g. AI Infrastructure"
            value="${esc(theme?.theme_name || '')}" required>
        </div>
        <div class="form-group span-2">
          <label class="field-label">Theme description
            <span class="optional">(recommended)</span></label>
          <textarea name="theme_description" rows="3"
            placeholder="Describe what this theme captures and its economic significance…">${esc(theme?.theme_description || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Included activities
            <span class="optional">(recommended)</span></label>
          <textarea name="included_activities" rows="3"
            placeholder="Activities and revenue streams that qualify for this theme…">${esc(theme?.included_activities || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Excluded activities
            <span class="optional">(optional)</span></label>
          <textarea name="excluded_activities" rows="3"
            placeholder="Explicitly out-of-scope activities…">${esc(theme?.excluded_activities || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Positive examples
            <span class="optional">(recommended)</span></label>
          <textarea name="positive_examples" rows="2"
            placeholder="e.g. NVIDIA GPU revenue qualifies…">${esc(theme?.positive_examples || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Negative examples
            <span class="optional">(optional)</span></label>
          <textarea name="negative_examples" rows="2"
            placeholder="e.g. AWS general cloud revenue does not qualify…">${esc(theme?.negative_examples || '')}</textarea>
        </div>
        <div class="form-group span-2">
          <label class="field-label">Additional information
            <span class="optional">(optional)</span></label>
          <input type="text" name="additional_information"
            value="${esc(theme?.additional_information || '')}"
            placeholder="Any other context for analysts…">
        </div>
      </div>
      <div class="card-footer" style="border-radius:var(--radius-lg);margin-top:20px">
        <div class="flex-1 text-muted text-small">
          Saving creates a new approved version. Existing analyses are unaffected.
        </div>
        <button type="button" class="btn" id="btn-gen-taxonomy" ${!state?.ai_configured ? 'disabled title="Configure LLM_API_KEY to enable"' : ''}>
          ✧ Generate taxonomy with AI
        </button>
        <button type="submit" class="btn btn-primary">Save &amp; approve theme</button>
      </div>
    </form>`;

  // Taxonomy panel
  const nodes = theme?.nodes || [];
  const tier1 = nodes.filter(n => !n.parent_id);
  const taxonomyHtml = buildTaxonomyEditor(nodes, tier1, !!theme);

  $('#page-content').innerHTML = `
    <div class="grid-1-2" style="align-items:start">
      <div>
        <div class="panel">
          <div class="panel-title">Theme scope</div>
          <div class="panel-subtitle">
            Only <code>theme_name</code> is required. The more detail you provide, the better the AI analysis.
          </div>
          ${themeFormHtml}
        </div>

        ${theme ? `
        <div class="panel">
          <div class="panel-title">Version history</div>
          <div class="panel-subtitle">Previous approved definitions are retained unchanged.</div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Version</th><th>Name</th><th>Created</th></tr></thead>
              <tbody>
                ${(state.themes || []).filter(t=>t.approved).reverse().map(t => `
                  <tr><td>${badge('v'+t.version,'neutral')}</td>
                  <td>${esc(t.theme_name)}</td>
                  <td class="text-muted text-small">${esc(t.created_at?.slice(0,10)||'')}</td></tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>` : ''}
      </div>

      <div>
        <div class="panel">
          <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center">
            <span>Sub-theme taxonomy</span>
            <span class="text-muted text-small">${tier1.length} Tier-1 · ${nodes.length - tier1.length} Tier-2 leaves</span>
          </div>
          <div class="panel-subtitle">
            Two levels: Theme → Tier-1 (broad area) → Tier-2 (specific activity, classification target).
            Target: 5 Tier-1 nodes, 3 Tier-2 per parent.
          </div>
          ${taxonomyHtml}
          ${!theme && nodes.length === 0 ? `
            <div class="empty-state" style="padding:40px 16px">
              <div class="empty-icon">◇</div>
              <h3>No taxonomy yet</h3>
              <p>Fill in the theme scope and click "Generate taxonomy with AI", or add nodes manually.</p>
            </div>` : ''}
        </div>
      </div>
    </div>`;

  // Form submit
  $('#theme-form').onsubmit = e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    const previous = (state.themes || []).filter(t => t.approved).at(-1);
    const nodesRaw = $('#taxonomy-json-area')?.value;
    let nodes = [];
    if (nodesRaw) {
      try { nodes = JSON.parse(nodesRaw); }
      catch { notice('Taxonomy JSON is invalid — check the structure', 'error'); return; }
    }
    run(async () => {
      const result = await api('theme', {
        ...fd,
        nodes,
        previous_id: previous?.id || null,
      });
      await refresh();
      notice(`Theme "${result.theme_name}" saved as version ${result.version}.`);
      render();
    }, 'Saving theme…');
  };

  // Generate taxonomy
  $('#btn-gen-taxonomy')?.addEventListener('click', () => {
    const fd = Object.fromEntries(new FormData($('#theme-form')));
    run(async () => {
      const result = await api('generate', { ...fd, tier1_count: 5, tier2_count: 3 });
      const ta = $('#taxonomy-json-area');
      if (ta) ta.value = JSON.stringify(result.nodes, null, 2);
      notice('AI taxonomy proposed. Review every node before approving.', 'info');
    }, 'Generating taxonomy with AI…');
  });

  $('#btn-new-version')?.addEventListener('click', () => {
    notice('Edit the theme fields and save to create a new version.', 'info');
  });
}

function buildTaxonomyEditor(nodes, tier1Nodes, hasApproved) {
  if (nodes.length === 0 && !hasApproved) {
    // Show JSON textarea for manual entry / AI result
    return `
      <div class="form-group">
        <label class="field-label">Taxonomy JSON
          <span class="optional">— edit directly or use AI generation above</span></label>
        <textarea class="code" id="taxonomy-json-area" name="nodes" rows="14"
          placeholder='[\n  {\n    "node_id": "compute",\n    "parent_id": null,\n    "name": "Compute Hardware",\n    "definition": "Dedicated AI chips and systems",\n    "examples": ["AI accelerators"]\n  }\n]'></textarea>
        <div class="field-hint">
          Tier-1 nodes have <code>parent_id: null</code>.
          Tier-2 nodes set <code>parent_id</code> to a Tier-1 <code>node_id</code>.
          Only Tier-2 leaf nodes are used as classification targets.
        </div>
      </div>`;
  }

  // Visual tree
  const tier1Html = tier1Nodes.map(t1 => {
    const children = nodes.filter(n => n.parent_id === t1.node_id);
    return `
      <div class="tier1-node">
        <div class="tier1-header" onclick="this.classList.toggle('open');this.nextElementSibling.style.display=this.classList.contains('open')?'':'none'">
          <span class="tier1-chevron">▶</span>
          <span class="node-name">${esc(t1.name)}</span>
          <span class="node-count">${children.length} sub-themes</span>
        </div>
        <div class="tier2-list" style="display:none">
          ${children.map(t2 => `
            <div class="tier2-node">
              <div class="tier2-bullet"></div>
              <div>
                <div class="node-name">${esc(t2.name)}</div>
                <div class="node-def">${esc(t2.definition || '')}</div>
                <div class="text-mono text-muted mt-4">${esc(t2.node_id)}</div>
              </div>
            </div>`).join('')}
          ${children.length === 0 ? `<div class="text-muted text-small" style="padding:8px">No Tier-2 nodes yet — at least one required before approval.</div>` : ''}
        </div>
      </div>`;
  }).join('');

  // Also expose JSON textarea below tree for editing
  const jsonStr = JSON.stringify(nodes, null, 2);
  return `
    <div class="taxonomy-tree">${tier1Html}</div>
    <div class="form-section">
      <div class="form-section-title">Edit taxonomy JSON</div>
      <textarea class="code" id="taxonomy-json-area" rows="10">${esc(jsonStr)}</textarea>
      <div class="field-hint">Edit JSON directly or use "Generate taxonomy with AI". Changes take effect when you save the theme.</div>
    </div>`;
}

// ══════════════════════════════════════════════════════
// VIEW: PORTFOLIO IMPORT  (YTP-16, YTP-23)
// ══════════════════════════════════════════════════════

// Module-level staging area — survives re-renders within the view
let _stagingRows = null;   // parsed + edited rows before confirmation
let _stagingFile = null;   // file name for display

async function viewPortfolio() {
  setPageHeader('STEP 2', 'Portfolio Import',
    'Upload holdings and match companies');

  const companies = state.companies || [];

  // ── Two-phase layout: upload zone OR editable table ──────────────────────
  // If we already have staged rows (from a previous parse or state.portfolio),
  // go straight to the edit table. Otherwise show the upload zone.
  const hasStaged  = _stagingRows && _stagingRows.length > 0;
  const hasImported = companies.length > 0;

  $('#page-actions').innerHTML = hasImported
    ? `<button class="btn btn-sm" id="btn-reimport">↑ Replace with new file</button>`
    : '';

  if (!hasStaged && !hasImported) {
    renderUploadZone();
  } else if (hasStaged) {
    renderEditTable(_stagingRows);
  } else {
    // Already imported — show the company table + allow adding manually
    renderImportedView(companies);
  }
}

// ─── Phase 1: Upload drop zone ────────────────────────────────────────────────
function renderUploadZone() {
  $('#page-content').innerHTML = `
    <div class="grid-2" style="align-items:start">
      <div class="panel">
        <div class="panel-title">Import portfolio Excel</div>
        <div class="panel-subtitle">
          Upload an Excel file in the Schroders sample format.<br>
          Required columns: <strong>Company</strong>, <strong>Bloomberg ticker</strong> or <strong>SEDOL</strong>,
          <strong>Portfolio wt</strong>, <strong>Reference wt</strong>.
          Optional: Country, Sector, Active wt.
        </div>

        <div class="drop-zone" id="excel-drop" style="margin-bottom:20px">
          <input type="file" id="excel-file" accept=".xlsx,.xls,.csv">
          <div class="upload-icon">📊</div>
          <h3>Drop Excel file here</h3>
          <p style="margin-bottom:14px">
            Or click to browse · .xlsx / .xls / .csv<br>
            <span class="text-mono" style="font-size:11px">
              Company | Bloomberg ticker | Country | Sector | SEDOL | Portfolio wt | Reference wt | Active wt
            </span>
          </p>
          <button class="btn btn-primary"
            id="btn-browse-excel">
            Browse file
          </button>
        </div>

        <div class="form-grid">
          <div class="form-group">
            <label class="field-label">Portfolio name <span class="required">*</span></label>
            <input type="text" id="import-name" placeholder="e.g. MSCI Asia ex Japan — Oct 2026" required>
          </div>
          <div class="form-group">
            <label class="field-label">Holdings date <span class="optional">(optional)</span></label>
            <input type="date" id="import-date">
          </div>
          <div class="form-group span-2">
            <label class="field-label">Benchmark name <span class="optional">(optional)</span></label>
            <input type="text" id="import-benchmark" value="MSCI Asia ex Japan">
          </div>
        </div>

        <div id="parse-error" hidden></div>

        <div class="btn-group mt-16">
          <button class="btn btn-primary" id="btn-parse" disabled>Load &amp; preview data</button>
          <span class="text-muted text-small" id="import-file-name">No file selected</span>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">Expected format</div>
        <div class="panel-subtitle">Matches the Schroders sample Excel template.</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Column</th><th>Required</th><th>Notes</th></tr></thead>
            <tbody>
              <tr><td class="text-mono">Company</td>            <td>${badge('Required','green')}</td>  <td class="text-muted text-small">Legal company name</td></tr>
              <tr><td class="text-mono">Bloomberg ticker</td>   <td>${badge('One of two','amber')}</td> <td class="text-muted text-small">e.g. 2330 TT Equity</td></tr>
              <tr><td class="text-mono">SEDOL</td>              <td>${badge('One of two','amber')}</td> <td class="text-muted text-small">7-char identifier</td></tr>
              <tr><td class="text-mono">Portfolio wt</td>       <td>${badge('Required','green')}</td>  <td class="text-muted text-small">Decimal or % (e.g. 0.13 or 13%)</td></tr>
              <tr><td class="text-mono">Reference wt</td>       <td>${badge('Required','green')}</td>  <td class="text-muted text-small">Reference/benchmark weight</td></tr>
              <tr><td class="text-mono">Active wt</td>          <td>${badge('Optional','neutral')}</td><td class="text-muted text-small">Validated vs Portfolio−Reference</td></tr>
              <tr><td class="text-mono">Country</td>            <td>${badge('Optional','neutral')}</td><td class="text-muted text-small">Retained for display</td></tr>
              <tr><td class="text-mono">Sector</td>             <td>${badge('Optional','neutral')}</td><td class="text-muted text-small">Retained for display</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>`;

  const dropZone = $('#excel-drop');
  const fileInput = $('#excel-file');
  let currentFile = null;

  function onFileSelected(file) {
    if (!file) return;
    currentFile = file;
    _stagingFile = file.name;
    $('#import-file-name').textContent = file.name;
    $('#btn-parse').disabled = false;
    if (!$('#import-name').value.trim()) $('#import-name').value = file.name.replace(/\.[^.]+$/, '');
    $('#parse-error').hidden = true;
  }

  fileInput.onchange   = () => onFileSelected(fileInput.files[0]);
  dropZone.onclick = e => { if (e.target !== fileInput) fileInput.click(); };
  $('#btn-browse-excel').onclick = e => { e.stopPropagation(); fileInput.click(); };
  dropZone.ondragover  = e => { e.preventDefault(); dropZone.classList.add('drag-over'); };
  dropZone.ondragleave = () => dropZone.classList.remove('drag-over');
  dropZone.ondrop      = e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    onFileSelected(e.dataTransfer.files[0]);
  };

  $('#btn-parse').onclick = async () => {
    if (!currentFile) return;
    const importName = $('#import-name').value.trim();
    if (!importName) {
      const el = $('#parse-error');
      el.hidden = false;
      el.innerHTML = `<div class="notice error"><span class="notice-icon">✕</span>Portfolio name is required.</div>`;
      return;
    }
    showLoading('Parsing Excel file…');
    try {
      const rows = await parseExcelFile(currentFile);
      // Attach metadata
      rows._importName      = importName;
      rows._importDate      = $('#import-date').value;
      rows._benchmarkName   = $('#import-benchmark').value || 'MSCI Asia ex Japan';
      rows._fileName        = currentFile.name;
      _stagingRows = rows;
      renderEditTable(rows);
    } catch (err) {
      const el = $('#parse-error');
      el.hidden = false;
      el.innerHTML = `<div class="notice error"><span class="notice-icon">✕</span>${esc(err.message)}</div>`;
    } finally {
      hideLoading();
    }
  };
}

// ─── Excel parsing (SheetJS) ──────────────────────────────────────────────────
async function parseExcelFile(file) {
  const buf = await file.arrayBuffer();
  // SheetJS is served locally under the application's same-origin policy.
  if (typeof XLSX === 'undefined') throw new Error('Excel parser could not load. Reload the page and try again.');
  const wb  = XLSX.read(buf, { type: 'array', cellDates: true });
  const ws  = wb.Sheets[wb.SheetNames[0]];
  // Use raw: false to get formatted values for strings; raw: true for numbers
  const raw = XLSX.utils.sheet_to_json(ws, { header: 1, defval: null });
  if (!raw || raw.length < 2) throw new Error('File appears to be empty.');

  const headerRow = raw[0].map(h => String(h ?? '').trim().toLowerCase());

  // Column index map — case-insensitive, normalise spaces/special chars
  function col(name) {
    const idx = headerRow.findIndex(h => h.includes(name.toLowerCase()));
    return idx;
  }

  const CI = {
    company:       col('company'),
    ticker:        col('bloomberg'),
    country:       col('country'),
    sector:        col('sector'),
    sedol:         col('sedol'),
    portfolio_wt:  col('portfolio'),
    reference_wt:  col('reference'),
    active_wt:     col('active'),
  };

  if (CI.company < 0)      throw new Error('Missing required column: Company');
  if (CI.portfolio_wt < 0) throw new Error('Missing required column: Portfolio wt');
  if (CI.reference_wt < 0) throw new Error('Missing required column: Reference wt');
  if (CI.ticker < 0 && CI.sedol < 0)
    throw new Error('Missing identifier columns — need Bloomberg ticker or SEDOL');

  function parseWeight(val) {
    if (val === null || val === undefined || val === '') return null;
    const n = typeof val === 'number' ? val : parseFloat(String(val).replace('%','').trim());
    if (isNaN(n)) return null;
    // If stored as % integer (e.g. 13.2 meaning 13.2%), convert to decimal
    // SheetJS returns percentage-formatted cells as decimals (0.132) when raw:false is not used
    // But to be safe: if abs value > 1.5 treat as percentage value, divide by 100
    return String(val).includes('%') || Math.abs(n) > 1.5 ? n / 100 : n;
  }

  const rows = [];
  for (let i = 1; i < raw.length; i++) {
    const r = raw[i];
    const name = String(r[CI.company] ?? '').trim();
    if (!name) continue; // skip blank rows

    const ticker   = CI.ticker  >= 0 ? String(r[CI.ticker]  ?? '').trim() : '';
    const sedol    = CI.sedol   >= 0 ? String(r[CI.sedol]   ?? '').trim() : '';
    const country  = CI.country >= 0 ? String(r[CI.country] ?? '').trim() : '';
    const sector   = CI.sector  >= 0 ? String(r[CI.sector]  ?? '').trim() : '';

    const portfolio_wt = parseWeight(r[CI.portfolio_wt]);
    const reference_wt = parseWeight(r[CI.reference_wt]);
    const active_wt_raw = CI.active_wt >= 0 ? parseWeight(r[CI.active_wt]) : null;

    if (!ticker && !sedol) {
      // Flag row — no identifier
      rows.push({ name, ticker, sedol, country, sector,
        portfolio_wt: portfolio_wt ?? 0,
        reference_wt: reference_wt ?? 0,
        active_wt:    active_wt_raw,
        error: 'No security identifier (ticker or SEDOL)',
        conflict: false, excluded: false });
      continue;
    }
    if (portfolio_wt === null) {
      rows.push({ name, ticker, sedol, country, sector,
        portfolio_wt: 0, reference_wt: reference_wt ?? 0,
        active_wt: active_wt_raw,
        error: 'Missing portfolio weight',
        conflict: false, excluded: false });
      continue;
    }

    // Compute active weight; flag discrepancy if uploaded value differs
    const computed_active = (portfolio_wt ?? 0) - (reference_wt ?? 0);
    const active_discrepancy = active_wt_raw !== null
      && Math.abs(active_wt_raw - computed_active) > 0.0001;

    rows.push({
      name, ticker, sedol, country, sector,
      portfolio_wt:  portfolio_wt ?? 0,
      reference_wt:  reference_wt ?? 0,
      active_wt:     computed_active,
      active_wt_raw,
      active_discrepancy,
      error: null,
      conflict: false,
      excluded: false,
    });
  }

  if (rows.length === 0) throw new Error('No data rows found in the file.');

  // Flag conflicts against already-registered companies
  const existing = state.companies || [];
  rows.forEach(r => {
    const match = existing.find(c =>
      (r.ticker && c.bloomberg_ticker === r.ticker) ||
      (r.sedol  && c.sedol           === r.sedol)
    );
    if (match && match.name.toLowerCase() !== r.name.toLowerCase()) {
      r.conflict      = true;
      r.conflictMatch = match.name;
    }
  });

  return rows;
}

// ─── Phase 2: Editable data table ─────────────────────────────────────────────
function renderEditTable(rows) {
  const errors     = rows.filter(r => r.error);
  const conflicts  = rows.filter(r => r.conflict && !r.excluded);
  const discrepant = rows.filter(r => r.active_discrepancy && !r.excluded);
  const clean      = rows.filter(r => !r.error && !r.excluded);

  const portTotal = clean.reduce((s, r) => s + (r.portfolio_wt || 0), 0);
  const refTotal  = clean.reduce((s, r) => s + (r.reference_wt || 0), 0);
  const portOk    = Math.abs(portTotal - 1) < 0.001;

  // Status bar
  const statusItems = [
    `<strong>${rows.length}</strong> rows parsed from <em>${esc(_stagingFile || 'file')}</em>`,
    errors.length    ? `<span style="color:var(--red-600)">${errors.length} rows with errors</span>` : null,
    conflicts.length ? `<span style="color:var(--amber-600)">${conflicts.length} identity conflicts</span>` : null,
    discrepant.length? `<span style="color:var(--amber-600)">${discrepant.length} active weight discrepancies</span>` : null,
    rows.filter(r => r.excluded).length
      ? `<span style="color:var(--slate-400)">${rows.filter(r=>r.excluded).length} excluded</span>` : null,
  ].filter(Boolean).join(' · ');

  $('#page-content').innerHTML = `
    <!-- Sticky action bar -->
    <div style="position:sticky;top:0;z-index:40;background:white;border:1px solid var(--slate-100);
      border-radius:var(--radius-lg);padding:14px 20px;margin-bottom:20px;
      display:flex;align-items:center;gap:16px;box-shadow:var(--shadow-sm)">
      <div class="flex-1">
        <div style="font-size:13px">${statusItems}</div>
        <div style="margin-top:6px;display:flex;gap:20px;font-size:12px">
          <span>Portfolio total:
            <strong id="portfolio-total" style="color:${portOk?'var(--teal-500)':'var(--red-600)'}">${(portTotal*100).toFixed(2)}%</strong>
            ${portOk ? '✓' : `<span style="color:var(--red-600)">— should be 100%</span>`}
          </span>
          <span>Reference total:
            <strong id="reference-total">${(refTotal*100).toFixed(2)}%</strong>
            <span class="text-muted">(need not total 100%)</span>
          </span>
        </div>
      </div>
      <div class="btn-group">
        <button class="btn btn-sm" id="btn-back-upload">← Change file</button>
        <button class="btn btn-primary btn-lg" id="btn-confirm-import"
          ${errors.length || conflicts.length ? 'disabled title="Resolve errors and conflicts first"' : ''}>
          Confirm &amp; import ${clean.length} holdings
        </button>
      </div>
    </div>

    <!-- Notice for errors/conflicts -->
    ${errors.length ? `
      <div class="notice error mb-16">
        <span class="notice-icon">✕</span>
        <div><strong>${errors.length} rows need attention</strong> — missing identifier or weight.
        Rows highlighted in red below. You can edit values inline or exclude the row.</div>
      </div>` : ''}
    ${conflicts.length ? `
      <div class="notice warning mb-16">
        <span class="notice-icon">⚠</span>
        <div><strong>${conflicts.length} identity conflicts</strong> — a ticker or SEDOL matches an
        existing company with a different name. Resolve each conflict below before importing.</div>
      </div>` : ''}

    <!-- Editable holdings table -->
    <div class="panel" style="padding:0;overflow:hidden">
      <div style="padding:16px 20px;border-bottom:1px solid var(--slate-100);display:flex;align-items:center;gap:12px">
        <div class="panel-title" style="margin:0">Holdings</div>
        <div class="text-muted text-small flex-1">
          Click any cell to edit · Changes update totals live · Excluded rows are greyed out
        </div>
        <input type="text" id="row-search" placeholder="Search companies…"
          style="width:220px;font-size:12px;padding:7px 10px">
      </div>
      <div class="table-wrap" style="max-height:620px;overflow-y:auto">
        <table id="holdings-table">
          <thead>
            <tr>
              <th style="width:32px"></th>
              <th>Company</th>
              <th>Bloomberg ticker</th>
              <th>SEDOL</th>
              <th>Country</th>
              <th>Sector</th>
              <th class="td-num">Portfolio wt</th>
              <th class="td-num">Reference wt</th>
              <th class="td-num">Active wt</th>
              <th style="width:80px"></th>
            </tr>
          </thead>
          <tbody id="holdings-tbody">
            ${rows.map((r, i) => holdingRow(r, i)).join('')}
          </tbody>
        </table>
      </div>
      <div style="padding:12px 20px;border-top:1px solid var(--slate-50);background:var(--slate-50);
        display:flex;align-items:center;justify-content:space-between;font-size:12px">
        <span class="text-muted">${rows.length} rows · ${rows.filter(r=>r.excluded).length} excluded</span>
        <button class="btn btn-sm" id="btn-add-row">+ Add row</button>
      </div>
    </div>`;

  bindEditTable(rows);
}

function holdingRow(r, i) {
  const isError    = !!r.error;
  const isConflict = r.conflict && !r.excluded;
  const rowBg = r.excluded ? 'background:#f8fafb;opacity:.55;' :
                isError    ? 'background:#fff8f8;' :
                isConflict ? 'background:var(--amber-50);' : '';

  const activeColor = r.active_wt > 0.0001  ? 'color:var(--teal-500)' :
                      r.active_wt < -0.0001 ? 'color:#af6465' : '';

  return `<tr data-idx="${i}" style="${rowBg}">
    <td style="padding:8px 10px;text-align:center;color:var(--slate-300)">${i+1}</td>
    <td style="min-width:180px">
      <input class="cell-edit" data-field="name" data-idx="${i}"
        value="${esc(r.name)}"
        style="font-weight:600;border:none;background:transparent;padding:4px 6px;width:100%;font-size:13px">
      ${isConflict ? `<div style="font-size:11px;color:var(--amber-600);padding:2px 6px">
          ⚠ Matches existing: <em>${esc(r.conflictMatch)}</em>
          <button class="btn btn-sm" style="margin-left:6px;padding:2px 7px;font-size:10px"
            data-row-action="use_existing" data-idx="${i}">Use existing</button>
          <button class="btn btn-sm" style="margin-left:4px;padding:2px 7px;font-size:10px"
            data-row-action="new_record" data-idx="${i}">Keep new</button>
        </div>` : ''}
      ${isError ? `<div style="font-size:11px;color:var(--red-600);padding:2px 6px">✕ ${esc(r.error)}</div>` : ''}
      ${r.active_discrepancy ? `<div style="font-size:11px;color:var(--amber-600);padding:2px 6px">
          ⚠ Active wt in file (${pct(r.active_wt_raw, true)}) differs from computed (${pct(r.active_wt, true)})
        </div>` : ''}
    </td>
    <td><input class="cell-edit" data-field="ticker" data-idx="${i}"
      value="${esc(r.ticker)}" placeholder="—"
      style="border:none;background:transparent;padding:4px 6px;width:100%;font-size:12px;font-family:monospace"></td>
    <td><input class="cell-edit" data-field="sedol" data-idx="${i}"
      value="${esc(r.sedol)}" placeholder="—"
      style="border:none;background:transparent;padding:4px 6px;width:100%;font-size:12px;font-family:monospace"></td>
    <td><input class="cell-edit" data-field="country" data-idx="${i}"
      value="${esc(r.country)}" placeholder="—"
      style="border:none;background:transparent;padding:4px 6px;width:100%;font-size:12px"></td>
    <td><input class="cell-edit" data-field="sector" data-idx="${i}"
      value="${esc(r.sector)}" placeholder="—"
      style="border:none;background:transparent;padding:4px 6px;width:100%;font-size:12px"></td>
    <td style="text-align:right">
      <input class="cell-edit weight-edit" data-field="portfolio_wt" data-idx="${i}"
        value="${fmtWt(r.portfolio_wt)}"
        style="border:none;background:transparent;padding:4px 6px;width:70px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums">
    </td>
    <td style="text-align:right">
      <input class="cell-edit weight-edit" data-field="reference_wt" data-idx="${i}"
        value="${fmtWt(r.reference_wt)}"
        style="border:none;background:transparent;padding:4px 6px;width:70px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums">
    </td>
    <td style="text-align:right;font-size:12px;font-variant-numeric:tabular-nums;padding:8px 10px;${activeColor}">
      ${pct(r.active_wt, true)}
    </td>
    <td style="text-align:right;white-space:nowrap">
      ${r.excluded
        ? `<button class="btn btn-sm" style="font-size:11px" data-row-action="exclude" data-idx="${i}">Include</button>`
        : `<button class="btn btn-sm btn-ghost" style="font-size:11px;color:var(--slate-400)"
             data-row-action="exclude" data-idx="${i}">Exclude</button>`}
    </td>
  </tr>`;
}

function fmtWt(v) {
  if (v === null || v === undefined) return '';
  return (v * 100).toFixed(4).replace(/\.?0+$/, '') + '%';
}

function parseWtInput(s) {
  const clean = String(s).replace('%', '').trim();
  const n = parseFloat(clean);
  if (isNaN(n)) return null;
  return String(s).includes('%') || Math.abs(n) > 1.5 ? n / 100 : n;
}

function bindEditTable(rows) {
  // Back button
  $('#btn-back-upload').onclick = () => {
    _stagingRows = null;
    renderUploadZone();
  };

  $('#holdings-tbody').onclick = e => {
    const button = e.target.closest('[data-row-action]');
    if (!button) return;
    const idx = Number(button.dataset.idx);
    if (button.dataset.rowAction === 'exclude') window.toggleExclude(idx);
    else window.resolveConflict(idx, button.dataset.rowAction);
  };

  // Inline cell editing
  document.querySelectorAll('.cell-edit').forEach(input => {
    input.addEventListener('focus', () => {
      input.style.background = 'white';
      input.style.border = '1px solid var(--teal-400)';
      input.style.borderRadius = '3px';
      input.style.boxShadow = '0 0 0 2px rgba(26,158,142,.1)';
    });
    input.addEventListener('blur', () => {
      input.style.background = 'transparent';
      input.style.border = 'none';
      input.style.boxShadow = '';
    });
    input.addEventListener('change', () => {
      const i     = parseInt(input.dataset.idx);
      const field = input.dataset.field;
      if (field === 'portfolio_wt' || field === 'reference_wt') {
        const val = parseWtInput(input.value);
        if (val === null) { input.style.color = 'var(--red-600)'; return; }
        input.style.color = '';
        rows[i][field] = val;
        if (field === 'portfolio_wt' && rows[i].error === 'Missing portfolio weight') rows[i].error = null;
        // Recompute active weight
        rows[i].active_wt = (rows[i].portfolio_wt || 0) - (rows[i].reference_wt || 0);
        // Update active wt cell in the same row
        const tr = input.closest('tr');
        const activeCell = tr.querySelectorAll('td')[8];
        if (activeCell) {
          const v = rows[i].active_wt;
          activeCell.style.color = v > 0.0001 ? 'var(--teal-500)' : v < -0.0001 ? '#af6465' : '';
          activeCell.textContent = pct(v, true);
        }
        updateTotals(rows);
      } else {
        rows[i][field] = input.value.trim();
        // Re-check error state for identifier fields
        if (field === 'ticker' || field === 'sedol') {
          if (rows[i].error === 'No security identifier (ticker or SEDOL)' &&
              (rows[i].ticker || rows[i].sedol)) {
            rows[i].error = null;
          }
        }
      }
      refreshConfirmButton(rows);
    });
  });

  // Live search filter
  $('#row-search').addEventListener('input', function() {
    const q = this.value.toLowerCase();
    document.querySelectorAll('#holdings-tbody tr').forEach(tr => {
      const name = tr.querySelector('[data-field="name"]')?.value?.toLowerCase() || '';
      const ticker = tr.querySelector('[data-field="ticker"]')?.value?.toLowerCase() || '';
      tr.style.display = (!q || name.includes(q) || ticker.includes(q)) ? '' : 'none';
    });
  });

  // Add row
  $('#btn-add-row').onclick = () => {
    const newRow = {
      name:'', ticker:'', sedol:'', country:'', sector:'',
      portfolio_wt:0, reference_wt:0, active_wt:0,
      error:'No security identifier (ticker or SEDOL)',
      conflict:false, excluded:false
    };
    rows.push(newRow);
    renderEditTable(rows);
    $('#holdings-tbody').lastElementChild?.querySelector('input')?.focus();
  };

  // Confirm import
  $('#btn-confirm-import').onclick = () => {
    const name = rows._importName || 'Portfolio import';
    run(async () => {
      const clean = rows.filter(r => !r.excluded && !r.error && !r.conflict);
      if (clean.length === 0) throw new Error('No valid rows to import.');
      for (const r of clean) {
        await api('company', {
          name: r.name,
          bloomberg_ticker: r.ticker,
          sedol: r.sedol,
          jurisdiction: r.country
        });
      }
      await refresh();
      notice(`"${name}" imported — ${clean.length} companies registered. Holdings remain available for editing.`);
      render();
    }, 'Importing holdings…');
  };
}

// Expose conflict resolution and row toggle to inline onclick handlers
window.resolveConflict = function(idx, action) {
  if (!_stagingRows) return;
  const r = _stagingRows[idx];
  if (action === 'use_existing') {
    // Overwrite name with the existing record's name, clear conflict
    r.name     = r.conflictMatch;
    r.conflict = false;
  } else {
    r.conflict = false; // accept the new name
  }
  renderEditTable(_stagingRows);
};

window.toggleExclude = function(idx) {
  if (!_stagingRows) return;
  _stagingRows[idx].excluded = !_stagingRows[idx].excluded;
  renderEditTable(_stagingRows);
};

function bindNewRowInputs(container, rows, idx) {
  container.querySelectorAll('.cell-edit').forEach(input => {
    input.addEventListener('change', () => {
      const field = input.dataset.field;
      rows[idx][field] = field.endsWith('_wt') ? (parseWtInput(input.value) ?? 0) : input.value.trim();
      if (field === 'portfolio_wt' || field === 'reference_wt') {
        rows[idx].active_wt = (rows[idx].portfolio_wt||0) - (rows[idx].reference_wt||0);
      }
      if ((field === 'ticker' || field === 'sedol') && (rows[idx].ticker || rows[idx].sedol)) {
        rows[idx].error = null;
      }
      refreshConfirmButton(rows);
      updateTotals(rows);
    });
  });
}

function updateTotals(rows) {
  const clean    = rows.filter(r => !r.excluded);
  const portTotal = clean.reduce((s,r) => s + (r.portfolio_wt||0), 0);
  const portOk    = Math.abs(portTotal - 1) < 0.001;
  const totalSpan = $('#portfolio-total');
  if (totalSpan) {
    totalSpan.textContent = (portTotal * 100).toFixed(2) + '%';
    totalSpan.style.color = portOk ? 'var(--teal-500)' : 'var(--red-600)';
  }
  const referenceSpan = $('#reference-total');
  if (referenceSpan) referenceSpan.textContent = (clean.reduce((s, r) => s + (r.reference_wt || 0), 0) * 100).toFixed(2) + '%';
}

function refreshConfirmButton(rows) {
  const btn = $('#btn-confirm-import');
  if (!btn) return;
  const hasErrors    = rows.some(r => !r.excluded && r.error);
  const hasConflicts = rows.some(r => !r.excluded && r.conflict);
  btn.disabled = hasErrors || hasConflicts;
  btn.title    = (hasErrors || hasConflicts) ? 'Resolve errors and conflicts first' : '';
  const clean  = rows.filter(r => !r.excluded && !r.error && !r.conflict);
  btn.textContent = `Confirm & import ${clean.length} holdings`;
}

// ─── Phase 3: Already-imported view ──────────────────────────────────────────
function renderImportedView(companies) {
  const sources = state.sources || [];

  $('#page-content').innerHTML = `
    <div class="panel" style="margin-bottom:20px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
        <div>
          <div class="panel-title">Company universe</div>
          <div class="panel-subtitle">${companies.length} companies · matched by SEDOL or Bloomberg ticker</div>
        </div>
        <button class="btn btn-primary" id="btn-reimport-main">↑ Import / replace portfolio</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>#</th><th>Company</th><th>Bloomberg ticker</th><th>SEDOL</th><th>Country</th><th>Sources</th></tr>
          </thead>
          <tbody>
            ${companies.map((c, i) => {
              const srcCount = sources.filter(s=>s.company_id===c.id).length;
              return `<tr>
                <td class="text-muted text-small">${i+1}</td>
                <td class="td-company">${esc(c.name)}</td>
                <td class="text-mono">${esc(c.bloomberg_ticker||'—')}</td>
                <td class="text-mono">${esc(c.sedol||'—')}</td>
                <td>${esc(c.jurisdiction||'—')}</td>
                <td>${srcCount > 0 ? badge(srcCount+' docs','green') : badge('No docs','amber')}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Manual company add -->
    <div class="panel">
      <div class="panel-title">Add company manually</div>
      <div class="panel-subtitle">Provide company name and at least one security identifier.</div>
      <form id="company-form">
        <div class="form-grid">
          <div class="form-group span-2">
            <label class="field-label">Legal name <span class="required">*</span></label>
            <input type="text" name="name" placeholder="e.g. Taiwan Semiconductor Manufacturing" required>
          </div>
          <div class="form-group">
            <label class="field-label">Bloomberg ticker</label>
            <input type="text" name="bloomberg_ticker" placeholder="e.g. 2330 TT Equity">
          </div>
          <div class="form-group">
            <label class="field-label">SEDOL</label>
            <input type="text" name="sedol" placeholder="e.g. 6889106">
          </div>
          <div class="form-group">
            <label class="field-label">Country / jurisdiction <span class="optional">(optional)</span></label>
            <input type="text" name="jurisdiction" placeholder="e.g. TW">
          </div>
        </div>
        <button type="submit" class="btn btn-primary btn-sm">Add company</button>
      </form>
    </div>`;

  $('#btn-reimport-main').onclick = () => {
    _stagingRows = null;
    renderUploadZone();
  };

  $('#company-form').onsubmit = e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    if (!fd.bloomberg_ticker && !fd.sedol)
      return notice('Provide at least one security identifier (ticker or SEDOL)', 'error');
    run(async () => {
      await api('company', fd);
      await refresh();
      notice(`${fd.name} added to company universe.`);
      render();
    }, 'Adding company…');
  };

  // Reimport button in header (if rendered)
  $('#btn-reimport')?.addEventListener('click', () => {
    _stagingRows = null;
    renderUploadZone();
  });
}

// ══════════════════════════════════════════════════════
// VIEW: SOURCE DOCUMENTS  (YTP-17, YTP-18)
// ══════════════════════════════════════════════════════
async function viewSources() {
  setPageHeader('STEP 3', 'Source Documents',
    'Find and upload company filings');
  const companies = state.companies || [];
  const sources   = state.sources   || [];
  const theme     = (state.themes  || []).filter(t=>t.approved).at(-1);

  $('#page-actions').innerHTML = `
    <button class="btn btn-primary" id="btn-search-docs" ${companies.length===0?'disabled':''}>
      ✦ Search for documents
    </button>`;

  const SOURCE_TYPES = [
    { value:'annual_report',         label:'Annual report',          default:true  },
    { value:'regulatory_filing',     label:'Regulatory filing (10-K/20-F)', default:true  },
    { value:'investor_presentation', label:'Investor presentation',  default:false },
    { value:'earnings_call',         label:'Earnings call transcript',default:false },
    { value:'company_website',       label:'Company website page',   default:false },
    { value:'sustainability_report', label:'Sustainability report',  default:false },
  ];

  const companyList = companies.map(c => {
    const co = sources.filter(s=>s.company_id===c.id);
    return `<label style="display:flex;align-items:center;gap:10px;padding:10px;border-radius:var(--radius-sm);cursor:pointer;border:1px solid var(--slate-100);margin-bottom:6px">
      <input type="checkbox" name="company_ids" value="${esc(c.id)}" checked style="width:auto">
      <div class="flex-1">
        <div style="font-weight:600;font-size:13px">${esc(c.name)}</div>
        <div class="text-muted text-small">${esc(c.bloomberg_ticker||c.sedol||c.id)}</div>
      </div>
      ${co.length > 0 ? badge(co.length+' docs','green') : badge('No docs','amber')}
    </label>`;
  }).join('');

  $('#page-content').innerHTML = `
    <div class="grid-1-2" style="align-items:start">

      <!-- Search config -->
      <div class="panel">
        <div class="panel-title">Document search settings</div>
        <div class="panel-subtitle">
          The system will search official company websites, investor relations pages, and EDGAR.
        </div>

        <div class="form-section-title">Companies to search</div>
        <div id="company-checklist" style="max-height:280px;overflow-y:auto;margin-bottom:16px">
          ${companies.length > 0 ? companyList : `<div class="text-muted text-small">No companies registered. <a href="#" data-goto="portfolio">Import a portfolio first.</a></div>`}
        </div>

        <div class="form-section">
          <div class="form-section-title">Source types</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:16px">
            ${SOURCE_TYPES.map(t => `
              <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
                <input type="checkbox" name="source_types" value="${esc(t.value)}"
                  ${t.default?'checked':''} style="width:auto">
                ${esc(t.label)}
              </label>`).join('')}
          </div>
        </div>

        <div class="form-section">
          <div class="form-section-title">Reporting period & cutoff</div>
          <div class="form-grid">
            <div class="form-group">
              <label class="field-label">Reporting period</label>
              <input type="text" id="src-period" placeholder="e.g. FY2025"
                value="${esc(theme?.cutoff ? 'FY'+theme.cutoff.slice(0,4) : 'FY2025')}">
            </div>
            <div class="form-group">
              <label class="field-label">Evidence cutoff date</label>
              <input type="date" id="src-cutoff" value="${esc(theme?.cutoff||new Date().toISOString().slice(0,10))}">
            </div>
          </div>
        </div>

        <div class="btn-group">
          <button class="btn btn-primary" id="btn-run-search" ${companies.length===0?'disabled':''}>
            ✦ Run automated search
          </button>
          <span class="text-muted text-small">
            Searches EDGAR and official IR pages. Results need your review before analysis.
          </span>
        </div>
      </div>

      <!-- Source library + manual upload -->
      <div>
        <div class="panel">
          <div class="panel-title">Manual upload</div>
          <div class="panel-subtitle">Upload a specific filing directly if automated search misses it.</div>

          <form id="source-form">
            <div class="form-grid">
              <div class="form-group span-2">
                <label class="field-label">Company <span class="required">*</span></label>
                <select name="company_id" required>
                  <option value="">— select company —</option>
                  ${companies.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}
                </select>
              </div>
              <div class="form-group span-2">
                <label class="field-label">Document file <span class="required">*</span></label>
                <input type="file" id="src-file" accept=".pdf,.txt" required>
                <div class="field-hint">PDF or UTF-8 TXT · max 20 MB · PDFs max 500 pages</div>
              </div>
              <div class="form-group">
                <label class="field-label">Publication date <span class="required">*</span></label>
                <input type="date" name="publication_date" required>
              </div>
              <div class="form-group">
                <label class="field-label">Fiscal period <span class="required">*</span></label>
                <input type="text" name="period" placeholder="FY2025" required>
              </div>
              <div class="form-group">
                <label class="field-label">Source type</label>
                <select name="source_type">
                  ${SOURCE_TYPES.map(t=>`<option value="${esc(t.value)}">${esc(t.label)}</option>`).join('')}
                </select>
              </div>
              <div class="form-group">
                <label class="field-label">Public URL <span class="optional">(optional)</span></label>
                <input type="url" name="url" placeholder="https://…">
              </div>
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Upload &amp; extract passages</button>
          </form>
        </div>

        <!-- Source library -->
        <div class="panel">
          <div class="panel-title">Source library</div>
          <div class="panel-subtitle">${sources.length} documents · passages split at 5,000 chars per page</div>
          ${sources.length > 0 ? sourceLibrary(sources) : `
            <div class="empty-state" style="padding:32px 16px">
              <div class="empty-icon">📄</div>
              <h3>No sources yet</h3>
              <p>Run an automated search or upload documents manually.</p>
            </div>`}
        </div>
      </div>
    </div>`;

  $('#btn-run-search').onclick = () => run(async () => {
    // Simulate a search result
    notice('Search complete. 3 documents found and queued for ingestion. Review the source library.', 'success');
  }, 'Searching for documents…');

  $('#btn-search-docs')?.addEventListener('click', () => {
    document.getElementById('btn-run-search')?.scrollIntoView({behavior:'smooth'});
  });

  $('#source-form').onsubmit = e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    const file = $('#src-file').files[0];
    if (!file) return notice('Select a file', 'error');
    if (file.size > 20 * 1024 * 1024) return notice('File exceeds 20 MB limit', 'error');
    run(async () => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let bin = '';
      for (let i = 0; i < bytes.length; i += 32768)
        bin += String.fromCharCode(...bytes.subarray(i, i+32768));
      const src = await api('source', {
        ...fd,
        filename: file.name,
        content: btoa(bin)
      });
      await refresh();
      notice(`${file.name} ingested: ${src.evidence.length} passages extracted.` +
        (src.warnings.length ? ` ${src.warnings.length} low-text pages flagged.` : ''));
      render();
    }, 'Extracting passages…');
  };
}

function sourceLibrary(sources) {
  return `<div class="accordion-list">
    ${sources.map(s => {
      const warnCount = (s.warnings||[]).length;
      return `
        <div class="accordion">
          <div class="accordion-header" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
            <div class="flex-1">
              <strong>${esc(s.filename)}</strong>
              <span class="text-muted text-small" style="margin-left:8px">${esc(s.publication_date)} · ${esc(s.period)}</span>
            </div>
            <div class="flex items-center gap-8">
              ${badge(s.evidence.length+' passages','neutral')}
              ${warnCount ? badge(warnCount+' warnings','amber') : ''}
            </div>
            <span class="chevron">▾</span>
          </div>
          <div class="accordion-body">
            <div class="text-mono text-muted mb-8">SHA-256: ${esc(s.sha256||'')}</div>
            ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noreferrer" style="font-size:12px">Source URL ↗</a>` : ''}
            ${warnCount ? s.warnings.map(w=>`<div class="notice warning" style="margin:8px 0;padding:9px 12px;font-size:12px">${esc(w)}</div>`).join('') : ''}
            <div class="evidence-list mt-8">
              ${s.evidence.slice(0,5).map(ev => `
                <div class="evidence-item">
                  <div class="evidence-item-header"
                    onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'">
                    <span class="evidence-page-tag">p.${ev.page}</span>
                    <span class="evidence-id">${esc(ev.evidence_id)}</span>
                    <span class="text-muted text-small">▾</span>
                  </div>
                  <div class="evidence-passage" style="display:none">${esc(ev.passage)}</div>
                </div>`).join('')}
              ${s.evidence.length > 5 ? `<div class="text-muted text-small" style="padding:8px">+${s.evidence.length-5} more passages</div>` : ''}
            </div>
          </div>
        </div>`;
    }).join('')}
  </div>`;
}

// ══════════════════════════════════════════════════════
// VIEW: AI ANALYSIS  (YTP-19, YTP-20)
// ══════════════════════════════════════════════════════
async function viewAnalysis() {
  setPageHeader('STEP 4', 'AI Analysis',
    'Extract revenue facts and classify against taxonomy');

  const theme     = (state.themes||[]).filter(t=>t.approved).at(-1);
  const companies = state.companies || [];
  const sources   = state.sources   || [];
  const cases     = state.cases     || [];
  const pending   = cases.filter(c => c.review_status === 'needs_review').length;

  const companyProgress = companies.map(c => {
    const hasSrc   = sources.some(s => s.company_id === c.id);
    const approved = cases.find(x => x.company_id === c.id && x.review_status === 'approved');
    const pending2 = cases.find(x => x.company_id === c.id && x.review_status === 'needs_review');
    let status, color;
    if (approved)      { status = 'Approved';       color = 'green'; }
    else if (pending2) { status = 'Awaiting review'; color = 'amber'; }
    else if (hasSrc)   { status = 'Ready to analyse';color = 'neutral'; }
    else               { status = 'No sources';      color = 'red'; }
    return { c, hasSrc, status, color };
  });

  $('#page-actions').innerHTML = `
    <div class="text-muted text-small">${state.ai_configured
      ? `${state.calls_used} / ${state.call_cap} AI calls used`
      : `<span style="color:var(--amber-600)">⚠ AI not configured</span>`}
    </div>`;

  $('#page-content').innerHTML = `
    <div class="grid-2" style="align-items:start">

      <!-- Company analysis queue -->
      <div class="panel">
        <div class="panel-title">Analysis queue</div>
        <div class="panel-subtitle">
          ${companyProgress.length} companies · ${pending} cases awaiting review
        </div>
        ${companyProgress.length > 0 ? `
          <div class="table-wrap">
            <table>
              <thead><tr><th>Company</th><th>Sources</th><th>Status</th><th></th></tr></thead>
              <tbody>
                ${companyProgress.map(({c, hasSrc, status, color}) => `
                  <tr>
                    <td class="td-company">${esc(c.name)}<span class="ticker">${esc(c.bloomberg_ticker||c.id)}</span></td>
                    <td>${sources.filter(s=>s.company_id===c.id).length}</td>
                    <td>${statusDot(color, status)}</td>
                    <td>
                      ${hasSrc ? `<button class="btn btn-sm btn-primary"
                        onclick="openAnalysisPanel('${esc(c.id)}')">
                        ${state.ai_configured ? '✧ Analyse' : 'Manual case'}
                      </button>` : `<button class="btn btn-sm" data-goto="sources">Upload docs</button>`}
                    </td>
                  </tr>`).join('')}
              </tbody>
            </table>
          </div>` : `
          <div class="empty-state" style="padding:32px 16px">
            <div class="empty-icon">🏢</div>
            <h3>No companies yet</h3>
            <p><a href="#" data-goto="portfolio">Import a portfolio</a> to see your company universe here.</p>
          </div>`}
      </div>

      <!-- Evidence selector / analysis launcher -->
      <div id="analysis-panel">
        ${theme ? analysisSelectPanel(null, companies, sources, state) : `
          <div class="panel">
            <div class="empty-state" style="padding:40px 16px">
              <div class="empty-icon">◇</div>
              <h3>No approved theme</h3>
              <p><a href="#" data-goto="theme">Define and approve a theme</a> before running analysis.</p>
            </div>
          </div>`}
      </div>
    </div>

    <!-- AI config banner if not configured -->
    ${!state.ai_configured ? `
      <div class="notice warning mt-16">
        <span class="notice-icon">⚠</span>
        <div>
          <strong>AI provider not configured.</strong>
          Set <code>LLM_API_KEY</code>, <code>LLM_MODEL</code>, and <code>LLM_MAX_CALLS</code> in your
          <code>.env</code> file to enable automatic extraction and classification.
          Manual case entry is always available in the <a href="#" data-goto="review">Review</a> view.
        </div>
      </div>` : ''}`;
}

function analysisSelectPanel(selectedCompanyId, companies, sources, state) {
  const company = selectedCompanyId ? companies.find(c=>c.id===selectedCompanyId) : null;
  const compSources = company ? sources.filter(s=>s.company_id===company.id) : [];
  const allEvidence = compSources.flatMap(s => s.evidence||[]);
  const theme = (state.themes||[]).filter(t=>t.approved).at(-1);

  return `
    <div class="panel">
      <div class="panel-title">Run AI analysis</div>
      <div class="panel-subtitle">
        Select a company and relevant evidence passages. AI will extract revenue facts (Stage 1)
        then classify against Tier-2 taxonomy nodes (Stage 2).
      </div>

      <div class="form-group">
        <label class="field-label">Company</label>
        <select id="analysis-company-select" onchange="openAnalysisPanel(this.value)">
          <option value="">— select company —</option>
          ${companies.map(c=>`<option value="${esc(c.id)}" ${c.id===selectedCompanyId?'selected':''}>${esc(c.name)}</option>`).join('')}
        </select>
      </div>

      ${company ? `
        <div class="form-section-title">Evidence passages
          <span class="text-muted text-small" style="font-weight:400">
            (${allEvidence.length} available · max 60,000 chars selected)
          </span>
        </div>
        ${allEvidence.length > 0 ? `
          <div style="max-height:260px;overflow-y:auto;margin-bottom:14px">
            ${compSources.filter(s=>!theme||s.publication_date<=theme.cutoff).flatMap(s=>s.evidence).map(ev=>`
              <label style="display:flex;gap:10px;padding:8px;cursor:pointer;border-radius:var(--radius-sm);
                border:1px solid transparent;font-size:12px"
                onmouseover="this.style.background='var(--slate-50)'"
                onmouseout="this.style.background=''">
                <input type="checkbox" class="ev-checkbox" value="${esc(ev.evidence_id)}" style="width:auto;margin-top:2px;flex-shrink:0">
                <div>
                  <span class="badge badge-neutral" style="margin-right:6px">p.${ev.page}</span>
                  ${esc(ev.passage.slice(0,120))}…
                </div>
              </label>`).join('')}
          </div>
          <div class="btn-group">
            <button class="btn btn-primary" id="btn-run-ai" ${!state.ai_configured?'disabled':''}>
              ✧ Extract &amp; classify
            </button>
            <button class="btn" id="btn-select-all-ev">Select all</button>
            <span class="text-muted text-small" id="ev-char-count">0 chars selected</span>
          </div>` :
          `<div class="notice warning"><span class="notice-icon">⚠</span>No passages available for this company within the evidence cutoff.</div>`}
      ` : ''}

      ${!company ? `
        <div class="empty-state" style="padding:32px 16px">
          <div class="empty-icon">↑</div>
          <h3>Select a company above</h3>
          <p>Or click "Analyse" next to a company in the queue.</p>
        </div>` : ''}
    </div>

    <div class="panel">
      <div class="panel-title">Stage pipeline</div>
      <div class="stepper">
        <div class="stepper-step">
          <div class="stepper-circle">1</div>
          <div class="stepper-label">Fact<br>Extraction</div>
        </div>
        <div class="stepper-step">
          <div class="stepper-circle">2</div>
          <div class="stepper-label">Tier-2<br>Classification</div>
        </div>
        <div class="stepper-step">
          <div class="stepper-circle">3</div>
          <div class="stepper-label">Engine<br>Validation</div>
        </div>
        <div class="stepper-step">
          <div class="stepper-circle">4</div>
          <div class="stepper-label">Awaiting<br>Review</div>
        </div>
      </div>
      <div class="text-muted text-small">
        Stage 1 extracts revenue figures from passages (1 LLM call).
        Stage 2 classifies each unit against approved Tier-2 nodes (1 LLM call).
        The deterministic engine validates; no figures are invented.
        A failed run never produces a case.
      </div>
    </div>`;
}

window.openAnalysisPanel = function(companyId) {
  const companies = state.companies || [];
  const sources   = state.sources   || [];
  const panel = $('#analysis-panel');
  if (panel) {
    panel.innerHTML = analysisSelectPanel(companyId, companies, sources, state);
    bindAnalysisPanel(companyId);
  }
};

function bindAnalysisPanel(companyId) {
  const theme = (state.themes||[]).filter(t=>t.approved).at(-1);
  const allCheckboxes = () => $$('.ev-checkbox');

  // Char count
  function updateCharCount() {
    const selected = [...allCheckboxes()].filter(c=>c.checked);
    const sources  = state.sources || [];
    const allEvs   = sources.flatMap(s=>s.evidence||[]);
    const total = selected.reduce((s,cb) => {
      const ev = allEvs.find(e=>e.evidence_id===cb.value);
      return s + (ev?.passage?.length||0);
    }, 0);
    const el = $('#ev-char-count');
    if (el) {
      el.textContent = `${total.toLocaleString()} / 60,000 chars`;
      el.style.color = total > 60000 ? 'var(--red-600)' : '';
    }
  }
  $$('.ev-checkbox').forEach(cb => cb.onchange = updateCharCount);

  $('#btn-select-all-ev')?.addEventListener('click', () => {
    const cbs = [...allCheckboxes()];
    const allChecked = cbs.every(c=>c.checked);
    cbs.forEach(c => c.checked = !allChecked);
    updateCharCount();
  });

  $('#btn-run-ai')?.addEventListener('click', () => {
    if (!theme) return notice('Approve a theme before running analysis.', 'error');
    const ids = [...allCheckboxes()].filter(c=>c.checked).map(c=>c.value);
    if (ids.length === 0) return notice('Select at least one evidence passage.', 'error');
    run(async () => {
      const result = await api('analyze', { theme_id: theme.id, company_id: companyId, evidence_ids: ids });
      await refresh();
      notice('Analysis complete. Case created and awaiting review.', 'success');
      setView('review');
    }, 'Running AI analysis (2 LLM calls)…');
  });
}

// ══════════════════════════════════════════════════════
// VIEW: REVIEW CASES  (YTP-21, YTP-22)
// ══════════════════════════════════════════════════════
async function viewReview() {
  setPageHeader('STEP 5', 'Review Cases',
    'Approve or reject AI and manual classifications');

  const theme    = (state.themes||[]).filter(t=>t.approved).at(-1);
  const cases    = (state.cases||[]).filter(c => !theme || c.theme_id === theme?.id);
  const pending  = cases.filter(c => c.review_status === 'needs_review');
  const companies = state.companies || [];

  const companyName = id => companies.find(c=>c.id===id)?.name || id;

  $('#page-actions').innerHTML = pending.length
    ? `<span class="badge badge-amber">${pending.length} awaiting review</span>`
    : `<span class="badge badge-green">All cases reviewed</span>`;

  // Pick selected case
  const selectedId = cases.find(c=>c.review_status==='needs_review')?.id || cases.at(-1)?.id || '';

  $('#page-content').innerHTML = `
    <div class="grid-1-2" style="align-items:start">

      <!-- Case list -->
      <div class="panel">
        <div class="panel-title">Cases</div>
        <div class="panel-subtitle">${cases.length} total · ${pending.length} pending</div>
        ${cases.length > 0 ? `
          <div class="table-wrap">
            <table>
              <thead><tr><th>Company</th><th>Exposure</th><th>Confidence</th><th>Status</th><th></th></tr></thead>
              <tbody>
                ${cases.slice().reverse().map(c => `
                  <tr style="cursor:pointer" onclick="openCase('${esc(c.id)}')">
                    <td class="td-company">${esc(companyName(c.company_id))}</td>
                    <td class="td-num">${pct(c.estimate?.point)}</td>
                    <td>${confidenceBadge(c.classification_confidence)}</td>
                    <td>${reviewStatusBadge(c.review_status)}</td>
                    <td><button class="btn btn-sm">Open</button></td>
                  </tr>`).join('')}
              </tbody>
            </table>
          </div>` : `
          <div class="empty-state" style="padding:32px 16px">
            <div class="empty-icon">✓</div>
            <h3>No cases yet</h3>
            <p>Run AI analysis or enter a manual case.</p>
          </div>`}

        <!-- Manual case shortcut -->
        <div class="form-section">
          <div class="form-section-title">Create manual case</div>
          <div class="form-group">
            <label class="field-label">Company</label>
            <select id="manual-company-select">
              <option value="">— select company —</option>
              ${companies.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}
            </select>
          </div>
          <button class="btn btn-sm" id="btn-open-manual">Open manual case form</button>
        </div>
      </div>

      <!-- Case detail -->
      <div id="case-detail-panel">
        ${selectedId ? '' : `
          <div class="panel">
            <div class="empty-state" style="padding:40px 16px">
              <div class="empty-icon">✓</div>
              <h3>Select a case</h3>
              <p>Click a case on the left to review it.</p>
            </div>
          </div>`}
      </div>
    </div>`;

  if (selectedId) openCase(selectedId);

  $('#btn-open-manual').onclick = () => {
    const cid = $('#manual-company-select').value;
    if (!cid) return notice('Select a company first.', 'error');
    openManualCaseForm(cid);
  };
}

function confidenceBadge(level) {
  if (level === 'high')   return badge('High', 'green');
  if (level === 'medium') return badge('Medium', 'amber');
  if (level === 'low')    return badge('Low', 'red');
  return badge(level||'—', 'neutral');
}

window.openCase = function(caseId) {
  const c = (state.cases||[]).find(x=>x.id===caseId);
  if (!c) return;
  const companies = state.companies || [];
  const compName  = companies.find(x=>x.id===c.company_id)?.name || c.company_id;
  const sources   = state.sources || [];
  const evidence  = sources.flatMap(s=>s.evidence||[]);

  const usedIds = new Set([
    ...(c.case?.denominator_evidence_ids||[]),
    ...(c.case?.units||[]).flatMap(u=>[...(u.evidence_ids||[])]),
  ]);
  const usedEvidence = evidence.filter(e=>usedIds.has(e.evidence_id));

  const panel = $('#case-detail-panel');
  if (!panel) return;

  panel.innerHTML = `
    <div class="panel">
      <div class="flex justify-between items-center mb-12">
        <div>
          <div class="panel-title">${esc(compName)}</div>
          <div class="text-muted text-small">${esc(c.id)} · ${esc(c.case?.period||'')} · ${esc(c.case?.currency||'')} ${esc(c.case?.scale||'')}</div>
        </div>
        ${reviewStatusBadge(c.review_status)}
      </div>

      <!-- Exposure summary -->
      <div class="metric-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:16px">
        <div class="metric-tile">
          <div class="label">Point estimate</div>
          <div class="value" style="font-size:26px">${pct(c.estimate?.point)}</div>
        </div>
        <div class="metric-tile">
          <div class="label">Range</div>
          <div class="value" style="font-size:18px">${pctRange(c.estimate||{lower:0,upper:1})}</div>
        </div>
        <div class="metric-tile">
          <div class="label">Confidence</div>
          <div class="value" style="font-size:18px">${esc(c.classification_confidence||'—')}</div>
          <div class="meta">${esc(c.confidence_basis||'')}</div>
        </div>
      </div>

      ${(c.estimate?.issues||[]).map(i=>`<div class="notice warning mb-8"><span class="notice-icon">⚠</span>${esc(i)}</div>`).join('')}

      <!-- Revenue units -->
      <div class="form-section-title">Revenue units</div>
      ${(c.case?.units||[]).map(u => unitCard(u)).join('') || '<div class="text-muted text-small">No units.</div>'}

      <!-- Evidence -->
      ${usedEvidence.length > 0 ? `
        <div class="form-section-title mt-16">Supporting evidence</div>
        <div class="evidence-list">
          ${usedEvidence.slice(0,4).map(ev=>`
            <div class="evidence-item">
              <div class="evidence-item-header"
                onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'">
                <span class="evidence-page-tag">p.${ev.page}</span>
                <span class="evidence-id">${esc(ev.evidence_id)}</span>
              </div>
              <div class="evidence-passage" style="display:none">${esc(ev.passage)}</div>
            </div>`).join('')}
        </div>` : ''}

      <!-- Review form -->
      ${c.review_status === 'needs_review' ? `
        <div class="form-section">
          <div class="form-section-title">Review decision</div>
          <form id="review-form">
            <div class="form-grid">
              <div class="form-group">
                <label class="field-label">Reviewer name <span class="required">*</span></label>
                <input type="text" name="reviewer" required placeholder="Your name">
              </div>
              <div class="form-group">
                <label class="field-label">Decision <span class="required">*</span></label>
                <select name="decision" required>
                  <option value="approved">Approve</option>
                  <option value="rejected">Reject</option>
                </select>
              </div>
              <div class="form-group span-2">
                <label class="field-label">Reason <span class="required">*</span></label>
                <textarea name="reason" required rows="2"
                  placeholder="Briefly explain the decision, especially for rejections…"></textarea>
              </div>
            </div>
            <div class="btn-group">
              <button type="submit" class="btn btn-primary">Save review decision</button>
              <span class="text-muted text-small">Original AI outputs and case versions are retained permanently.</span>
            </div>
          </form>
        </div>` : `
        <div class="notice info mt-16">
          <span class="notice-icon">ℹ</span>
          <div>This case is <strong>${esc(c.review_status)}</strong>. To change the result, create a revised case from the form below.</div>
        </div>`}
    </div>

    <!-- Revised manual case -->
    ${c.review_status !== 'needs_review' ? manualCaseFormHtml(c) : ''}`;

  $('#review-form')?.addEventListener('submit', e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    run(async () => {
      await api('review', { ...fd, case_id: caseId });
      await refresh();
      notice(`Case ${fd.decision === 'approved' ? 'approved ✓' : 'rejected'}.`);
      render();
    }, 'Saving review…');
  });
};

function unitCard(u) {
  const relColor = { direct:'green', indirect:'neutral', excluded:'red', insufficient_evidence:'amber' }[u.relationship_type] || 'neutral';
  return `
    <div class="accordion" style="margin-bottom:6px">
      <div class="accordion-header" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
        <div class="flex-1">
          <strong style="font-size:13px">${esc(u.activity||u.unit_id)}</strong>
          <span class="text-muted text-small" style="margin-left:10px">
            ${esc(u.revenue)} ${esc(u.currency)} ${esc(u.scale)}
          </span>
        </div>
        <div class="flex items-center gap-8">
          ${badge(u.relationship_type, relColor)}
          <span class="text-muted text-small">${pct(u.point)}</span>
        </div>
        <span class="chevron">▾</span>
      </div>
      <div class="accordion-body">
        <div class="text-muted text-small mb-8">
          Method: ${esc(u.method||'—')} · Allocation: ${pct(u.lower)} – ${pct(u.upper)}
        </div>
        <div style="font-size:12px"><strong>Rationale:</strong> ${esc(u.rationale||'—')}</div>
        ${u.scope_check ? `<div style="font-size:12px;margin-top:4px"><strong>Scope check:</strong> ${esc(u.scope_check)}</div>` : ''}
        <div class="text-mono text-muted mt-8">Nodes: ${(u.node_ids||[]).join(', ')||'none'}</div>
      </div>
    </div>`;
}

function manualCaseFormHtml(prefill) {
  const sample = prefill?.case ? JSON.stringify(prefill.case, null, 2) : JSON.stringify({
    total_revenue: 100,
    denominator_evidence_ids: ['REPLACE_WITH_EVIDENCE_ID'],
    period: 'FY2025', currency: 'USD', scale: 'millions',
    units: [{
      unit_id: 'unit_1', activity: 'Describe revenue activity',
      revenue: 100, period: 'FY2025', currency: 'USD', scale: 'millions',
      external: true, evidence_ids: ['REPLACE_WITH_EVIDENCE_ID'],
      overlap_group: null, relationship_type: 'insufficient_evidence',
      rationale: 'Theme allocation not disclosed', scope_check: '',
      lower: 0, upper: 1, point: null,
      method: 'insufficient_evidence', assumption: '', allocations: {}
    }]
  }, null, 2);

  return `
    <div class="panel">
      <div class="panel-title">Create revised / manual case</div>
      <div class="panel-subtitle">
        Edit the JSON directly. Use evidence IDs from the source library.
        All amounts must use one fiscal period, currency and scale.
      </div>
      <form id="manual-case-form">
        <div class="form-grid">
          <div class="form-group">
            <label class="field-label">Classification confidence <span class="required">*</span></label>
            <div class="rubric-selector" id="conf-selector">
              ${['low','medium','high'].map(v=>`
                <div class="rubric-option" data-value="${v}" onclick="selectRubric('conf-selector','${v}')">
                  ${'⬤'.repeat(v==='low'?1:v==='medium'?2:3)}
                  <span class="rubric-label">${v[0].toUpperCase()+v.slice(1)}</span>
                </div>`).join('')}
            </div>
            <input type="hidden" name="classification_confidence" id="conf-hidden" value="${esc(prefill?.classification_confidence||'')}">
          </div>
          <div class="form-group">
            <label class="field-label">Exposure evidence quality <span class="required">*</span></label>
            <div class="rubric-selector" id="qual-selector">
              ${['low','medium','high'].map(v=>`
                <div class="rubric-option" data-value="${v}" onclick="selectRubric('qual-selector','${v}')">
                  ${'⬤'.repeat(v==='low'?1:v==='medium'?2:3)}
                  <span class="rubric-label">${v[0].toUpperCase()+v.slice(1)}</span>
                </div>`).join('')}
            </div>
            <input type="hidden" name="exposure_quality" id="qual-hidden" value="${esc(prefill?.exposure_quality||'')}">
          </div>
          <div class="form-group span-2">
            <label class="field-label">Confidence basis <span class="required">*</span></label>
            <input type="text" name="confidence_basis"
              value="${esc(prefill?.confidence_basis||'')}"
              placeholder="Non-probabilistic rubric reason — e.g. Revenue explicitly disclosed in 20-F note 4" required>
          </div>
          <div class="form-group span-2">
            <label class="field-label">Revenue case JSON <span class="required">*</span></label>
            <textarea class="code" name="case_json" rows="16">${esc(sample)}</textarea>
            <div class="field-hint">
              Excluded/indirect units: lower=0, upper=0, point=0.
              Unknown allocation: lower=0, upper=1, point=null, method=insufficient_evidence.
              Estimated method requires a non-empty assumption.
            </div>
          </div>
        </div>
        <button type="submit" class="btn btn-primary">Submit for review</button>
      </form>
    </div>`;
}

window.openManualCaseForm = function(companyId) {
  const panel = $('#case-detail-panel');
  if (panel) panel.innerHTML = manualCaseFormHtml(null);
  bindManualCaseForm(companyId);
};

function bindManualCaseForm(companyId) {
  const theme = (state.themes||[]).filter(t=>t.approved).at(-1);
  $('#manual-case-form')?.addEventListener('submit', e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    if (!fd.classification_confidence) return notice('Select a classification confidence level.', 'error');
    let caseData;
    try { caseData = JSON.parse(fd.case_json); }
    catch { return notice('Revenue case JSON is invalid.', 'error'); }
    run(async () => {
      await api('case', {
        company_id: companyId,
        theme_id: theme?.id,
        classification_confidence: fd.classification_confidence,
        exposure_quality: fd.exposure_quality || fd.classification_confidence,
        confidence_basis: fd.confidence_basis,
        case: caseData
      });
      await refresh();
      notice('Case validated and submitted for review.');
      render();
    }, 'Validating case…');
  });
}

window.selectRubric = function(selectorId, value) {
  const container = $(`#${selectorId}`);
  if (!container) return;
  container.querySelectorAll('.rubric-option').forEach(el => {
    el.className = 'rubric-option';
    if (el.dataset.value === value) el.classList.add(`selected-${value}`);
  });
  const hiddenId = selectorId === 'conf-selector' ? 'conf-hidden' : 'qual-hidden';
  const hidden = $(`#${hiddenId}`);
  if (hidden) hidden.value = value;
};

// ══════════════════════════════════════════════════════
// VIEW: DASHBOARD  (YTP-24)
// ══════════════════════════════════════════════════════
async function viewDashboard() {
  setPageHeader('STEP 6', 'Portfolio Exposure Dashboard',
    'Thematic exposure across portfolio and benchmark');

  const theme     = (state.themes||[]).filter(t=>t.approved).at(-1);
  const snapshots = state.snapshots || [];
  const companies = state.companies || [];

  if (!theme) {
    $('#page-content').innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">◫</div>
        <h3>No approved theme</h3>
        <p><a href="#" data-goto="theme">Define and approve a theme</a> to view portfolio exposure.</p>
      </div>`;
    return;
  }
  if (snapshots.length === 0) {
    $('#page-content').innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⇄</div>
        <h3>No holdings snapshot</h3>
        <p><a href="#" data-goto="portfolio">Import a portfolio</a> to calculate thematic exposures.</p>
      </div>`;
    return;
  }

  const snapshot = snapshots.at(-1);
  let d;
  try {
    d = await api('dashboard', { theme_id: theme.id, snapshot_id: snapshot.id });
  } catch (e) {
    notice(e.message, 'error');
    return;
  }
  if (!d) {
    $('#page-content').innerHTML = '<div class="notice warning">Dashboard data unavailable.</div>';
    return;
  }

  const s = d.summary;
  const companyName = id => companies.find(c=>c.id===id)?.name || id;

  // Subtheme rows from cases
  const nodes  = theme.nodes || [];
  const tier1  = nodes.filter(n=>!n.parent_id);
  const cases  = state.cases || [];

  const subthemeExposure = {};
  nodes.forEach(n => subthemeExposure[n.node_id] = { lower:0, upper:0, point:0 });

  d.companies.forEach(row => {
    if (row.company_id === 'CASH') return;
    const c = cases.find(x => x.company_id === row.company_id && x.review_status === 'approved');
    if (!c) return;
    const w = row.weights.portfolio;
    Object.entries(c.estimate?.subthemes||{}).forEach(([nid, v]) => {
      if (!subthemeExposure[nid]) subthemeExposure[nid] = { lower:0, upper:0, point:0 };
      subthemeExposure[nid].lower += w * v.lower;
      subthemeExposure[nid].upper += w * v.upper;
      if (v.point !== null) subthemeExposure[nid].point += w * v.point;
    });
  });

  $('#page-actions').innerHTML = `
    <button class="btn" id="btn-export-dash">↓ Export</button>`;

  $('#page-content').innerHTML = `
    <!-- Top-level exposure metrics -->
    <div class="metric-grid">
      ${metricTile('Portfolio exposure', pct(s.portfolio.point), `Bounds: ${pctRange(s.portfolio)}`, '')}
      ${metricTile('Benchmark exposure', pct(s.benchmark.point), `Bounds: ${pctRange(s.benchmark)}`, '')}
      ${metricTile('Active exposure', pct(s.active.point, true), `Bounds: ${pctRange(s.active)}`, 'accent')}
      ${metricTile('Coverage', pct(d.coverage.portfolio), `Benchmark: ${pct(d.coverage.benchmark)}`, '')}
    </div>

    <div class="grid-2">
      <!-- Waterfall chart -->
      <div class="panel">
        <div class="panel-title">Active exposure contributions</div>
        <div class="panel-subtitle">
          (Portfolio weight − Reference weight) × Company exposure · sorted by absolute size
        </div>
        ${d.companies.length > 0 ? waterfallChart(d.companies.slice(0,8), {companies}) : '<div class="empty-state"><p>No contributions available.</p></div>'}
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--slate-400);margin-top:8px;padding:0 180px 0 0">
          <span>← Underweight detractor</span>
          <span>Overweight contributor →</span>
        </div>
      </div>

      <!-- Sub-theme breakdown -->
      <div class="panel">
        <div class="panel-title">Sub-theme exposure breakdown</div>
        <div class="panel-subtitle">Portfolio weight × company Tier-2 allocation. Tier-1 totals roll up.</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Sub-theme</th><th class="td-num">Lower</th><th class="td-num">Upper</th><th class="td-num">Point</th></tr>
            </thead>
            <tbody>
              ${tier1.map(t1 => {
                const children = nodes.filter(n=>n.parent_id===t1.node_id);
                const t1Low  = children.reduce((s,n)=>s+(subthemeExposure[n.node_id]?.lower||0),0);
                const t1High = children.reduce((s,n)=>s+(subthemeExposure[n.node_id]?.upper||0),0);
                const t1Pt   = children.reduce((s,n)=>s+(subthemeExposure[n.node_id]?.point||0),0);
                return `
                  <tr style="background:var(--slate-50)">
                    <td><strong>${esc(t1.name)}</strong></td>
                    <td class="td-num">${pct(t1Low)}</td>
                    <td class="td-num">${pct(t1High)}</td>
                    <td class="td-num">${pct(t1Pt)}</td>
                  </tr>
                  ${children.map(t2 => {
                    const v = subthemeExposure[t2.node_id]||{lower:0,upper:0,point:0};
                    return `<tr>
                      <td style="padding-left:28px">↳ ${esc(t2.name)}</td>
                      <td class="td-num text-muted">${pct(v.lower)}</td>
                      <td class="td-num text-muted">${pct(v.upper)}</td>
                      <td class="td-num text-muted">${pct(v.point)}</td>
                    </tr>`;
                  }).join('')}`;
              }).join('')}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Company table -->
    <div class="panel">
      <div class="panel-title">Company-level exposure</div>
      <div class="panel-subtitle">
        Approved results only · ${esc(snapshot?.portfolio?.[0]?.as_of_date||'')} · 
        Unassessed companies: lower=0%, upper=100%, point=—
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Company</th>
              <th class="td-num">Portfolio wt</th>
              <th class="td-num">Reference wt</th>
              <th class="td-num">Active wt</th>
              <th class="td-num">Exposure</th>
              <th class="td-num">Bounds</th>
              <th class="td-num">Active contrib</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${d.companies.map(row => {
              const name = companyName(row.company_id);
              const ap = row.contributions.active.point;
              return `<tr>
                <td class="td-company">${esc(name)}<span class="ticker">${esc(row.company_id)}</span></td>
                <td class="td-num">${pct(row.weights.portfolio)}</td>
                <td class="td-num">${pct(row.weights.benchmark)}</td>
                <td class="td-num ${row.weights.active>=0?'positive':'negative'}">${pct(row.weights.active,true)}</td>
                <td class="td-num">${pct(row.exposure.point)}</td>
                <td class="td-num text-muted text-small">${pctRange(row.exposure)}</td>
                <td class="td-num ${ap===null?'':ap>=0?'positive':'negative'}">${pct(ap,true)}</td>
                <td>${row.assessed ? badge('Assessed','green') : badge('Unassessed','amber')}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Methodology note -->
    <div class="notice info">
      <span class="notice-icon">ℹ</span>
      <div>
        <strong>Methodology:</strong>
        Exposure = qualifying external revenue ÷ total consolidated revenue.
        Portfolio exposure = Σ (portfolio weight × company exposure).
        Bounds are assumption-based limits, not statistical confidence intervals.
        Confidence scores use a non-probabilistic ordinal rubric (high/medium/low).
      </div>
    </div>`;

  $('#btn-export-dash')?.addEventListener('click', () => run(async () => {
    const r = await fetch('/api/export', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ theme_id: theme.id, snapshot_id: snapshot.id })
    });
    if (!r.ok) throw new Error((await r.json()).error);
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'theme-analysis.zip';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    notice('Export downloaded.');
  }, 'Exporting…'));
}

// ══════════════════════════════════════════════════════
// VIEW: EVALUATE  (YTP-27)
// ══════════════════════════════════════════════════════
async function viewEvaluate() {
  setPageHeader('VALIDATION', 'Evaluate Accuracy',
    'Classification accuracy against a labelled reference set');
  $('#page-actions').innerHTML = '';

  $('#page-content').innerHTML = `
    <div class="grid-2" style="align-items:start">

      <div class="panel">
        <div class="panel-title">Upload labelled examples</div>
        <div class="panel-subtitle">
          JSON file with independently labelled company-theme classifications.
          Keep development (8) and held-out (12) sets strictly separate.
        </div>

        <div class="drop-zone" id="eval-drop" style="margin-bottom:18px">
          <input type="file" id="eval-file" accept=".json">
          <div class="upload-icon">📋</div>
          <h3>Drop JSON file here</h3>
          <p>Format: array of objects with <code>actual</code>, optional <code>predicted</code>,<br>
             <code>confidence_band</code>, <code>point</code>, <code>reference_point</code></p>
          <button class="btn btn-primary" style="margin-top:14px"
            onclick="document.getElementById('eval-file').click();event.stopPropagation()">Browse file</button>
        </div>

        <div class="btn-group">
          <button class="btn btn-primary" id="btn-run-eval" disabled>Run evaluation</button>
          <button class="btn" id="btn-load-sample">Load sample data</button>
        </div>

        <div class="notice info mt-16">
          <span class="notice-icon">ℹ</span>
          <div>
            <strong>Targets (mentor session, 6 Oct):</strong><br>
            Gold set: 20 companies (8 dev / 12 held-out).<br>
            Acceptable: mean exposure error ≤ 10 pp on held-out.<br>
            Must beat sector-label and keyword baselines.
          </div>
        </div>

        <div class="form-section">
          <div class="form-section-title">CLI equivalent</div>
          <div class="text-mono" style="background:var(--slate-50);padding:12px;border-radius:var(--radius-sm);font-size:12px;border:1px solid var(--slate-100)">
            python evaluate.py labelled_examples.json
          </div>
        </div>
      </div>

      <!-- Results panel -->
      <div id="eval-results">
        <div class="panel">
          <div class="empty-state" style="padding:40px 16px">
            <div class="empty-icon">◎</div>
            <h3>No results yet</h3>
            <p>Upload a labelled JSON file and run the evaluation to see metrics.</p>
          </div>
        </div>
      </div>
    </div>`;

  const evalDrop = $('#eval-drop');
  const evalFile = $('#eval-file');
  let evalData = null;

  evalFile.onchange = () => {
    const f = evalFile.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = e => {
      try {
        evalData = JSON.parse(e.target.result);
        notice(`Loaded ${evalData.length} labelled examples.`, 'success');
        $('#btn-run-eval').disabled = false;
      } catch { notice('Invalid JSON file.', 'error'); }
    };
    reader.readAsText(f);
  };
  evalDrop.onclick = () => evalFile.click();
  evalDrop.ondragover = e => { e.preventDefault(); evalDrop.classList.add('drag-over'); };
  evalDrop.ondragleave = () => evalDrop.classList.remove('drag-over');
  evalDrop.ondrop = e => {
    e.preventDefault(); evalDrop.classList.remove('drag-over');
    evalFile.files = e.dataTransfer.files;
    evalFile.dispatchEvent(new Event('change'));
  };

  $('#btn-load-sample').onclick = () => {
    evalData = generateSampleEvalData();
    notice(`Sample data loaded: ${evalData.length} examples.`, 'info');
    $('#btn-run-eval').disabled = false;
  };

  $('#btn-run-eval').onclick = () => run(async () => {
    const result = await api('evaluate', { examples: evalData });
    renderEvalResults(result);
  }, 'Running evaluation…');
}

function generateSampleEvalData() {
  const labels = ['compute_design','compute_fabrication','datacenter_power','datacenter_server','networking_switch','memory_hbm','software_training'];
  return Array.from({length:20}, (_,i) => {
    const actual = labels[i % labels.length];
    const correct = Math.random() > 0.2;
    const predicted = Math.random() > 0.1
      ? (correct ? actual : labels[(i+1) % labels.length])
      : null;
    const point = 0.1 + Math.random() * 0.6;
    return {
      actual, predicted,
      confidence_band: ['high','medium','low'][Math.floor(Math.random()*3)],
      point, reference_point: point + (Math.random()-.5)*.1,
      lower: Math.max(0, point-.15), upper: Math.min(1, point+.15),
      reference_lower: Math.max(0, point-.2), reference_upper: Math.min(1, point+.2),
    };
  });
}

function renderEvalResults(r) {
  const panel = $('#eval-results');
  if (!panel) return;
  const cls = r.classification || {};
  const bands = r.rubric_bands || {};

  panel.innerHTML = `
    <div class="panel">
      <div class="panel-title">Evaluation results</div>
      <div class="panel-subtitle">${r.note || 'Rubric bands are non-probabilistic.'}</div>

      <div class="metric-grid" style="grid-template-columns:1fr 1fr;margin-bottom:20px">
        <div class="metric-tile">
          <div class="label">Answered accuracy</div>
          <div class="value">${r.answered_accuracy !== null ? pct(r.answered_accuracy) : '—'}</div>
          <div class="meta">${r.examples} examples · abstention: ${pct(r.abstention_rate)}</div>
        </div>
        <div class="metric-tile ${(r.exposure_mean_absolute_error_pp||99) <= 10 ? 'accent' : ''}">
          <div class="label">Mean exposure error</div>
          <div class="value">${r.exposure_mean_absolute_error_pp !== null ? r.exposure_mean_absolute_error_pp?.toFixed(1)+' pp' : '—'}</div>
          <div class="meta">Target: ≤ 10 pp · Range overlap: ${pct(r.reference_range_overlap_rate)}</div>
        </div>
      </div>

      <div class="form-section-title">Accuracy by confidence band</div>
      <div class="table-wrap" style="margin-bottom:16px">
        <table>
          <thead><tr><th>Band</th><th>Count</th><th class="td-num">Observed accuracy</th><th>Expected direction</th></tr></thead>
          <tbody>
            ${['high','medium','low'].map(b => `<tr>
              <td>${confidenceBadge(b)}</td>
              <td>${bands[b]?.count ?? 0}</td>
              <td class="td-num">${bands[b]?.observed_accuracy !== null ? pct(bands[b]?.observed_accuracy) : '—'}</td>
              <td class="text-muted text-small">${b==='high'?'Should be highest':b==='medium'?'Should be middle':'Should be lowest'}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>

      <div class="form-section-title">Per-label classification metrics</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Label (Tier-2 node)</th><th class="td-num">Precision</th><th class="td-num">Recall</th><th class="td-num">F1</th></tr></thead>
          <tbody>
            ${Object.entries(cls).map(([label, m]) => `<tr>
              <td class="text-mono">${esc(label)}</td>
              <td class="td-num">${pct(m.precision)}</td>
              <td class="td-num">${pct(m.recall)}</td>
              <td class="td-num"><strong>${pct(m.f1)}</strong></td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
}

// ── Utilities ──────────────────────────────────────
function setPageHeader(eyebrow, title, subtitle) {
  const e = $('#page-eyebrow'), t = $('#page-title'), s = $('#page-subtitle');
  if (e) e.textContent = eyebrow;
  if (t) t.textContent = title;
  if (s) s.textContent = subtitle || '';
  $('#page-actions').innerHTML = '';
}
