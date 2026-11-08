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
  assert.doesNotMatch(node('#page-content').innerHTML,/class="label">Portfolio exposure</);
  assert.match(node('#page-content').innerHTML,/class="label">Benchmark exposure</);
  assert.match(node('#page-content').innerHTML,/class="metric-grid dashboard-summary-metrics"/);
});
test('Dashboard charts top ten active contributions and filters the Tier-2 pie by Tier-1',async()=>{
  const {run,node} = setup();
  run(`state = {
    themes:[{id:'theme-1',theme_name:'AI',approved:true,nodes:[
      {node_id:'compute',parent_id:null,name:'Compute'},
      {node_id:'chips',parent_id:'compute',name:'Chips'},
      {node_id:'cloud',parent_id:'compute',name:'Cloud'},
      {node_id:'energy',parent_id:null,name:'Energy'},
      {node_id:'wind',parent_id:'energy',name:'Wind'}
    ]}],
    portfolio_imports:[{id:'portfolio-1',theme_id:'theme-1',name:'Core',snapshot_id:'snapshot-1'}],
    snapshots:[{id:'snapshot-1',portfolio:[]}],
    companies:Array.from({length:12},(_,i)=>({id:'company-'+(i+1),name:i===0?'TSMC':'Company '+(i+1)})),
    sources:[],
    cases:Array.from({length:12},(_,i)=>({
      company_id:'company-'+(i+1),theme_id:'theme-1',review_status:'approved',
      estimate:{subthemes:{chips:{point:0.5},cloud:{point:0.5},wind:{point:1}}}
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
  assert.match(html,/<div class="label">Delta<\/div>\s*<div class="value">15\.0%<\/div>\s*<div class="meta">Benchmark exposure − active exposure<\/div>/);
  assert.match(html,/Top 10 of 12/);
  assert.match(html,/Portfolio exposure contribution \(%\)/);
  assert.match(html,/TSMC/);
  const chart = html.match(/class="portfolio-contribution-chart"[\s\S]*?<\/svg>/)?.[0] || '';
  assert.match(chart,/Company 10/);
  assert.doesNotMatch(chart,/Company 11/);
  assert.doesNotMatch(chart,/contribution-bar negative/);
  assert.doesNotMatch(chart,/>-[\d.]+%<\/text>/);
  assert.match(html,/id="dashboard-tier1-select"/);
  assert.match(html,/aria-label="Tier-2 exposure split for Compute"/);
  assert.match(html,/Chips/);
  assert.match(html,/Cloud/);
  node('#dashboard-tier1-select').change({target:{value:'energy'}});
  assert.match(node('#subtheme-pie-content').innerHTML,/Wind/);
  assert.doesNotMatch(node('#subtheme-pie-content').innerHTML,/Chips/);
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
  assert.match(demoHtml,/Demo Semiconductor Co\./);
  assert.match(demoHtml,/Portfolio exposure contributions/);
  assert.equal(run('dashboardApiCalls'),1);
  assert.equal(run('JSON.stringify(state)'),initialWorkspace);
  assert.equal(run('selectedDashboardThemeId'),'real-theme');
  assert.equal(run('selectedDashboardPortfolioId'),'real-portfolio');
  assert.equal(storage.get('theme-lab-dashboard-selection'),savedSelection);

  node('#dashboard-demo-toggle').change({target:{checked:false}});
  await run('new Promise(resolve=>setTimeout(resolve,0))');
  assert.match(node('#page-content').innerHTML,/Real Theme/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Demo Semiconductor Co\./);
  assert.equal(run('dashboardApiCalls'),2);
  assert.equal(run('JSON.stringify(state)'),initialWorkspace);
});
test('Dashboard demo toggle works when the workspace has no approved theme or portfolio',async()=>{
  const {run,node} = setup();
  run(`let dashboardApiCalls=0;
  api=async()=>{dashboardApiCalls++;return null;};`);
  await run('viewDashboard()');
  node('#dashboard-demo-toggle').change({target:{checked:true}});
  assert.match(node('#page-content').innerHTML,/Demo Semiconductor Co\./);
  assert.match(node('#page-content').innerHTML,/id="dashboard-tier1-select"/);
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
  assert.match(node('#source-search-status').innerHTML,/Source search stopped/);
  assert.equal(node('#btn-cancel-source-search').hidden,true);
  assert.equal(node('#btn-run-search').disabled,false);
  assert.equal(node('#btn-run-long-search').disabled,false);
});
for (const mode of ['short','long']) {
  test(`${mode} source search cancels without waiting for a workspace refresh`,async()=>{
    const {run,node,context} = setup();
    context.setTimeout=()=>1;
    run(`currentView='sources'; selectedDashboardThemeId='t'; selectedSourcePortfolioId='p';
      state.portfolio_imports=[{id:'p',theme_id:'t',name:'Portfolio',rows:[{company_id:'a'}]}];
      state.search_runs=[{id:'source-job',portfolio_id:'p',search_mode:'${mode}',status:'running',results_count:2}];
      api=async()=>({search_run:{...state.search_runs[0],status:'cancelled'}});
      refresh=async()=>{throw new Error('A slow workspace refresh must not block cancellation');};`);
    await run('viewSources()');
    await node('#btn-cancel-source-search').click();
    assert.match(node('#source-search-status').innerHTML,/Source search stopped. 2 documents saved/);
    assert.doesNotMatch(node('#source-search-status').innerHTML,/spinner|Stopping/);
    assert.equal(node('#btn-run-search').disabled,false);
    assert.equal(node('#btn-run-long-search').disabled,false);
    assert.equal(node('#source-search-settings').disabled,false);
    assert.equal(run('activeSourceSearch()'),undefined);
  });
  test(`${mode} source search can be cancelled while the start request is pending`,async()=>{
    const {run,node,context} = setup();
    context.setTimeout=()=>1;
    context.document.querySelectorAll = selector =>
      selector === 'input[name="company_ids"]:checked' ? [{value:'a'}] :
        selector === 'input[name="source_types"]:checked' ? [{value:'annual_report'}] : [];
    run(`currentView='sources'; selectedDashboardThemeId='t'; selectedSourcePortfolioId='p';
      state.portfolio_imports=[{id:'p',theme_id:'t',name:'Portfolio',rows:[{company_id:'a'}]}];
      let resolveStart; const calls=[];
      api=async(path,data)=>{calls.push({path,data});
        if (path==='source/search') return new Promise(resolve=>{resolveStart=resolve;});
        return {search_run:{id:data.id,search_mode:'${mode}',status:'cancelled'}};
      };`);
    await run('viewSources()');
    const starting = node(mode==='long' ? '#btn-run-long-search' : '#btn-run-search').onclick();
    assert.equal(node('#btn-cancel-source-search').hidden,false);
    assert.equal(node('#btn-cancel-source-search').disabled,false);
    await node('#btn-cancel-source-search').click();
    assert.equal(node('#btn-cancel-source-search').disabled,true);
    assert.match(node('#source-search-status').innerHTML,/Stopping source search/);
    run("resolveStart({search_run:{id:'new-source-job',portfolio_id:'p',status:'running'}})");
    await starting;
    assert.equal(run('calls[1].path'),'source/search/cancel');
    assert.equal(run('calls[1].data.id'),'new-source-job');
    assert.equal(node('#btn-run-search').disabled,false);
    assert.equal(node('#btn-run-long-search').disabled,false);
  });
  test(`${mode} source cancellation stays stopped after an older status response arrives`,async()=>{
    const {run,node,context} = setup();
    context.setTimeout=()=>1;
    run(`currentView='sources'; selectedSourcePortfolioId='p';
      const runningSource={id:'source-job',portfolio_id:'p',search_mode:'${mode}',status:'running'};
      state.search_runs=[runningSource];
      let resolveStatus;
      apiGet=()=>new Promise(resolve=>{resolveStatus=resolve;});
      const refreshing=refresh();
      api=async()=>({search_run:{...runningSource,status:'cancelled'}});`);
    await run('window.cancelSourceSearch()');
    run('resolveStatus({search_runs:[runningSource]})');
    await run('refreshing');
    run('updateSourceSearchLock(); updateSourceSearchStatus()');
    assert.equal(run('activeSourceSearch()'),undefined);
    assert.equal(node('#btn-run-search').disabled,false);
    assert.equal(node('#btn-run-long-search').disabled,false);
    assert.match(node('#source-search-status').innerHTML,/Source search stopped/);
  });
}
test('Source library can cancel an active financial lookup',async()=>{
  const {run,node} = setup();
  run(`state = {
    companies:[{id:'company-a',name:'TSMC'}],sources:[],source_summaries:[],cases:[],themes:[],
    search_runs:[{id:'financial-1',job_type:'financials',portfolio_id:'portfolio-a',status:'running',results:[]}],
    portfolio_imports:[{id:'portfolio-a',theme_id:'theme-1',name:'Asia Core',rows:[{company_id:'company-a'}]}]
  }; selectedDashboardThemeId='theme-1'; selectedSourcePortfolioId='portfolio-a'; currentView='sources';
  const calls=[];
  api=async(path,data)=>{calls.push({path,data}); state.search_runs[0].status='cancelled'; return {financial_run:state.search_runs[0]};};
  refresh=async()=>{};`);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/id="btn-cancel-financials"/);
  await run("window.cancelPortfolioFinancials('financial-1')");
  assert.equal(run('calls[0].path'),'financials/cancel');
  assert.equal(run('calls[0].data.id'),'financial-1');
  assert.match(node('#financial-search-status').textContent,/Financial lookup cancelled/);
  assert.equal(node('#financial-lookup-spinner').hidden,true);
  assert.equal(node('#btn-cancel-financials').hidden,true);
  assert.equal(node('#btn-populate-financials').disabled,false);
});
test('Cancel is immediately usable while the financial lookup is being queued',async()=>{
  const {run,node,context} = setup();
  context.setTimeout=()=>1;
  run(`selectedSourcePortfolioId='p';
    let resolvePopulate;
    const calls=[];
    api=async(path,data)=>{
      calls.push({path,data});
      if (path==='financials/populate') return new Promise(resolve=>{resolvePopulate=resolve;});
      return {financial_run:{id:data.id,job_type:'financials',portfolio_id:'p',status:'cancelled'}};
    };
    const starting=window.populatePortfolioFinancials('p');`);
  assert.equal(node('#btn-cancel-financials').hidden,false);
  assert.equal(node('#btn-cancel-financials').disabled,false);
  assert.equal(node('#btn-populate-financials').disabled,true);
  await run("window.cancelPortfolioFinancials('', 'p')");
  assert.equal(node('#btn-cancel-financials').disabled,true);
  assert.match(node('#financial-search-status').textContent,/Stopping/);
  assert.equal(run('calls.length'),1);
  run("resolvePopulate({financial_run:{id:'just-queued',job_type:'financials',portfolio_id:'p',status:'running'}})");
  await run('starting');
  assert.equal(run('calls[1].path'),'financials/cancel');
  assert.equal(run('calls[1].data.id'),'just-queued');
  assert.equal(node('#btn-cancel-financials').hidden,true);
  assert.equal(node('#financial-lookup-spinner').hidden,true);
  assert.equal(node('#btn-populate-financials').disabled,false);
});
test('A stale status refresh cannot revive a confirmed financial cancellation',async()=>{
  const {run,node,context} = setup();
  context.setTimeout=()=>1;
  run(`selectedSourcePortfolioId='p';
    const financial={id:'financial-1',job_type:'financials',portfolio_id:'p',status:'running'};
    state.search_runs=[financial];
    let resolveStatus;
    apiGet=()=>new Promise(resolve=>{resolveStatus=resolve;});
    const refreshing=refresh();
    api=async()=>({financial_run:{...financial,status:'cancelled'}});`);
  await run("window.cancelPortfolioFinancials('financial-1')");
  run("resolveStatus({search_runs:[financial]})");
  await run('refreshing');
  run('updateFinancialLookupStatus()');
  assert.equal(run("financialLookupRun('p').status"),'cancelled');
  assert.equal(node('#financial-lookup-spinner').hidden,true);
  assert.equal(node('#btn-populate-financials').disabled,false);
});
test('A failed financial cancellation leaves the running lookup available to cancel again',async()=>{
  const {run,node,context} = setup();
  context.setTimeout=()=>1;
  run(`selectedSourcePortfolioId='p';
    state.search_runs=[{id:'financial-1',job_type:'financials',portfolio_id:'p',status:'running'}];
    let attempts=0;
    api=async()=>{if (++attempts===1) throw new Error('Temporary failure');
      return {financial_run:{id:'financial-1',job_type:'financials',portfolio_id:'p',status:'cancelled'}};};`);
  await run("window.cancelPortfolioFinancials('financial-1')");
  assert.equal(node('#btn-cancel-financials').hidden,false);
  assert.equal(node('#btn-cancel-financials').disabled,false);
  await run("window.cancelPortfolioFinancials('financial-1')");
  assert.equal(node('#btn-cancel-financials').hidden,true);
  assert.equal(run('attempts'),2);
});
test('Active financial lookup keeps its cancel button when an older completed run is also present',async()=>{
  const {run,node,context} = setup();
  context.setTimeout=()=>1;
  run(`state = {
    companies:[{id:'company-a',name:'TSMC'}],sources:[],source_summaries:[],cases:[],themes:[],
    search_runs:[
      {id:'financial-running',job_type:'financials',portfolio_id:'portfolio-a',status:'running',results:[]},
      {id:'financial-complete',job_type:'financials',portfolio_id:'portfolio-a',status:'completed',results:[]}
    ],
    portfolio_imports:[{id:'portfolio-a',theme_id:'theme-1',name:'Asia Core',rows:[{company_id:'company-a'}]}]
  }; selectedDashboardThemeId='theme-1'; selectedSourcePortfolioId='portfolio-a';`);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/btn-cancel-financials/);
  assert.equal(run("financialLookupRun('portfolio-a').id"),'financial-running');
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
test('Source library shows summary financial facts before document rows are opened',()=>{
  const {run} = setup();
  run(`state = {cases:[],search_runs:[]};`);
  const html = run(`sourceLibrary([], [{company_id:'a',count:2,types:{annual_report:2},financial_sources:[
    {id:'source-1',financial_facts:[
      {metric:'market_cap',display:'USD 50 billion',publication_date:'2026-01-01'},
      {metric:'revenue',display:'USD 20 billion',publication_date:'2026-01-01'}
    ]}
  ]}], [{id:'a',name:'Example Co'}], {id:'portfolio-a',rows:[{company_id:'a'}]})`);
  assert.match(html,/USD 50 billion/);
  assert.match(html,/USD 20 billion/);
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
  await run('refresh(); updateFinancialLookupStatus(); notifyFinishedSourceSearches()');
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
test('Unified Step 4 renders queue, review workspace and bulk button',async()=>{
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
  assert.match(node('#page-content').innerHTML,/case-detail-panel/);
  assert.match(node('#page-content').innerHTML,/<option value="core" selected>Asia Core<\/option>/);
  assert.match(node('#page-content').innerHTML,/TSMC/);
  assert.match(node('#page-content').innerHTML,/3 docs/);
  assert.match(node('#page-content').innerHTML,/AI theme/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);
  assert.match(node('#page-content').innerHTML,/Korea Growth/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Samsung/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Targeted passage analysis|Create manual case|btn-open-manual/);
  assert.match(node('#page-content').innerHTML,/Analyse all ready companies/);
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
test('Batch exposure appears for each company before the next calculation completes',async()=>{
  const {run,node} = setup();
  run(`currentView='analysis'; state.ai_configured=true; state.calculation_version='1.2.0';
    state.themes=[{id:'theme-a',approved:true}];
    state.portfolio_imports=[{id:'portfolio-a',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.companies.push({id:'outside',name:'Outside portfolio'});
    state.source_summaries=[{company_id:'a',count:2},{company_id:'b',count:2},{company_id:'outside',count:2}];
    selectedDashboardThemeId='theme-a'; selectedSourcePortfolioId='portfolio-a';
    refresh=async()=>{}; window.openCase=()=>{};
    let requests=[], firstFinish, secondFinish;
    api=async(path,data)=>{
      requests.push({path,data});
      return new Promise(resolve=>{if(requests.length===1) firstFinish=resolve; else secondFinish=resolve;});
    };`);
  await run('viewAnalysis()');
  const running = node('#btn-analyze-all').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(JSON.parse(run('JSON.stringify(requests)')), [
    {path:'analyze_all',data:{theme_id:'theme-a',portfolio_id:'portfolio-a',company_ids:['a'],allow_unresolved_web_search:true}}
  ]);
  assert.match(node('#page-actions').innerHTML,/Calculating exposure \(0\/2\)/);
  assert.match(node('#page-content').innerHTML,/data-exposure-company="a"[^]*?Calculating…/);
  run(`firstFinish({analysed_count:1,errors:[],results:[{case:{id:'a-case',company_id:'a',theme_id:'theme-a',
    review_status:'needs_review',classification_confidence:'high',estimate:{point:.5,calculation_version:'1.2.0'}}}]})`);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(run('requests.length'),2);
  assert.deepEqual(JSON.parse(run('JSON.stringify(requests[1].data.company_ids)')),['b']);
  assert.match(node('#page-content').innerHTML,/data-exposure-company="a"[^]*?50\.0%/);
  assert.match(node('#page-actions').innerHTML,/Calculating exposure \(1\/2\)/);
  assert.equal(run('analysisBatch.running'),true);
  run(`secondFinish({analysed_count:0,results:[],errors:[{company_name:'Samsung',error:'Revenue could not be established'}]})`);
  await running;
  assert.equal(run('analysisBatch.running'),false);
  assert.match(node('#page-content').innerHTML,/50\.0%/);
  assert.match(node('#notice-area').innerHTML,/1 companies calculated.*Samsung: Revenue could not be established/);
  assert.equal(run('state.cases.length'),1);
});

test('Company exposure distinguishes unresolved evidence from a supported zero and recalculates stale cases',async()=>{
  const {run,node}=setup();
  assert.match(run(`companyExposureCell({estimate:{point:0}})`),/0\.0%/);
  assert.match(run(`companyExposureCell({estimate:{point:null,issues:['Missing revenue evidence']}})`),/Unresolved/);
  assert.doesNotMatch(run(`companyExposureCell({estimate:{point:null}})`),/0\.0%/);
  const unresolved = run(`companyExposureCell({estimate:{point:null,lower:0,upper:.6}})`);
  assert.match(unresolved,/Unresolved/);
  assert.doesNotMatch(unresolved,/0\.0%|60\.0%|–/);
  assert.match(run(`companyExposureCell({estimate:{point:null},exposure_percent:42})`),/42\.0%/);
  run(`state.calculation_version='1.2.0'; state.themes=[{id:'theme-a',approved:true}];
    state.portfolio_imports=[{id:'p',theme_id:'theme-a',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:2}]; selectedDashboardThemeId='theme-a'; selectedSourcePortfolioId='p';
    state.cases=[{id:'old',company_id:'a',theme_id:'theme-a',review_status:'needs_review',estimate:{point:null,calculation_version:'1.1.0'}}];
    window.openCase=()=>{};`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/Analyse all ready companies \(1\)/);
  assert.match(node('#page-content').innerHTML,/Ready to recalculate/);
});

test('Saved unresolved company runs one ordinary evidence fallback and shows its result',async()=>{
  const {run,node}=setup();
  run(`currentView='analysis'; state.ai_configured=true; state.calculation_version='1.2.0';
    state.themes=[{id:'theme-a',approved:true}];
    state.portfolio_imports=[{id:'portfolio-a',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:2}];
    state.cases=[{id:'unresolved-a',company_id:'a',theme_id:'theme-a',review_status:'needs_review',
      source_confidence_light:'yellow',source_confidence_basis:'One credible filing',
      case:{units:[]},estimate:{point:null,issues:['Mixed segment share is undisclosed'],calculation_version:'1.2.0'}}];
    selectedDashboardThemeId='theme-a'; selectedSourcePortfolioId='portfolio-a';
    refresh=async()=>{}; let requests=[];
    api=async(path,data)=>{requests.push({path,data}); return {analysed_count:1,reused_count:1,
      calculated_count:0,errors:[],results:[{status:'reused',reused:true,
        fallback_status:'not_found',fallback_message:'No new numeric AI revenue evidence found.',
        case:{...state.cases[0],fallback_web_search:{status:'not_found',
          message:'No new numeric AI revenue evidence found.',searched_at:'2026-11-08T00:00:00Z',queries:[],source_ids:[]}}}]};};`);
  await run('viewAnalysis()');
  assert.equal(run('analysisNeedsCalculation(state.cases[0],state.themes[0])'),true);
  assert.match(node('#page-content').innerHTML,/Ready to search evidence/);
  assert.match(node('#page-content').innerHTML,/data-exposure-company="a"[^]*?Unresolved/);
  assert.match(node('#page-content').innerHTML,/dot-yellow[^]*?Yellow/);
  assert.match(node('#page-actions').innerHTML,/Analyse all ready companies \(1\)/);
  assert.match(node('#page-content').innerHTML,/onclick="window\.analyzeCompany\('a'\)"/);
  await run("window.analyzeCompany('a')");
  assert.deepEqual(JSON.parse(run('JSON.stringify(requests)')), [
    {path:'analyze_all',data:{theme_id:'theme-a',portfolio_id:'portfolio-a',company_ids:['a'],allow_unresolved_web_search:true}}
  ]);
  assert.equal(run('analysisNeedsCalculation(state.cases[0],state.themes[0])'),false);
  assert.match(node('#page-content').innerHTML,/data-exposure-company="a"[^]*?Unresolved/);
  assert.match(node('#page-content').innerHTML,/Search checked/);
  assert.match(node('#case-detail-panel').innerHTML,/No new numeric AI revenue evidence found/);
  assert.match(node('#notice-area').innerHTML,/No new numeric AI revenue evidence found/);
  await run("window.analyzeCompany('a')");
  assert.equal(run('requests.length'),1,'A saved no-result search should not run again on the next click');
  run(`state.cases[0].fallback_web_search.searched_at = new Date(Date.now() - 25*3600000).toISOString()`);
  assert.equal(run('analysisNeedsCalculation(state.cases[0],state.themes[0])'),true,
    'A search older than the 24-hour cache window can be retried');
  run(`state.cases[0].fallback_web_search.status='analysis_deferred';
    state.cases[0].fallback_web_search.message='Saved source is awaiting the Gemini budget.'`);
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/Ready to analyse saved evidence/);
});

test('Analyse All caps web fallbacks across its per-company requests',async()=>{
  const {run}=setup();
  run(`currentView='dashboard'; state.unresolved_search_batch_limit=3;
    refresh=async()=>{}; let requests=[];
    api=async(path,data)=>{requests.push(data); return {analysed_count:1,reused_count:0,errors:[],
      results:[{fallback_status:data.allow_unresolved_web_search ? 'not_found' : 'deferred',
        fallback_search_attempted:data.allow_unresolved_web_search}]};};`);
  await run(`runAnalysisBatch({id:'theme-a'}, {id:'portfolio-a'},
    ['a','b','c','d','e'].map(id=>({id,name:id.toUpperCase()})))`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(requests.map(item=>item.company_ids[0]))')),
    ['a','b','c','d','e']);
  assert.deepEqual(JSON.parse(run('JSON.stringify(requests.map(item=>item.allow_unresolved_web_search))')),
    [true,true,true,false,false]);
  assert.equal(run('analysisBatch.fallbackSearches'),3);
});

test('Analyse All shows its rolling Gemini budget and pauses when that budget is reached',async()=>{
  const {run,node}=setup();
  run(`currentView='analysis'; state.ai_configured=true; state.call_cap=60; state.calls_used_24h=12;
    state.themes=[{id:'t',approved:true}];
    state.portfolio_imports=[{id:'p',theme_id:'t',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.source_summaries=[{company_id:'a',count:1},{company_id:'b',count:1}];
    selectedSourcePortfolioId='p'; selectedDashboardThemeId='t'; refresh=async()=>{};
    let requests=[]; api=async(path,data)=>{requests.push(data); return {analysed_count:0,results:[],
      errors:[{company_name:'TSMC',error:'Gemini safety budget reached: 60 requests in the last 24 hours.'}]};};`);
  await run('viewAnalysis()');
  assert.match(node('#page-actions').innerHTML,/12 \/ 60 Gemini calls in the last 24 hours/);
  await run(`runAnalysisBatch(state.themes[0], state.portfolio_imports[0], state.companies)`);
  assert.equal(run('requests.length'),1);
  assert.equal(run('analysisBatch.budgetExhausted'),true);
  assert.match(node('#notice-area').innerHTML,/Batch paused after 1 of 2 companies/);
});

test('Row Analyse starts only that company from saved sources and fills its exposure',async()=>{
  const {run,node}=setup();
  run(`currentView='analysis'; state.ai_configured=true; state.calculation_version='1.2.0';
    state.themes=[{id:'theme-a',approved:true}];
    state.portfolio_imports=[{id:'portfolio-a',theme_id:'theme-a',name:'Asia Core',rows:[{company_id:'a'},{company_id:'b'}]}];
    state.source_summaries=[{company_id:'a',count:12},{company_id:'b',count:12}];
    selectedDashboardThemeId='theme-a'; selectedSourcePortfolioId='portfolio-a';
    refresh=async()=>{}; let openedCases=[]; window.openCase=id=>openedCases.push(id);
    window.openAnalysisPanel=()=>{throw new Error('Row Analyse must calculate without manual passage selection');};
    let requests=[], finishCompany;
    api=async(path,data)=>{
      requests.push({path,data}); return new Promise(resolve=>{finishCompany=resolve;});
    };`);
  await run('viewAnalysis()');
  const button=node('#page-content').innerHTML.match(/<button\b[^>]*onclick="(window\.analyzeCompany\('b'\))"[^>]*>/);
  assert.ok(button,'Samsung row should render its own calculation handler');
  const running=run(button[1]);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(JSON.parse(run('JSON.stringify(requests)')), [
    {path:'analyze_all',data:{theme_id:'theme-a',portfolio_id:'portfolio-a',company_ids:['b'],allow_unresolved_web_search:true}}
  ]);
  assert.equal(run('state.sources.length'),0,'Saved source summaries are sufficient to start server-side evidence preparation');
  assert.match(node('#page-content').innerHTML,/data-exposure-company="b"[^]*?Calculating…/);
  await run("window.analyzeCompany('a')");
  assert.equal(run('requests.length'),1,'A running calculation should prevent duplicate requests');
  run(`finishCompany({analysed_count:1,errors:[],results:[{case:{id:'b-case',company_id:'b',theme_id:'theme-a',
    review_status:'needs_review',classification_confidence:'high',estimate:{point:.35,calculation_version:'1.2.0'}}}]})`);
  await running;
  assert.equal(run('state.cases.length'),1);
  assert.equal(run('state.cases[0].company_id'),'b');
  assert.match(node('#page-content').innerHTML,/data-exposure-company="b"[^]*?35\.0%/);
  assert.match(node('#page-content').innerHTML,/data-exposure-company="a"[^]*?—/);
  assert.equal(run('openedCases.at(-1)'),'b-case');
  assert.match(node('#notice-area').innerHTML,/Exposure analysis complete for Samsung/);
});

test('Row Analyse rejects companies outside the portfolio and unavailable AI',async()=>{
  const {run,node}=setup();
  run(`state.ai_configured=true; state.themes=[{id:'theme-a',approved:true}];
    state.portfolio_imports=[{id:'portfolio-a',theme_id:'theme-a',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:12}];
    selectedDashboardThemeId='theme-a'; selectedSourcePortfolioId='portfolio-a';
    let requests=[]; api=async(path,data)=>{requests.push({path,data});};`);
  await run("window.analyzeCompany('b')");
  assert.equal(run('requests.length'),0);
  assert.match(node('#notice-area').innerHTML,/current portfolio/);
  await run("state.ai_configured=false; window.analyzeCompany('a')");
  assert.equal(run('requests.length'),0);
  assert.match(node('#notice-area').innerHTML,/Configure AI/);
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/<button\b[^>]*disabled[^>]*onclick="window\.analyzeCompany\('a'\)"/);
});

test('Company source rows expand and collapse',()=>{
  const {run,node}=setup(); node('#src-detail-a').style.display='none';
  run("window.toggleSourceRow('a')"); assert.equal(node('#src-detail-a').style.display,'table-row');
  run("window.toggleSourceRow('a')"); assert.equal(node('#src-detail-a').style.display,'none');
});
test('Case tiles show saved confidence and exposure; absent numeric confidence stays a rubric score',()=>{
  const {run,node}=setup();
  run(`state.cases=[{id:'case',company_id:'a',classification_confidence:'high',
    source_confidence_light:'yellow',source_confidence_basis:'Credible sources use different reporting periods',
    review_status:'approved',case:{units:[]},estimate:{point:0.4,lower:0.3,upper:0.5,issues:[]}}]; window.openCase('case')`);
  let html=node('#case-detail-panel').innerHTML;
  for(const label of ['Exposure estimate','40.0%','3 / 3','High Confidence (3/3)']) assert.ok(html.includes(label),label);
  assert.doesNotMatch(html,/Point estimate|<div class="label">Range<\/div>|30\.0% – 50\.0%/);
  assert.match(html,/Classification confidence/);
  assert.match(html,/Source confidence[^]*?dot-yellow[^]*?Yellow/);
  assert.match(html,/Credible sources use different reporting periods/);
  assert.doesNotMatch(html,/AI Confidence Score &amp; Rubric/);
  assert.doesNotMatch(html,/92%/);
  run(`state.cases[0].estimate.point_method='model_estimate'; window.openCase('case')`);
  assert.match(node('#case-detail-panel').innerHTML,/Exposure estimate[^]*?40\.0%[^]*?AI-estimated allocation/);
  run(`state.cases[0].confidence_percent=84; window.openCase('case')`);
  assert.match(node('#case-detail-panel').innerHTML,/84%/);
  run(`state.cases[0].estimate.point=null; window.openCase('case')`);
  html=node('#case-detail-panel').innerHTML; assert.match(html,/font-size:26px">Unresolved/);
  assert.doesNotMatch(html,/30\.0% – 50\.0%/);
  const unit=run(`unitCard({activity:'Mixed HPC',revenue:100,currency:'TWD',scale:'millions',
    relationship_type:'insufficient_evidence',point:null,lower:0,upper:1})`);
  assert.match(unit,/Allocation estimate: Unresolved/);
  assert.doesNotMatch(unit,/0\.0% – 100\.0%/);
});

test('AI confidence column uses only the saved traffic light and explains the color',()=>{
  const {run}=setup();
  for (const light of ['green','yellow','red']) {
    const html=run(`companySourceConfidenceCell({source_confidence_light:'${light}',
      source_confidence_basis:'Revenue sources reviewed',classification_confidence:'high',confidence_percent:95})`);
    assert.match(html,new RegExp(`dot-${light}`));
    assert.match(html,new RegExp(`AI confidence: ${light[0].toUpperCase()+light.slice(1)}`));
    assert.match(html,/Revenue sources reviewed/);
    assert.doesNotMatch(html,/95%|High/);
  }
  assert.match(run(`companySourceConfidenceCell({classification_confidence:'high',confidence_percent:95})`),/Not rated/);
  assert.doesNotMatch(run(`companySourceConfidenceCell({source_confidence_light:'purple'})`),/dot-purple/);
  assert.match(run(`companySourceConfidenceCell({source_confidence_light:'red',source_confidence_basis:'<unsafe>'})`),/&lt;unsafe&gt;/);
});

test('Saved exposure and confidence are reused across portfolios without AI or locally loaded sources',async()=>{
  const {run,node}=setup();
  run(`currentView='analysis'; state.ai_configured=false; state.calculation_version='1.2.0';
    state.themes=[{id:'theme-a',approved:true,analysis_scope_key:'same-scope',updated_at:'new-theme-save'},
      {id:'theme-b',approved:true,analysis_scope_key:'different-scope'}];
    state.portfolio_imports=[
      {id:'one',theme_id:'theme-a',name:'First portfolio',rows:[{company_id:'a'}]},
      {id:'two',theme_id:'theme-a',name:'Other portfolio',rows:[{company_id:'a'},{company_id:'b'}]},
      {id:'other-theme',theme_id:'theme-b',name:'Different theme',rows:[{company_id:'a'}]}];
    state.company_analysis_library=[{id:'saved-a',company_id:'a',theme_id:'theme-a',analysis_scope_key:'same-scope',
      taxonomy_updated_at:'older-theme-save',review_status:'needs_review',classification_confidence:'high',confidence_percent:87,
      source_confidence_light:'yellow',source_confidence_basis:'Credible source values diverge',
      exposure_percent:50,estimate:{point:.5,calculation_version:'1.2.0'}}];
    state.source_summaries=[]; state.cases=[];
    selectedDashboardThemeId='theme-a'; selectedSourcePortfolioId='one';
    let requests=[], opened=[]; api=async(...args)=>{requests.push(args);}; window.openCase=id=>opened.push(id);`);
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/data-exposure-company="a"[^]*?50\.0%/);
  assert.match(node('#page-content').innerHTML,/dot-yellow[^]*?Yellow/);
  run(`selectedSourcePortfolioId='two'`);
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/Other portfolio/);
  assert.match(node('#page-content').innerHTML,/data-exposure-company="a"[^]*?50\.0%/);
  assert.match(node('#page-content').innerHTML,/dot-yellow[^]*?Yellow/);
  await run(`window.analyzeCompany('a')`);
  assert.equal(run('requests.length'),0);
  assert.equal(run('opened.at(-1)'),'saved-a');
  assert.match(node('#notice-area').innerHTML,/Saved exposure and AI confidence loaded for TSMC/);
  run(`selectedDashboardThemeId='theme-b'; selectedSourcePortfolioId='other-theme'`);
  await run('viewAnalysis()');
  assert.doesNotMatch(node('#page-content').innerHTML,/50\.0%|dot-yellow[^]*?Yellow/);
});

test('A server-reused company answer is remembered with saved confidence and remains a successful result',async()=>{
  const {run,node}=setup();
  run(`currentView='analysis'; state.ai_configured=true; state.calculation_version='1.2.0';
    state.themes=[{id:'t',approved:true}]; state.portfolio_imports=[{id:'p',theme_id:'t',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:2}]; selectedDashboardThemeId='t'; selectedSourcePortfolioId='p';
    refresh=async()=>{}; window.openCase=()=>{};
    let requests=[]; api=async(path,data)=>{requests.push({path,data}); return {analysed_count:1,reused_count:1,
      calculated_count:0,errors:[],results:[{status:'reused',reused:true,case:{id:'old-a',company_id:'a',theme_id:'t',
      review_status:'needs_review',confidence_percent:81,classification_confidence:'high',source_confidence_light:'green',
      source_confidence_basis:'Three trusted sources agree',exposure_percent:40,
      estimate:{point:.4,calculation_version:'1.2.0'}}}]};};`);
  await run(`window.analyzeCompany('a')`);
  assert.equal(run('requests.length'),1);
  assert.equal(run('state.company_analysis_library[0].confidence_percent'),81);
  assert.equal(run('state.company_analysis_library[0].analysis_reusable'),true);
  assert.match(node('#page-content').innerHTML,/40\.0%/);
  assert.match(node('#page-content').innerHTML,/dot-green[^]*?Green/);
  assert.match(node('#notice-area').innerHTML,/1 saved company results reused/);
  assert.doesNotMatch(node('#notice-area').innerHTML,/No exposure result/);
});

test('A result invalidated by changed company sources is not reused despite matching scope and calculation',async()=>{
  const {run,node}=setup();
  run(`currentView='analysis'; state.ai_configured=false; state.calculation_version='1.2.0';
    state.themes=[{id:'t',approved:true,analysis_scope_key:'same-scope'}];
    state.portfolio_imports=[{id:'p',theme_id:'t',rows:[{company_id:'a'}]}];
    state.source_summaries=[{company_id:'a',count:3}];
    state.cases=[{id:'stale',company_id:'a',theme_id:'t',analysis_scope_key:'same-scope',analysis_reusable:false,
      review_status:'needs_review',estimate:{point:.5,calculation_version:'1.2.0'}}];
    selectedDashboardThemeId='t'; selectedSourcePortfolioId='p';
    let opened=[]; window.openCase=id=>opened.push(id);`);
  assert.equal(run(`analysisNeedsCalculation(state.cases[0],state.themes[0])`),true);
  await run(`window.analyzeCompany('a')`);
  assert.match(node('#notice-area').innerHTML,/Configure AI/);
  assert.equal(run('opened.length'),0);
  await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/Recalculate/);
  assert.doesNotMatch(node('#page-content').innerHTML,/50\.0%/);
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
