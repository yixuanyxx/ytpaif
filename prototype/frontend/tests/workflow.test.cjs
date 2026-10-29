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
test('Dashboard selectors load theme and portfolio choices and refresh the selected snapshot',async()=>{
  const {run,node} = setup();
  run(`state = {
    themes: [
      {id:'theme-1',theme_name:'AI Infrastructure',approved:true,nodes:[]},
      {id:'theme-2',theme_name:'Clean Energy',approved:true,nodes:[]}
    ],
    portfolio_imports: [
      {id:'portfolio-1',name:'Core',holdings_date:'2026-10-01',snapshot_id:'snapshot-1'},
      {id:'portfolio-2',name:'Growth',holdings_date:'2026-10-02',snapshot_id:'snapshot-2'}
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
  assert.match(node('#page-content').innerHTML,/Core/);
  assert.match(node('#page-content').innerHTML,/Growth/);
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
    portfolio_imports:[{id:'portfolio-1',name:'Core',snapshot_id:'snapshot-1'}],
    snapshots:[{id:'snapshot-1',portfolio:[]}],
    companies:[],sources:[],cases:[]
  };
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
});
test('Theme Definition opens as a new theme with New Theme beside the saved themes heading',async()=>{
  const {run,node} = setup();
  run(`state.themes=[{id:'theme-1',theme_name:'AI Infrastructure',approved:true,nodes:[]}]`);
  await run('viewTheme()');
  assert.match(node('#page-content').innerHTML,/New theme scope/);
  assert.match(node('#page-content').innerHTML,/name="theme_name"[^>]*value=""/);
  assert.match(node('#page-content').innerHTML,/Saved themes <span class="badge badge-neutral">1<\/span>[\s\S]*id="btn-new-theme"/);
  assert.equal(node('#page-actions').innerHTML,'');
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
test('Portfolio Import defaults to Excel import, lists saved portfolios, and opens holdings',async()=>{
  const {run,node,portfolioCards,deletePortfolioButtons} = setup();
  run(`state = {
    companies:[{id:'company-1',name:'TSMC',bloomberg_ticker:'2330 TT',sedol:'6889106'}],
    sources:[],
    portfolio_imports:[{
      id:'portfolio-1',name:'Asia Core',holdings_date:'2026-10-01',file_name:'asia-core.xlsx',
      benchmark_name:'MSCI Asia ex Japan',snapshot_id:'snapshot-1',
      rows:[{company_id:'company-1',company_name:'TSMC',bloomberg_ticker:'2330 TT',
        sedol:'6889106',country:'Taiwan',sector:'Semiconductors',
        portfolio_weight:0.2,reference_weight:0.1,active_weight:0.1}]
    }]
  };`);
  portfolioCards.push({
    dataset:{openPortfolio:'portfolio-1'},
    addEventListener(type, handler) { this[type] = handler; }
  });
  await run('viewPortfolio()');
  assert.equal(node('#page-actions').innerHTML,'');
  assert.match(node('#page-content').innerHTML,/Import portfolio Excel/);
  assert.match(node('#page-content').innerHTML,/Existing portfolios/);
  assert.match(node('#page-content').innerHTML,/Existing portfolios <span class="badge badge-neutral">1<\/span>/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);
  assert.match(node('#page-content').innerHTML,/asia-core\.xlsx/);
  assert.doesNotMatch(node('#page-content').innerHTML,/btn-add-row|\+ Add row/);
  assert.match(node('#page-content').innerHTML,/data-open-portfolio="portfolio-1"/);
  assert.match(node('#page-content').innerHTML,/data-del-portfolio="portfolio-1"/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Company universe/);
  portfolioCards[0].click();
  assert.match(node('#page-content').innerHTML,/TSMC/);
  assert.match(node('#page-content').innerHTML,/Portfolio wt/);
  assert.equal(node('#page-content').innerHTML.includes('20.0%'),true);
  node('#btn-back-portfolios').onclick();
  assert.match(node('#page-content').innerHTML,/Import portfolio Excel/);
  assert.match(node('#page-content').innerHTML,/Asia Core/);
});
test('Deleting an existing portfolio confirms and removes it from the portfolio list',async()=>{
  const {run,node,deletePortfolioButtons} = setup();
  run(`state={portfolio_imports:[{id:'portfolio-1',name:'Asia Core',snapshot_id:'snapshot-1',rows:[]}],
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
    portfolio_imports:[{id:'portfolio-1',name:'Core',snapshot_id:'snapshot-1',rows:[]}],
    snapshots:[{id:'snapshot-1',benchmark_name:'Index',portfolio:[]}],
    companies:[],sources:[],cases:[]
  };
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
test('Source Documents portfolio selector scopes companies, documents and search request',async()=>{
  const {run,node,context} = setup();
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
      {id:'portfolio-a',name:'Asia Core',rows:[{company_id:'company-a',portfolio_weight:1}]},
      {id:'portfolio-b',name:'Korea Growth',rows:[{company_id:'company-b',portfolio_weight:1}]}
    ]
  };
  const searchCalls=[];
  api=async(path,data)=>{searchCalls.push({path,data});return {results:[]};};
  refresh=async()=>{};
  render=async()=>{};`);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/id="source-portfolio-select"/);
  assert.match(node('#page-content').innerHTML,/Korea Growth/);
  assert.match(node('#page-content').innerHTML,/Samsung/);
  assert.doesNotMatch(node('#page-content').innerHTML,/TSMC/);

  await node('#source-portfolio-select').change({target:{value:'portfolio-a'}});
  assert.match(node('#page-content').innerHTML,/TSMC/);
  assert.doesNotMatch(node('#page-content').innerHTML,/Samsung/);

  context.document.querySelectorAll = selector =>
    selector === 'input[name="company_ids"]:checked' ? [{value:'company-a'}] : [];
  await node('#btn-run-search').onclick();
  assert.equal(run('searchCalls[0].path'),'source/search');
  assert.deepEqual(JSON.parse(JSON.stringify(run('searchCalls[0].data.company_ids'))),['company-a']);
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
  run(`state.portfolio_imports=[{id:'p',name:'Both',rows:[{company_id:'a'},{company_id:'b'}]}]`);
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
  run(`state.portfolio_imports=[{id:'p',name:'Both',rows:[{company_id:'a'},{company_id:'b'}]}]`);
  await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/Market Cap/);
  assert.match(node('#page-content').innerHTML,/Net Revenue/);
  assert.match(node('#page-content').innerHTML,/Expand all 6 sources/);
  const html = run(`sourceLibrary(
    [{company_id:'a',source_type:'annual_report'}, {company_id:'a',source_type:'annual_report'}],
    [{id:'a',name:'TSMC'}], {rows:[{company_id:'a'}]}
  )`);
  assert.match(html,/1 \/ 6/); assert.doesNotMatch(html,/2 \/ 6/);
  for(const label of ['Annual Report','Earnings Call Transcript','Investor Presentation','Sustainability / ESG Report','Company Website / IR Page','Regulatory Filing']) assert.ok(html.includes(label));
});
test('Financials use evidence and keep missing values unknown',()=>{
  const {run} = setup();
  assert.equal(run(`getCompanyFinancials({id:'a',bloomberg_ticker:'2330 TT'}, {}, [], []).mcap`),'—');
  assert.equal(run(`getCompanyFinancials({id:'a'}, {}, [{filename:'report.txt',financial_facts:[{metric:'revenue',display:'USD 100 million',page:2}]}], []).rev`),'USD 100 million');
});
test('Unified Step 4 renders queue, review workspace and bulk button',async()=>{
  const {run,node} = setup(); await run('viewAnalysis()');
  assert.match(node('#page-content').innerHTML,/Analysis &amp; Review Queue/);
  assert.match(node('#page-content').innerHTML,/case-detail-panel/);
  const sidebar = fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.match(sidebar,/AI Analysis &amp; Review/);
  assert.doesNotMatch(sidebar,/data-view="review"/);
});
test('Company source rows expand and collapse',()=>{
  const {run,node}=setup(); node('#src-detail-a').style.display='none';
  run("window.toggleSourceRow('a')"); assert.equal(node('#src-detail-a').style.display,'table-row');
  run("window.toggleSourceRow('a')"); assert.equal(node('#src-detail-a').style.display,'none');
});
test('Case tiles show point, bounds and rubric percentage; unknown point stays a dash',()=>{
  const {run,node}=setup();
  run(`state.cases=[{id:'case',company_id:'a',classification_confidence:'high', review_status:'approved',case:{units:[]},estimate:{point:0.4,lower:0.3,upper:0.5,issues:[]}}]; window.openCase('case')`);
  let html=node('#case-detail-panel').innerHTML;
  for(const label of ['Point estimate','40.0%','30.0% – 50.0%','92%','High Confidence (3/3)']) assert.ok(html.includes(label),label);
  run(`state.cases[0].estimate.point=null; window.openCase('case')`);
  html=node('#case-detail-panel').innerHTML; assert.match(html,/font-size:26px">—/);
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
