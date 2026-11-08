const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function setup() {
  const elements = new Map();
  const storage = new Map();
  const portfolioCards = [];
  const deletePortfolioButtons = [];
  const loadThemeButtons = [];
  const deleteThemeButtons = [];
  const node = id => {
    if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', value:'', style:{}, dataset:{},
      addEventListener(type, fn) { this[type] = fn; }, classList:{add(){},remove(){},toggle(){}}});
    return elements.get(id);
  };
  const rows = ['6889106', '6771720'].map((sedol, i) => ({style:{}, dataset:{sedol, name:['tsmc','samsung'][i],ticker:['2330 tt','005930 ks'][i]}}));
  const context = vm.createContext({console, setTimeout, clearTimeout, btoa:globalThis.btoa,
    localStorage:{
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); }
    },
    FormData:class { constructor(form){return Object.entries(form.fields || {});} }, window:{}, document:{
    addEventListener(){}, querySelector:node, getElementById:id=>node('#'+id),
    querySelectorAll:selector=>selector === '.company-search-item' ? rows :
      selector === '[data-open-portfolio]' ? portfolioCards :
      selector === '[data-del-portfolio]' ? deletePortfolioButtons :
      selector === '[data-load-theme]' ? loadThemeButtons :
      selector === '[data-del-theme]' ? deleteThemeButtons : []
  }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8'), context);
  vm.runInContext(`state = {companies:[{id:'a',name:'TSMC',sedol:'6889106'}, {id:'b',name:'Samsung',sedol:'6771720'}],sources:[],themes:[],cases:[]}`,context);
  return {context,node,rows,portfolioCards,deletePortfolioButtons,loadThemeButtons,deleteThemeButtons,storage,
    run:code=>vm.runInContext(code,context)};
}

test('Portfolio format rejects missing and duplicate template headers', async()=>{
  for (const headers of [
    ['Company notes','Portfolio wt','Reference wt','SEDOL'],
    ['Company','Company','Portfolio wt','Reference wt','SEDOL'],
    ['Company','SEDOL','Reference wt']
  ]) {
    const {context,run}=setup();
    context.XLSX={read:()=>({Sheets:{Sheet1:{}},SheetNames:['Sheet1']}),
      utils:{sheet_to_json:()=>[headers,['TSMC','123',1,1]]}};
    context.testFile={arrayBuffer:async()=>new ArrayBuffer(0)};
    await assert.rejects(run('parseExcelFile(testFile)'),/column|headers/i);
  }
});

test('Portfolio format accepts extra columns and extracts only relevant fields', async()=>{
  const {context,run}=setup();
  const headers=['Company','Bloomberg ticker','Country','Sector','SEDOL','Portfolio wt','Reference wt','Active wt','AI rationale','Remarks'];
  context.XLSX={read:()=>({Sheets:{Sheet1:{}},SheetNames:['Sheet1']}),
    utils:{sheet_to_json:()=>[headers,['TSMC','2330 TT','Taiwan','Technology','6889106',1,1,0,'Ignored rationale','Ignored remark']]}};
  context.testFile={arrayBuffer:async()=>new ArrayBuffer(0)};
  const rows=await run('parseExcelFile(testFile)');
  assert.equal(rows.length,1);
  assert.equal(rows[0].name,'TSMC');
  assert.equal(rows[0].portfolio_wt,1);
  assert.doesNotMatch(JSON.stringify(rows),/Ignored rationale|Ignored remark/);
});
test('Portfolio import reports an HTML proxy error clearly', async()=>{
  const {context,run} = setup();
  context.fetch = async()=>({status:502, json:async()=>{ throw new SyntaxError('Unexpected token <'); }});
  await assert.rejects(run("api('portfolio/import', {}, {timeoutMs: 50})"),
    /Portfolio import could not be completed by the server \(HTTP 502\)/);
});
test('Workspace state redirects to sign in when the session expires', async()=>{
  const {context,run} = setup();
  context.window.location = { pathname:'/', assign(url) { this.redirectedTo = url; } };
  context.fetch = async()=>({status:401, ok:false, json:async()=>({error:'Login required'})});
  await assert.rejects(run("apiGet('state')"),/session has expired/i);
  assert.equal(context.window.location.redirectedTo, '/login');
});
test('Workspace state reports a non-JSON server response clearly', async()=>{
  const {context,run} = setup();
  context.fetch = async()=>({status:502, ok:false, json:async()=>{ throw new SyntaxError('Unexpected token <'); }});
  await assert.rejects(run("apiGet('state')"),/server returned an invalid response \(HTTP 502\)/i);
});
test('Dashboard selection survives reload with independently selected theme and portfolio',()=>{
  const first=setup();
  first.run("selectedDashboardThemeId='t1'; selectedDashboardPortfolioId='p1'; saveDashboardSelection()");
  const next=setup();
  next.storage.set('theme-lab-dashboard-selection',first.storage.get('theme-lab-dashboard-selection'));
  next.run("state.themes=[{id:'t1',approved:true}]; state.portfolio_imports=[{id:'p1',theme_id:'t1'}]; restoreDashboardSelection()");
  assert.equal(next.run('selectedDashboardThemeId'),'t1');
  assert.equal(next.run('selectedDashboardPortfolioId'),'p1');
  next.run("state.portfolio_imports[0].theme_id='other'; restoreDashboardSelection()");
  assert.equal(next.run('selectedDashboardPortfolioId'),'p1');
});

test('AI source completion notifies on other pages only once',()=>{
  const {run,node}=setup();
  const dialog = node('#alert-modal');
  let opened = 0;
  dialog.showModal = () => { opened++; dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  node('#alert-modal-ok').focus = () => {};
  run("currentView='dashboard'; trackedSourceSearches.add('job1'); state.search_runs=[{id:'job1',status:'incomplete',results_count:3,missing:[{}]}]; notifyFinishedSourceSearches()");
  assert.match(node('#alert-modal-message').textContent,/AI source search is over.*3 documents saved.*1 company/);
  assert.equal(dialog.open,true);
  node('#alert-modal-ok').onclick();
  run('notifyFinishedSourceSearches()');
  assert.equal(dialog.open,false);
  assert.equal(opened,1);
});

test('AI source completion explains when the five-minute limit is reached',()=>{
  const {run,node}=setup();
  node('#alert-modal').showModal = () => {};
  node('#alert-modal-ok').focus = () => {};
  run("trackedSourceSearches.add('job1'); state.search_runs=[{id:'job1',status:'incomplete',completion_reason:'time_limit_reached',results_count:3,missing:[{}]}]; notifyFinishedSourceSearches()");
  assert.equal(node('#alert-modal-title').textContent,'Source search timed out');
  assert.match(node('#alert-modal-message').textContent,/reached its 5-minute limit.*3 documents saved.*1 company/);
});

test('Source search shows portfolios independently of theme and locks during an active search', async()=>{
  const {context,run,node}=setup();
  context.setTimeout = () => 1;
  context.document.querySelectorAll = selector =>
    selector === 'input[name="company_ids"]:checked' ? [{value:'a'}] :
      selector === 'input[name="source_types"]:checked' ? [{value:'earnings_call'}] : [];
  await run(`currentView='sources'; selectedDashboardThemeId='t';
    state.portfolio_imports=[
      {id:'p',theme_id:'unrelated-theme',name:'Portfolio',rows:[{company_id:'a'}]},
      {id:'p2',theme_id:'t',name:'Another Portfolio',rows:[{company_id:'a'}]}
    ];
    let finishSubmission;
    api=()=>new Promise(resolve=>{finishSubmission=resolve;});
    refresh=async()=>{};
    viewSources();`);
  assert.match(node('#page-content').innerHTML,/Another Portfolio/);
  assert.match(node('#page-content').innerHTML,/Portfolio/);
  const request = node('#btn-run-search').onclick();
  assert.equal(node('#source-search-settings').disabled,true);
  assert.equal(node('#source-portfolio-select').disabled,true);
  run(`finishSubmission({search_run:{id:'job',status:'running',portfolio_id:'p',
    company_ids:['a'],source_types:['earnings_call'],latest_documents:false,cutoff:'2025-03-01'}})`);
  await request;
  assert.match(node('#page-content').innerHTML,/<fieldset id="source-search-settings"[^>]* disabled/);
  assert.match(node('#page-content').innerHTML,/value="earnings_call"\s+checked/);
  assert.equal(node('#src-cutoff').value,'2025-03-01');
  assert.match(node('#page-content').innerHTML,/id="btn-cancel-source-search"\s*>/);
  await run("state.search_runs[0].status='cancelling'; viewSources()");
  assert.match(node('#page-content').innerHTML,/id="btn-cancel-source-search" disabled/);
  run("state.search_runs[0].status='cancelled'; updateSourceSearchLock()");
  assert.equal(node('#source-search-settings').disabled,false);
  assert.equal(node('#source-portfolio-select').disabled,false);
  assert.equal(node('#btn-run-search').disabled,false);
});

test('Source counts and search results update during polling without rerendering the form', async()=>{
  const {context,run,node,rows}=setup();
  rows[0].dataset.cid = 'a';
  const checkbox = {checked:false};
  rows[0].querySelector = () => checkbox;
  let poll;
  context.setTimeout = callback => { poll = callback; return 1; };
  run(`selectedSourcePortfolioId='p'; state.portfolio_imports=[{id:'p',rows:[{company_id:'a'}]}];
    currentView='sources'; state.search_runs=[{id:'job',status:'running',results:[]}];
    refresh=async()=>{
      state.source_summaries=[{company_id:'a',count:3,types:{annual_report:2,regulatory_filing:1}}];
      state.search_runs[0].results=[{company_id:'a',title:'Annual Report'},{company_id:'b',title:'Other Portfolio'}];
    };`);
  node('#src-detail-a').style.display='none';
  node('#src-lib-filter').value='TSMC';
  run('monitorSourceSearches()');
  await poll();
  assert.match(node('#src-checklist-count-a').innerHTML,/2\/6 source types/);
  assert.match(node('#src-coverage-a').innerHTML,/2 \/ 6/);
  assert.match(node('#source-library-count').textContent,/3 documents/);
  assert.match(node('#source-search-results').innerHTML,/1 candidate filings.*[\s\S]*Annual Report/);
  assert.doesNotMatch(node('#source-search-results').innerHTML,/Other Portfolio/);
  assert.equal(node('#src-lib-filter').value,'TSMC');
  assert.equal(checkbox.checked,false);
  assert.equal(node('#btn-run-search').disabled,true);
});

test('An open company row reloads its documents when coverage changes', async()=>{
  const {context,run,node}=setup();
  run(`currentView='sources'; selectedSourcePortfolioId='p';
    state.portfolio_imports=[{id:'p',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:2,types:{annual_report:2}}];
    api=async()=>({sources:[{id:'new',company_id:'a',source_type:'annual_report'}]})`);
  node('#src-detail-a').style.display='table-row';
  context.document.createElement=()=>({content:{querySelector:()=>({innerHTML:'updated documents'})},innerHTML:''});
  await run("updateSourceLibraryProgress([{company_id:'a',count:1,types:{annual_report:1}}])");
  assert.equal(node('#src-detail-a').innerHTML,'updated documents');
  assert.equal(run("state.sources[0].id"),'new');
});

test('The selected page is restored after reloading the app',()=>{
  const first = setup();
  first.run(`setView('sources')`);
  assert.equal(first.storage.get('theme-lab-current-view'),'sources');
  const reloaded = setup();
  reloaded.storage.set('theme-lab-current-view','sources');
  reloaded.run('restoreSavedView()');
  assert.equal(reloaded.run('currentView'),'sources');
});
test('Portfolio Exposure Dashboard replaces Overview as the landing page',async()=>{
  const {run,node} = setup();
  await run('render()');
  assert.equal(node('#page-title').textContent,'Portfolio Exposure Dashboard');
  assert.match(node('#page-content').innerHTML,/No approved theme/);
  const sidebar = fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.match(sidebar,/data-view="dashboard">\s*<div class="step-num">◈<\/div>\s*<div class="step-text">\s*<strong>Portfolio Exposure Dashboard<\/strong>/);
  assert.doesNotMatch(sidebar,/data-view="overview"|<strong>Overview<\/strong>/);
  assert.doesNotMatch(sidebar,/Local workspace|Revenue exposure research|ai-status-label/);
  assert.equal((sidebar.match(/data-view="dashboard"/g)||[]).length,1);
});
test('Portfolio Import and Source Documents can be opened independently of theme selection',async()=>{
  const {run,node}=setup();
  await run('viewPortfolio()');
  assert.match(node('#page-content').innerHTML,/Import portfolio Excel/);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/Import a portfolio to view its source documents/);
  assert.doesNotMatch(node('#page-content').innerHTML,/source-portfolio-select|source-form/);
});

test('Dashboard theme and portfolio selectors are independent and refresh the selected snapshot',async()=>{
  const {run,node} = setup();
  run(`state = {
    themes: [
      {id:'theme-1',theme_name:'AI Infrastructure',approved:true,nodes:[]},
      {id:'theme-2',theme_name:'Clean Energy',approved:true,nodes:[]}
    ],
    portfolio_imports: [
      {id:'portfolio-1',theme_id:'theme-1',name:'Core',holdings_date:'2026-10-01',snapshot_id:'snapshot-1'},
      {id:'portfolio-2',theme_id:'theme-2',name:'Growth',holdings_date:'2026-10-02',snapshot_id:'snapshot-2'}
    ],
    snapshots: [
      {id:'snapshot-1',benchmark_name:'Index A',portfolio:[]},
      {id:'snapshot-2',benchmark_name:'Index B',portfolio:[]}
    ],
    companies:[],sources:[],cases:[]
  };
  const summaryMetric = {point:0,lower:0,upper:0};
  const dashboardData = {
    summary:{portfolio:summaryMetric,benchmark:summaryMetric,active:summaryMetric},
    coverage:{portfolio:0,benchmark:0},companies:[]
  };
  const dashboardCalls=[];
  api=async (_path, data)=>{dashboardCalls.push(data);return dashboardData;};`);
  await run('viewDashboard()');
  assert.match(node('#page-content').innerHTML,/AI Infrastructure/);
  assert.match(node('#page-content').innerHTML,/Clean Energy/);
  assert.match(node('#page-content').innerHTML,/value="portfolio-1"/);
  assert.match(node('#page-content').innerHTML,/value="portfolio-2"/);
  assert.equal(run('dashboardCalls.length'),0);
  node('#dashboard-theme-select').change({target:{value:'theme-2'}});
  await run('new Promise(resolve=>setTimeout(resolve,0))');
  assert.match(node('#page-content').innerHTML,/Growth/);
  assert.match(node('#page-content').innerHTML,/Core/);
  assert.equal(run('dashboardCalls[0].theme_id'),'theme-2');
  assert.equal(run('dashboardCalls[0].snapshot_id'),'snapshot-2');

  node('#dashboard-theme-select').change({target:{value:'theme-1'}});
  await run('new Promise(resolve=>setTimeout(resolve,0))');
  assert.equal(run('dashboardCalls.at(-1).theme_id'),'theme-1');
  assert.equal(run('dashboardCalls.at(-1).snapshot_id'),'snapshot-2');

  node('#dashboard-portfolio-select').change({target:{value:'portfolio-1'}});
  await run('new Promise(resolve=>setTimeout(resolve,0))');
  assert.equal(run('dashboardCalls.at(-1).snapshot_id'),'snapshot-1');
});
test('Dashboard shows a loading spinner until its data request completes',async()=>{
  const {run,node} = setup();
  run(`state = {
    themes:[{id:'theme-1',theme_name:'AI',approved:true,nodes:[]}],
    portfolio_imports:[{id:'portfolio-1',theme_id:'theme-1',name:'Core',snapshot_id:'snapshot-1'}],
    snapshots:[{id:'snapshot-1',portfolio:[]}],
    companies:[],sources:[],cases:[]
  };
  selectedDashboardThemeId='theme-1';
  let resolveDashboard;
  api=()=>new Promise(resolve=>{resolveDashboard=resolve;});`);

  const render = run('viewDashboard()');
  assert.match(node('#page-content').innerHTML,/dashboard-loading/);
  assert.match(node('#page-content').innerHTML,/dashboard-loading-spinner/);
  assert.match(node('#page-content').innerHTML,/Loading portfolio exposure/);
  run(`resolveDashboard({
    summary:{portfolio:{point:0},benchmark:{point:0},active:{point:0}},
    coverage:{portfolio:0,benchmark:0},companies:[]
  })`);
  await render;
  assert.doesNotMatch(node('#page-content').innerHTML,/dashboard-loading/);
  assert.match(node('#page-content').innerHTML,/Company-level exposure/);
  assert.match(node('#page-content').innerHTML,/class="label">Portfolio exposure</);
  assert.match(node('#page-content').innerHTML,/class="label">ETF AAXJ exposure</);
  assert.match(node('#page-content').innerHTML,/class="metric-grid dashboard-summary-metrics"/);
});
test('Dashboard charts top ten portfolio contributions and aggregates the pie by Tier-1',async()=>{
  const {run,node} = setup();
  run(`state = {
    themes:[{id:'theme-1',theme_name:'AI',approved:true,nodes:[
      {node_id:'compute',name:'Compute',children:[
        {node_id:'chips',name:'Chips',children:[]},
        {node_id:'cloud',name:'Cloud',children:[]}
      ]},
      {node_id:'energy',name:'Energy',children:[
        {node_id:'wind',name:'Wind',children:[]}
      ]}
    ]}],
    portfolio_imports:[{id:'portfolio-1',theme_id:'theme-1',name:'Core',snapshot_id:'snapshot-1'}],
    snapshots:[{id:'snapshot-1',portfolio:[]}],
    companies:Array.from({length:12},(_,i)=>({id:'company-'+(i+1),name:i===0?'TSMC':'Company '+(i+1)})),
    sources:[],
    cases:Array.from({length:12},(_,i)=>({
      company_id:'company-'+(i+1),theme_id:'theme-1',review_status:'approved',
      estimate:{
        node_exposures:{compute:{point:null,upper:1},energy:{point:1,upper:1}},
        subthemes:{chips:{point:0.5},cloud:{point:null,upper:0.5},wind:{point:1}}
      }
    }))
  };
  selectedDashboardThemeId='theme-1';
  api=async()=>({
    summary:{portfolio:{point:0.25},benchmark:{point:0.20},active:{point:0.05}},
    coverage:{portfolio:0,benchmark:0},
    companies:Array.from({length:12},(_,i)=>({
      company_id:'company-'+(i+1),assessed:true,
      weights:{portfolio:0.24-i*0.02,benchmark:0.3,active:-0.06-i*0.02},
      exposure:{point:0.5},
      contributions:{
        active:{point:-0.06-i*0.02},
        portfolio:{point:0.12-i*0.01}
      }
    }))
  });`);
  await run('viewDashboard()');
  const html = node('#page-content').innerHTML;
  assert.match(html,/<div class="label">Portfolio exposure<\/div>/);
  assert.match(html,/<div class="label">ETF AAXJ exposure<\/div>/);
  assert.match(html,/<div class="label">Active exposure<\/div>/);
  assert.match(html,/Top 10 of 12/);
  assert.match(html,/Portfolio exposure contribution \(%\)/);
  assert.match(html,/TSMC/);
  const chart = html.match(/class="portfolio-contribution-chart"[\s\S]*?<\/svg>/)?.[0] || '';
  assert.match(chart,/Company 10/);
  assert.doesNotMatch(chart,/Company 11/);
  assert.doesNotMatch(chart,/contribution-bar negative/);
  assert.doesNotMatch(chart,/>-[\d.]+%<\/text>/);
  assert.doesNotMatch(html,/id="dashboard-tier1-select"/);
  assert.match(html,/aria-label="Portfolio exposure breakdown by Tier-1 theme"/);
  assert.match(html,/Compute/);
  assert.match(html,/Energy/);
  assert.doesNotMatch(node('#subtheme-pie-content').innerHTML,/No Tier-1 exposure/);
  assert.doesNotMatch(node('#subtheme-pie-content').innerHTML,/Chips|Cloud|Wind/);
});
test('Dashboard demo toggle previews sample data without changing workspace data or selections',async()=>{
  const {run,node,storage} = setup();
  run(`state = {
    themes:[{id:'real-theme',theme_name:'Real Theme',approved:true,nodes:[]}],
    portfolio_imports:[{id:'real-portfolio',theme_id:'real-theme',name:'Real Portfolio',snapshot_id:'real-snapshot'}],
    snapshots:[{id:'real-snapshot',portfolio:[]}],
    companies:[{id:'real-company',name:'Real Company'}],
    sources:[],cases:[]
  };
  selectedDashboardThemeId='real-theme';
  selectedDashboardPortfolioId='real-portfolio';
  let dashboardApiCalls=0;
  api=async()=>{dashboardApiCalls++;return {
    summary:{portfolio:{point:0},benchmark:{point:0},active:{point:0}},
    coverage:{portfolio:0,benchmark:0},companies:[]
  };};`);
  const initialWorkspace = run('JSON.stringify(state)');
  await run('viewDashboard()');
  const savedSelection = storage.get('theme-lab-dashboard-selection');
  assert.match(node('#page-content').innerHTML,/id="dashboard-demo-toggle"/);
  node('#dashboard-demo-toggle').change({target:{checked:true}});
  const demoHtml = node('#page-content').innerHTML;
  assert.match(demoHtml,/Illustrative sample only; your workspace data is unchanged/);
  assert.match(demoHtml,/Taiwan Semiconductor Mfg/);
  assert.match(demoHtml,/Portfolio exposure contributions/);
  assert.equal(run('dashboardApiCalls'),1);
  assert.equal(run('JSON.stringify(state)'),initialWorkspace);
  assert.equal(run('selectedDashboardThemeId'),'real-theme');
  assert.equal(run('selectedDashboardPortfolioId'),'real-portfolio');
  assert.equal(storage.get('theme-lab-dashboard-selection'),savedSelection);

  node('#dashboard-demo-toggle').change({target:{checked:false}});
  await run('new Promise(resolve=>setTimeout(resolve,0))');
  assert.match(node('#page-content').innerHTML,/Real Theme/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Taiwan Semiconductor Mfg/);
  assert.equal(run('dashboardApiCalls'),2);
  assert.equal(run('JSON.stringify(state)'),initialWorkspace);
});
test('First 26 demo exposures and weighted totals reconcile to the manual workbook',()=>{
  const {run}=setup();
  const fixture=JSON.parse(run('JSON.stringify(analysisDemoData())'));
  assert.deepEqual(fixture.companyRows.map(row=>row.exposure.point),
    [.25,.13,.55,0,.03,.063,0,0,.38,0,.095,0,0,.29,0,0,0,.20,0,0,0,0,0,0,.2665,0]);
  assert.ok(Math.abs(fixture.dashboard.summary.portfolio.point-.14152794808977343)<1e-12);
  assert.ok(Math.abs(fixture.dashboard.summary.benchmark.point-.0950110085681377)<1e-12);
  assert.ok(Math.abs(fixture.dashboard.summary.active.point-.04651693952163573)<1e-12);
});
test('Dashboard demo toggle works when the workspace has no approved theme or portfolio',async()=>{
  const {run,node} = setup();
  run(`let dashboardApiCalls=0;
  api=async()=>{dashboardApiCalls++;return null;};`);
  await run('viewDashboard()');
  node('#dashboard-demo-toggle').change({target:{checked:true}});
  assert.match(node('#page-content').innerHTML,/Taiwan Semiconductor Mfg/);
  assert.doesNotMatch(node('#page-content').innerHTML,/id="dashboard-tier1-select"/);
  assert.match(node('#page-content').innerHTML,/Portfolio exposure breakdown by Tier-1 theme/);
  assert.equal(run('dashboardApiCalls'),0);
});
test('Dashboard New theme sits above the selector and opens a blank editor',async()=>{
  const {run,node} = setup();
  run(`state.themes=[{id:'theme-1',theme_name:'AI Infrastructure',approved:true,nodes:[]}]`);
  await run('viewDashboard()');
  const html = node('#page-content').innerHTML;
  assert.ok(html.indexOf('id="btn-new-theme"') < html.indexOf('id="dashboard-theme-select"'));
  assert.match(html,/class="dashboard-selectors">[\s\S]*class="dashboard-setup-header"[\s\S]*id="btn-new-theme"/);
  assert.match(html,/Analysis setup/);
  node('#btn-new-theme').click();
  assert.match(node('#page-content').innerHTML,/New theme scope/);
  assert.match(node('#page-content').innerHTML,/name="theme_name"[^>]*value=""/);
});

test('Theme Definition loads the dashboard-selected theme and keeps New theme blank',async()=>{
  const {run,node} = setup();
  run(`state.themes=[{id:'theme-1',theme_name:'AI Infrastructure',approved:true,nodes:[
    {node_id:'compute',parent_id:null,name:'Compute'},
    {node_id:'chips',parent_id:'compute',name:'Chips'}
  ]}]; _activeThemeId=null`);
  await run("selectedDashboardThemeId='theme-1'; setView('theme')");
  assert.match(node('#page-content').innerHTML,/Edit theme scope/);
  assert.match(node('#page-content').innerHTML,/name="theme_name"[^>]*value="AI Infrastructure"/);
  assert.match(node('#page-content').innerHTML,/1 Tier-1 · 1 Tier-2 activities/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Saved themes|data-load-theme/);
  assert.match(node('#page-actions').innerHTML,/id="btn-new-theme"/);
  assert.match(node('#page-actions').innerHTML,/id="btn-saved-themes"/);
  node('#btn-new-theme').click();
  assert.match(node('#page-content').innerHTML,/New theme scope/);
  assert.match(node('#page-content').innerHTML,/name="theme_name"[^>]*value=""/);
});
test('Theme Definition shows saved themes and loads one for editing',async()=>{
  const {run,node,loadThemeButtons} = setup();
  run(`state.themes=[
    {id:'theme-a',theme_name:'AI Infrastructure',approved:true,nodes:[]},
    {id:'theme-b',theme_name:'Renewable Energy',approved:true,nodes:[]}
  ]`);
  loadThemeButtons.push({
    dataset:{loadTheme:'theme-b'},
    addEventListener(type, handler) { this[type] = handler; }
  });
  await run('viewTheme()');
  node('#btn-saved-themes').click();
  assert.match(node('#page-content').innerHTML,/AI Infrastructure/);
  assert.match(node('#page-content').innerHTML,/Renewable Energy/);
  assert.match(node('#page-actions').innerHTML,/aria-expanded="true"/);
  loadThemeButtons[0].onclick();
  assert.match(node('#page-content').innerHTML,/Edit theme scope/);
  assert.match(node('#page-content').innerHTML,/value="Renewable Energy"/);
});
test('Generate taxonomy updates the mind map draft without saving a theme',async()=>{
  const {run,node} = setup();
  node('#theme-form').fields={theme_name:'AI Infrastructure'};
  run(`state.ai_configured=true;
    buildTaxonomyEditor=()=>'<textarea id="taxonomy-json-area"></textarea><div id="taxonomy-mindmap"></div>';
    updateTaxonomyMindmap=()=>{};
    const calls=[];
    api=async(path,data)=>{calls.push({path,data});return {nodes:[
      {node_id:'compute',parent_id:null,name:'Compute',definition:'Compute',examples:[]}
    ]};};
    refresh=async()=>{};
    render=async()=>{};`);
  await run('viewTheme()');
  node('#btn-gen-taxonomy').click();
  await run('new Promise(resolve=>setTimeout(resolve,0))');
  assert.equal(JSON.parse(node('#taxonomy-json-area').value)[0].name,'Compute');
  assert.equal(run('JSON.stringify(calls.map(call=>call.path))'),'["generate"]');
  assert.equal(node('#notice-area').innerHTML,'');
});
test('Selecting a saved theme or New theme clears the top banner',async()=>{
  const {run,node,loadThemeButtons} = setup();
  run(`state.themes=[{id:'theme-1',theme_name:'AI Infrastructure',approved:true,nodes:[]}]`);
  loadThemeButtons.push({
    dataset:{loadTheme:'theme-1'},
    addEventListener(type, handler) { this[type] = handler; }
  });
  await run('viewTheme()');
  run(`notice('Previous theme notice','info')`);
  loadThemeButtons[0].onclick();
  assert.equal(node('#notice-area').innerHTML,'');
  assert.match(node('#page-content').innerHTML,/Edit theme scope/);
  run(`notice('Previous new-theme notice','info')`);
  node('#btn-new-theme').click();
  assert.equal(node('#notice-area').innerHTML,'');
  assert.match(node('#page-content').innerHTML,/New theme scope/);
});
test('Portfolio Import lists all saved portfolios regardless of selected theme',async()=>{
  const {run,node,portfolioCards,deletePortfolioButtons} = setup();
  run(`state = {
    companies:[{id:'company-1',name:'TSMC',bloomberg_ticker:'2330 TT',sedol:'6889106'}],
    sources:[],
    portfolio_imports:[{
      id:'portfolio-1',theme_id:'other-theme',name:'Asia Core',holdings_date:'2026-10-01',file_name:'asia-core.xlsx',
      benchmark_name:'MSCI Asia ex Japan',snapshot_id:'snapshot-1',
      rows:[{company_id:'company-1',company_name:'TSMC',bloomberg_ticker:'2330 TT',
        sedol:'6889106',country:'Taiwan',sector:'Semiconductors',
        portfolio_weight:0.2,reference_weight:0.1,active_weight:0.1}]
    }, {
      id:'portfolio-2',theme_id:'theme-2',name:'Other Portfolio',holdings_date:'2026-10-02',
      benchmark_name:'MSCI Asia ex Japan',snapshot_id:'snapshot-2',rows:[]
    }]
  }; selectedDashboardThemeId='theme-1'; selectedDashboardPortfolioId='portfolio-1';`);
  portfolioCards.push({
    dataset:{openPortfolio:'portfolio-1'},
    addEventListener(type, handler) { this[type] = handler; }
  });
  await run('viewPortfolio()');
  assert.equal(node('#page-actions').innerHTML,'');
  assert.match(node('#page-content').innerHTML,/Import portfolio Excel/);
  assert.match(node('#page-content').innerHTML,/Existing portfolios/);
  assert.match(node('#page-content').innerHTML,/Existing portfolios <span class="badge badge-neutral">2<\/span>/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);
  assert.match(node('#page-content').innerHTML,/Other Portfolio/);
  assert.match(node('#page-content').innerHTML,/asia-core\.xlsx/);
  assert.doesNotMatch(node('#page-content').innerHTML,/btn-add-row|\+ Add row/);
  assert.match(node('#page-content').innerHTML,/data-open-portfolio="portfolio-1"/);
  assert.match(node('#page-content').innerHTML,/data-del-portfolio="portfolio-1"/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Company universe/);
  run("selectedDashboardPortfolioId=''");
  portfolioCards[0].click();
  assert.equal(run('selectedDashboardPortfolioId'),'');
  assert.equal(run('selectedSourcePortfolioId'),'portfolio-1');
  assert.match(node('#page-content').innerHTML,/TSMC/);
  assert.match(node('#page-content').innerHTML,/Portfolio wt/);
  assert.equal(node('#page-content').innerHTML.includes('20.0%'),true);
  node('#btn-back-portfolios').onclick();
  assert.match(node('#page-content').innerHTML,/Import portfolio Excel/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);
});
test('Deleting an existing portfolio confirms and removes it from the portfolio list',async()=>{
  const {run,node,deletePortfolioButtons} = setup();
  run(`selectedDashboardThemeId='theme-1'; state={portfolio_imports:[{id:'portfolio-1',theme_id:'theme-1',name:'Asia Core',snapshot_id:'snapshot-1',rows:[]}],
    snapshots:[],companies:[],sources:[],themes:[],cases:[]};
    showConfirm=async(message,options)=>{globalThis.confirmation={message,options};return true};
    const calls=[];
    api=async(path,data)=>{
      calls.push({path,data});
      state.portfolio_imports=state.portfolio_imports.filter(item=>item.id!==data.id);
      return {deleted:data.id}
    };
    refresh=async()=>{};
    render=async()=>{};`);
  deletePortfolioButtons.push({
    dataset:{delPortfolio:'portfolio-1'},
    addEventListener(type, handler) { this[type] = handler; }
  });
  await run('viewPortfolio()');
  await deletePortfolioButtons[0].click();
  assert.equal(run('confirmation.options.title'),'Delete portfolio');
  assert.match(run('confirmation.message'),/Asia Core/);
  assert.equal(run('JSON.stringify(calls)'), '[{"path":"portfolio/delete","data":{"id":"portfolio-1"}}]');
  assert.doesNotMatch(node('#page-content').innerHTML,/Asia Core/);
});
test('Delete confirmation buttons are centered and consistently aligned',()=>{
  const stylesheet = fs.readFileSync(path.join(__dirname,'../style.css'),'utf8');
  const pairStyles = stylesheet.match(/\.alert-modal-actions-pair\s*\{([^}]+)\}/)?.[1] || '';
  const buttonStyles = stylesheet.match(/\.alert-modal-actions-pair \.btn\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(pairStyles,/justify-content:\s*center/);
  assert.match(pairStyles,/align-items:\s*center/);
  assert.match(buttonStyles,/flex:\s*0 0 110px/);
  assert.match(buttonStyles,/justify-content:\s*center/);
});
test('Portfolio upload retains the original file bytes for the import request',async()=>{
  const {run} = setup();
  const encoded = await run(`fileToBase64({
    arrayBuffer:async()=>new Uint8Array([0,1,255]).buffer
  })`);
  assert.equal(encoded,'AAH/');
});
test('Dashboard hides bounds and export; Source Documents has no search header action',async()=>{
  const {run,node} = setup();
  run(`state = {
    themes:[{id:'theme-1',theme_name:'AI',approved:true,nodes:[]}],
    portfolio_imports:[{id:'portfolio-1',theme_id:'theme-1',name:'Core',snapshot_id:'snapshot-1',rows:[]}],
    snapshots:[{id:'snapshot-1',benchmark_name:'Index',portfolio:[]}],
    companies:[],sources:[],cases:[]
  };
  selectedDashboardThemeId='theme-1';
  const metric={point:0,lower:0,upper:0};
  api=async()=>({summary:{portfolio:metric,benchmark:metric,active:metric},
    coverage:{portfolio:0,benchmark:0},companies:[]});`);
  await run('viewDashboard()');
  assert.doesNotMatch(node('#page-actions').innerHTML,/Export/);
  assert.doesNotMatch(node('#page-content').innerHTML,/\b(bounds|lower|upper)\b/i);
  await run('viewSources()');
  assert.equal(node('#page-actions').innerHTML,'');
  assert.doesNotMatch(node('#page-actions').innerHTML,/Search for documents/);
  assert.match(node('#page-content').innerHTML,/btn-run-search/);
});
test('Source Documents has an independent portfolio selector and scopes the AI search',async()=>{
  const {run,node,context,storage} = setup();
  run(`state = {
    companies:[
      {id:'company-a',name:'TSMC',bloomberg_ticker:'2330 TT',sedol:'6889106'},
      {id:'company-b',name:'Samsung',bloomberg_ticker:'005930 KS',sedol:'6771720'}
    ],
    sources:[
      {id:'source-a',company_id:'company-a',source_type:'annual_report'},
      {id:'source-b',company_id:'company-b',source_type:'annual_report'}
    ],
    cases:[],themes:[],search_runs:[],
    portfolio_imports:[
      {id:'portfolio-a',theme_id:'theme-1',name:'Asia Core',rows:[{company_id:'company-a',portfolio_weight:1}]},
      {id:'portfolio-b',theme_id:'theme-1',name:'Korea Growth',rows:[{company_id:'company-b',portfolio_weight:1}]}
    ]
  };
  selectedDashboardThemeId='theme-1'; selectedDashboardPortfolioId='portfolio-b'; selectedSourcePortfolioId='portfolio-b';
  const searchCalls=[];
  api=async(path,data)=>{searchCalls.push({path,data});return {results:[]};};
  refresh=async()=>{};
  render=async()=>{};`);
  const originalQuery = context.document.querySelectorAll;
  const sourceTypes = [true, true, false].map(checked => ({
    checked, addEventListener(type, handler) { this[type] = handler; }
  }));
  context.document.querySelectorAll = selector =>
    selector === 'input[name="source_types"]' ? sourceTypes : originalQuery(selector);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/id="source-portfolio-select"/);
  assert.match(node('#page-content').innerHTML,/Choose up to 2 source types/);
  assert.equal(sourceTypes[2].disabled, true);
  sourceTypes[0].checked = false;
  sourceTypes[0].change();
  assert.equal(sourceTypes[2].disabled, false);
  sourceTypes[2].checked = true;
  sourceTypes[2].change();
  assert.equal(sourceTypes[0].disabled, true);
  assert.match(node('#page-content').innerHTML,/Korea Growth/);
  assert.match(node('#page-content').innerHTML,/Samsung/);
  assert.doesNotMatch(node('#page-content').innerHTML,/TSMC/);

  await node('#source-portfolio-select').change({target:{value:'portfolio-a'}});
  assert.equal(run('selectedSourcePortfolioId'),'portfolio-a');
  assert.equal(run('selectedDashboardPortfolioId'),'portfolio-b');
  assert.match(node('#page-content').innerHTML,/TSMC/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Samsung/);
  assert.equal(storage.get('theme-lab-source-portfolio-selection'),'portfolio-a');
  const year = Number(new Intl.DateTimeFormat('en', {timeZone:'Asia/Singapore',year:'numeric'}).format(new Date()));
  assert.match(node('#page-content').innerHTML, new RegExp(`<div id="src-period">${year - 2} to ${year}</div>`));
  node('#src-latest-documents').checked = true;

  context.document.querySelectorAll = selector =>
    selector === 'input[name="company_ids"]:checked' ? [{value:'company-a'}] :
      selector === 'input[name="source_types"]:checked' ? [{value:'annual_report'}] : [];
  await node('#btn-run-search').onclick();
  assert.equal(run('searchCalls[0].path'),'source/search');
  assert.equal(run('searchCalls[0].data.portfolio_id'),'portfolio-a');
  assert.equal(run('searchCalls[0].data.discover_web'),true);
  assert.equal(run('searchCalls[0].data.latest_documents'),true);
  assert.equal(run('searchCalls[0].data.period'),undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(run('searchCalls[0].data.company_ids'))),['company-a']);
  assert.deepEqual(JSON.parse(JSON.stringify(run('searchCalls[0].data.source_types'))),['annual_report']);
});
test('Source search failure explains provider quota limits and search coverage',async()=>{
  const {run,node} = setup();
  run(`state = {
    companies:[{id:'company-a',name:'TSMC'}],sources:[],cases:[],themes:[],
    search_runs:[{
      id:'search-failed',status:'failed',results_count:0,results:[],missing:[],
      company_ids:Array.from({length:75},(_,i)=>'company-'+i),companies_completed:[],
      errors:[{reason:'AI source provider rate limit exceeded (HTTP 429); retry after the quota resets.'}]
    }],
    portfolio_imports:[{id:'portfolio-a',theme_id:'theme-1',name:'Asia Core',rows:[{company_id:'company-a'}]}]
  };
  selectedDashboardThemeId='theme-1';`);
  await run('viewSources()');
  assert.doesNotMatch(node('#page-content').innerHTML,/0 of 75 companies searched/);
  run("displayedSourceSearchId='search-failed'");
  await run('viewSources()');
  const html = node('#page-content').innerHTML;
  assert.match(html,/0 documents saved; 0 of 75 companies searched/);
  assert.match(html,/Check the configured key, model availability, and free-tier quota/);
});
test('Source search quota completion shows actual completed companies',async()=>{
  const {run,node} = setup();
  run(`state = {
    companies:[{id:'company-a',name:'TSMC'}],sources:[],cases:[],themes:[],
    search_runs:[{
      id:'search-quota',status:'incomplete',completion_reason:'provider_quota',results_count:3,
      missing:[{},{}], company_ids:['company-a','company-b','company-c'], companies_completed:['company-a','company-b'],
      errors:[{reason:'Gemini returned no Google Search citations'},{reason:'Google Search quota reached (HTTP 429)'}]
    }],
    portfolio_imports:[{id:'portfolio-a',theme_id:'theme-1',name:'Asia Core',rows:[{company_id:'company-a'}]}]
  }; selectedDashboardThemeId='theme-1'; displayedSourceSearchId='search-quota';`);
  await run('viewSources()');
  const html = node('#page-content').innerHTML;
  assert.match(html,/stopped after 2 of 3 companies because the Gemini quota was reached/);
  assert.match(html,/3 documents saved/);
});
test('Source Documents can cancel an active AI search',async()=>{
  const {run,node,context} = setup();
  context.setTimeout=()=>1;
  run(`state = {
    companies:[{id:'company-a',name:'TSMC',bloomberg_ticker:'2330 TT',sedol:'6889106'}],
    sources:[],cases:[],themes:[],search_runs:[{id:'search-1',status:'running',results:[]}],
    portfolio_imports:[{id:'portfolio-a',theme_id:'theme-1',name:'Asia Core',rows:[{company_id:'company-a'}]}]
  };
  selectedDashboardThemeId='theme-1';
  currentView='sources';
  const calls=[];
  api=async(path,data)=>{calls.push({path,data});state.search_runs[0].status='cancelled';return {search_run:state.search_runs[0]};};
  refresh=async()=>{};`);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/Cancel search/);
  await node('#btn-cancel-source-search').click();
  assert.equal(run('calls[0].path'),'source/search/cancel');
  assert.equal(run('calls[0].data.id'),'search-1');
  assert.equal(run('state.search_runs[0].status'),'cancelled');
  assert.match(node('#page-content').innerHTML,/Source search stopped/);
});
test('Evaluate page and navigation entry are removed',()=>{
  const {run} = setup();
  const sidebar = fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.equal(run("VALID_VIEWS.has('evaluate')"),false);
  assert.equal(run("typeof viewEvaluate"),'undefined');
  assert.doesNotMatch(sidebar,/data-view="evaluate"|<strong>Evaluate<\/strong>/);
});
test('Obsolete standalone Review route is removed; unified Analysis & Review remains',()=>{
  const {run} = setup();
  assert.equal(run("VALID_VIEWS.has('review')"),false);
  assert.equal(run("typeof viewReview"),'undefined');
  assert.equal(run("typeof viewAnalysis"),'function');
});
test('Step 3 filters immediately by SEDOL, ticker, name and dropdown; clearing restores all', async()=>{
  const {run,node,rows} = setup();
  run(`selectedDashboardThemeId='t'; selectedDashboardPortfolioId='p'; state.portfolio_imports=[{id:'p',theme_id:'t',name:'Both',rows:[{company_id:'a'},{company_id:'b'}]}]`);
  await run('viewSources()');
  const search = node('#src-global-search');
  for (const query of ['6889106','2330','TSMC']) {
    search.input({target:{value:query}});
    assert.equal(rows[0].style.display,'flex'); assert.equal(rows[1].style.display,'none');
  }
  node('#src-sedol-select').change({target:{value:'b'}});
  assert.equal(search.value,'6771720'); assert.equal(rows[0].style.display,'none');
  node('#src-sedol-select').change({target:{value:''}});
  assert.equal(search.value,''); assert.ok(rows.every(r=>r.style.display==='flex'));
});
test('Step 3 renders six source categories even before uploads, and counts distinct types', async()=>{
  const {run,node} = setup();
  run(`selectedDashboardThemeId='t'; selectedDashboardPortfolioId='p'; state.portfolio_imports=[{id:'p',theme_id:'t',name:'Both',rows:[{company_id:'a'},{company_id:'b'}]}]`);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/Market Cap/);
  assert.match(node('#page-content').innerHTML,/Net Revenue/);
  assert.match(node('#page-content').innerHTML,/Expand all companies/);
  const html = run(`sourceLibrary(
    [{company_id:'a',source_type:'annual_report'}, {company_id:'a',source_type:'annual_report'}],
    [{id:'a',name:'TSMC'}], {rows:[{company_id:'a'}]}
  )`);
  assert.match(html,/1 \/ 6/); assert.doesNotMatch(html,/2 \/ 6/);
  for(const label of ['Annual Report','Earnings Call Transcript','Investor Presentation','Sustainability / ESG Report','Company Website / IR Page','Regulatory Filing']) assert.ok(html.includes(label));
});
test('Expanding all companies still displays returned document links when one company request fails', async()=>{
  const {context,run,node} = setup();
  const masters = [
    {id:'src-master-a', style:{display:''}},
    {id:'src-master-b', style:{display:''}}
  ];
  const detailA = node('#src-detail-a');
  const detailB = node('#src-detail-b');
  detailA.style.display = 'none';
  detailB.style.display = 'none';
  const chevronA = node('#src-chev-a');
  const chevronB = node('#src-chev-b');
  const button = node('#btn-toggle-all-sources');
  context.document.querySelectorAll = selector => {
    if (selector === '.source-master-row') return masters;
    if (selector === '.source-detail-row') return [detailA, detailB];
    if (selector === '.src-chevron') return [chevronA, chevronB];
    return [];
  };
  run(`state.sources=[];
    api=async (path, data) => data.company_id === 'a'
      ? {sources:[{id:'doc-a',company_id:'a',source_type:'annual_report',title:'TSMC annual report',url:'https://example.test/a'}]}
      : Promise.reject(new Error('Source service unavailable'));
    viewSources=async()=>{};`);
  await run('window.toggleAllSourceRows()');
  assert.equal(run('state.sources[0].id'), 'doc-a');
  assert.equal(detailA.style.display, 'table-row');
  assert.equal(detailB.style.display, 'table-row');
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Collapse all companies');
  assert.match(node('#notice-area').innerHTML, /Links for the other companies are shown/);
});
test('Financials use evidence and keep missing values unknown',()=>{
  const {run} = setup();
  assert.equal(run(`getCompanyFinancials({id:'a',bloomberg_ticker:'2330 TT'}, {}, [], []).mcap`),'—');
  assert.equal(run(`getCompanyFinancials({id:'a'}, {}, [{filename:'report.txt',financial_facts:[{metric:'revenue',display:'USD 100 million',page:2}]}], []).rev`),'USD 100 million');
});

test('Background financial completion shows quota failures and unresolved companies in a modal',async()=>{
  const {run,node,context} = setup();
  context.setTimeout = () => 1;
  node('#loading-overlay').hidden = true;
  const dialog = node('#alert-modal');
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  node('#alert-modal-ok').focus = () => {};
  dialog.classList.toggle = (name, enabled) => { dialog.dataset[name] = enabled; };
  run(`currentView = 'dashboard';
    const completed = {id:'financial-job',job_type:'financials',portfolio_id:'p',status:'incomplete',updated:0,failed:2,unresolved:1,results:{
      a:{error:'Financial AI quota or rate limit exceeded (HTTP 429).'},
      b:{error:'Financial AI quota or rate limit exceeded (HTTP 429).'}
    }};
    api = async () => ({financial_run:{...completed,status:'running'}});
    refresh = async () => {state.search_runs=[completed];};`);
  await run("window.populatePortfolioFinancials('p')");
  const message = node('#alert-modal-message').textContent;
  assert.match(message,/Lookup failed for 2 companies/);
  assert.match(message,/No supported financial figures found for 1 of the companies/);
  assert.match(message,/quota.*HTTP 429/);
  assert.equal(message.match(/HTTP 429/g).length,1);
  assert.equal(dialog.open,true);
  assert.equal(dialog.dataset['search-completion-modal'],true);
  assert.equal(node('#notice-area').innerHTML,'');
  assert.equal(node('#loading-overlay').hidden,true);
  assert.equal(node('#btn-populate-financials').disabled,false);
  node('#alert-modal-ok').onclick();
  assert.equal(dialog.open,false);
});

test('Financial request errors reach the modal through the normal button handler',async()=>{
  const {run,node} = setup();
  const dialog = node('#alert-modal');
  node('#loading-overlay').hidden = true;
  dialog.showModal = () => { dialog.open = true; };
  node('#alert-modal-ok').focus = () => {};
  run(`api = async () => { throw new Error('Service unavailable (HTTP 503)'); };`);
  await run("window.populatePortfolioFinancials('p')");
  assert.equal(dialog.open,true);
  assert.equal(node('#alert-modal-title').textContent,'Financial lookup failed');
  assert.match(node('#alert-modal-message').textContent,/Service unavailable \(HTTP 503\)/);
  assert.equal(node('#notice-area').innerHTML,'');
  assert.equal(node('#loading-overlay').hidden,true);
  assert.equal(node('#btn-populate-financials').disabled,false);
});

test('Financial lookup queues without a blocking overlay and survives returning to the page',async()=>{
  const {run,node,context} = setup();
  context.setTimeout = () => 1;
  node('#loading-overlay').hidden = true;
  run(`selectedSourcePortfolioId='p';
    api = async () => ({financial_run:{id:'financial-job',job_type:'financials',portfolio_id:'p',
      status:'running',phase:'queued',company_ids:['a','b'],companies_completed:[]}});
    refresh = async () => {};`);
  await run("window.populatePortfolioFinancials('p')");
  assert.equal(node('#loading-overlay').hidden,true);
  assert.equal(node('#btn-populate-financials').disabled,true);
  assert.match(node('#financial-search-status').textContent,/You can leave or log out.*Telegram/);
  assert.equal(run('activeSourceSearch()'),undefined);
  run('trackedSourceSearches.clear(); monitorSourceSearches()');
  assert.equal(run("trackedSourceSearches.has('financial-job')"),true);
});

test('Financial lookup shows and uses its cancel control',async()=>{
  const {run,node} = setup();
  run(`selectedSourcePortfolioId='p'; state.search_runs=[{id:'financial-job',job_type:'financials',portfolio_id:'p',
    status:'running',phase:'queued',company_ids:['a'],companies_completed:[]}];`);
  const html = run(`sourceLibrary([], [], [{id:'a',name:'TSMC'}], {id:'p',rows:[{company_id:'a'}]})`);
  assert.match(html,/id="btn-cancel-financials"[\s\S]*Cancel financial lookup/);
  run(`let cancelRequest; api=async(path,data)=>{
    cancelRequest={path,data};
    return {financial_run:{...state.search_runs[0],status:'cancelled'}};
  }; refresh=async()=>{};`);
  await run("window.cancelPortfolioFinancials('p')");
  assert.equal(run('cancelRequest.path'),'financials/cancel');
  assert.equal(run('cancelRequest.data.id'),'financial-job');
  assert.equal(run("state.search_runs[0].status"),'cancelled');
  assert.equal(node('#btn-cancel-financials').disabled,true);
});

test('Short and long search submission errors open the completion modal',async()=>{
  for (const button of ['#btn-run-search','#btn-run-long-search']) {
    const {run,node,context} = setup();
    context.document.querySelectorAll = selector =>
      selector === 'input[name="company_ids"]:checked' ? [{value:'a'}] :
      selector === 'input[name="source_types"]:checked' ? [{value:'annual_report'}] : [];
    const dialog = node('#alert-modal');
    dialog.showModal = () => { dialog.open = true; };
    node('#alert-modal-ok').focus = () => {};
    dialog.classList.toggle = (name, enabled) => { dialog.dataset[name] = enabled; };
    await run(`currentView='sources'; selectedDashboardThemeId='t'; selectedSourcePortfolioId='p';
      state.portfolio_imports=[{id:'p',theme_id:'t',name:'Portfolio',rows:[{company_id:'a'}]}];
      api=async()=>{throw new Error('Provider quota exceeded (HTTP 429)');};
      viewSources();`);
    await node(button).onclick();
    assert.equal(dialog.open,true);
    assert.equal(dialog.dataset['search-completion-modal'],true);
    assert.match(node('#alert-modal-message').textContent,/Could not start source search.*HTTP 429/);
    assert.equal(node(button).disabled,false);
  }
});
test('Financial citations count documents separately from source categories',()=>{
  const {run} = setup();
  const html = run(`sourceLibrary([], [{
    company_id:'a', count:12,
    types:Object.fromEntries(SIX_SOURCE_TYPES.map(({type}) => [type, 2]))
  }], [{
    id:'a', name:'TSMC', market_cap:'USD 980.0B', net_revenue:'USD 89.6B',
    market_cap_citation:'market cap 980.0b (4/6 sources)',
    net_revenue_citation:'net revenue 89.6 billion (4/6 sources)'
  }], {rows:[{company_id:'a'}]})`);
  assert.match(html,/market cap 980\.0b \(4\/12 documents\)/);
  assert.match(html,/net revenue 89\.6 billion \(4\/12 documents\)/);
  assert.match(html,/6 \/ 6/);
  assert.match(html,/View documents/);
  assert.equal(run(`getCompanyFinancials({id:'a'}, {}, [
    {financial_facts:Array(2).fill({metric:'revenue',display:'USD 100 million'})}, {}
  ], []).revCitation`), 'net revenue usd 100 million (1/2 documents)');
});
test('Unified Step 4 renders the queue and bulk analysis controls',async()=>{
  const {run,node} = setup();
  run(`state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[
      {id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b',excluded:true}]},
      {id:'korea',theme_id:'theme-a',name:'Korea Growth',rows:[{company_id:'b'}]}
    ];
    state.source_summaries=[{company_id:'a',count:3}];
    selectedDashboardThemeId='theme-a';
    selectedSourcePortfolioId='core';`);
  await run('viewAnalysis()');
  assert.equal(node('#page-title').textContent,'Calculate Exposure');
  assert.match(node('#page-content').innerHTML,/id="analysis-theme-select"/);
  assert.match(node('#page-content').innerHTML,/id="analysis-portfolio-select"/);
  assert.match(node('#page-content').innerHTML,/Company-level Exposure/);
  assert.doesNotMatch(node('#page-content').innerHTML,/analysis-company-search|Search company name/);
  assert.doesNotMatch(node('#page-content').innerHTML,/max-height:480px;overflow-y:auto/);
  assert.doesNotMatch(node('#page-content').innerHTML,/case-detail-panel|Review →|Review decision/);
  assert.match(node('#page-content').innerHTML,/<option value="core" selected>Asia Core<\/option>/);
  assert.match(node('#page-content').innerHTML,/TSMC/);
  assert.match(node('#page-content').innerHTML,/3 docs/);
  assert.match(node('#page-content').innerHTML,/AI theme/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);
  assert.match(node('#page-content').innerHTML,/Korea Growth/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Samsung/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Targeted passage analysis|Create manual case|btn-open-manual/);
  assert.match(node('#page-actions').innerHTML,/Analyse all ready companies/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Confidence color guide|Revenue figures align across all credible sources/);
  const sidebar = fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.match(sidebar,/<strong>Calculate Exposure<\/strong>/);
  assert.doesNotMatch(sidebar,/data-view="review"/);
  run("selectedDashboardPortfolioId='korea'; saveDashboardSelection()");
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/<option value="korea" selected>Korea Growth<\/option>/);
  assert.match(node('#page-content').innerHTML,/Samsung/);
  assert.doesNotMatch(node('#page-content').innerHTML,/TSMC/);
});
test('Analysis view omits manual AI analysis and stage pipeline cards',async()=>{
  const {run,node} = setup();
  run(`state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:3}];
    selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html = node('#page-content').innerHTML;
  assert.doesNotMatch(html,/Run AI analysis|Evidence passages|Stage pipeline|Fact<br>Extraction|Awaiting<br>Review/);
  assert.match(node('#page-actions').innerHTML,/Analyse all ready companies/);
  assert.match(html,/Analyse →/);
});
test('Per-stock Analyse queues a fresh theme exposure and confidence calculation',async()=>{
  const {run,node,context} = setup();
  context.setTimeout=()=>1;
  run(`state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:3}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core'; currentView='analysis';`);
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/onclick="rerunCompanyAnalysis\('a', this\)"[\s\S]*Analyse →/);
  run(`let rerunRequest; api=async(path,data)=>{
    rerunRequest={path,data};
    return {analysis_run:{id:'analysis-a',job_type:'analysis',status:'running',phase:'queued',theme_id:'theme-a',portfolio_id:'core',company_ids:['a'],companies_completed:[]}};
  };`);
  await run("window.rerunCompanyAnalysis('a', {disabled:false,textContent:'Analyse →'})");
  assert.equal(run('rerunRequest.path'),'analysis/start');
  assert.deepEqual(JSON.parse(run('JSON.stringify(rerunRequest.data)')),
    {theme_id:'theme-a',portfolio_id:'core',company_ids:['a'],force_recalculate:true});
  assert.equal(run("trackedAnalysisRuns.has('analysis-a')"),true);
  assert.match(node('#page-actions').innerHTML,/Analysis queued.*0 of 1 companies processed/);
  assert.match(node('#page-content').innerHTML,/Analysis queued/);
});
test('Bulk analysis queues source-ready and source-less companies that need exposure',async()=>{
  const {run,node,context} = setup();
  context.setTimeout=()=>1;
  run(`state.companies=[
      {id:'a',name:'TSMC'}, {id:'b',name:'Samsung'}, {id:'c',name:'SK Hynix'}, {id:'d',name:'No documents'}
    ];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'},{company_id:'a',security_id:'a2'},{company_id:'b'},{company_id:'c'},{company_id:'d'}]}];
    state.source_summaries=[{company_id:'a',count:3},{company_id:'b',count:2},{company_id:'c',count:1}];
    state.cases=[{id:'existing',theme_id:'theme-a',company_id:'b',review_status:'needs_review',estimate:{point:.2}}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core'; currentView='analysis';
    let bulkRequest; api=async(path,data)=>{
      bulkRequest={path,data};
      return {analysis_run:{id:'analysis-bulk',job_type:'analysis',status:'running',phase:'queued',theme_id:'theme-a',portfolio_id:'core',company_ids:data.company_ids,companies_completed:[]}};
    };`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/4 unique companies across 5 portfolio holdings/);
  await node('#btn-analyze-all').onclick();
  assert.equal(run('bulkRequest.path'),'analysis/start');
  assert.deepEqual(JSON.parse(run('JSON.stringify(bulkRequest.data)')),
    {theme_id:'theme-a',portfolio_id:'core',company_ids:['a','c','d']});
  assert.match(node('#page-actions').innerHTML,/Analysis queued.*0 of 3 companies processed/);
  assert.doesNotMatch(run('JSON.stringify(bulkRequest.data)'),/"b"/);
});
test('Bulk analysis includes source-ready failures and deferred companies for retry',async()=>{
  const {run,node,context}=setup();
  context.setTimeout=()=>1;
  run(`state.companies=[
      {id:'a',name:'Failed rerun'}, {id:'b',name:'Deferred'},
      {id:'c',name:'Completed'}, {id:'d',name:'New ready'}, {id:'e',name:'No sources'}
    ];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[
      {company_id:'a'},{company_id:'b'},{company_id:'c'},{company_id:'d'},{company_id:'e'}]}];
    state.source_summaries=[
      {company_id:'a',count:2},{company_id:'b',count:2},
      {company_id:'c',count:2},{company_id:'d',count:2}];
    state.cases=[
      {id:'old-a',theme_id:'theme-a',company_id:'a',review_status:'needs_review',estimate:{point:.1}},
      {id:'done-c',theme_id:'theme-a',company_id:'c',review_status:'approved',estimate:{point:.2}}];
    state.search_runs=[
      {id:'prior',job_type:'analysis',status:'incomplete',theme_id:'theme-a',portfolio_id:'core',
       company_ids:['a','b'],companies_completed:['a'],deferred_company_ids:['b'],
       outcomes:{a:'failed'},results:{a:{company_id:'a',status:'failed',error:'Provider quota reached'}}},
      {id:'done',job_type:'analysis',status:'completed',theme_id:'theme-a',portfolio_id:'core',
       company_ids:['c'],companies_completed:['c'],outcomes:{c:'success'}}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a';
    selectedDashboardPortfolioId='core'; currentView='analysis';
    let retryBulkRequest; api=async(path,data)=>{
      retryBulkRequest={path,data};
      return {analysis_run:{id:'retry-bulk',job_type:'analysis',status:'running',phase:'queued',
        theme_id:'theme-a',portfolio_id:'core',company_ids:data.company_ids,companies_completed:[]}};
    };`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/Analyse all ready companies \(4 of 5\)/);
  assert.match(node('#page-content').innerHTML,/queue-row pending-row ready-row[\s\S]*Failed rerun/);
  await node('#btn-analyze-all').onclick();
  assert.equal(run('retryBulkRequest.path'),'analysis/start');
  assert.deepEqual(JSON.parse(run('JSON.stringify(retryBulkRequest.data)')),
    {theme_id:'theme-a',portfolio_id:'core',company_ids:['a','b','d','e'],force_recalculate:true});
  assert.match(node('#page-actions').innerHTML,/id="btn-analyze-all" disabled/);
});
test('Bulk analysis retries saved zero and unknown cases but suppresses recent completed fallback searches',async()=>{
  const {run,node,context}=setup();
  context.setTimeout=()=>1;
  run(`state.companies=[
      {id:'zero',name:'Saved zero'}, {id:'unknown',name:'Saved unknown'},
      {id:'recent',name:'Recently checked zero'}, {id:'positive',name:'Resolved positive'}
    ];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[
      {company_id:'zero'},{company_id:'unknown'},{company_id:'recent'},{company_id:'positive'}]}];
    state.cases=[
      {id:'zero-case',theme_id:'theme-a',company_id:'zero',review_status:'needs_review',estimate:{point:0,upper:0}},
      {id:'unknown-case',theme_id:'theme-a',company_id:'unknown',review_status:'needs_review',estimate:{point:null,upper:.5}},
      {id:'recent-case',theme_id:'theme-a',company_id:'recent',review_status:'needs_review',estimate:{point:0,upper:0},
       fallback_web_search:{search_version:'3',status:'confirmed_zero',searched_at:'2999-01-01T00:00:00Z'}},
      {id:'positive-case',theme_id:'theme-a',company_id:'positive',review_status:'needs_review',estimate:{point:.2,upper:.2}}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core'; currentView='analysis';
    let fallbackBulkRequest; api=async(path,data)=>{
      fallbackBulkRequest={path,data};
      return {analysis_run:{id:'fallback-bulk',job_type:'analysis',status:'running',phase:'queued',
        theme_id:'theme-a',portfolio_id:'core',company_ids:data.company_ids,companies_completed:[]}};
    };`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/Analyse all ready companies \(2 of 4\)/);
  await node('#btn-analyze-all').onclick();
  assert.deepEqual(JSON.parse(run('JSON.stringify(fallbackBulkRequest.data)')),
    {theme_id:'theme-a',portfolio_id:'core',company_ids:['zero','unknown']});
});
test('Bulk analysis recalculates cases produced by an older exposure methodology',async()=>{
  const {run,node,context}=setup();
  context.setTimeout=()=>1;
  run(`state.companies=[{id:'old',name:'Old method'},{id:'current',name:'Current method'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'old'},{company_id:'current'}]}];
    state.cases=[
      {id:'old-case',theme_id:'theme-a',company_id:'old',review_status:'needs_review',estimate:{point:.6,calculation_version:'1.2.2'}},
      {id:'current-case',theme_id:'theme-a',company_id:'current',review_status:'needs_review',estimate:{point:.2,calculation_version:'1.3.0'}}];
    state.calculation_version='1.3.0'; state.ai_configured=true;
    selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core'; currentView='analysis';
    let versionRequest; api=async(path,data)=>{ versionRequest={path,data}; return {analysis_run:{
      id:'version-rerun',job_type:'analysis',status:'running',phase:'queued',theme_id:'theme-a',portfolio_id:'core',
      company_ids:data.company_ids,companies_completed:[]}}; };`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/Analyse all ready companies \(1 of 2\)/);
  await node('#btn-analyze-all').onclick();
  assert.deepEqual(JSON.parse(run('JSON.stringify(versionRequest.data)')),
    {theme_id:'theme-a',portfolio_id:'core',company_ids:['old'],force_recalculate:true});
});
test('Active batch locks only its companies and lets an unrelated failed stock retry',async()=>{
  const {run,node,context}=setup();
  context.setTimeout=()=>1;
  run(`state.companies=[{id:'a',name:'Active stock'},{id:'b',name:'Retry stock'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.source_summaries=[{company_id:'a',count:2},{company_id:'b',count:2}];
    state.cases=[{id:'old-b',theme_id:'theme-a',company_id:'b',review_status:'needs_review',estimate:{point:.1}}];
    state.search_runs=[
      {id:'failed-b',job_type:'analysis',status:'failed',theme_id:'theme-a',portfolio_id:'core',
       company_ids:['b'],companies_completed:['b'],outcomes:{b:'failed'},
       results:{b:{company_id:'b',status:'failed',error:'Provider quota reached'}}},
      {id:'active-a',job_type:'analysis',status:'running',phase:'analysing',theme_id:'theme-a',portfolio_id:'core',
       company_ids:['a'],companies_completed:[],outcomes:{},results:{}}
    ];
    state.ai_configured=true; selectedDashboardThemeId='theme-a';
    selectedDashboardPortfolioId='core'; currentView='analysis';
    let unrelatedRetryRequest; api=async(path,data)=>{
      unrelatedRetryRequest={path,data};
      return {analysis_run:{id:'retry-b',job_type:'analysis',status:'running',phase:'queued',
        theme_id:'theme-a',portfolio_id:'core',company_ids:['b'],companies_completed:[]}};
    };`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  const activeStart=html.indexOf('Active stock');
  const activeRow=html.slice(activeStart,html.indexOf('</tr>',activeStart));
  const retryStart=html.indexOf('Retry stock');
  const retryRow=html.slice(retryStart,html.indexOf('</tr>',retryStart));
  assert.match(node('#page-actions').innerHTML,/Analysing companies/);
  assert.match(activeRow,/Analysing…[\s\S]*data-analysis-run-id="active-a"[\s\S]*Cancel/);
  assert.match(retryRow,/Retry →/);
  assert.doesNotMatch(retryRow,/onclick="rerunCompanyAnalysis\('b', this\)" disabled/);
  await run("window.rerunCompanyAnalysis('b', {disabled:false,textContent:'Retry →'})");
  assert.equal(run('unrelatedRetryRequest.path'),'analysis/start');
  assert.deepEqual(JSON.parse(run('JSON.stringify(unrelatedRetryRequest.data)')),
    {theme_id:'theme-a',portfolio_id:'core',company_ids:['b'],force_recalculate:true});
});
test('Active bulk analysis exposes a top batch cancel and per-company row cancels',async()=>{
  const {run,node,context}=setup();
  context.setTimeout=()=>1;
  run(`state.companies=[{id:'a',name:'TSMC'},{id:'b',name:'Samsung'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.source_summaries=[{company_id:'a',count:2},{company_id:'b',count:2}];
    state.search_runs=[{id:'bulk-active',job_type:'analysis',status:'running',phase:'analysing',
      theme_id:'theme-a',portfolio_id:'core',company_ids:['a','b'],companies_completed:[]}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';
    currentView='analysis'; trackedAnalysisRuns.add('bulk-active');`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/id="btn-cancel-analysis"[\s\S]*data-analysis-run-id="bulk-active"/);
  assert.equal((node('#page-content').innerHTML.match(/cancelCompanyAnalysis/g)||[]).length,2);

  const controls=[0,1,2].map(()=>({dataset:{analysisRunId:'bulk-active'},disabled:false,textContent:'Cancel batch'}));
  context.document.querySelectorAll=selector=>selector==='[data-analysis-run-id]'?controls:[];
  run(`let cancelRequest; api=async(path,data)=>{
    cancelRequest={path,data,allDisabled:[...document.querySelectorAll('[data-analysis-run-id]')].every(button=>button.disabled)};
    return {analysis_run:{id:'bulk-active',job_type:'analysis',status:'cancelled',phase:'analysing',
      theme_id:'theme-a',portfolio_id:'core',company_ids:['a','b'],companies_completed:['a']}};
  }; refresh=async()=>{};`);
  await run("window.cancelAnalysisRun('bulk-active', document.querySelectorAll('[data-analysis-run-id]')[1])");
  assert.equal(run('cancelRequest.path'),'analysis/cancel');
  assert.deepEqual(JSON.parse(run('JSON.stringify(cancelRequest.data)')),{id:'bulk-active'});
  assert.equal(run('cancelRequest.allDisabled'),true);
  assert.equal(run("trackedAnalysisRuns.has('bulk-active')"),false);
  assert.doesNotMatch(node('#page-actions').innerHTML,/btn-cancel-analysis/);
  assert.doesNotMatch(node('#page-content').innerHTML,/cancelCompanyAnalysis/);
  assert.match(node('#notice-area').innerHTML,/Analysis cancelled.*1 of 2 companies processed.*Completed company results were kept/);
});
test('Top and per-stock cancel controls target the jobs they describe',async()=>{
  const {run,node,context}=setup();
  run(`state.companies=[{id:'a',name:'Bulk stock'},{id:'b',name:'Queued stock'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.source_summaries=[{company_id:'a',count:2},{company_id:'b',count:2}];
    state.search_runs=[
      {id:'bulk-running',job_type:'analysis',status:'running',phase:'analysing',theme_id:'theme-a',portfolio_id:'core',company_ids:['a','another'],companies_completed:[]},
      {id:'stock-queued',job_type:'analysis',status:'running',phase:'queued',theme_id:'theme-a',portfolio_id:'core',company_ids:['b'],companies_completed:[]}
    ]; state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core'; currentView='analysis';`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/data-analysis-run-id="bulk-running"[\s\S]*Cancel analysis/);
  const html=node('#page-content').innerHTML;
  const bulkRow=html.slice(html.indexOf('Bulk stock'),html.indexOf('</tr>',html.indexOf('Bulk stock')));
  const stockRow=html.slice(html.indexOf('Queued stock'),html.indexOf('</tr>',html.indexOf('Queued stock')));
  assert.match(bulkRow,/cancelCompanyAnalysis\('bulk-running', 'a',[\s\S]*>\s*Cancel\s*</);
  assert.match(stockRow,/cancelCompanyAnalysis\('stock-queued', 'b',[\s\S]*>\s*Cancel\s*</);

  run(`let companyCancelRequest; api=async(path,data)=>{
    companyCancelRequest={path,data};
    return {analysis_run:{id:'bulk-running',job_type:'analysis',status:'running',phase:'analysing',
      theme_id:'theme-a',portfolio_id:'core',company_ids:['a','another'],companies_completed:[],cancelled_company_ids:['a']},
      cancelled_company_id:'a'};
  }; refresh=async()=>{};`);
  const rowButton={disabled:false,textContent:'Cancel'};
  context.rowButton=rowButton;
  await run("window.cancelCompanyAnalysis('bulk-running','a',rowButton)");
  assert.equal(run('companyCancelRequest.path'),'analysis/cancel');
  assert.deepEqual(JSON.parse(run('JSON.stringify(companyCancelRequest.data)')),{id:'bulk-running',company_id:'a'});
  assert.equal(run("state.search_runs.find(run=>run.id==='bulk-running').status"),'running');
  assert.match(node('#page-content').innerHTML,/Bulk stock[\s\S]*Analysis cancelled[\s\S]*Retry →/);
});
test('Cancelling a singleton stock run stops its spinner immediately',async()=>{
  const {run,node,context}=setup();
  context.setTimeout=()=>1;
  run(`state.companies=[{id:'a',name:'TSMC'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:2}];
    state.search_runs=[{id:'stock-running',job_type:'analysis',status:'running',phase:'analysing',
      theme_id:'theme-a',portfolio_id:'core',company_ids:['a'],companies_completed:[]}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';
    currentView='analysis'; trackedAnalysisRuns.add('stock-running');
    api=async()=>({analysis_run:{id:'stock-running',job_type:'analysis',status:'cancelled',phase:'finished',
      theme_id:'theme-a',portfolio_id:'core',company_ids:['a'],companies_completed:[],cancelled_company_ids:['a']},
      cancelled_company_id:'a'});
    refresh=async()=>{};`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/Calculating exposure[\s\S]*0 of 1 companies processed/);
  context.rowButton={disabled:false,textContent:'Cancel'};
  await run("window.cancelCompanyAnalysis('stock-running','a',rowButton)");
  assert.equal(run("trackedAnalysisRuns.has('stock-running')"),false);
  assert.doesNotMatch(node('#page-actions').innerHTML,/Calculating exposure|dashboard-loading-spinner/);
  assert.match(node('#page-actions').innerHTML,/Analysis cancelled/);
  assert.doesNotMatch(node('#notice-area').innerHTML,/remaining batch is still running/);
});
test('A cancelled rerun keeps the previous result and shows the cancellation status',async()=>{
  const {run,node}=setup();
  run(`state.companies=[{id:'a',name:'TSMC'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:3}];
    state.cases=[{id:'previous',theme_id:'theme-a',company_id:'a',review_status:'needs_review',
      source_confidence_light:'green',estimate:{point:.4}}];
    state.search_runs=[{id:'rerun-cancelled',job_type:'analysis',status:'cancelled',phase:'finished',
      theme_id:'theme-a',portfolio_id:'core',company_ids:['a'],companies_completed:[],cancelled_company_ids:['a']}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  assert.match(html,/TSMC[\s\S]*Analysis cancelled[\s\S]*40\.0%[\s\S]*dot-green[\s\S]*Retry →/);
});
test('Analysis cancellation errors restore the clicked control',async()=>{
  const {run,node,context}=setup();
  node('#alert-modal').showModal=()=>{};
  node('#alert-modal-ok').focus=()=>{};
  run(`state.search_runs=[{id:'analysis-error',job_type:'analysis',status:'running',phase:'analysing',
    theme_id:'theme-a',portfolio_id:'core',company_ids:['a'],companies_completed:[]}];
    api=async()=>{throw new Error('network unavailable')};`);
  const button={dataset:{analysisRunId:'analysis-error'},disabled:false,textContent:'Cancel'};
  context.contextButton=button;
  await run("window.cancelAnalysisRun('analysis-error', contextButton)");
  assert.equal(button.disabled,false);
  assert.equal(button.textContent,'Cancel');
  assert.match(node('#alert-modal-message').textContent,/Could not cancel exposure analysis: network unavailable/);
});
test('Analysis polling refreshes terminal results and keeps per-company failures visible',async()=>{
  const {run,node,context} = setup();
  let poll;
  context.setTimeout=callback=>{poll=callback;return 1;};
  run(`state.companies=[{id:'a',name:'TSMC'},{id:'b',name:'Samsung'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.source_summaries=[{company_id:'a',count:3},{company_id:'b',count:3}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core'; currentView='analysis';
    state.search_runs=[{id:'analysis-poll',job_type:'analysis',status:'running',phase:'analysing',theme_id:'theme-a',portfolio_id:'core',company_ids:['a','b'],companies_completed:['a']}];
    let statusRequest; apiGet=async path=>{
      statusRequest=path;
      return {analysis_run:{id:'analysis-poll',job_type:'analysis',status:'incomplete',phase:'complete',theme_id:'theme-a',portfolio_id:'core',company_ids:['a','b'],companies_completed:['a','b'],analysed_count:1,failed_count:1,outcomes:{a:'success',b:'failed'},results:{a:{company_id:'a',status:'success',case_id:'fresh'},b:{company_id:'b',status:'failed',error:'Provider quota reached'}},errors:[{reason:'Provider quota reached'}],completion_reason:'provider_quota'}};
    };
    refresh=async()=>{state.cases=[{id:'fresh',theme_id:'theme-a',company_id:'a',review_status:'needs_review',estimate:{point:.3},source_confidence_light:'amber'}];};`);
  await run('viewAnalysis()');
  run('monitorAnalysisRuns()');
  assert.equal(typeof poll,'function');
  await poll();
  assert.equal(run('statusRequest'),'analysis/run?id=analysis-poll');
  assert.match(node('#page-actions').innerHTML,/Analysis finished incompletely.*2 of 2 companies processed.*1 failed/);
  assert.match(node('#page-content').innerHTML,/30\.0%/);
  assert.match(node('#page-content').innerHTML,/Analysis failed: Provider quota reached[\s\S]*Retry →/);
  assert.equal(run("trackedAnalysisRuns.has('analysis-poll')"),false);
});
test('Analysis jobs are excluded from source-search polling and use an analysis-specific timeout message',async()=>{
  const {run,context} = setup();
  let timerCalls=0;
  context.setTimeout=()=>{timerCalls++;return 1;};
  run(`state.search_runs=[{id:'analysis-only',job_type:'analysis',status:'running'}];`);
  assert.equal(run('activeSourceSearch()'),undefined);
  run('monitorSourceSearches()');
  assert.equal(run('trackedSourceSearches.size'),0);
  assert.equal(timerCalls,0);
  context.AbortController=class { constructor(){this.signal={};} abort(){} };
  context.fetch=async()=>{const error=new Error('aborted');error.name='AbortError';throw error;};
  await assert.rejects(run("api('analysis/start', {}, {timeoutMs: 1})"),
    /Analysis request took too long.*may already be queued/i);
});
test('Per-stock rerun uses the latest case result in the queue',async()=>{
  const {run,node} = setup();
  run(`state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:3}]; state.ai_configured=true;
    state.cases=[
      {id:'old',theme_id:'theme-a',company_id:'a',created_at:'2026-01-01T00:00:00Z',review_status:'approved',source_confidence_light:'red',estimate:{point:.2}},
      {id:'new',theme_id:'theme-a',company_id:'a',created_at:'2026-01-02T00:00:00Z',review_status:'needs_review',source_confidence_light:'green',source_confidence_basis:'Three cited sources agree.',estimate:{point:.9}}
    ]; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  assert.match(html,/1 companies · 1 cases · 1 awaiting review/);
  assert.match(html,/90\.0%[\s\S]*dot-green[\s\S]*AI confidence: Green[\s\S]*Re-run →/);
  assert.doesNotMatch(html,/20\.0%/);
  assert.doesNotMatch(html,/High · 92%|Med · 70%|Low · 35%/);
});
test('Calculate Exposure queue filter survives polling rerenders',async()=>{
  const {run,node}=setup();
  run(`state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.source_summaries=[{company_id:'a',count:3},{company_id:'b',count:3}];
    state.cases=[{id:'pending',theme_id:'theme-a',company_id:'a',created_at:'2026-01-02T00:00:00Z',
      review_status:'needs_review',source_confidence_light:'green',estimate:{point:.4}}];
    state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  run("window.filterAnalysisQueue('pending')");
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  assert.equal(run('analysisQueueFilter'),'pending');
  assert.match(html,/filter-tab active" data-analysis-filter="pending"/);
  assert.doesNotMatch(html,/filter-tab active" data-analysis-filter="all"/);
});
test('Calculate Exposure confidence uses source provenance, not classification confidence',async()=>{
  const {run,node}=setup();
  run(`state.companies=[{id:'a',name:'Legacy result'}];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:3}];
    state.cases=[{id:'legacy',theme_id:'theme-a',company_id:'a',review_status:'needs_review',
      classification_confidence:'high',estimate:{point:.4}}];
    selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  assert.match(html,/AI confidence is not available/);
  assert.doesNotMatch(html,/dot-green|AI confidence: Green/);
});
test('Calculate Exposure shows a point percentage and colour-only AI confidence dots',async()=>{
  const {run,node} = setup();
  run(`state.companies=[
      {id:'a',name:'TSMC',sedol:'6889106'},
      {id:'b',name:'Samsung',sedol:'6771720'},
      {id:'c',name:'Example Co',sedol:'9999999'}
    ];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'},{company_id:'c'}]}];
    state.source_summaries=[{company_id:'a',count:3},{company_id:'b',count:3},{company_id:'c',count:3}];
    state.cases=[
      {id:'green',theme_id:'theme-a',company_id:'a',review_status:'needs_review',exposure_percent:50,source_confidence_light:'green',source_confidence_basis:'Three trusted sources agree.'},
      {id:'amber',theme_id:'theme-a',company_id:'b',review_status:'needs_review',estimate:{point:.25},source_confidence_light:'yellow',source_confidence_basis:'Credible sources diverge.'},
      {id:'red',theme_id:'theme-a',company_id:'c',review_status:'needs_review',estimate:{point:.75},source_confidence_light:'red',source_confidence_basis:'Unapproved website cited.'}
    ];
    selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  assert.match(html,/50\.0%[\s\S]*dot-green[\s\S]*AI confidence: Green/);
  assert.match(html,/25\.0%[\s\S]*dot-amber[\s\S]*AI confidence: Amber/);
  assert.match(html,/75\.0%[\s\S]*dot-red[\s\S]*AI confidence: Red/);
  assert.doesNotMatch(html,/AI confidence[\s\S]*>\d+%<\/span>/);
  assert.doesNotMatch(html,/High · 92%|Med · 70%|Low · 35%|0\.0% –/);
});
test('Calculate Exposure uses the evidence floor when no point estimate exists',async()=>{
  const {run,node}=setup();
  run(`state.companies=[
      {id:'range',name:'Range company'}, {id:'zero',name:'Exact zero'},
      {id:'legacy',name:'Legacy point'}, {id:'missing',name:'Missing estimate'}
    ];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[
      {company_id:'range'},{company_id:'zero'},{company_id:'legacy'},{company_id:'missing'}]}];
    state.source_summaries=[
      {company_id:'range',count:2},{company_id:'zero',count:2},
      {company_id:'legacy',count:2},{company_id:'missing',count:2}];
    state.cases=[
      {id:'range-case',theme_id:'theme-a',company_id:'range',review_status:'needs_review',
       exposure_percent:null,estimate:{point:null,lower:0,upper:.5666}},
      {id:'zero-case',theme_id:'theme-a',company_id:'zero',review_status:'needs_review',
       exposure_percent:0,estimate:{point:0,lower:0,upper:.8}},
      {id:'legacy-case',theme_id:'theme-a',company_id:'legacy',review_status:'needs_review',exposure_percent:50},
      {id:'missing-case',theme_id:'theme-a',company_id:'missing',review_status:'needs_review',
       exposure_percent:null,estimate:{point:null,lower:null,upper:null}}
    ]; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  const row=name=>html.slice(html.indexOf(name),html.indexOf('</tr>',html.indexOf(name)));
  assert.match(row('Range company'),/title="Conservative evidence-based exposure; unresolved revenue was not counted\."[\s\S]*0\.0%/);
  assert.match(row('Exact zero'),/0\.0%/);
  assert.doesNotMatch(row('Exact zero'),/80\.0%/);
  assert.match(row('Legacy point'),/50\.0%/);
  assert.match(row('Missing estimate'),/data-exposure-basis="not_calculated"[\s\S]*—/);
  assert.match(row('Missing estimate'),/No calculated exposure is available because the analysis did not complete/);
  assert.match(row('Missing estimate'),/AI confidence is not available">—<\/span>/);
  assert.deepEqual(JSON.parse(run("JSON.stringify(caseExposureValue({estimate:{point:null,upper:.5666},exposure_percent:null}))")),
    {value:null,basis:'not_calculated'});
});
test('Calculate Exposure does not show failed or pending analyses as zero exposure',async()=>{
  const {run,node}=setup();
  run(`state.companies=[
      {id:'failed',name:'Failed search'}, {id:'active',name:'Active search'},
      {id:'new',name:'Not analysed'}
    ];
    state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',name:'Asia Core',rows:[
      {company_id:'failed'},{company_id:'active'},{company_id:'new'}]}];
    state.source_summaries=[
      {company_id:'failed',count:2},{company_id:'active',count:2},{company_id:'new',count:2}];
    state.search_runs=[
      {id:'failed-run',job_type:'analysis',status:'failed',phase:'finished',theme_id:'theme-a',portfolio_id:'core',
       company_ids:['failed'],companies_completed:['failed'],outcomes:{failed:'failed'},
       results:{failed:{company_id:'failed',status:'failed',error:'No readable evidence',
         fallback_status:'not_found',fallback_message:'No approved numeric document found.'}}},
      {id:'active-run',job_type:'analysis',status:'running',phase:'analysing',theme_id:'theme-a',portfolio_id:'core',
       company_ids:['active'],companies_completed:[],outcomes:{},results:{}}
    ]; state.ai_configured=true; selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  const row=name=>html.slice(html.indexOf(name),html.indexOf('</tr>',html.indexOf(name)));
  assert.match(row('Failed search'),/data-exposure-basis="not_calculated"[\s\S]*—/);
  assert.match(row('Failed search'),/after targeted web search/);
  assert.match(row('Failed search'),/AI confidence is not available">—<\/span>/);
  assert.match(row('Failed search'),/Retry →/);
  assert.match(row('Active search'),/data-exposure-basis="not_calculated"[\s\S]*—/);
  assert.match(row('Active search'),/Exposure is being calculated/);
  assert.match(row('Active search'),/AI confidence is not available">—<\/span>/);
  assert.match(row('Not analysed'),/data-exposure-basis="not_calculated"[\s\S]*—/);
  assert.match(row('Not analysed'),/Exposure has not been calculated yet/);
  assert.match(row('Not analysed'),/AI confidence is not available">—<\/span>/);
  assert.deepEqual(JSON.parse(run('JSON.stringify(caseExposureValue(null))')),
    {value:null,basis:'not_calculated'});
  assert.deepEqual(JSON.parse(run('JSON.stringify(caseExposureValue({estimate:{point:3,upper:4}}))')),
    {value:1,basis:'point'});
  assert.deepEqual(JSON.parse(run('JSON.stringify(caseExposureValue({estimate:{point:null,upper:-.5}}))')),
    {value:null,basis:'not_calculated'});
  assert.equal(run('state.cases.length'),0);
});
test('Calculate Exposure theme and portfolio selectors update the analysis scope',async()=>{
  const {run,node,storage} = setup();
  run(`state.themes=[
      {id:'theme-a',theme_name:'AI theme',approved:true},
      {id:'theme-b',theme_name:'Energy theme',approved:true}
    ];
    state.portfolio_imports=[
      {id:'portfolio-a',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'}]},
      {id:'portfolio-b1',theme_id:'theme-b',name:'Wind Portfolio',rows:[{company_id:'b'}]},
      {id:'portfolio-b2',theme_id:'theme-b',name:'Solar Portfolio',rows:[{company_id:'a'}]}
    ];
    selectedDashboardThemeId='theme-a';
    selectedDashboardPortfolioId='portfolio-a';
    selectedSourcePortfolioId='portfolio-a';`);
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/AI theme/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);

  node('#analysis-theme-select').change({target:{value:'theme-b'}});
  assert.equal(run('selectedDashboardThemeId'),'theme-b');
  assert.equal(run('selectedDashboardPortfolioId'),'portfolio-a');
  assert.match(node('#page-content').innerHTML,/value="theme-b" selected/);
  assert.match(node('#page-content').innerHTML,/Solar Portfolio/);
  assert.match(node('#page-content').innerHTML,/Wind Portfolio/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);
  assert.match(node('#page-content').innerHTML,/TSMC/);

  node('#analysis-portfolio-select').change({target:{value:'portfolio-b1'}});
  assert.equal(run('selectedDashboardPortfolioId'),'portfolio-b1');
  assert.equal(run('selectedSourcePortfolioId'),'portfolio-b1');
  assert.match(node('#page-content').innerHTML,/<option value="portfolio-b1" selected>Wind Portfolio<\/option>/);
  assert.match(node('#page-content').innerHTML,/Samsung/);
  assert.doesNotMatch(node('#page-content').innerHTML,/TSMC/);
  assert.equal(JSON.parse(storage.get('theme-lab-dashboard-selection')).themeId,'theme-b');
  assert.equal(JSON.parse(storage.get('theme-lab-dashboard-selection')).portfolioId,'portfolio-b1');
});
test('Queue Upload opens manual upload with the clicked company selected',async()=>{
  const {run,node} = setup();
  run(`state.themes=[{id:'theme-a',approved:true}];
    state.portfolio_imports=[{id:'portfolio-a',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='portfolio-a'`);
  await run('viewAnalysis()');
  assert.ok(node('#page-content').innerHTML.includes("openManualUploadForCompany('a')"));
  run("window.openManualUploadForCompany('b')");
  assert.equal(run('currentView'),'sources');
  assert.match(node('#page-content').innerHTML,/<option value="b" selected>Samsung<\/option>/);
});
test('Company source rows expand and collapse',()=>{
  const {run,node}=setup(); node('#src-detail-a').style.display='none';
  run("window.toggleSourceRow('a')"); assert.equal(node('#src-detail-a').style.display,'table-row');
  run("window.toggleSourceRow('a')"); assert.equal(node('#src-detail-a').style.display,'none');
});
test('Calculate Exposure omits opened case-detail and review cards',async()=>{
  const {run,node}=setup();
  run(`state.themes=[{id:'theme-a',theme_name:'AI theme',approved:true}];
    state.portfolio_imports=[{id:'core',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:3}];
    state.cases=[{id:'case',theme_id:'theme-a',company_id:'a',classification_confidence:'high',review_status:'needs_review',case:{units:[{activity:'High Performance Computing'}]},estimate:{point:.4,lower:.3,upper:.5}}];
    selectedDashboardThemeId='theme-a'; selectedDashboardPortfolioId='core';`);
  await run('viewAnalysis()');
  const html=node('#page-content').innerHTML;
  assert.match(html,/1 awaiting review/);
  assert.doesNotMatch(html,/case-detail-panel|Point estimate|Revenue units|Review decision|Save review decision/);
});

test('Banners clear when changing views and stay cleared on return',()=>{
  const {run,node}=setup();
  run('render = async () => {}; currentView = "theme"');
  for(const type of ['info','warning','error','success']) {
    run(`notice('Taxonomy proposed', '${type}')`);
    assert.match(node('#notice-area').innerHTML,/Taxonomy proposed/);
    run('setView("sources")');
    assert.equal(node('#notice-area').innerHTML,'');
    run('setView("theme")');
    assert.equal(node('#notice-area').innerHTML,'');
  }
});
test('A previous success timer cannot dismiss a newer banner',()=>{
  const {run,node,context}=setup();
  const callbacks=new Map(); let id=0;
  context.setTimeout=fn=>{callbacks.set(++id,fn);return id;};
  context.clearTimeout=timer=>callbacks.delete(timer);
  run('notice("Saved", "success")');
  assert.equal(callbacks.size,1);
  run('notice("Review needed", "warning")');
  assert.equal(callbacks.size,0);
  assert.match(node('#notice-area').innerHTML,/Review needed/);
});
test('Generated taxonomy and unsaved scope survive leaving and returning to Theme Definition',async()=>{
  const {run,node,context}=setup();
  context.FormData=class { constructor(form){return Object.entries(form.fields || {});} };
  node('#theme-form').fields={theme_name:'AI Infrastructure',positive_examples:'AI chips',id:''};
  run(`currentView='theme';
    const originalEditor = buildTaxonomyEditor;
    buildTaxonomyEditor = nodes => {
      document.querySelector('#taxonomy-json-area').value=JSON.stringify(nodes);
      return originalEditor(nodes);
    };`);
  await run('viewTheme()');
  const generated=[{node_id:'compute',parent_id:null,name:'Compute',definition:'AI compute',examples:[]}];
  node('#taxonomy-json-area').value=JSON.stringify(generated);
  run('updateTaxonomyMindmap()');
  assert.match(node('#taxonomy-mindmap').innerHTML,/Compute/);
  run('setView("sources")');
  node('#taxonomy-json-area').value='[]';
  run('setView("theme")');
  assert.deepEqual(JSON.parse(node('#taxonomy-json-area').value),generated);
  assert.match(node('#taxonomy-mindmap').innerHTML,/Compute/);
  assert.match(node('#page-content').innerHTML,/value="AI Infrastructure"/);
  assert.equal(node('#notice-area').innerHTML,'');
});
