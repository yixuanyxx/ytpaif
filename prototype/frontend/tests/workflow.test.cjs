const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function setup() {
  const elements = new Map();
  const node = id => {
    if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', value:'', style:{}, dataset:{},
      addEventListener(type, fn) { this[type] = fn; }, classList:{add(){},remove(){},toggle(){}}});
    return elements.get(id);
  };
  const rows = ['6889106', '6771720'].map((sedol, i) => ({style:{}, dataset:{sedol, name:['tsmc','samsung'][i],ticker:['2330 tt','005930 ks'][i]}}));
  const context = vm.createContext({console, setTimeout, clearTimeout, FormData:class { constructor(form){return Object.entries(form.fields || {});} }, window:{}, document:{
    addEventListener(){}, querySelector:node, getElementById:id=>node('#'+id),
    querySelectorAll:selector=>selector === '.company-search-item' ? rows : []
  }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8'), context);
  vm.runInContext(`state = {companies:[{id:'a',name:'TSMC',sedol:'6889106'}, {id:'b',name:'Samsung',sedol:'6771720'}],sources:[],themes:[],cases:[]}`,context);
  return {context,node,rows,run:code=>vm.runInContext(code,context)};
}
test('Step 3 filters immediately by SEDOL, ticker, name and dropdown; clearing restores all', async()=>{
  const {run,node,rows} = setup(); await run('viewSources()');
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
  const {run,node} = setup(); await run('viewSources()');
  assert.match(node('#page-content').innerHTML,/Market Cap/);
  assert.match(node('#page-content').innerHTML,/Net Revenue/);
  assert.match(node('#page-content').innerHTML,/Expand all 6 sources/);
  const html = run(`sourceLibrary([{company_id:'a',source_type:'annual_report'}, {company_id:'a',source_type:'annual_report'}])`);
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
