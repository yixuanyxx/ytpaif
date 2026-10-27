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
    headers: { 'Content-Type': 'application/json', 'X-Theme-Lab-View': currentView },
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

let noticeTimer = null;

function notice(msg, type = 'success') {
  clearNotice();
  const icons = { success: '✓', warning: '⚠', error: '✕', info: 'ℹ' };
  $('#notice-area').innerHTML = `
    <div class="notice ${type}">
      <span class="notice-icon">${icons[type]}</span>
      <div>${esc(msg)}</div>
    </div>`;
  if (type === 'success') noticeTimer = setTimeout(clearNotice, 5000);
}
function clearNotice() {
  clearTimeout(noticeTimer);
  noticeTimer = null;
  $('#notice-area').innerHTML = '';
}

// Centred modal alert with a greyed-out backdrop and an OK button.
function showAlert(message, title = 'Limit reached') {
  const dialog = $('#alert-modal');
  if (!dialog) { alert(message); return; }
  $('#alert-modal-title').textContent = title;
  $('#alert-modal-message').textContent = message;
  const ok = $('#alert-modal-ok');
  ok.onclick = () => dialog.close();
  dialog.onclick = e => { if (e.target === dialog) dialog.close(); };
  if (!dialog.open) dialog.showModal();
  ok.focus();
}

// Centred confirm modal (greyed-out backdrop). Resolves true if confirmed.
function showConfirm(message, { title = 'Confirm deletion', confirmLabel = 'Delete' } = {}) {
  return new Promise(resolve => {
    const dialog = $('#confirm-modal');
    if (!dialog) { resolve(window.confirm(message)); return; }
    $('#confirm-modal-title').textContent = title;
    $('#confirm-modal-message').textContent = message;
    const okBtn = $('#confirm-modal-ok');
    const cancelBtn = $('#confirm-modal-cancel');
    okBtn.textContent = confirmLabel;
    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      dialog.close();
      resolve(result);
    };
    okBtn.onclick = () => finish(true);
    cancelBtn.onclick = () => finish(false);
    dialog.onclick = e => { if (e.target === dialog) finish(false); };
    dialog.oncancel = () => finish(false);   // Esc key
    if (!dialog.open) dialog.showModal();
    cancelBtn.focus();
  });
}

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
  if (currentView === 'theme') captureThemeDraft();
  if (view !== currentView) clearNotice();
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

  // Global delegation for navigation and interactive accordion controls
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-goto]');
    if (el) { e.preventDefault(); setView(el.dataset.goto); return; }

    const acc = e.target.closest('.accordion-header');
    if (acc) {
      if (e.target.closest('button, a, input, select, textarea')) return;
      acc.classList.toggle('open');
      const body = acc.nextElementSibling;
      if (body) body.classList.toggle('open');
      return;
    }

    const evH = e.target.closest('.evidence-item-header');
    if (evH) {
      const p = evH.nextElementSibling;
      if (p) p.style.display = (p.style.display === 'none' || !p.style.display) ? 'block' : 'none';
      return;
    }

    const rubric = e.target.closest('.rubric-option');
    if (rubric && rubric.dataset.value) {
      const parent = rubric.closest('.rubric-selector');
      if (parent && parent.id && typeof window.selectRubric === 'function') {
        window.selectRubric(parent.id, rubric.dataset.value);
      }
      return;
    }
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

  $('#btn-export').onclick = () => run(async () => {
    if (!theme) throw new Error('Approve a theme before exporting');
    const r = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Theme-Lab-View': currentView },
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
let _activeThemeId = null;
let _themeEditorKey = 'new';
const themeDrafts = new Map();

function captureThemeDraft() {
  const form = document.querySelector('#theme-form');
  const taxonomy = document.querySelector('#taxonomy-json-area');
  if (currentView !== 'theme' || !form || !taxonomy) return;
  themeDrafts.set(_themeEditorKey, {
    fields: Object.fromEntries(new FormData(form)),
    taxonomyJson: taxonomy.value
  });
}

// Renders the "Saved themes" card: a flat list of distinct themes. Clicking a
// row loads that theme's definition + taxonomy into the editor below.
function savedThemesListHtml(savedThemes, activeTheme) {
  if (!savedThemes.length) {
    return `
      <div class="panel saved-themes-panel">
        <div class="panel-title">Saved themes</div>
        <div class="panel-subtitle">No themes saved yet. Define a theme below and save it to build your reusable library.</div>
      </div>`;
  }
  return `
    <div class="panel saved-themes-panel">
      <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <span>Saved themes <span class="badge badge-neutral">${savedThemes.length}</span></span>
      </div>
      <div class="panel-subtitle">Click a theme to load its definition and sub-theme taxonomy into the editor.</div>
      <ul class="saved-theme-list">
        ${savedThemes.slice().reverse().map(t => {
          const t1 = (t.nodes || []).filter(n => !n.parent_id).length;
          const t2 = (t.nodes || []).filter(n => n.parent_id).length;
          const isActive = activeTheme && t.id === activeTheme.id;
          return `
            <li class="saved-theme-row ${isActive ? 'is-active' : ''}">
              <button type="button" class="saved-theme-main" data-load-theme="${esc(t.id)}" title="Load this theme into the editor">
                <span class="saved-theme-name">${esc(t.theme_name || 'Untitled theme')}</span>
                <span class="saved-theme-meta">${t1} Tier-1 · ${t2} Tier-2 activities</span>
              </button>
              <div class="saved-theme-actions">
                ${isActive ? '<span class="badge badge-green">Loaded</span>' : ''}
                <button type="button" class="btn btn-sm btn-ghost saved-theme-del" data-del-theme="${esc(t.id)}" title="Delete this theme">🗑</button>
              </div>
            </li>`;
        }).join('')}
      </ul>
    </div>`;
}

async function viewTheme(overrideThemeId) {
  if (overrideThemeId !== undefined) {
    _activeThemeId = overrideThemeId;
  }
  const savedThemes = (state.themes || []).filter(t => t.approved);
  // `_activeThemeId === ''` means "new blank theme"; otherwise load the selected
  // theme, falling back to the most recently saved one.
  const isNewTheme = _activeThemeId === '';
  const theme = isNewTheme
    ? null
    : (_activeThemeId ? savedThemes.find(t => t.id === _activeThemeId) : null) || savedThemes.at(-1);

  _themeEditorKey = theme?.id || 'new';
  const draft = themeDrafts.get(_themeEditorKey);
  const formValues = { ...theme, ...draft?.fields };

  setPageHeader('STEP 1', 'Theme Definition',
    'Define an investment theme and its sub-theme taxonomy');
  $('#page-actions').innerHTML = `
    <div class="btn-group">
      ${theme ? `<button class="btn btn-sm btn-primary" id="btn-new-theme">+ New theme</button>` : ''}
    </div>
  `;

  const themeFormHtml = `
    <form id="theme-form">
      <div class="form-grid">
        <div class="form-group span-2">
          <label class="field-label">Theme name <span class="required">*</span></label>
          <input type="text" name="theme_name" placeholder="e.g. AI Infrastructure"
            value="${esc(formValues.theme_name || '')}" required>
        </div>
        <div class="form-group span-2">
          <label class="field-label">Theme description
            <span class="optional">(recommended)</span></label>
          <textarea name="theme_description" rows="3"
            placeholder="Describe what this theme captures and its economic significance…">${esc(formValues.theme_description || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Included activities
            <span class="optional">(recommended)</span></label>
          <textarea name="included_activities" rows="3"
            placeholder="Activities and revenue streams that qualify for this theme…">${esc(formValues.included_activities || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Excluded activities
            <span class="optional">(optional)</span></label>
          <textarea name="excluded_activities" rows="3"
            placeholder="Explicitly out-of-scope activities…">${esc(formValues.excluded_activities || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Positive examples
            <span class="optional">(recommended)</span></label>
          <textarea name="positive_examples" rows="2"
            placeholder="e.g. NVIDIA GPU revenue qualifies…">${esc(formValues.positive_examples || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="field-label">Negative examples
            <span class="optional">(optional)</span></label>
          <textarea name="negative_examples" rows="2"
            placeholder="e.g. AWS general cloud revenue does not qualify…">${esc(formValues.negative_examples || '')}</textarea>
        </div>
        <div class="form-group span-2">
          <label class="field-label">Additional information
            <span class="optional">(optional)</span></label>
          <input type="text" name="additional_information"
            value="${esc(formValues.additional_information || '')}"
            placeholder="Any other context for analysts…">
        </div>
      </div>
      <input type="hidden" name="id" value="${esc(formValues.id || '')}">
      <div class="card-footer theme-save-footer">
        <button type="button" class="btn" id="btn-gen-taxonomy" ${!state?.ai_configured ? 'disabled title="Configure LLM_API_KEY to enable"' : ''}>
          ✧ Generate taxonomy with AI
        </button>
      </div>
    </form>`;

  // Taxonomy panel
  let nodes = theme?.nodes || [];
  if (draft) {
    try { const parsed = JSON.parse(draft.taxonomyJson); if (Array.isArray(parsed)) nodes = parsed; } catch {}
  }
  const tier1 = nodes.filter(n => !n.parent_id);
  const taxonomyHtml = buildTaxonomyEditor(nodes);

  $('#page-content').innerHTML = `
    <div class="theme-layout">
      ${savedThemesListHtml(savedThemes, theme)}

      <!-- 1. Theme Scope Card (Top, full width) -->
      <details class="panel theme-scope" open>
        <summary class="panel-title">${theme ? `Edit theme scope` : 'New theme scope'}</summary>
        <div class="panel-subtitle">
          Only <code>theme_name</code> is required. The more detail you provide, the better the AI analysis.
        </div>
        ${themeFormHtml}
      </details>

      <!-- 2. Sub-theme Taxonomy Card (Below Theme Scope, full width) -->
      <div class="panel taxonomy-panel">
        <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div style="display:flex;align-items:center;gap:10px">
            <span>Sub-theme taxonomy</span>
            <span class="badge badge-neutral" id="taxonomy-counts">${tier1.length} Tier-1 · ${nodes.length - tier1.length} Tier-2 activities</span>
          </div>
          <button type="button" class="btn btn-sm btn-secondary" id="btn-map-export">↓ Export mind map</button>
        </div>
        <div class="panel-subtitle">
          Two levels: Theme → Tier-1 (broad area) → Tier-2 (specific activity, classification target). Click any node to edit or delete it, or add new nodes at any level.
        </div>
        ${taxonomyHtml}
        <div class="card-footer theme-save-footer" id="taxonomy-save-actions" ${nodes.length ? '' : 'hidden'}>
          <button type="submit" form="theme-form" class="btn btn-primary" id="btn-save-theme">${theme ? 'Update theme' : 'Save theme'}</button>
        </div>
      </div>
    </div>`;

  if (draft) $('#taxonomy-json-area').value = draft.taxonomyJson;
  $('#theme-form').addEventListener('input', captureThemeDraft);
  $('#btn-map-export').onclick = exportMindmap;
  updateTaxonomyMindmap();
  $('#taxonomy-json-area').addEventListener('input', updateTaxonomyMindmap);
  $('#theme-form [name="theme_name"]').addEventListener('input', updateTaxonomyMindmap);

  // Form submit — edit-in-place when `id` is present, otherwise create new.
  $('#theme-form').onsubmit = e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    const isUpdate = !!fd.id;
    const nodesRaw = $('#taxonomy-json-area')?.value;
    let nodes = [];
    if (nodesRaw) {
      try { nodes = JSON.parse(nodesRaw); }
      catch { notice('Taxonomy JSON is invalid — check the structure', 'error'); return; }
    }
    // Block saving a taxonomy that breaks the tier limits.
    const limitErr = tierLimitError(nodes);
    if (limitErr) { notice(limitErr, 'error'); return; }
    run(async () => {
      const result = await api('theme', { ...fd, nodes });
      themeDrafts.delete(_themeEditorKey);
      _activeThemeId = result.id;
      await refresh();
      const savedMessage = `Theme "${result.theme_name}" ${isUpdate ? 'updated' : 'saved'}`;
      if (result.audit_sync?.state === 'synced') {
        notice(`${savedMessage} and copied to Supabase.`);
      } else {
        const detail = result.audit_sync?.error ? ` (${result.audit_sync.error})` : '';
        notice(`${savedMessage} locally. Supabase audit copy is pending${detail}.`, 'warning');
      }
      render();
    }, 'Saving theme…');
  };

  // Trigger AI generation helper
  const triggerGenerateTaxonomy = () => {
    const formEl = $('#theme-form');
    if (!formEl) return;
    const fd = Object.fromEntries(new FormData(formEl));
    if (!fd.theme_name?.trim()) {
      notice('Please enter a theme name before generating a taxonomy.', 'warn');
      formEl.querySelector('[name="theme_name"]')?.focus();
      return;
    }
    run(async () => {
      const result = await api('generate', { ...fd, tier1_count: 5, tier2_count: 3 });
      const ta = $('#taxonomy-json-area');
      if (ta) ta.value = JSON.stringify(result.nodes, null, 2);
      updateTaxonomyMindmap();
      notice('AI taxonomy proposed. You can now click any node to edit, add, or delete.', 'info');
    }, 'Generating taxonomy with AI…');
  };

  $('#btn-gen-taxonomy')?.addEventListener('click', triggerGenerateTaxonomy);
  // Tier-1 / child "+" add buttons are handled by the #taxonomy-mindmap
  // click delegation (data-add-tier1 / data-add-leaf).


  // Mindmap event delegation (editing nodes, adding activities, empty state triggers)
  const mindmapEl = $('#taxonomy-mindmap');
  if (mindmapEl) {
    mindmapEl.onclick = event => {
      // 1. "+" corner control → AI breakdown popup
      const breakdownBtn = event.target.closest('[data-breakdown-id]');
      if (breakdownBtn) {
        openBreakdownModal(breakdownBtn.dataset.breakdownId);
        return;
      }
      // 2. "−" corner control → delete node + descendants
      const deleteBtn = event.target.closest('[data-delete-id]');
      if (deleteBtn) {
        deleteTaxonomyNodeById(deleteBtn.dataset.deleteId);
        return;
      }
      // 3. Node body click → edit
      const nodeBtn = event.target.closest('[data-mindmap-id]');
      if (nodeBtn) {
        let nodes = [];
        try { nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]'); } catch { return; }
        const node = nodes.find(n => n.node_id === nodeBtn.dataset.mindmapId);
        if (node) openTaxonomyNodeModal('edit', node);
        return;
      }
      // 4. Add child node (on-line "+" button under any node)
      const addLeafBtn = event.target.closest('[data-add-leaf]');
      if (addLeafBtn) {
        const full = childTierFullError(addLeafBtn.dataset.addLeaf);
        if (full) { showAlert(full); return; }
        openTaxonomyNodeModal('add', addLeafBtn.dataset.addLeaf);
        return;
      }
      // 4b. Add Tier-1 area (on-line "+" button next to the theme)
      if (event.target.closest('[data-add-tier1]')) {
        const full = childTierFullError(null);
        if (full) { showAlert(full); return; }
        openTaxonomyNodeModal('add', null);
        return;
      }
      // 5. Empty state buttons
      if (event.target.closest('#btn-empty-add-tier1')) {
        openTaxonomyNodeModal('add', null);
        return;
      }
      if (event.target.closest('#btn-empty-gen-taxonomy')) {
        triggerGenerateTaxonomy();
        return;
      }
    };
  }

  // Saved themes list: load on click, delete on trash button.
  document.querySelectorAll('[data-load-theme]').forEach(btn => {
    btn.onclick = () => {
      const t = savedThemes.find(x => x.id === btn.dataset.loadTheme);
      if (t) {
        captureThemeDraft();
        _activeThemeId = t.id;
        viewTheme();
        notice(`Loaded "${t.theme_name}" into the editor.`, 'info');
      }
    };
  });

  document.querySelectorAll('[data-del-theme]').forEach(btn => {
    btn.onclick = async () => {
      const t = savedThemes.find(x => x.id === btn.dataset.delTheme);
      if (!t) return;
      const ok = await showConfirm(`Delete theme "${t.theme_name}"? This cannot be undone.`,
                                   { title: 'Delete theme' });
      if (!ok) return;
      run(async () => {
        await api('theme/delete', { id: t.id });
        themeDrafts.delete(t.id);
        if (_activeThemeId === t.id) _activeThemeId = null;
        await refresh();
        notice(`Deleted "${t.theme_name}".`, 'info');
        render();
      }, 'Deleting theme…');
    };
  });

  // Modal dialog controls
  $('#node-dialog-close')?.addEventListener('click', () => $('#taxonomy-node-dialog')?.close());
  $('#node-dialog-cancel')?.addEventListener('click', () => $('#taxonomy-node-dialog')?.close());
  $('#node-dialog-save')?.addEventListener('click', saveTaxonomyNodeModal);
  $('#node-dialog-delete')?.addEventListener('click', deleteTaxonomyNodeModal);

  // Close modal when backdrop is clicked
  const dialogEl = $('#taxonomy-node-dialog');
  if (dialogEl) {
    dialogEl.addEventListener('click', e => {
      if (e.target === dialogEl) dialogEl.close();
    });
  }

  // AI breakdown dialog controls are bound in openBreakdownModal (via onclick)
  // so they stay valid across re-renders and don't double-fire.

  $('#btn-new-theme')?.addEventListener('click', () => {
    captureThemeDraft();
    themeDrafts.delete('new');
    _activeThemeId = '';
    viewTheme();
    notice('Started a new theme. Fill in the fields and save.', 'info');
  });
}

function taxonomyMindmap(nodes, themeName) {
  const roots = nodes.filter(n => !n.parent_id);
  if (!roots.length) {
    return `<div class="empty-state">
      <h3>No taxonomy yet</h3>
      <p>Generate a taxonomy with AI or click "+ Add Tier-1 Area" to build your own taxonomy tree.</p>
      <div class="btn-group mt-12" style="justify-content:center;">
        <button type="button" class="btn btn-sm btn-primary" id="btn-empty-add-tier1">+ Add Tier-1 Area</button>
        <button type="button" class="btn btn-sm" id="btn-empty-gen-taxonomy" ${!state?.ai_configured ? 'disabled title="Configure LLM_API_KEY to enable"' : ''}>✧ Generate with AI</button>
      </div>
    </div>`;
  }

  const maxTiers = state?.max_tiers || 3;

  const nodeCard = (node, level, count) => `
    <div class="mm-node mm-node-t${level}">
      <div class="mm-node-ctrls">
        ${level < maxTiers ? `<button type="button" class="mm-ctl mm-ctl-add" data-breakdown-id="${esc(node.node_id)}"
          title="Break this node down further with AI" aria-label="Break down with AI">+</button>` : ''}
        <button type="button" class="mm-ctl mm-ctl-del" data-delete-id="${esc(node.node_id)}"
          title="Delete this node and everything under it" aria-label="Delete node">−</button>
      </div>
      <button type="button" class="mm-node-body" data-mindmap-id="${esc(node.node_id)}" title="Click to edit this Tier-${level} node">
        <span class="mm-tier">Tier ${level}</span>
        <span class="mm-name">${esc(node.name)}</span>
        ${count ? `<span class="mm-count">${count} ${level === 1 ? (count === 1 ? 'activity' : 'activities') : (count === 1 ? 'sub-node' : 'sub-nodes')}</span>` : ''}
        <span class="mm-edit" aria-hidden="true">✎ edit</span>
      </button>
    </div>`;

  // A node row: the card (+ its "add" button) on the left, and a column of
  // child rows on the right. Connectors are rendered as explicit SVG-free
  // boxes keyed by the real child count, so leaves never show stray stubs.
  function renderNode(node, level) {
    const children = nodes.filter(n => n.parent_id === node.node_id);
    const childTier = level + 1;
    const childLabel = `Tier ${childTier} node`;
    const canAddChild = childTier <= maxTiers;
    const hasChildren = children.length > 0;

    // The "+ Add" button lives near the connector split point, to the right of
    // the node. Shown whenever this node can accept children (below max depth).
    const addOnLine = canAddChild
      ? `<button type="button" class="mm-add mm-add-online" data-add-leaf="${esc(node.node_id)}" title="Add a ${childLabel} under ${esc(node.name)}">
           <span aria-hidden="true">+</span> Add ${childLabel}
         </button>`
      : '';

    // Connector is drawn when the node has children (to carry the lines) or
    // when it can accept children (to host the add button).
    const connector = (hasChildren || canAddChild)
      ? `<div class="mm-connector ${hasChildren ? '' : 'is-empty'} ${children.length === 1 ? 'is-single' : ''}">${addOnLine}</div>`
      : '';

    const childCol = hasChildren
      ? `<div class="mm-children">${children.map(c => renderNode(c, childTier)).join('')}</div>`
      : '';

    return `<div class="mm-row ${hasChildren ? 'has-children' : 'is-leaf'}">
      <div class="mm-cell">
        ${nodeCard(node, level, children.length)}
      </div>
      ${connector}
      ${childCol}
    </div>`;
  }

  const rootChildrenHtml = roots.length
    ? `<div class="mm-connector ${roots.length === 1 ? 'is-single' : ''}">
         <button type="button" class="mm-add mm-add-online" id="btn-add-tier1-line" data-add-tier1="1" title="Add a Tier 1 node"><span aria-hidden="true">+</span> Add Tier 1 node</button>
       </div>
       <div class="mm-children mm-children-root">${roots.map(r => renderNode(r, 1)).join('')}</div>`
    : '';

  return `<div class="mm" role="region" aria-label="Theme taxonomy mind map">
    <div class="mm-row has-children">
      <div class="mm-cell">
        <div class="mm-theme">
          <span class="mm-tier">Theme</span>
          <span class="mm-name">${esc(themeName || 'Investment theme')}</span>
        </div>
      </div>
      ${rootChildrenHtml}
    </div>
  </div>`;
}

function buildTaxonomyEditor(nodes) {
  return `<div id="taxonomy-mindmap"></div>
    <details class="form-section mt-16">
      <summary class="field-label" style="cursor:pointer;font-weight:600;">Advanced: Edit taxonomy JSON directly</summary>
      <textarea class="code" id="taxonomy-json-area" rows="10" placeholder="Enter a JSON array of taxonomy nodes">${esc(JSON.stringify(nodes, null, 2))}</textarea>
      <div id="taxonomy-preview-error" class="field-hint" role="status"></div>
      <div class="field-hint">Tier-1 nodes use parent_id: null; Tier-2 nodes reference a Tier-1 node_id. The mindmap updates live as you edit.</div>
    </details>
    <dialog id="taxonomy-node-dialog" class="mindmap-dialog">
      <div class="mindmap-dialog-header">
        <div style="display:flex;align-items:center;gap:10px;">
          <span id="node-dialog-badge" class="mindmap-level">TIER 1</span>
          <h3 id="node-dialog-title" style="margin:0;font-size:16px;font-weight:600;">Edit Node</h3>
        </div>
        <button type="button" class="btn btn-sm btn-ghost" id="node-dialog-close" aria-label="Close dialog" style="font-size:14px;padding:4px 8px;">✕</button>
      </div>
      <form id="taxonomy-node-form" onsubmit="return false;" style="margin-top:16px;">
        <input type="hidden" id="node-dialog-mode" value="edit">
        <input type="hidden" id="node-dialog-orig-id" value="">
        
        <div class="form-group" id="node-dialog-parent-group" style="margin-bottom:14px;">
          <label class="field-label">Parent Tier-1 Area <span class="required">*</span></label>
          <select id="node-dialog-parent" class="form-select" style="width:100%;padding:8px 10px;border-radius:var(--radius-md);border:1px solid var(--slate-200);background:white;font:inherit;font-size:13px;"></select>
        </div>

        <div class="form-group" style="margin-bottom:14px;">
          <label class="field-label">Node name <span class="required">*</span></label>
          <input type="text" id="node-dialog-name" required placeholder="e.g. AI Chip Fabrication" style="width:100%;box-sizing:border-box;">
        </div>

        <div class="form-group" style="margin-bottom:14px;">
          <label class="field-label">Definition <span class="required">*</span></label>
          <textarea id="node-dialog-def" rows="3" required placeholder="Describe what this node represents and what qualifies..." style="width:100%;box-sizing:border-box;"></textarea>
        </div>

        <div class="form-group" style="margin-bottom:14px;">
          <label class="field-label">Examples <span class="optional">(comma-separated)</span></label>
          <input type="text" id="node-dialog-examples" placeholder="e.g. Foundry services, advanced packaging" style="width:100%;box-sizing:border-box;">
          <div class="field-hint" style="margin-top:4px;">Comma-separated list of companies, products, or technologies.</div>
        </div>

        <!-- Node ID is auto-generated from the name; kept hidden so the save
             logic still has an element to read/write. -->
        <input type="hidden" id="node-dialog-id">

        <div id="node-dialog-error" class="field-hint" style="color:var(--red-600);font-weight:500;margin-bottom:12px;" role="alert"></div>

        <div class="mindmap-dialog-footer">
          <button type="button" class="btn btn-danger btn-sm" id="node-dialog-delete">Delete</button>
          <div style="display:flex;gap:8px;">
            <button type="button" class="btn btn-sm" id="node-dialog-cancel">Cancel</button>
            <button type="button" class="btn btn-primary btn-sm" id="node-dialog-save">Save changes</button>
          </div>
        </div>
      </form>
    </dialog>
    <dialog id="breakdown-dialog" class="mindmap-dialog mindmap-dialog-wide">
      <div class="mindmap-dialog-header">
        <div style="display:flex;align-items:center;gap:10px;">
          <span class="mindmap-level badge-teal">✧ AI</span>
          <h3 style="margin:0;font-size:18px;font-weight:600;">Break this node down further</h3>
        </div>
        <button type="button" class="btn btn-sm btn-ghost" id="breakdown-close" aria-label="Close dialog" style="font-size:16px;padding:4px 10px;">✕</button>
      </div>
      <div style="margin-top:16px;">
        <p class="text-muted" style="margin:0 0 14px;font-size:14px;line-height:1.55;">
          AI suggests finer-grained sub-nodes under <strong id="breakdown-node-name"></strong>. Review and edit any suggestion, then click <strong>+ Add</strong> on the ones you want. Existing sub-nodes are kept — you're only adding.
        </p>
        <div class="form-group" style="margin-bottom:12px;">
          <label class="field-label">How should it be broken down? <span class="optional">(optional guidance)</span></label>
          <textarea id="breakdown-instructions" rows="2" placeholder="e.g. Focus on hardware vs. software sub-categories, keep it to 4 activities" style="width:100%;box-sizing:border-box;"></textarea>
        </div>
        <div id="breakdown-error" class="field-hint" style="color:var(--red-600);font-weight:500;margin-bottom:10px;" role="alert"></div>
        <div id="breakdown-suggestions" class="breakdown-suggestions" style="display:none;"></div>
      </div>
      <div class="mindmap-dialog-footer" style="justify-content:space-between;gap:8px;">
        <span id="breakdown-added-note" class="text-small text-muted"></span>
        <div style="display:flex;gap:8px;">
          <button type="button" class="btn btn-sm btn-ghost" id="breakdown-discard" title="Discard and close">Cancel</button>
          <button type="button" class="btn btn-sm btn-primary" id="breakdown-cancel">Done</button>
          <button type="button" class="btn btn-sm" id="breakdown-confirm" ${!state?.ai_configured ? 'disabled title="Configure LLM_API_KEY to enable"' : ''}>✧ Get AI suggestions</button>
        </div>
      </div>
    </dialog>`;
}

function updateTaxonomyMindmap() {
  captureThemeDraft();
  const target = $('#taxonomy-mindmap');
  if (!target) return;
  const error = $('#taxonomy-preview-error');
  try {
    const raw = $('#taxonomy-json-area')?.value || '[]';
    const nodes = JSON.parse(raw);
    if (!Array.isArray(nodes) || nodes.some(n => !n || typeof n !== 'object' || Array.isArray(n))) throw new Error();
    
    // Update count badge in panel header
    const tier1 = nodes.filter(n => !n.parent_id);
    const tier2 = nodes.filter(n => n.parent_id);
    const countsEl = $('#taxonomy-counts');
    if (countsEl) {
      countsEl.textContent = `${tier1.length} Tier-1 · ${tier2.length} Tier-2 activities`;
    }

    const saveActions = $('#taxonomy-save-actions');
    if (saveActions) saveActions.hidden = nodes.length === 0;
    target.innerHTML = taxonomyMindmap(nodes, $('#theme-form [name="theme_name"]')?.value || '');
    if (error) error.textContent = '';
  } catch {
    if (error) error.textContent = 'Enter a valid JSON array of nodes to update the map. The last valid preview is retained.';
  }
}

function openTaxonomyNodeModal(mode, nodeOrParentId) {
  const dialog = $('#taxonomy-node-dialog');
  if (!dialog) return;

  const errorEl = $('#node-dialog-error');
  if (errorEl) errorEl.textContent = '';

  let nodes = [];
  try {
    nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]');
  } catch {
    notice('Taxonomy JSON is currently invalid. Please fix JSON syntax first.', 'error');
    return;
  }

  const maxTiers = state?.max_tiers || 3;
  const tier1Nodes = nodes.filter(n => !n.parent_id);
  const parentSelect = $('#node-dialog-parent');
  const parentGroup = $('#node-dialog-parent-group');
  const deleteBtn = $('#node-dialog-delete');
  const badgeEl = $('#node-dialog-badge');
  const titleEl = $('#node-dialog-title');
  const saveBtn = $('#node-dialog-save');

  // Any node shallow enough to accept a child can be a parent (depth < maxTiers).
  // A child of a depth-d node sits at tier d+1.
  const parentCandidates = nodes.filter(n => nodeDepthOf(n.node_id, nodes) < maxTiers);
  parentSelect.innerHTML = parentCandidates
    .map(p => `<option value="${esc(p.node_id)}">${'— '.repeat(Math.max(0, nodeDepthOf(p.node_id, nodes) - 1))}${esc(p.name)} (Tier-${nodeDepthOf(p.node_id, nodes)})</option>`)
    .join('');

  if (mode === 'edit') {
    const node = nodeOrParentId;
    const isTier1 = !node.parent_id;
    const tier = nodeDepthOf(node.node_id, nodes);
    $('#node-dialog-mode').value = 'edit';
    $('#node-dialog-orig-id').value = node.node_id;
    badgeEl.textContent = `TIER ${tier}`;
    badgeEl.className = isTier1 ? 'mindmap-level badge-teal' : 'mindmap-level badge-neutral';
    titleEl.textContent = `Edit Tier-${tier} node`;
    $('#node-dialog-name').value = node.name || '';
    $('#node-dialog-def').value = node.definition || '';
    $('#node-dialog-examples').value = Array.isArray(node.examples) ? node.examples.join(', ') : (node.examples || '');
    $('#node-dialog-id').value = node.node_id || '';
    $('#node-dialog-id').disabled = true;

    if (isTier1) {
      parentGroup.style.display = 'none';
    } else {
      parentGroup.style.display = 'block';
      parentSelect.value = node.parent_id || (parentCandidates[0]?.node_id || '');
    }

    deleteBtn.style.display = 'inline-flex';
    deleteBtn.textContent = 'Delete node';
    saveBtn.textContent = 'Save changes';
  } else if (mode === 'add') {
    const parentId = typeof nodeOrParentId === 'string' ? nodeOrParentId : null;
    const isTier1 = !parentId;
    const tier = parentId ? nodeDepthOf(parentId, nodes) + 1 : 1;
    $('#node-dialog-mode').value = 'add';
    $('#node-dialog-orig-id').value = '';
    badgeEl.textContent = `NEW TIER ${tier}`;
    badgeEl.className = isTier1 ? 'mindmap-level badge-teal' : 'mindmap-level badge-neutral';
    titleEl.textContent = `Add Tier-${tier} node`;
    $('#node-dialog-name').value = '';
    $('#node-dialog-def').value = '';
    $('#node-dialog-examples').value = '';
    $('#node-dialog-id').value = '';
    $('#node-dialog-id').disabled = false;

    if (isTier1) {
      parentGroup.style.display = 'none';
    } else {
      parentGroup.style.display = 'block';
      if (parentId) {
        parentSelect.value = parentId;
      } else if (parentCandidates[0]) {
        parentSelect.value = parentCandidates[0].node_id;
      }
    }

    deleteBtn.style.display = 'none';
    saveBtn.textContent = isTier1 ? 'Add Tier-1 Area' : `Add Tier-${tier} node`;
  }

  dialog.showModal();
}

function saveTaxonomyNodeModal() {
  const mode = $('#node-dialog-mode').value;
  const origId = $('#node-dialog-orig-id').value;
  const name = $('#node-dialog-name').value.trim();
  const def = $('#node-dialog-def').value.trim();
  const rawExamples = $('#node-dialog-examples').value;
  const examples = rawExamples
    .split(',')
    .map(s => s.trim())
    .filter(Boolean);

  const errorEl = $('#node-dialog-error');

  if (!name) {
    if (errorEl) errorEl.textContent = 'Node name is required.';
    return;
  }
  const definition = def || `Activities and businesses related to ${name}.`;

  let nodes = [];
  try {
    nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]');
  } catch {
    if (errorEl) errorEl.textContent = 'Invalid JSON in editor. Cannot save.';
    return;
  }

  if (mode === 'edit') {
    const targetNode = nodes.find(n => n.node_id === origId);
    if (!targetNode) {
      if (errorEl) errorEl.textContent = 'Node not found in taxonomy.';
      return;
    }
    targetNode.name = name;
    targetNode.definition = definition;
    targetNode.examples = examples;

    // If Tier-2, parent might have been modified
    if (targetNode.parent_id) {
      const parentVal = $('#node-dialog-parent').value;
      if (parentVal) {
        targetNode.parent_id = parentVal;
      }
    }
    notice(`Updated "${name}".`, 'info');
  } else if (mode === 'add') {
    const isTier1 = $('#node-dialog-parent-group').style.display === 'none';
    const parentId = isTier1 ? null : $('#node-dialog-parent').value;

    let customId = $('#node-dialog-id').value.trim();
    if (!customId) {
      const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || (isTier1 ? 'tier1' : 'tier2');
      customId = isTier1 ? slug : `${parentId}_${slug}`;
    }
    // Ensure uniqueness
    let finalId = customId;
    let counter = 1;
    while (nodes.some(n => n.node_id === finalId)) {
      counter++;
      finalId = `${customId}_${counter}`;
    }

    const newNode = {
      node_id: finalId,
      parent_id: parentId,
      name,
      definition,
      examples,
    };
    nodes.push(newNode);
  }

  // Enforce tier limits before committing the change.
  const limitErr = tierLimitError(nodes);
  if (limitErr) {
    if (errorEl) errorEl.textContent = limitErr;
    notice(limitErr, 'error');
    return;
  }

  if (mode === 'add') notice(`Added "${name}".`, 'info');

  const ta = $('#taxonomy-json-area');
  if (ta) ta.value = JSON.stringify(nodes, null, 2);
  updateTaxonomyMindmap();
  $('#taxonomy-node-dialog')?.close();
}

function deleteTaxonomyNodeModal() {
  const origId = $('#node-dialog-orig-id').value;
  if (!origId) return;
  // Close the edit dialog first, then run the shared modal-confirm delete.
  $('#taxonomy-node-dialog')?.close();
  deleteTaxonomyNodeById(origId);
}

// Collect a node and all of its descendant ids.
function descendantIds(nodeId, nodes) {
  const ids = new Set([nodeId]);
  let added = true;
  while (added) {
    added = false;
    for (const n of nodes) {
      if (n.parent_id && ids.has(n.parent_id) && !ids.has(n.node_id)) {
        ids.add(n.node_id); added = true;
      }
    }
  }
  return ids;
}

// Delete a node (and all its descendants) directly from the mindmap "−" control.
async function deleteTaxonomyNodeById(nodeId) {
  if (!nodeId) return;
  let nodes = [];
  try { nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]'); }
  catch { showAlert('The taxonomy JSON is invalid, so this node cannot be deleted.', 'Cannot delete'); return; }

  const target = nodes.find(n => n.node_id === nodeId);
  if (!target) return;

  const tier = nodeDepthOf(nodeId, nodes);
  const toRemove = descendantIds(nodeId, nodes);
  const descCount = toRemove.size - 1;
  const msg = descCount
    ? `Delete Tier-${tier} node "${target.name}" and its ${descCount} sub-node${descCount === 1 ? '' : 's'}? This cannot be undone.`
    : `Delete Tier-${tier} node "${target.name}"? This cannot be undone.`;

  const ok = await showConfirm(msg);
  if (!ok) return;

  nodes = nodes.filter(n => !toRemove.has(n.node_id));

  const ta = $('#taxonomy-json-area');
  if (ta) ta.value = JSON.stringify(nodes, null, 2);
  updateTaxonomyMindmap();
  notice(`Deleted "${target.name}".`, 'info');
}

// "+" control → open the AI-breakdown popup for a specific node.
// The taxonomy is strictly two tiers, so "break down" always targets a Tier-1
// area and (re)generates its Tier-2 activities. Clicking "+" on a Tier-2
// activity targets its Tier-1 parent.
let _breakdownNodeId = null;    // node the breakdown targets (children added here)
let _breakdownSuggestions = []; // current AI suggestions being reviewed
let _breakdownStaged = [];      // picks marked to add; committed only on "Done"

// Depth of a node (1 = Tier-1) by walking parent_id within a node list.
function nodeDepthOf(nodeId, nodes) {
  const byId = Object.fromEntries(nodes.map(n => [n.node_id, n]));
  let depth = 0, cur = nodeId, seen = new Set();
  while (cur && byId[cur] && !seen.has(cur)) {
    seen.add(cur); depth += 1; cur = byId[cur].parent_id;
  }
  return depth;
}

// The per-tier sub-node caps. Mirrors engine.MAX_CHILDREN on the backend.
function tierCaps() {
  const c = state?.max_children || { 1: 5, 2: 3, 3: 4 };
  return { 1: c[1] ?? c['1'] ?? 5, 2: c[2] ?? c['2'] ?? 3, 3: c[3] ?? c['3'] ?? 4 };
}

// Check, before opening the add dialog, whether adding a child under `parentId`
// (null = add a Tier-1 area) would exceed that tier's cap. Returns an error
// message string if full, or null if there's room.
function childTierFullError(parentId) {
  let nodes = [];
  try { nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]'); } catch { return null; }
  const caps = tierCaps();
  if (!parentId) {
    const tier1 = nodes.filter(n => !n.parent_id).length;
    if (tier1 >= caps[1]) return `You can only have ${caps[1]} Tier-1 areas.`;
    return null;
  }
  const childTier = nodeDepthOf(parentId, nodes) + 1;
  const cap = caps[childTier];
  const count = nodes.filter(n => n.parent_id === parentId).length;
  if (cap && count >= cap) {
    return `You can only have ${cap} Tier-${childTier} nodes here.`;
  }
  return null;
}

// Validate a complete node list against the tier limits. Returns an error
// string if any limit is exceeded, or null if the taxonomy is within limits.
function tierLimitError(nodes) {
  const caps = tierCaps();
  const maxTiers = state?.max_tiers || 3;

  // Tier-1 (top-level) count.
  const tier1 = nodes.filter(n => !n.parent_id).length;
  if (tier1 > caps[1]) {
    return `You can only have ${caps[1]} Tier-1 areas.`;
  }
  // Children per parent, capped by the child's tier.
  const childCounts = {};
  for (const n of nodes) {
    if (n.parent_id) childCounts[n.parent_id] = (childCounts[n.parent_id] || 0) + 1;
  }
  const byId = Object.fromEntries(nodes.map(n => [n.node_id, n]));
  for (const [pid, count] of Object.entries(childCounts)) {
    const parent = byId[pid];
    const childTier = nodeDepthOf(pid, nodes) + 1;
    if (childTier > maxTiers) {
      return `Nodes can't go deeper than Tier-${maxTiers}.`;
    }
    const cap = caps[childTier];
    if (cap && count > cap) {
      const label = parent ? `"${parent.name}"` : 'this node';
      return `A Tier-${childTier - 1} node can have at most ${cap} Tier-${childTier} sub-nodes. ${label} now has ${count}.`;
    }
  }
  return null;
}

function openBreakdownModal(nodeId) {
  const dialog = $('#breakdown-dialog');
  if (!dialog) return;
  let nodes = [];
  try { nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]'); }
  catch { notice('Taxonomy JSON is currently invalid. Fix it first.', 'error'); return; }

  const target = nodes.find(n => n.node_id === nodeId);
  if (!target) return;

  // Guard: a node already at the deepest allowed level can't be broken down.
  const maxTiers = state?.max_tiers || 3;
  if (nodeDepthOf(nodeId, nodes) >= maxTiers) {
    notice(`This node is already at the deepest level (Tier-${maxTiers}).`, 'info');
    return;
  }

  _breakdownNodeId = nodeId;
  _breakdownSuggestions = [];
  _breakdownStaged = [];
  $('#breakdown-node-name').textContent = target.name || target.node_id;
  $('#breakdown-instructions').value = '';
  const err = $('#breakdown-error'); if (err) err.textContent = '';
  const added = $('#breakdown-added-note'); if (added) added.textContent = '';
  const sug = $('#breakdown-suggestions');
  if (sug) { sug.style.display = 'none'; sug.innerHTML = ''; }
  const confirmBtn = $('#breakdown-confirm');
  if (confirmBtn) {
    confirmBtn.textContent = '✧ Get AI suggestions';
    // Ensure the button is interactive (it may have rendered disabled if the
    // dialog HTML was built before AI config loaded into state).
    confirmBtn.disabled = !state?.ai_configured;
    // Bind directly every time the modal opens so the handler can never be
    // stale (e.g. after the theme view re-renders the dialog element).
    confirmBtn.onclick = confirmBreakdown;
  }
  // "Done" commits the staged picks to the taxonomy, then closes. The X and
  // "Cancel" discard the staged picks without changing the taxonomy.
  const closeBtn = $('#breakdown-close'); if (closeBtn) closeBtn.onclick = () => cancelBreakdown();
  const discardBtn = $('#breakdown-discard'); if (discardBtn) discardBtn.onclick = () => cancelBreakdown();
  const cancelBtn = $('#breakdown-cancel'); if (cancelBtn) cancelBtn.onclick = () => commitBreakdown();
  dialog.showModal();
}

// X button: discard staged picks and close without changing the taxonomy.
function cancelBreakdown() {
  _breakdownStaged = [];
  $('#breakdown-dialog')?.close();
}

// Done button: write all staged suggestions into the taxonomy at once.
function commitBreakdown() {
  const dialog = $('#breakdown-dialog');
  if (!_breakdownStaged.length) { dialog?.close(); return; }

  let nodes = [];
  try { nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]'); }
  catch { notice('Taxonomy JSON is invalid; cannot add.', 'error'); return; }

  const used = new Set(nodes.map(n => n.node_id));
  const additions = _breakdownStaged.map(s => {
    let base = (s.name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '')) || 'node';
    let id = base, i = 1;
    while (used.has(id)) { i += 1; id = `${base}_${i}`; }
    used.add(id);
    return {
      node_id: id,
      parent_id: _breakdownNodeId,
      name: s.name.trim(),
      definition: (s.definition || '').trim(),
      examples: Array.isArray(s.examples) ? s.examples : [],
    };
  });

  const candidate = nodes.concat(additions);
  const limitErr = tierLimitError(candidate);
  if (limitErr) {
    const errEl = $('#breakdown-error');
    if (errEl) errEl.textContent = limitErr;
    notice(limitErr, 'error');
    return;
  }

  const ta = $('#taxonomy-json-area');
  if (ta) ta.value = JSON.stringify(candidate, null, 2);
  updateTaxonomyMindmap();
  const n = additions.length;
  _breakdownStaged = [];
  dialog?.close();
  notice(`Added ${n} node${n === 1 ? '' : 's'}.`, 'info');
}

// Fetch AI suggestions (preview only — nothing is added to the taxonomy yet).
// Runs self-contained so it works while the modal dialog is in the top layer
// (the global loading overlay sits behind the modal and is inert).
async function confirmBreakdown() {
  const nodeId = _breakdownNodeId;
  if (!nodeId) return;
  const errEl = $('#breakdown-error');
  const confirmBtn = $('#breakdown-confirm');
  if (errEl) errEl.textContent = '';

  const formEl = $('#theme-form');
  const fd = formEl ? Object.fromEntries(new FormData(formEl)) : {};
  if (!fd.theme_name?.trim()) {
    if (errEl) errEl.textContent = 'Enter a theme name on the form before using AI.';
    return;
  }

  let existing = [];
  try { existing = JSON.parse($('#taxonomy-json-area')?.value || '[]'); }
  catch { if (errEl) errEl.textContent = 'Taxonomy JSON is invalid.'; return; }

  const instructions = $('#breakdown-instructions')?.value?.trim() || '';

  // Disable + show progress on the button itself (visible above the modal).
  const originalLabel = confirmBtn ? confirmBtn.textContent : '';
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = '✧ Thinking…'; }
  try {
    const result = await api('generate', {
      ...fd,
      node_id: nodeId,
      existing_nodes: existing,
      instructions,
      preview: true,
      tier1_count: 5,
      tier2_count: 3,
      // Fresh value each click so the backend LLM cache doesn't return the
      // identical previous suggestions when "Regenerate" is pressed.
      nonce: Date.now(),
    });
    _breakdownSuggestions = (result.suggestions || []).map((s, i) => ({ ...s, _key: i }));
    renderBreakdownSuggestions();
    if (confirmBtn) confirmBtn.textContent = '✧ Regenerate suggestions';
  } catch (e) {
    if (errEl) errEl.textContent = e.message || 'Failed to get suggestions.';
    if (confirmBtn) confirmBtn.textContent = originalLabel || '✧ Get AI suggestions';
  } finally {
    if (confirmBtn) confirmBtn.disabled = false;
  }
}

// Render the editable suggestion cards, each with its own "+ Add" button.
function renderBreakdownSuggestions() {
  const wrap = $('#breakdown-suggestions');
  if (!wrap) return;
  if (!_breakdownSuggestions.length) {
    wrap.style.display = 'block';
    wrap.innerHTML = '<p class="text-small text-muted">No suggestions returned. Try adjusting the guidance and regenerate.</p>';
    return;
  }
  wrap.style.display = 'block';
  wrap.innerHTML = _breakdownSuggestions.map(s => `
    <div class="breakdown-card ${s._added ? 'is-added' : ''}" data-key="${s._key}">
      <div class="breakdown-card-fields">
        <div class="breakdown-field-group">
          <label class="breakdown-field-label">Node name</label>
          <input type="text" class="breakdown-field-name" data-key="${s._key}" value="${esc(s.name || '')}" placeholder="Node name" ${s._added ? 'disabled' : ''}>
        </div>
        <div class="breakdown-field-group">
          <label class="breakdown-field-label">Definition</label>
          <textarea class="breakdown-field-def" data-key="${s._key}" rows="4" placeholder="What this node covers" ${s._added ? 'disabled' : ''}>${esc(s.definition || '')}</textarea>
        </div>
        <div class="breakdown-field-group">
          <label class="breakdown-field-label">Examples <span class="breakdown-field-hint">(comma-separated)</span></label>
          <input type="text" class="breakdown-field-examples" data-key="${s._key}" value="${esc((s.examples || []).join(', '))}" placeholder="e.g. Company A, Company B" ${s._added ? 'disabled' : ''}>
        </div>
      </div>
      <button type="button" class="btn btn-sm ${s._added ? 'btn-danger breakdown-remove-one' : 'btn-primary breakdown-add-one'}" data-key="${s._key}"
        title="${s._added ? 'Click to remove this from the list' : 'Add this node'}">
        ${s._added ? '✕ Remove' : '+ Add'}
      </button>
    </div>`).join('');

  // Keep edits in sync with the staged suggestion objects.
  wrap.querySelectorAll('.breakdown-field-name').forEach(el =>
    el.oninput = () => { const s = _breakdownSuggestions.find(x => x._key == el.dataset.key); if (s) s.name = el.value; });
  wrap.querySelectorAll('.breakdown-field-def').forEach(el =>
    el.oninput = () => { const s = _breakdownSuggestions.find(x => x._key == el.dataset.key); if (s) s.definition = el.value; });
  wrap.querySelectorAll('.breakdown-field-examples').forEach(el =>
    el.oninput = () => { const s = _breakdownSuggestions.find(x => x._key == el.dataset.key); if (s) s.examples = el.value.split(',').map(x => x.trim()).filter(Boolean); });
  wrap.querySelectorAll('.breakdown-add-one').forEach(el =>
    el.onclick = () => addBreakdownSuggestion(el.dataset.key));
  wrap.querySelectorAll('.breakdown-remove-one').forEach(el =>
    el.onclick = () => removeBreakdownSuggestion(el.dataset.key));
}

// STAGE one reviewed suggestion. Nothing is written to the taxonomy here — the
// staged picks are committed together when the user clicks "Done".
function addBreakdownSuggestion(key) {
  const s = _breakdownSuggestions.find(x => x._key == key);
  if (!s || s._added) return;
  if (!s.name?.trim()) { notice('Give the node a name before adding it.', 'error'); return; }

  let nodes = [];
  try { nodes = JSON.parse($('#taxonomy-json-area')?.value || '[]'); }
  catch { notice('Taxonomy JSON is invalid; cannot add.', 'error'); return; }

  // Cap check counts what's already on the node PLUS everything staged so far.
  const caps = tierCaps();
  const childTier = nodeDepthOf(_breakdownNodeId, nodes) + 1;
  const cap = caps[childTier];
  const existing = nodes.filter(n => n.parent_id === _breakdownNodeId).length;
  if (cap && existing + _breakdownStaged.length >= cap) {
    const msg = `You can only have ${cap} Tier-${childTier} sub-nodes here.`;
    const errEl = $('#breakdown-error');
    if (errEl) errEl.textContent = msg;
    notice(msg, 'error');
    return;
  }

  // Mark as staged (not yet committed). Keep the _key so it can be un-staged.
  s._added = true;
  _breakdownStaged.push({ _key: s._key, name: s.name.trim(),
                          definition: (s.definition || '').trim(),
                          examples: Array.isArray(s.examples) ? s.examples : [] });
  renderBreakdownSuggestions();
  updateBreakdownStagedNote();
  const err = $('#breakdown-error'); if (err) err.textContent = '';
}

// UN-STAGE a previously added suggestion so it won't be committed.
function removeBreakdownSuggestion(key) {
  const s = _breakdownSuggestions.find(x => x._key == key);
  if (s) s._added = false;
  _breakdownStaged = _breakdownStaged.filter(x => x._key != key);
  renderBreakdownSuggestions();
  updateBreakdownStagedNote();
  const err = $('#breakdown-error'); if (err) err.textContent = '';
}

function updateBreakdownStagedNote() {
  const note = $('#breakdown-added-note');
  if (!note) return;
  const n = _breakdownStaged.length;
  note.textContent = n ? `${n} node${n === 1 ? '' : 's'} ready — click Done to add` : '';
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

  // Resume the current preview or show the saved company universe for testing.
  const hasStaged = _stagingRows && _stagingRows.length > 0;
  $('#page-actions').innerHTML = companies.length
    ? '<button class="btn btn-sm" id="btn-view-universe">View company universe</button>'
    : '';
  $('#btn-view-universe')?.addEventListener('click', () => {
    $('#page-actions').innerHTML = '';
    renderImportedView(companies);
  });

  if (hasStaged) {
    renderEditTable(_stagingRows);
  } else if (companies.length) {
    renderImportedView(companies);
  } else {
    renderUploadZone();
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
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <div>
            <div class="panel-title">Expected format</div>
            <div class="panel-subtitle">Matches the Schroders sample Excel template.</div>
          </div>
          <button type="button" class="btn btn-sm btn-secondary" id="btn-portfolio-template">↓ Download Excel template</button>
        </div>
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

  $('#btn-portfolio-template').onclick = downloadPortfolioTemplate;
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
      viewPortfolio();
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
      const res = await api('portfolio/import', {
        name: name,
        holdings_date: new Date().toISOString().slice(0, 10),
        benchmark_name: 'MSCI Asia ex Japan',
        rows: clean
      });
      await refresh();
      notice(`"${name}" imported — ${clean.length} companies registered and snapshot created. Dashboard is ready.`);
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
    const sedolText = c.sedol ? ` · SEDOL: ${c.sedol}` : '';
    return `<label class="company-search-item" data-cid="${esc(c.id)}" data-name="${esc(c.name.toLowerCase())}" data-ticker="${esc((c.bloomberg_ticker||'').toLowerCase())}" data-sedol="${esc((c.sedol||'').toLowerCase())}" style="display:flex;align-items:center;gap:10px;padding:10px;border-radius:var(--radius-sm);cursor:pointer;border:1px solid var(--slate-100);margin-bottom:6px">
      <input type="checkbox" name="company_ids" value="${esc(c.id)}" checked style="width:auto">
      <div class="flex-1">
        <div style="font-weight:600;font-size:13px">${esc(c.name)}</div>
        <div class="text-muted text-small">${esc(c.bloomberg_ticker||c.id)}${esc(sedolText)}</div>
      </div>
      ${co.length > 0 ? badge(co.length+' docs','green') : badge('No docs','amber')}
    </label>`;
  }).join('');

  const latestSearchRun = (state.search_runs && state.search_runs.length > 0)
    ? state.search_runs[state.search_runs.length - 1]
    : null;
  const candidateResults = latestSearchRun?.results || [];

  $('#page-content').innerHTML = `
    <div class="grid-1-2" style="align-items:start">

      <!-- Search config -->
      <div class="panel">
        <div class="panel-title">Document search settings</div>
        <div class="panel-subtitle">
          Searches your uploaded company documents. Upload real PDF or TXT filings below; automated web retrieval is unavailable.
        </div>

        <div class="form-section-title">Companies to search</div>
        <div class="form-group mb-8">
          <label class="field-label">Search universe by SEDOL (London Stock Exchange), Ticker or Name</label>
          <div style="display:flex;gap:8px">
            <input type="text" id="src-global-search" placeholder="Type SEDOL (e.g. 6889106), ticker, or name..." style="flex:1">
            <select id="src-sedol-select" style="max-width:220px">
              <option value="">— Select by SEDOL —</option>
              ${companies.filter(c=>c.sedol).map(c=>`<option value="${esc(c.id)}">${esc(c.sedol)} · ${esc(c.name)}</option>`).join('')}
            </select>
          </div>
        </div>
        <div id="company-checklist" style="max-height:260px;overflow-y:auto;margin-bottom:16px">
          ${companies.length > 0 ? companyList : `<div class="text-muted text-small">No companies registered. <a href="#" data-goto="portfolio">Import a portfolio first.</a></div>`}
        </div>

        <div class="form-section">
          <div style="margin-bottom:12px;padding:10px 12px;background:var(--teal-50);border-radius:6px;border:1px solid var(--teal-200)">
            <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--teal-900);cursor:pointer">
              <input type="checkbox" id="src-stick-earnings-call" style="width:auto">
              <span>Stick to latest earnings call document</span>
            </label>
            <div class="text-muted text-small" style="margin-left:22px;margin-top:2px">
              Restricts source discovery to the latest quarterly earnings conference call transcript for direct management commentary.
            </div>
          </div>

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
            ✦ Search uploaded documents
          </button>
          <span class="text-muted text-small">
            Filters uploaded documents by company, source type and cutoff.
          </span>
        </div>
      </div>

      <!-- Source library + manual upload -->
      <div>
        <!-- Candidate Search Results -->
        ${candidateResults.length > 0 ? `
          <div class="panel" style="margin-bottom:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:12px;flex-wrap:wrap">
              <div>
                <div class="panel-title">Document search results</div>
                <div class="panel-subtitle">${candidateResults.length} candidate filings identified across regional exchanges (${latestSearchRun.company_ids?.length || 0} companies)</div>
              </div>
            </div>
            <div class="table-wrap" style="max-height:240px;overflow-y:auto">
              <table>
                <thead><tr><th>Company</th><th>Filing Title</th><th>Type</th><th>Period</th><th>Cutoff</th><th>Registry Source</th></tr></thead>
                <tbody>
                  ${candidateResults.map(r => `
                    <tr>
                      <td class="td-company">${esc(r.company_name)}</td>
                      <td>${esc(r.title)}</td>
                      <td>${badge(r.source_type, 'neutral')}</td>
                      <td>${esc(r.period)}</td>
                      <td>${r.eligible ? badge('Eligible', 'green') : badge(r.ineligible_reason||'Cutoff', 'amber')}</td>
                      <td><a href="${esc(r.url)}" target="_blank" rel="noopener" class="text-small" style="font-weight:600">${esc(r.short_registry || 'Official Registry')} ↗</a></td>
                    </tr>`).join('')}
                </tbody>
              </table>
            </div>
          </div>` : ''}

        <div class="panel">
          <div class="panel-title">Manual upload</div>
          <div class="panel-subtitle">Upload actual company filings as evidence for analysis.</div>

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
          ${companies.length > 0 ? sourceLibrary(sources) : `
            <div class="empty-state" style="padding:32px 16px">
              <div class="empty-icon">📄</div>
              <h3>No sources yet</h3>
              <p>Upload actual company documents to begin analysis.</p>
            </div>`}
        </div>
      </div>
    </div>`;

  // Real-time filtering by SEDOL, ticker or company name
  const filterChecklist = (query) => {
    query = (query || '').toLowerCase().trim();
    $$('.company-search-item').forEach(item => {
      const match = !query || item.dataset.name.includes(query) || item.dataset.ticker.includes(query) || item.dataset.sedol.includes(query);
      item.style.display = match ? 'flex' : 'none';
    });
  };
  $('#src-global-search')?.addEventListener('input', e => filterChecklist(e.target.value));
  $('#src-sedol-select')?.addEventListener('change', e => {
    const cid = e.target.value;
    if (!cid) { $('#src-global-search').value = ''; filterChecklist(''); return; }
    const comp = companies.find(c => c.id === cid);
    if (comp) {
      $('#src-global-search').value = comp.sedol || comp.name;
      filterChecklist(comp.sedol || comp.name);
    }
  });

  // Stick to earnings call toggle
  $('#src-stick-earnings-call')?.addEventListener('change', e => {
    if (e.target.checked) {
      $$('input[name="source_types"]').forEach(cb => {
        cb.checked = (cb.value === 'earnings_call');
      });
    }
  });

  $('#btn-run-search').onclick = () => run(async () => {
    const selectedCompanyIds = Array.from($$('input[name="company_ids"]:checked')).map(cb => cb.value);
    const selectedTypes = Array.from($$('input[name="source_types"]:checked')).map(cb => cb.value);
    const period = $('#src-period')?.value || 'FY2025';
    const cutoff = $('#src-cutoff')?.value || new Date().toISOString().slice(0, 10);
    const stickEarnings = $('#src-stick-earnings-call')?.checked ?? false;
    const res = await api('source/search', {
      company_ids: selectedCompanyIds.length ? selectedCompanyIds : (state.companies || []).map(c => c.id),
      source_types: stickEarnings ? ['earnings_call'] : selectedTypes,
      period: period,
      cutoff: cutoff,
      stick_to_earnings_call: stickEarnings
    });
    await refresh();
    render();
    const count = res.results?.length || 0;
    notice(`Search complete. ${count} uploaded documents evaluated against cutoff (${cutoff}). Review the results below.`, 'success');
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

function getCompanyFinancials(comp, row, compSources, cases) {
  const facts = compSources.flatMap(source => (source.financial_facts || []).map(fact => ({
    ...fact, filename: source.filename
  }))).sort((a, b) => (b.publication_date || '').localeCompare(a.publication_date || ''));
  const marketCap = facts.find(f => f.metric === 'market_cap');
  const revenue = facts.find(f => f.metric === 'revenue');
  const approved = [...(cases || [])].reverse().find(c =>
    c.company_id === comp.id && c.review_status === 'approved');
  const reviewedRevenue = approved?.case;
  return {
    mcap: marketCap?.display || '—',
    rev: revenue?.display || (reviewedRevenue
      ? `${reviewedRevenue.currency} ${reviewedRevenue.total_revenue} ${reviewedRevenue.scale}` : '—'),
    mcapCitation: marketCap ? `${marketCap.filename}, p.${marketCap.page}` : 'No sourced market capitalisation',
    revCitation: revenue ? `${revenue.filename}, p.${revenue.page}`
      : reviewedRevenue ? `Approved case ${approved.id}` : 'No sourced revenue'
  };
}

const SIX_SOURCE_TYPES = [
  { type: 'annual_report',         label: 'Annual Report',                  icon: '📄', sub: 'Audited Statutory Accounts' },
  { type: 'regulatory_filing',     label: 'Regulatory Filing (10-K/20-F)',  icon: '🏛', sub: 'Exchange Disclosure & Segment Notes' },
  { type: 'earnings_call',         label: 'Earnings Call Transcript',       icon: '🎙', sub: 'Executive Commentary & Transcripts' },
  { type: 'investor_presentation', label: 'Investor Presentation',          icon: '📊', sub: 'IR Slide Deck & Operations Review' },
  { type: 'sustainability_report', label: 'Sustainability / ESG Report',    icon: '🌱', sub: 'CSR & ESG Disclosures' },
  { type: 'company_website',       label: 'Company Website / IR Page',      icon: '🌐', sub: 'Official Online Disclosures' },
];

function sourceLibrary(sources) {
  const rowMap = new Map((state.portfolio_imports?.[0]?.rows || []).map(r => [r.company_id, r]));
  const companies = (state.companies || []);
  const cases = (state.cases || []);

  const companyGroups = companies.map((c, idx) => {
    const compSources = sources.filter(s => s.company_id === c.id);
    const row = rowMap.get(c.id) || {};
    const financials = getCompanyFinancials(c, row, compSources, cases);
    return {
      index: idx + 1,
      comp: c,
      row: row,
      sources: compSources,
      financials: financials
    };
  });

  return `
    <div style="margin-bottom:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <div style="flex:1;min-width:260px">
        <input type="text" id="src-lib-filter" placeholder="🔍 Filter library by company name, ticker, SEDOL, country..."
          oninput="filterSourceLibraryTable(this.value)"
          style="width:100%;padding:9px 12px;border:1px solid var(--slate-300);border-radius:6px;font-size:13px">
      </div>
      <button class="btn btn-sm btn-secondary" onclick="toggleAllSourceRows()" id="btn-toggle-all-sources">
        Expand all 6 sources
      </button>
    </div>

    <div class="table-wrap" style="background:#ffffff;border:1px solid var(--slate-200);border-radius:8px;overflow:hidden">
      <table style="width:100%;border-collapse:collapse;margin:0">
        <thead>
          <tr style="background:var(--slate-50);border-bottom:1px solid var(--slate-200)">
            <th style="width:40px;text-align:center">#</th>
            <th>Company</th>
            <th>SEDOL</th>
            <th class="td-num">Market Cap</th>
            <th class="td-num">Net Revenue</th>
            <th style="text-align:center">Available (6 Types)</th>
            <th style="text-align:right;width:140px">Actions</th>
          </tr>
        </thead>
        <tbody id="src-table-body">
          ${companyGroups.map(({ index, comp, row, sources: compSources, financials }) => {
            const displayName = comp.name;
            const ticker = comp.bloomberg_ticker || row.bloomberg_ticker || '';
            const sedol = comp.sedol || row.sedol || '—';
            const sector = row.sector || '';
            const country = row.country || comp.jurisdiction || '';
            const portWt = row.portfolio_weight != null ? (row.portfolio_weight * 100).toFixed(2) + '%' : null;
            const searchTerms = `${displayName} ${ticker} ${sedol} ${sector} ${country}`.toLowerCase();
            const docsCount = SIX_SOURCE_TYPES.filter(st => compSources.some(s => s.source_type === st.type)).length;

            return `
              <tr class="source-master-row" id="src-master-${esc(comp.id)}" data-search="${esc(searchTerms)}"
                onclick="toggleSourceRow('${esc(comp.id)}')"
                style="cursor:pointer;border-bottom:1px solid var(--slate-100);transition:background 0.15s ease">
                <td style="text-align:center;font-weight:600;font-size:12px;color:var(--slate-400)">
                  <span class="src-chevron" id="src-chev-${esc(comp.id)}" style="display:inline-block;transition:transform 0.2s">▸</span>
                </td>
                <td class="td-company">
                  <div style="font-weight:650;font-size:13.5px;color:var(--slate-900)">${esc(displayName)}</div>
                  <div style="display:flex;align-items:center;gap:6px;margin-top:2px">
                    ${ticker ? `<span class="ticker" style="font-size:11px">${esc(ticker)}</span>` : ''}
                    ${country ? `<span class="badge neutral" style="font-size:10px">${esc(country)}</span>` : ''}
                    ${portWt ? `<span class="text-muted text-small" style="font-family:monospace;font-size:11px">Port: ${portWt}</span>` : ''}
                  </div>
                </td>
                <td style="font-family:monospace;font-size:12px;color:var(--slate-700);font-weight:600">
                  ${esc(sedol)}
                </td>
                <td class="td-num" style="font-weight:700;font-size:13px;color:var(--slate-800)">
                  ${esc(financials.mcap)}<div class="text-muted text-small">${esc(financials.mcapCitation)}</div>
                </td>
                <td class="td-num" style="font-weight:700;font-size:13px;color:var(--teal-700)">
                  ${esc(financials.rev)}<div class="text-muted text-small">${esc(financials.revCitation)}</div>
                </td>
                <td style="text-align:center">
                  <div style="display:inline-flex;align-items:center;gap:6px;background:${docsCount>0?'#f0fdf4':'#fffbeb'};border:1px solid ${docsCount>0?'#bbf7d0':'#fef3c7'};padding:3px 8px;border-radius:12px">
                    <span style="font-size:11.5px;font-weight:700;color:${docsCount>0?'#15803d':'#b45309'}">${docsCount} / 6</span>
                    <div style="display:flex;gap:3px">
                      ${SIX_SOURCE_TYPES.map(st => {
                        const hasIt = compSources.some(s => s.source_type === st.type);
                        return `<span title="${st.label}: ${hasIt?'Ingested':'Missing'}" style="width:6px;height:6px;border-radius:50%;background:${hasIt?'#22c55e':'#cbd5e1'};display:inline-block"></span>`;
                      }).join('')}
                    </div>
                  </div>
                </td>
                <td style="text-align:right" onclick="event.stopPropagation()">
                  <button class="btn btn-sm btn-secondary" onclick="toggleSourceRow('${esc(comp.id)}')" style="font-size:11.5px;padding:4px 9px">
                    View 6 sources
                  </button>
                </td>
              </tr>

              <!-- Expandable Row showing all 6 source types -->
              <tr class="source-detail-row" id="src-detail-${esc(comp.id)}" data-search="${esc(searchTerms)}" style="display:none;background:#f8fafc">
                <td colspan="7" style="padding:16px 20px;border-bottom:2px solid var(--slate-200)">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                    <div style="display:flex;align-items:center;gap:8px">
                      <strong style="font-size:13px;color:var(--slate-800)">Official Filing Library: ${esc(displayName)}</strong>
                      <span class="text-muted text-small">(Showing 6 standard regulatory disclosure categories)</span>
                    </div>
                    <div class="text-muted text-small">
                      Market Cap: <strong>${esc(financials.mcap)}</strong> · Consolidated Revenue: <strong>${esc(financials.rev)}</strong>
                    </div>
                  </div>

                  <!-- 6 Source Types Grid -->
                  <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(290px, 1fr));gap:12px">
                    ${SIX_SOURCE_TYPES.map((st, sIdx) => {
                      const matched = compSources.filter(s => s.source_type === st.type);
                      const isIngested = matched.length > 0;
                      const primaryDoc = matched[0];

                      return `
                        <div style="background:#ffffff;border:1px solid ${isIngested ? '#99f6e4' : '#e2e8f0'};border-radius:8px;padding:12px;box-shadow:0 1px 2px rgba(0,0,0,0.03);position:relative">
                          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:6px">
                            <div style="display:flex;align-items:center;gap:6px">
                              <span style="font-size:16px">${st.icon}</span>
                              <div>
                                <div style="font-weight:700;font-size:12.5px;color:var(--slate-900)">${st.label}</div>
                                <div class="text-muted" style="font-size:10.5px">${st.sub}</div>
                              </div>
                            </div>
                            <span class="badge ${isIngested ? 'green' : 'neutral'}" style="font-size:10px;font-weight:700">
                              ${isIngested ? '✓ Ingested' : 'Not Ingested'}
                            </span>
                          </div>

                          ${isIngested ? `
                            <div style="font-size:11.5px;color:var(--slate-700);line-height:1.4;margin:6px 0;background:#f0fdfa;padding:6px 8px;border-radius:5px;border:1px solid #ccfbf1">
                              <div style="font-weight:600;color:var(--teal-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(primaryDoc.title || primaryDoc.filename)}">
                                ${esc(primaryDoc.title || primaryDoc.filename)}
                              </div>
                              <div style="display:flex;gap:8px;font-size:10.5px;color:var(--teal-700);margin-top:3px">
                                <span>Period: <strong>${esc(primaryDoc.period || 'FY2025')}</strong></span>
                                <span>Date: ${esc(primaryDoc.publication_date)}</span>
                                <span>${primaryDoc.evidence?.length || 0} passages</span>
                              </div>
                            </div>
                            <div style="display:flex;gap:6px;margin-top:8px">
                              <button class="btn btn-sm btn-primary" onclick="openDocModal('${esc(primaryDoc.id)}')" style="font-size:11px;padding:3px 8px">
                                🔍 Full Reader
                              </button>
                              ${primaryDoc.url ? `
                                <a href="${esc(primaryDoc.url)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm" style="font-size:11px;padding:3px 8px;text-decoration:none">
                                  🏛 Registry ↗
                                </a>` : ''}
                            </div>
                          ` : `
                            <div style="font-size:11px;color:var(--slate-500);line-height:1.4;margin:6px 0;padding:6px 8px;background:var(--slate-50);border-radius:5px;border:1px dashed var(--slate-200)">
                              Expected from regional registry or IR platform.
                            </div>
                            <div style="display:flex;gap:6px;margin-top:8px">
                              <button class="btn btn-sm btn-ghost" onclick="selectCompanyForUpload('${esc(comp.id)}', '${esc(st.type)}')" style="font-size:11px;padding:3px 8px;color:var(--teal-700)">
                                + Upload filing
                              </button>
                            </div>
                          `}
                        </div>`;
                    }).join('')}
                  </div>
                </td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
}

// Global table helpers for Source Library
window.toggleSourceRow = function(companyId) {
  const detail = document.getElementById(`src-detail-${companyId}`);
  const chev = document.getElementById(`src-chev-${companyId}`);
  if (!detail) return;
  const isCurrentlyOpen = detail.style.display !== 'none';
  detail.style.display = isCurrentlyOpen ? 'none' : 'table-row';
  if (chev) chev.style.transform = isCurrentlyOpen ? 'rotate(0deg)' : 'rotate(90deg)';
};

window.toggleAllSourceRows = function() {
  const masters = Array.from(document.querySelectorAll('.source-master-row')).filter(row => row.style.display !== 'none');
  const details = masters.map(row => document.getElementById(row.id.replace('src-master-', 'src-detail-')));
  const chevs = masters.map(row => document.getElementById(row.id.replace('src-master-', 'src-chev-')));
  const btn = document.getElementById('btn-toggle-all-sources');
  const anyClosed = Array.from(details).some(d => d.style.display === 'none');

  details.forEach(d => d.style.display = anyClosed ? 'table-row' : 'none');
  chevs.forEach(c => c.style.transform = anyClosed ? 'rotate(90deg)' : 'rotate(0deg)');
  if (btn) btn.textContent = anyClosed ? 'Collapse all 6 sources' : 'Expand all 6 sources';
};

window.filterSourceLibraryTable = function(query) {
  const q = (query || '').trim().toLowerCase();
  document.querySelectorAll('.source-master-row').forEach(row => {
    const cid = row.id.replace('src-master-', '');
    const detail = document.getElementById(`src-detail-${cid}`);
    const match = !q || row.dataset.search.includes(q);
    row.style.display = match ? '' : 'none';
    if (!match && detail) detail.style.display = 'none';
  });
};

window.selectCompanyForUpload = function(companyId, sourceType) {
  const compSelect = document.querySelector('#source-form select[name="company_id"]');
  const typeSelect = document.querySelector('#source-form select[name="source_type"]');
  if (compSelect && companyId) compSelect.value = companyId;
  if (typeSelect && sourceType) typeSelect.value = sourceType;
  document.querySelector('#source-form')?.scrollIntoView({ behavior: 'smooth' });
  notice('Company and source type selected in manual upload form.', 'info');
};

window.openDocModal = function(sourceId) {
  const s = (state.sources || []).find(src => src.id === sourceId);
  if (!s) return;
  const company = (state.companies || []).find(c => c.id === s.company_id) || {};
  const fullText = (s.evidence || []).map(e => e.passage).join('\n\n');
  const registry = s.registry || s.short_registry || 'Official Regional Disclosure Portal';

  $('#doc-modal-title').textContent = (company.name || s.company_name) + ' — ' + (s.title || s.filename);
  $('#doc-modal-subtitle').innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <span>Ticker: <strong>${esc(company.bloomberg_ticker || '')}</strong></span>
      <span>Filing Registry: <strong>${esc(registry)}</strong></span>
      <span>Period: <strong>${esc(s.period || 'FY2025')}</strong></span>
      ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" style="color:#0d9488;font-weight:600">Open Official Registry Portal ↗</a>` : ''}
    </div>
  `;
  $('#doc-modal-content').textContent = fullText;
  $('#doc-modal-overlay').hidden = false;
};

window.closeDocModal = function() {
  const el = $('#doc-modal-overlay');
  if (el) el.hidden = true;
};

window.copyDocModalContent = function() {
  const text = $('#doc-modal-content').textContent;
  navigator.clipboard.writeText(text).then(() => {
    notice('Document text copied to clipboard', 'success');
  }).catch(() => {
    notice('Could not copy to clipboard', 'warning');
  });
};

// ══════════════════════════════════════════════════════
// VIEW: AI ANALYSIS  (YTP-19, YTP-20)
// ══════════════════════════════════════════════════════
async function viewAnalysis() {
  setPageHeader('STEP 4', 'AI Analysis & Case Review',
    'Extract revenue facts, audit classifications with Claude Sonnet 5, and review cases');

  const theme     = (state.themes||[]).filter(t=>t.approved).at(-1);
  const companies = state.companies || [];
  const sources   = state.sources   || [];
  const cases     = (state.cases    || []).filter(c => !theme || c.theme_id === theme?.id);
  const approved  = cases.filter(c => c.review_status === 'approved');
  const pending   = cases.filter(c => c.review_status === 'needs_review');

  const companyProgress = companies.map(c => {
    const compSources = sources.filter(s => s.company_id === c.id);
    const hasSrc      = compSources.length > 0;
    const caseRec     = cases.find(x => x.company_id === c.id);
    let status, color;
    if (caseRec?.review_status === 'approved')          { status = 'Approved';        color = 'green'; }
    else if (caseRec?.review_status === 'needs_review') { status = 'Awaiting review';  color = 'amber'; }
    else if (caseRec?.review_status === 'rejected')     { status = 'Rejected';         color = 'red'; }
    else if (hasSrc)                                    { status = 'Ready to analyse'; color = 'neutral'; }
    else                                                { status = 'No sources';       color = 'red'; }
    return { c, hasSrc, status, color, caseRec, compSources };
  });

  const readyCount = companyProgress.filter(p => p.hasSrc && p.status === 'Ready to analyse').length;

  $('#page-actions').innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      ${theme ? `
        <button class="btn btn-primary" id="btn-analyze-all" ${!state.ai_configured || !readyCount ? 'disabled' : ''} style="box-shadow:0 2px 4px rgba(13,148,136,0.2)">
          ✦ Analyse all ready companies (${readyCount})
        </button>` : ''}
      <div class="text-muted text-small">${state.ai_configured
        ? `${state.calls_used} / ${state.call_cap} AI calls used`
        : `<span style="color:var(--amber-600)">⚠ AI not configured</span>`}
      </div>
      <div style="display:flex;gap:6px">
        ${pending.length > 0 ? `<span class="badge badge-amber">${pending.length} awaiting review</span>` : ''}
        ${approved.length > 0 ? `<span class="badge badge-green">${approved.length} approved</span>` : ''}
      </div>
    </div>`;

  // Default selected case: first pending case, or newest case, or null
  const selectedCaseId = cases.find(c => c.review_status === 'needs_review')?.id || cases.at(-1)?.id || null;

  $('#page-content').innerHTML = `
    <div class="grid-1-2" style="align-items:start">

      <!-- Left Column: Queue & Cases -->
      <div class="panel">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <div>
            <div class="panel-title">Analysis &amp; Review Queue</div>
            <div class="panel-subtitle">
              ${companies.length} companies · ${cases.length} cases · ${pending.length} awaiting review
            </div>
          </div>
          <div style="display:flex;gap:4px">
            <button class="btn btn-sm btn-ghost filter-tab active" onclick="filterAnalysisQueue('all', this)">All (${companies.length})</button>
            <button class="btn btn-sm btn-ghost filter-tab" onclick="filterAnalysisQueue('pending', this)">Pending (${pending.length})</button>
            <button class="btn btn-sm btn-ghost filter-tab" onclick="filterAnalysisQueue('ready', this)">Ready (${readyCount})</button>
          </div>
        </div>

        ${companyProgress.length > 0 ? `
          <div class="table-wrap" style="max-height:480px;overflow-y:auto;margin-bottom:16px">
            <table style="width:100%;font-size:12.5px">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Sources</th>
                  <th class="td-num">Exposure</th>
                  <th>Confidence</th>
                  <th style="text-align:right">Action</th>
                </tr>
              </thead>
              <tbody id="analysis-queue-rows">
                ${companyProgress.map(({ c, hasSrc, status, color, caseRec, compSources }) => {
                  const pt = caseRec?.estimate?.point != null ? pct(caseRec.estimate.point) : (caseRec ? '—' : '');
                  const conf = caseRec?.classification_confidence || '';
                  const confDisplay = conf === 'high' ? 'High · 92%' : (conf === 'medium' ? 'Med · 70%' : (conf === 'low' ? 'Low · 35%' : '—'));
                  const confColor = conf === 'high' ? 'green' : (conf === 'medium' ? 'amber' : (conf === 'low' ? 'red' : 'neutral'));
                  const rowClass = caseRec?.review_status === 'needs_review' ? 'pending-row' : (caseRec?.review_status === 'approved' ? 'approved-row' : (hasSrc && !caseRec ? 'ready-row' : 'other-row'));

                  return `
                    <tr class="queue-row ${rowClass}" style="cursor:pointer;border-bottom:1px solid var(--slate-100)"
                      onclick="${caseRec ? `openCase('${esc(caseRec.id)}')` : (hasSrc ? `openAnalysisPanel('${esc(c.id)}')` : `setView('sources')`)}">
                      <td class="td-company">
                        <div style="font-weight:650;color:var(--slate-900)">${esc(c.name)}</div>
                        <div class="ticker" style="font-size:10.5px">${esc(c.bloomberg_ticker || c.id)}</div>
                      </td>
                      <td>
                        <span class="badge ${compSources.length > 0 ? 'neutral' : 'amber'}" style="font-size:11px">
                          ${compSources.length} docs
                        </span>
                      </td>
                      <td class="td-num" style="font-weight:700">
                        ${pt || '<span class="text-muted text-small">—</span>'}
                      </td>
                      <td>
                        ${caseRec ? `<span class="badge ${confColor}" style="font-size:10.5px">${confDisplay}</span>` : statusDot(color, status)}
                      </td>
                      <td style="text-align:right" onclick="event.stopPropagation()">
                        ${caseRec ? `
                          <button class="btn btn-sm ${caseRec.review_status==='needs_review'?'btn-primary':'btn-secondary'}"
                            onclick="openCase('${esc(caseRec.id)}')">
                            Review →
                          </button>
                        ` : (hasSrc ? `
                          <button class="btn btn-sm btn-primary" onclick="openAnalysisPanel('${esc(c.id)}')">
                            ✧ Analyse
                          </button>
                        ` : `
                          <button class="btn btn-sm" data-goto="sources">Upload</button>
                        `)}
                      </td>
                    </tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>` : `
          <div class="empty-state" style="padding:32px 16px">
            <div class="empty-icon">🏢</div>
            <h3>No companies yet</h3>
            <p><a href="#" data-goto="portfolio">Import a portfolio</a> to see your company universe here.</p>
          </div>`}

        <!-- Accordion 1: Targeted Single-Company Passage Selection -->
        <details class="accordion" style="margin-bottom:8px">
          <summary class="accordion-header" style="cursor:pointer;font-weight:600;font-size:13px;display:flex;justify-content:space-between;align-items:center">
            <span>✧ Targeted passage analysis</span>
            <span class="chevron">▾</span>
          </summary>
          <div class="accordion-body" style="padding:14px">
            <div class="text-muted text-small mb-12">
              Select a company to choose specific evidence passages to send to the AI model.
            </div>
            <div class="form-group mb-8">
              <label class="field-label">Company</label>
              <select id="analysis-company-select" onchange="openAnalysisPanel(this.value)">
                <option value="">— select company —</option>
                ${companies.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}
              </select>
            </div>
          </div>
        </details>

        <!-- Accordion 2: Manual Case Form -->
        <details class="accordion">
          <summary class="accordion-header" style="cursor:pointer;font-weight:600;font-size:13px;display:flex;justify-content:space-between;align-items:center">
            <span>✍ Create manual case</span>
            <span class="chevron">▾</span>
          </summary>
          <div class="accordion-body" style="padding:14px">
            <div class="text-muted text-small mb-12">
              Manually enter or override revenue numbers without running the AI.
            </div>
            <div class="form-group mb-8">
              <label class="field-label">Company</label>
              <select id="manual-company-select">
                <option value="">— select company —</option>
                ${companies.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}
              </select>
            </div>
            <button class="btn btn-sm btn-secondary" id="btn-open-manual">Open manual form in workspace</button>
          </div>
        </details>
      </div>

      <!-- Right Column: Case Review & Auditor Workspace -->
      <div id="case-detail-panel">
        ${selectedCaseId ? '' : `
          <div class="panel">
            <div class="empty-state" style="padding:40px 16px">
              <div class="empty-icon">✓</div>
              <h3>Select a case or company</h3>
              <p>Click a case or company from the queue on the left to review, or click "Analyse all ready companies" above.</p>
            </div>
          </div>`}
      </div>
    </div>`;

  // Filter helper for queue tabs
  window.filterAnalysisQueue = function(type, btn) {
    $$('.filter-tab').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    $$('.queue-row').forEach(row => {
      if (type === 'all') row.style.display = '';
      else if (type === 'pending') row.style.display = row.classList.contains('pending-row') ? '' : 'none';
      else if (type === 'approved') row.style.display = row.classList.contains('approved-row') ? '' : 'none';
      else if (type === 'ready') row.style.display = row.classList.contains('ready-row') ? '' : 'none';
    });
  };

  const btnAll = $('#btn-analyze-all');
  if (btnAll) {
    btnAll.onclick = () => run(async () => {
      showLoading(`Running batch AI extraction & classification across ready companies...`);
      const res = await api('analyze_all', { theme_id: theme.id });
      await refresh();
      notice(`Batch analysis complete: ${res.analysed_count} companies sent to review.` +
        (res.errors?.length ? ` ${res.errors.length} failed: ${res.errors.map(e => `${e.company_name}: ${e.error}`).join('; ')}` : ''),
        res.errors?.length ? 'warning' : 'success');
      render();
    }, 'Batch analyzing…');
  }

  $('#btn-open-manual')?.addEventListener('click', () => {
    const cid = $('#manual-company-select').value;
    if (!cid) return notice('Select a company first.', 'error');
    openManualCaseForm(cid);
  });

  if (selectedCaseId) {
    openCase(selectedCaseId);
  }
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
  const panel = $('#case-detail-panel') || $('#analysis-panel');
  if (panel) {
    panel.innerHTML = analysisSelectPanel(companyId, companies, sources, state);
    bindAnalysisPanel(companyId);
    panel.scrollIntoView({ behavior: 'smooth' });
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
      notice('Analysis complete. Case created and ready for review.', 'success');
      render();
      const newCase = (state.cases || []).filter(x => x.company_id === companyId).at(-1);
      if (newCase) openCase(newCase.id);
    }, 'Running AI analysis (2 LLM calls)…');
  });
}

// ══════════════════════════════════════════════════════
// VIEW: REVIEW CASES  (YTP-21, YTP-22)
// ══════════════════════════════════════════════════════
async function viewReview() {
  await viewAnalysis();
}

function confidenceBadge(level) {
  if (level === 'high')   return badge('High · 92%', 'green');
  if (level === 'medium') return badge('Medium · 70%', 'amber');
  if (level === 'low')    return badge('Low · 35%', 'red');
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

  const chk = c.checker_result;

  const confLevel = (c.classification_confidence || 'medium').toLowerCase();
  const confScoreMap = {
    high: {
      pct: 92,
      points: '3 / 3',
      label: 'High Confidence (3/3)',
      color: '#0d9488',
      bg: '#f0fdfa',
      border: '#99f6e4',
      badge: 'green',
      criteria: [
        '✓ Explicit business segment disclosure in audited statutory filing',
        '✓ Direct 1-to-1 match to specific Tier-2 leaf activity without ambiguity',
        '✓ Verified denominator substantiation with exact page-level citations'
      ]
    },
    medium: {
      pct: 70,
      points: '2 / 3',
      label: 'Medium Confidence (2/3)',
      color: '#d97706',
      bg: '#fffbeb',
      border: '#fde68a',
      badge: 'amber',
      criteria: [
        '✓ Disclosed segment requires reasonable inference from qualitative commentary',
        '✓ General business segment encompasses multiple activities, bounded by assumptions',
        '⚠ Indirect or partial allocation applied'
      ]
    },
    low: {
      pct: 35,
      points: '1 / 3',
      label: 'Low Confidence (1/3)',
      color: '#dc2626',
      bg: '#fef2f2',
      border: '#fecaca',
      badge: 'red',
      criteria: [
        '⚠ Ambiguous or unallocated residual in company disclosures',
        '⚠ Broad assumption-based range bound (0% - 100%) due to missing granularity',
        '⚠ High reliance on non-statutory qualitative statements'
      ]
    }
  };
  const confMeta = confScoreMap[confLevel] || confScoreMap.medium;

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
          <div class="label">Confidence Score</div>
          <div class="value" style="font-size:18px;color:${confMeta.color}">${confMeta.pct}%</div>
          <div class="meta">${confMeta.label}</div>
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

      <!-- Dedicated Confidence Score & Calibration Card (Requirement 7) -->
      <div class="confidence-score-card mt-16" style="background:${confMeta.bg};border:1.5px solid ${confMeta.border};border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,0.03)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
          <div>
            <div style="font-size:10.5px;font-weight:700;color:var(--slate-500);text-transform:uppercase;letter-spacing:0.8px">
              AI Confidence Score &amp; Rubric
            </div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:3px">
              <span style="font-size:18px;font-weight:750;color:${confMeta.color}">${confMeta.label}</span>
              <span class="badge ${confMeta.badge}" style="font-size:12px;font-weight:700">${confMeta.pct}% Score</span>
            </div>
          </div>
          <div style="text-align:right">
            <div style="font-size:12px;font-weight:700;color:var(--slate-800)">Rubric Score: ${confMeta.points}</div>
            <div class="text-muted text-small">Display score mapped from rubric; not a calibrated probability</div>
          </div>
        </div>

        <!-- Visual Score Gauge -->
        <div style="height:8px;width:100%;background:rgba(0,0,0,0.08);border-radius:4px;overflow:hidden;margin-bottom:12px">
          <div style="height:100%;width:${confMeta.pct}%;background:${confMeta.color};border-radius:4px;transition:width 0.4s ease"></div>
        </div>

        <!-- Rubric Basis & Criteria Checklist -->
        <div style="background:#ffffff;border:1px solid ${confMeta.border};border-radius:6px;padding:12px;margin-top:8px">
          <div style="font-weight:700;font-size:12px;color:var(--slate-800);margin-bottom:4px">
            Rubric Evaluation &amp; Evidence Basis:
          </div>
          <div style="font-size:12.5px;color:var(--slate-700);line-height:1.5;margin-bottom:8px">
            ${esc(c.confidence_basis || 'Direct segment disclosure and substantiated classification.')}
          </div>
          <div style="display:grid;gap:4px;font-size:11.5px;color:var(--slate-600)">
            ${confMeta.criteria.map(crit => `<div>${crit}</div>`).join('')}
          </div>
        </div>
      </div>

      <!-- AI Checker Model (Claude Sonnet 5) -->
      <div class="form-section mt-16" style="background:var(--slate-50);border:1px solid var(--slate-200);border-radius:8px;padding:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px">
          <div>
            <div style="font-weight:700;font-size:14px;display:flex;align-items:center;gap:8px">
              <span>🛡️ AI Checker Verification (Claude Sonnet 5)</span>
              <span class="badge ${chk ? (chk.verdict==='VERIFIED'?'badge-green':'badge-amber') : 'badge-neutral'}">
                ${chk ? `${esc(chk.verdict)}` : 'Not yet audited'}
              </span>
            </div>
            <div class="text-muted text-small" style="margin-top:2px">
              Independent secondary LLM auditor cross-checks revenue extractions, Tier-2 classifications, and confidence calibration.
            </div>
          </div>
          <button type="button" class="btn btn-sm ${chk ? 'btn-secondary' : 'btn-primary'}" id="btn-run-checker">
            ${chk ? '↻ Re-run AI Checker' : '✧ Run AI Checker (Claude Sonnet 5)'}
          </button>
        </div>

        ${chk ? `
          <div style="background:white;border:1px solid var(--slate-200);border-radius:6px;padding:12px;margin-top:10px">
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px">
              <div style="background:var(--slate-50);padding:8px;border-radius:4px">
                <span style="font-size:10px;color:var(--slate-400);text-transform:uppercase;font-weight:700">Agreement Score</span>
                <div style="font-size:16px;font-weight:700;color:var(--teal-800)">${chk.agreement_score ? `${chk.agreement_score} / 5` : '—'}</div>
              </div>
              <div style="background:var(--slate-50);padding:8px;border-radius:4px">
                <span style="font-size:10px;color:var(--slate-400);text-transform:uppercase;font-weight:700">Confidence Calibration</span>
                <div style="font-size:14px;font-weight:600;color:var(--slate-800);text-transform:capitalize">${esc(chk.confidence_assessment || 'Agreed')}</div>
              </div>
              <div style="background:var(--slate-50);padding:8px;border-radius:4px">
                <span style="font-size:10px;color:var(--slate-400);text-transform:uppercase;font-weight:700">Recommendation</span>
                <div style="font-size:14px;font-weight:600;color:${chk.recommendation==='Approve as-is'?'var(--teal-700)':'var(--amber-700)'}">${esc(chk.recommendation || 'Review')}</div>
              </div>
            </div>
            
            <div style="font-size:13px;line-height:1.5;color:var(--slate-700);margin-bottom:10px">
              <strong>Audit Summary:</strong> ${esc(chk.summary || '')}
            </div>

            <div style="display:grid;gap:6px;font-size:12px;color:var(--slate-600)">
              ${chk.extraction_audit ? `<div><strong>• Fact Extraction Audit:</strong> ${esc(chk.extraction_audit)}</div>` : ''}
              ${chk.classification_audit ? `<div><strong>• Taxonomy Alignment Audit:</strong> ${esc(chk.classification_audit)}</div>` : ''}
              ${chk.confidence_audit ? `<div><strong>• Confidence Rubric Audit:</strong> ${esc(chk.confidence_audit)}</div>` : ''}
            </div>
            <div class="text-muted text-small" style="margin-top:8px;font-size:11px">
              Audited by ${esc(chk.checker_model || 'Claude Sonnet 5')} at ${esc(chk.checked_at || '')}
            </div>
          </div>
        ` : `
          <div style="padding:12px;background:white;border:1px dashed var(--slate-300);border-radius:6px;font-size:12px;color:var(--slate-500);text-align:center">
            Click "Run AI Checker" to dispatch an independent audit verification to Claude Sonnet before approving.
          </div>
        `}
      </div>

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

  const btnChecker = $('#btn-run-checker');
  if (btnChecker) {
    btnChecker.onclick = () => run(async () => {
      showLoading('Running AI Checker audit with Claude Sonnet 5...');
      const chkRes = await api('case/check', { case_id: caseId });
      await refresh();
      notice(`Claude Sonnet audit: ${chkRes.verdict} (${chkRes.agreement_score}/5 agreement).`, chkRes.verdict === 'VERIFIED' ? 'success' : 'warning');
      openCase(caseId);
    }, 'Auditing case…');
  }

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
      <div class="accordion-header">
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
    total_revenue: null,
    denominator_evidence_ids: ['REPLACE_WITH_EVIDENCE_ID'],
    period: 'FY2025', currency: 'USD', scale: 'millions',
    units: [{
      unit_id: 'unit_1', activity: 'Describe revenue activity',
      revenue: null, period: 'FY2025', currency: 'USD', scale: 'millions',
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
function dashboardEmptyState(title, description, actionView, actionLabel) {
  return `<div class="dashboard-empty" role="status">
    <div class="dashboard-empty-icon" aria-hidden="true">
      <svg viewBox="0 0 48 48" fill="none"><path d="M10 35h28M14 29v-7m10 7V13m10 16V18" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><circle cx="36" cy="12" r="7" fill="var(--teal-100)"/><path d="M36 9v6m-3-3h6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    </div>
    <h3>${esc(title)}</h3>
    <p>${esc(description)}</p>
    <button type="button" class="btn btn-primary btn-sm" data-goto="${esc(actionView)}">${esc(actionLabel)} <span aria-hidden="true">→</span></button>
  </div>`;
}

function activeContributionsPanel(rows, companies, hasSources) {
  const holdings = rows.filter(r => r.company_id !== 'CASH' && (r.weights.portfolio || r.weights.benchmark));
  const assessed = holdings.filter(r => r.assessed);
  const plotted = assessed.filter(r => Number.isFinite(r.exposure?.point) && Number.isFinite(r.contributions?.active?.point))
    .sort((a, b) => Math.abs(b.contributions.active.point) - Math.abs(a.contributions.active.point));
  const body = plotted.length
    ? `${waterfallChart(plotted.slice(0, 8), {companies})}
       <div class="contribution-legend"><span><i class="legend-dot negative-dot"></i>Negative contribution</span><span><i class="legend-dot positive-dot"></i>Positive contribution</span></div>`
    : dashboardEmptyState(assessed.length ? 'Point estimates pending' : 'No approved exposure yet',
        assessed.length ? 'Reviewed cases currently contain ranges only. Resolve the missing revenue allocations to plot point contributions.'
          : 'Contributions will appear after company revenue exposure is analysed and approved. Missing evidence is not treated as zero exposure.',
        hasSources ? 'analysis' : 'sources', hasSources ? 'Review company analyses' : 'Add source documents');
  return `<div class="panel dashboard-chart-panel">
    <div class="dashboard-panel-heading"><div class="panel-title">Active exposure contributions</div>
      <span class="badge badge-${plotted.length ? 'green' : 'neutral'}">${plotted.length ? `${plotted.length} available` : 'Awaiting estimates'}</span></div>
    <div class="panel-subtitle">How each holding contributes to exposure relative to the benchmark.</div>
    ${body}
    <div class="dashboard-panel-footer"><span>${assessed.length} of ${holdings.length} holdings assessed</span><span>Active weight × company exposure</span></div>
  </div>`;
}

async function viewDashboard() {
  $('#page-actions').innerHTML = '';
  setPageHeader('STEP 5', 'Portfolio Exposure Dashboard',
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
  const assessedHoldings = d.companies.filter(r => r.company_id !== 'CASH' && r.assessed && (r.weights.portfolio || r.weights.benchmark));
  const hasSources = (state.sources || []).some(source => d.companies.some(row => row.company_id === source.company_id));
  const companyName = id => companies.find(c=>c.id===id)?.name || id;

  // Subtheme rows from cases
  const nodes  = theme.nodes || [];
  const tier1  = nodes.filter(n=>!n.parent_id);
  const cases  = state.cases || [];

  const subthemeExposure = {};
  nodes.forEach(n => subthemeExposure[n.node_id] = { lower:0, upper:0, point:0 });

  d.companies.forEach(row => {
    if (row.company_id === 'CASH') return;
    const c = cases.filter(x => x.company_id === row.company_id && x.theme_id === theme.id && x.review_status === 'approved').at(-1);
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
    <div style="display:flex;gap:8px">
      <button class="btn btn-sm btn-secondary" id="btn-reference" title="Filter to reference benchmark constituents">
        Benchmark: ${esc(snapshot.benchmark_name || 'MSCI Asia ex Japan')}
      </button>
      <button class="btn btn-sm" id="btn-export-dash">↓ Export</button>
    </div>`;

  $('#page-content').innerHTML = `
    <!-- Top-level exposure metrics -->
    <div class="metric-grid">
      ${metricTile('Portfolio exposure', pct(s.portfolio.point), `Bounds: ${pctRange(s.portfolio)}`, '')}
      ${metricTile('Benchmark exposure', pct(s.benchmark.point), `Bounds: ${pctRange(s.benchmark)}`, '')}
      ${metricTile('Active exposure', pct(s.active.point, true), `Bounds: ${pctRange(s.active)}`, 'accent')}
      ${metricTile('Coverage', pct(d.coverage.portfolio), `Benchmark: ${pct(d.coverage.benchmark)}`, '')}
    </div>

    <div class="grid-2 dashboard-breakdown-grid">
      ${activeContributionsPanel(d.companies, companies, hasSources)}

      <!-- Sub-theme breakdown -->
      <div class="panel dashboard-chart-panel">
        <div class="panel-title">Sub-theme exposure breakdown</div>
        <div class="panel-subtitle">Portfolio weight × company Tier-2 allocation. Click any sub-theme row to filter companies.</div>
        ${assessedHoldings.length && tier1.length ? `<div class="table-wrap">
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
                  <tr data-subtheme="${esc(t1.node_id)}" style="background:var(--slate-50);cursor:pointer" title="Click to filter companies by ${esc(t1.name)}">
                    <td><strong>${esc(t1.name)}</strong> <span class="text-muted text-small">(Tier 1)</span></td>
                    <td class="td-num">${pct(t1Low)}</td>
                    <td class="td-num">${pct(t1High)}</td>
                    <td class="td-num">${pct(t1Pt)}</td>
                  </tr>
                  ${children.map(t2 => {
                    const v = subthemeExposure[t2.node_id]||{lower:0,upper:0,point:0};
                    return `<tr data-subtheme="${esc(t2.node_id)}" style="cursor:pointer" title="Click to filter companies by ${esc(t2.name)}">
                      <td style="padding-left:28px">↳ ${esc(t2.name)} <span class="text-muted text-small">(Tier 2)</span></td>
                      <td class="td-num text-muted">${pct(v.lower)}</td>
                      <td class="td-num text-muted">${pct(v.upper)}</td>
                      <td class="td-num text-muted">${pct(v.point)}</td>
                    </tr>`;
                  }).join('')}`;
              }).join('')}
            </tbody>
          </table>
        </div>` : dashboardEmptyState('Sub-theme breakdown pending',
          tier1.length ? 'Approve company analyses to see how portfolio exposure is allocated across your taxonomy.' : 'Add a sub-theme taxonomy to organise company exposure by activity.',
          tier1.length ? (hasSources ? 'analysis' : 'sources') : 'theme',
          tier1.length ? (hasSources ? 'Open analysis & review' : 'Add source documents') : 'Define sub-themes')}
      </div>
    </div>

    <!-- Company table -->
    <div class="panel" id="dashboard-companies">
      <div class="panel-title">Company-level exposure</div>
      <div class="panel-subtitle">
        Approved results only · ${esc(snapshot?.portfolio?.[0]?.as_of_date||'')} · 
        Unassessed companies: lower=0%, upper=100%, point=—
      </div>

      <div class="company-filter-banner" id="company-filter-banner" hidden style="align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;margin:12px 0;border:1px solid var(--teal-200);border-left:4px solid var(--teal-500);border-radius:8px;background:var(--teal-50)">
        <div>
          <span style="font-size:10px;font-weight:700;letter-spacing:.08em;color:var(--teal-600)">ACTIVE FILTER</span>
          <div id="company-filter-name" style="font-size:14px;font-weight:600;color:var(--teal-900)"></div>
          <div id="company-filter-count" style="font-size:11px;color:var(--slate-500)"></div>
        </div>
        <button type="button" class="btn btn-sm" id="btn-remove-filter">✕ Clear filter</button>
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
          <tbody id="dashboard-company-rows">
            ${d.companies.map(row => {
              const name = companyName(row.company_id);
              const ap = row.contributions.active.point;
              return `<tr data-dashboard-company="${esc(row.company_id)}">
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

  const allCompRows = [...document.querySelectorAll('[data-dashboard-company]')];
  const filterCompanies = (title, matchingIds, label) => {
    const idSet = new Set(matchingIds);
    let count = 0;
    allCompRows.forEach(tr => {
      const show = idSet.has(tr.dataset.dashboardCompany);
      tr.style.display = show ? '' : 'none';
      if (show) count++;
    });
    $('#company-filter-banner').hidden = false;
    $('#company-filter-name').textContent = `${label}: ${title}`;
    $('#company-filter-count').textContent = `${count} of ${d.companies.length} companies match`;
  };

  const clearFilter = () => {
    allCompRows.forEach(tr => tr.style.display = '');
    $('#company-filter-banner').hidden = true;
    $$('[data-subtheme]').forEach(r => r.style.outline = '');
  };

  $('#btn-remove-filter').onclick = clearFilter;

  $('#btn-reference').onclick = () => {
    const benchmarkIds = d.companies.filter(r => r.weights.benchmark > 0).map(r => r.company_id);
    filterCompanies(snapshot.benchmark_name || 'MSCI Asia ex Japan', benchmarkIds, 'Benchmark');
  };

  $$('[data-subtheme]').forEach(rowEl => {
    rowEl.onclick = () => {
      const nid = rowEl.dataset.subtheme;
      const isAlreadyActive = rowEl.style.outline !== '';

      if (isAlreadyActive) {
        clearFilter();
        return;
      }

      $$('[data-subtheme]').forEach(r => r.style.outline = '');
      rowEl.style.outline = '2px solid var(--teal-500)';

      const ids = new Set([nid]);
      for (let sz = 0; sz !== ids.size;) {
        sz = ids.size;
        nodes.forEach(n => { if (ids.has(n.parent_id)) ids.add(n.node_id); });
      }

      const matchingCids = d.companies.filter(row => {
        const c = cases.filter(c => c.company_id === row.company_id && c.theme_id === theme.id && c.review_status === 'approved').at(-1);
        if (!c) return false;
        return c.case?.units?.some(u => (u.node_ids || []).some(n => ids.has(n)) || Object.entries(u.allocations || {}).some(([n, s]) => ids.has(n) && s > 0));
      }).map(r => r.company_id);

      const targetNode = nodes.find(n => n.node_id === nid);
      filterCompanies(targetNode?.name || nid, matchingCids, 'Sub-theme');

      // Smoothly scroll down to the filtered companies table
      document.getElementById('dashboard-companies')?.scrollIntoView({ behavior: 'smooth' });
    };
  });

  $('#btn-export-dash')?.addEventListener('click', () => run(async () => {
    const r = await fetch('/api/export', {
      method:'POST',
      headers:{'Content-Type':'application/json', 'X-Theme-Lab-View':currentView},
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

  $('#btn-run-eval').onclick = () => run(async () => {
    const result = await api('evaluate', { examples: evalData });
    renderEvalResults(result);
  }, 'Running evaluation…');
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

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function downloadPortfolioTemplate() {
  try {
    const res = await fetch('/portfolio-template.xlsx');
    if (res.ok) {
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'portfolio-template.xlsx';
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { a.remove(); URL.revokeObjectURL(a.href); }, 2000);
      notice('Excel template downloaded (headers only).', 'info');
      return;
    }
  } catch (err) {
    // Fallback to SheetJS below
  }

  if (typeof XLSX === 'undefined') {
    notice('Excel export library (SheetJS) is not loaded. Refresh and try again.', 'error');
    return;
  }
  const workbook = XLSX.utils.book_new();
  const rows = [
    ['Company', 'Bloomberg ticker', 'Country', 'Sector', 'SEDOL', 'Portfolio wt', 'Reference wt', 'Active wt']
  ];
  const sheet = XLSX.utils.aoa_to_sheet(rows);
  sheet['!cols'] = [32, 20, 16, 24, 12, 16, 16, 16].map(wch => ({wch}));
  XLSX.utils.book_append_sheet(workbook, sheet, 'Portfolio holdings');
  XLSX.writeFile(workbook, 'portfolio-template.xlsx');
  notice('Excel template downloaded (headers only).', 'info');
}

function exportMindmap() {
  let nodes;
  try {
    nodes = JSON.parse($('#taxonomy-json-area').value);
    if (!Array.isArray(nodes)) throw Error();
  } catch {
    notice('Fix the taxonomy JSON before exporting.', 'error');
    return;
  }
  const roots = nodes.filter(n => !n.parent_id);
  if (!roots.length) {
    notice('Add taxonomy nodes before exporting.', 'error');
    return;
  }
  const themeName = $('#theme-form [name="theme_name"]')?.value || 'Investment theme';

  // Sizing constants matching the website's CSS (.mm-theme, .mm-node, .mm-connector)
  const cardW = 210;
  const t2CardH = 58;
  const t1CardH = 68;
  const rootCardH = 76;
  const gapX = 80;        // horizontal gap for connector line
  const gapY_T2 = 14;     // vertical gap between Tier-2 cards
  const gapY_T1 = 28;     // vertical gap between Tier-1 blocks
  const padX = 40;
  const padY = 40;

  // Measure each Tier-1 subtree height
  const t1Blocks = roots.map(root => {
    const children = nodes.filter(n => n.parent_id === root.node_id);
    const childrenH = children.length
      ? children.length * t2CardH + (children.length - 1) * gapY_T2
      : t1CardH;
    const blockH = Math.max(t1CardH, childrenH);
    return {
      root,
      children,
      childrenH,
      blockH
    };
  });

  const totalTreeH = t1Blocks.reduce((sum, b) => sum + b.blockH, 0) + (t1Blocks.length - 1) * gapY_T1;
  const svgH = Math.max(padY * 2 + totalTreeH, 300);
  const svgW = padX * 2 + cardW * 3 + gapX * 2;

  // X Coordinates
  const xRoot = padX;
  const xT1 = xRoot + cardW + gapX;
  const xT2 = xT1 + cardW + gapX;

  // Root Card Y (vertically centered against entire tree)
  const rootCenterY = padY + totalTreeH / 2;
  const rootY = rootCenterY - rootCardH / 2;

  const xml = str => esc(str).replace(/&#39;/g, '&apos;');

  // Text truncation / wrapping helper for SVG text
  const wrapText = (text, maxChars = 22, maxLines = 2) => {
    const words = String(text || '').split(/\s+/);
    const lines = [];
    let cur = '';
    for (const w of words) {
      if ((cur + ' ' + w).trim().length > maxChars && cur) {
        lines.push(cur.trim());
        cur = w;
      } else {
        cur = (cur ? cur + ' ' : '') + w;
      }
    }
    if (cur) lines.push(cur.trim());
    return lines.slice(0, maxLines);
  };

  let pathsSvg = '';
  let cardsSvg = '';

  // 1. Root / Theme Card (Dark Teal Gradient)
  const rootTitleLines = wrapText(themeName, 20, 2);
  cardsSvg += `
    <g class="theme-card">
      <defs>
        <linearGradient id="themeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#115e59"/>
          <stop offset="100%" stop-color="#042f2e"/>
        </linearGradient>
        <filter id="cardShadow" x="-5%" y="-5%" width="115%" height="120%" filterUnits="userSpaceOnUse">
          <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#0f172a" flood-opacity="0.08"/>
        </filter>
      </defs>
      <rect x="${xRoot}" y="${rootY}" width="${cardW}" height="${rootCardH}" rx="14" fill="url(#themeGrad)" stroke="#134e4a" stroke-width="1.5" filter="url(#cardShadow)"/>
      <rect x="${xRoot + 14}" y="${rootY + 12}" width="54" height="18" rx="9" fill="rgba(255,255,255,0.2)"/>
      <text x="${xRoot + 41}" y="${rootY + 24.5}" fill="#ffffff" font-size="9" font-weight="700" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif" letter-spacing="1">THEME</text>
      ${rootTitleLines.map((l, i) => `<text x="${xRoot + 14}" y="${rootY + 46 + i * 16}" fill="#ffffff" font-size="13.5" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">${xml(l)}</text>`).join('')}
    </g>`;

  // Spine X from Theme to Tier 1
  const spine1X = xRoot + cardW + gapX / 2;

  // Trunk from Theme out to spine
  pathsSvg += `<path d="M ${xRoot + cardW} ${rootCenterY} H ${spine1X}" stroke="#0d9488" stroke-width="2" fill="none" stroke-linecap="round"/>`;

  let curY = padY;
  const t1Centers = [];

  t1Blocks.forEach((block) => {
    const t1CenterY = curY + block.blockH / 2;
    const t1Y = t1CenterY - t1CardH / 2;
    t1Centers.push(t1CenterY);

    // Branch from Spine1 into Tier 1 card
    pathsSvg += `<path d="M ${spine1X} ${t1CenterY} H ${xT1}" stroke="#0d9488" stroke-width="2" fill="none" stroke-linecap="round"/>`;

    // Tier 1 Card
    const t1Lines = wrapText(block.root.name, 22, 2);
    cardsSvg += `
      <g class="tier1-card">
        <rect x="${xT1}" y="${t1Y}" width="${cardW}" height="${t1CardH}" rx="12" fill="#f0fdfa" stroke="#99f6e4" stroke-width="1.5" filter="url(#cardShadow)"/>
        <rect x="${xT1}" y="${t1Y + 3}" width="4" height="${t1CardH - 6}" rx="2" fill="#0d9488"/>
        <rect x="${xT1 + 12}" y="${t1Y + 10}" width="50" height="17" rx="8.5" fill="#ccfbf1"/>
        <text x="${xT1 + 37}" y="${t1Y + 22}" fill="#0f766e" font-size="9" font-weight="700" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif" letter-spacing="0.5">TIER 1</text>
        <text x="${xT1 + cardW - 14}" y="${t1Y + 22}" fill="#0d9488" font-size="10.5" font-weight="600" text-anchor="end" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">${block.children.length} ${block.children.length === 1 ? 'activity' : 'activities'}</text>
        ${t1Lines.map((l, i) => `<text x="${xT1 + 14}" y="${t1Y + 44 + i * 15}" fill="#0f172a" font-size="13" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">${xml(l)}</text>`).join('')}
      </g>`;

    // If has children: draw connectors and Tier 2 cards
    if (block.children.length > 0) {
      const spine2X = xT1 + cardW + gapX / 2;
      const t2StartTop = t1CenterY - block.childrenH / 2;
      const t2Centers = [];

      // Trunk from Tier 1 to Spine 2
      pathsSvg += `<path d="M ${xT1 + cardW} ${t1CenterY} H ${spine2X}" stroke="#0d9488" stroke-width="2" fill="none" stroke-linecap="round"/>`;

      block.children.forEach((child, cIdx) => {
        const t2Y = t2StartTop + cIdx * (t2CardH + gapY_T2);
        const t2CenterY = t2Y + t2CardH / 2;
        t2Centers.push(t2CenterY);

        // Branch from Spine 2 into Tier 2 card
        pathsSvg += `<path d="M ${spine2X} ${t2CenterY} H ${xT2}" stroke="#0d9488" stroke-width="2" fill="none" stroke-linecap="round"/>`;

        // Tier 2 Card
        const t2Lines = wrapText(child.name, 22, 2);
        cardsSvg += `
          <g class="tier2-card">
            <rect x="${xT2}" y="${t2Y}" width="${cardW}" height="${t2CardH}" rx="10" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.5" filter="url(#cardShadow)"/>
            <rect x="${xT2}" y="${t2Y + 3}" width="4" height="${t2CardH - 6}" rx="2" fill="#2dd4bf"/>
            <rect x="${xT2 + 12}" y="${t2Y + 8}" width="48" height="15" rx="7.5" fill="#f1f5f9"/>
            <text x="${xT2 + 36}" y="${t2Y + 19}" fill="#475569" font-size="8.5" font-weight="700" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif" letter-spacing="0.5">TIER 2</text>
            ${t2Lines.map((l, i) => `<text x="${xT2 + 14}" y="${t2Y + 39 + i * 14}" fill="#1e293b" font-size="12" font-weight="650" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">${xml(l)}</text>`).join('')}
          </g>`;
      });

      // Spine 2 vertical line
      if (t2Centers.length > 1) {
        const minT2Y = Math.min(...t2Centers);
        const maxT2Y = Math.max(...t2Centers);
        pathsSvg += `<path d="M ${spine2X} ${minT2Y} V ${maxT2Y}" stroke="#0d9488" stroke-width="2" fill="none" stroke-linecap="round"/>`;
      }
    }

    curY += block.blockH + gapY_T1;
  });

  // Spine 1 vertical line connecting all Tier 1 centers
  if (t1Centers.length > 1) {
    const minT1Y = Math.min(...t1Centers);
    const maxT1Y = Math.max(...t1Centers);
    pathsSvg += `<path d="M ${spine1X} ${minT1Y} V ${maxT1Y}" stroke="#0d9488" stroke-width="2" fill="none" stroke-linecap="round"/>`;
  }

  const svgContent = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${svgH}" viewBox="0 0 ${svgW} ${svgH}">
  <rect width="100%" height="100%" fill="#f8fafc"/>
  ${pathsSvg}
  ${cardsSvg}
</svg>`;

  downloadBlob(new Blob([svgContent], { type: 'image/svg+xml' }), `${themeName.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-mindmap.svg`);
  notice('Mind map exported as SVG.', 'info');
}
