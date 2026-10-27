// Run the actual page script with a small DOM: verify filtering, safe text rendering,
// expired/pending exclusion, Japanese weekday labels and a visible fetch error.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('site/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.listeners={};}
  append(e){this.children.push(e);}
  replaceChildren(){this.children=[];this.textContent='';}
  addEventListener(event,fn){this.listeners[event]=fn;}
}
function setup(data,fail=false){
  const nodes=Object.fromEntries(['filter','prefecture','search','count','events'].map(k=>[k,new Element()]));
  const context=vm.createContext({document:{getElementById:k=>nodes[k],createElement:t=>new Element(t)},Intl,Date,Set,Error,Object,URL,
    fetch:async()=>{if(fail)throw Error('offline');return {ok:true,json:async()=>data};},location:{reload(){}}});
  vm.runInContext(script,context);
  return {nodes,context};
}
const base={status:'approved',date_start:'2099-03-07',date_end:'2099-03-07',category:'speed',prefecture:'東京都',official_url:'https://example.org'};
(async()=>{
  const {nodes,context}=setup([{...base,title:'<img src=x onerror=alert(1)>'},{...base,title:'ウルトラ大会',category:'marathon',prefecture:'岐阜県'},
    {...base,title:'未承認大会',status:'pending'},{...base,title:'終了大会',date_start:'2000-01-01',date_end:'2000-01-01'}]);
  await new Promise(setImmediate);
  assert.equal(nodes.events.children.length,2);
  assert.equal(nodes.events.children[0].children[1].textContent,'<img src=x onerror=alert(1)>');
  assert.match(nodes.events.children[0].children[2].textContent,/曜日|\([日月火水木金土]\)|（[日月火水木金土]）/);
  nodes.filter.value='marathon';nodes.filter.listeners.change();
  assert.equal(nodes.events.children.length,1);
  assert.equal(nodes.events.children[0].children[1].textContent,'ウルトラ大会');
  nodes.search.value='見つからない';nodes.search.listeners.input();
  assert.match(nodes.events.children[0].textContent,/条件に合う大会はありません/);
  assert.match(vm.runInContext('todayJST()',context),/^\d{4}-\d{2}-\d{2}$/);
  const empty=setup([]);await new Promise(setImmediate);
  assert.match(empty.nodes.events.children[0].textContent,/現在、掲載中の大会はありません/);
  const error=setup([],true);await new Promise(setImmediate);
  assert.match(error.nodes.events.children[0].textContent,/読み込めません/);
  assert.equal(error.nodes.events.children[1].tag,'button');
  const pdfURL='https://example.org/guide.PDF?download=1#details';
  const sourceURL='https://example.org/race';
  const pdf=setup([{...base,title:'PDF要項の大会',official_url:pdfURL,source_url:sourceURL}]);await new Promise(setImmediate);
  const pdfLinks=pdf.nodes.events.children[0].children.find(e=>e.className==='links').children;
  assert.equal(pdfLinks.length,2);
  assert.equal(pdfLinks[0].textContent,'掲載元のWebページ ↗');
  assert.equal(pdfLinks[0].href,sourceURL);
  assert.equal(pdfLinks[1].textContent,'大会要項（PDF）を見る ↗');
  assert.equal(pdfLinks[1].href,pdfURL);
  assert.equal(pdfLinks[0].rel,'noopener noreferrer');
  for(const source of [undefined,pdfURL,'https://example.org/another.pdf','javascript:alert(1)','https://user:password@example.org/']){
    const page=setup([{...base,title:'掲載元なしの大会',official_url:pdfURL,source_url:source}]);await new Promise(setImmediate);
    const links=page.nodes.events.children[0].children.find(e=>e.className==='links').children;
    assert.equal(links.length,1);
    assert.equal(links[0].href,pdfURL);
  }
  const website=setup([{...base,title:'通常のWeb大会',source_url:sourceURL}]);await new Promise(setImmediate);
  const webLinks=website.nodes.events.children[0].children.find(e=>e.className==='links').children;
  assert.equal(webLinks.length,1);assert.equal(webLinks[0].textContent,'大会の公式情報を見る ↗');
  const unsafe=setup([{...base,title:'不正URLの大会',official_url:'javascript:alert(1)',source_url:sourceURL}]);await new Promise(setImmediate);
  assert.equal(unsafe.nodes.events.children[0].children.filter(e=>e.className==='links').length,0);
  console.log('Site checks passed: filtering, dates, pending/expired exclusion, text safety, empty/error states.');
})().catch(e=>{console.error(e);process.exit(1);});
