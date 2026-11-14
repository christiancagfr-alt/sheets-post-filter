import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const html=await fs.readFile(new URL('../summary-gallery/index.html',import.meta.url),'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\binit\(\);\s*$/,'');
function frontend() {
  const elements=new Map();
  const session=new Map([['summary-gallery-token-v1','fixture-token']]);
  const element=id=>{
    if(!elements.has(id)) elements.set(id,{value:'',dataset:{},classList:{add(){},remove(){},toggle(){}},innerHTML:'',textContent:'',querySelectorAll:()=>[],insertAdjacentHTML(_position,html){this.innerHTML+=html;}});
    return elements.get(id);
  };
  const context=vm.createContext({console,URL,URLSearchParams,setInterval:()=>1,clearInterval(){},
    window:{},document:{getElementById:element,querySelectorAll:()=>[],hidden:false},
    sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)},localStorage:{getItem:()=>null}});
  vm.runInContext(source,context);
  return {run:code=>vm.runInContext(code,context),context,elements};
}

function localDatabase(f, storage=new Map()) {
  // A small IndexedDB API adapter tests real client open/get/put/clear calls.
  const db={objectStoreNames:{contains:()=>true},close(){},transaction(){
    const tx={};const done=()=>queueMicrotask(()=>tx.oncomplete?.());
    tx.objectStore=()=>({
      get(key){const request={};queueMicrotask(()=>{request.result=structuredClone(storage.get(key));request.onsuccess?.();done();});return request;},
      put(value){storage.set(value.key,structuredClone(value));done();},
      clear(){storage.clear();done();},
    });return tx;
  }};
  f.context.window.setTimeout=setTimeout;f.context.window.clearTimeout=clearTimeout;
  f.context.window.indexedDB={open(){const request={};queueMicrotask(()=>{request.result=db;request.onsuccess?.();});return request;}};
  return storage;
}

function cacheApi(f, key='build:2026-10-30', options={}) {
  const actions=[];
  f.run(`renderNav=renderFilters=renderGalleryPreservingPosition=()=>{}; showLogin=()=>{};`);
  f.context.fetch=async(_url,request)=>{
    const body=JSON.parse(request.body);actions.push(body.action);
    const data=options.unauthorized?{ok:false,error:'登录已过期'}:{ok:true,assets:[{id:'one',date:'2026-01-01',maker:'甲'}],catalogs:[],cacheMeta:{cacheKey:key,total:1},total:1,hasMore:false,nextOffset:null};
    return {ok:!options.unauthorized,status:options.unauthorized?401:200,text:async()=>JSON.stringify(data)};
  };return actions;
}

test('persistent cache roundtrip reopens with one authenticated version check and no list pages',async()=>{
  const first=frontend(),storage=localDatabase(first),initial=cacheApi(first);
  await first.run('loadRemoteData()');await first.run('browserCacheWrite');
  assert.deepEqual(initial,['list']);assert.equal(storage.get('assets').value.complete,true);
  const reopened=frontend();localDatabase(reopened,storage);const calls=cacheApi(reopened);
  await reopened.run('loadRemoteData()');
  assert.deepEqual(calls,['cacheMeta']);
  assert.equal(reopened.run('assets.length'),1);
  assert.match(reopened.elements.get('resultText').textContent,/已使用本地缓存/);
});

test('new builds and new display dates invalidate disk cache, while forced refresh always reloads',async()=>{
  const first=frontend(),storage=localDatabase(first);cacheApi(first);
  await first.run('loadRemoteData()');await first.run('browserCacheWrite');
  for(const key of ['next-build:2026-10-30','next-build:2026-10-31']) {
    const next=frontend();localDatabase(next,storage);const actions=cacheApi(next,key);
    await next.run('loadRemoteData()');await next.run('browserCacheWrite');
    assert.deepEqual(actions,['cacheMeta','list']);
    assert.equal(storage.get('assets').value.cacheMeta.cacheKey,key);
    actions.length=0;await next.run('refreshData()');await next.run('browserCacheWrite');
    assert.deepEqual(actions,['list']);
  }
});

test('expired sessions clear cache, and failed network validation never displays disk data',async()=>{
  const first=frontend(),storage=localDatabase(first);cacheApi(first);
  await first.run('loadRemoteData()');await first.run('browserCacheWrite');
  const offline=frontend();localDatabase(offline,storage);
  offline.context.fetch=async()=>{throw new Error('offline');};
  await assert.rejects(offline.run('loadRemoteData()'),/offline/);
  assert.equal(offline.run('assets.length'),0);
  const expired=frontend();localDatabase(expired,storage);const calls=cacheApi(expired,'same',{unauthorized:true});
  await assert.rejects(expired.run('loadRemoteData()'),/登录已过期/);
  assert.deepEqual(calls,['cacheMeta']);assert.equal(storage.size,0);assert.equal(expired.run('assets.length'),0);
});

test('cache storage errors fall back to fresh data without blocking it',async()=>{
  const f=frontend();localDatabase(f);const actions=cacheApi(f);
  f.run(`readBrowserAssetCache=async()=>{throw new Error('database denied');}; writeBrowserAssetCache=async()=>{throw new Error('quota exceeded');};`);
  await f.run('loadRemoteData()');await f.run('browserCacheWrite');
  assert.deepEqual(actions,['list']);assert.equal(f.run('assets.length'),1);
});

test('a build change halfway through pagination restarts once and never caches mixed versions',async()=>{
  const f=frontend(),storage=localDatabase(f);cacheApi(f);let calls=0;
  f.context.fetch=async()=>{
    calls++;
    const first=calls===1;
    const data={ok:true,assets:[{id:first?'old':'new',date:'2026-01-01'}],catalogs:[],
      cacheMeta:{cacheKey:first?'old:day':'new:day',total:first?2:1},total:first?2:1,
      hasMore:first,nextOffset:first?1:null,nextCursor:first?'continue':null};
    return {ok:true,status:200,text:async()=>JSON.stringify(data)};
  };
  await f.run('loadRemoteData()');await f.run('browserCacheWrite');
  assert.equal(calls,3);
  assert.equal(storage.get('assets').value.cacheMeta.cacheKey,'new:day');
  assert.deepEqual(storage.get('assets').value.assets.map(item=>item.id),['new']);
});

test('logging out while a list request is pending cannot repopulate memory or disk cache',async()=>{
  const f=frontend(),storage=localDatabase(f);cacheApi(f);
  let finish;
  f.context.fetch=()=>new Promise(resolve=>{finish=resolve;});
  const pending=f.run('loadAssetsPages({force:true})');
  await f.run('invalidateLogin()');
  finish({ok:true,status:200,text:async()=>JSON.stringify({ok:true,assets:[{id:'late'}],catalogs:[],cacheMeta:{cacheKey:'same',total:1},total:1,hasMore:false,nextOffset:null})});
  await pending;await f.run('browserCacheWrite');
  assert.equal(f.run('assets.length'),0);assert.equal(storage.size,0);
});

test('only the requested grid layout remains, producer names populate and filter independently',()=>{
  assert.equal((html.match(/data-view=/g)||[]).length,1);
  assert.match(html,/<section class="masonry is-grid" id="gallery"/);
  const f=frontend();
  f.run(`setAssets([{id:'one',maker:'制作人甲',title:'标题一',date:'2026-01-01'}, {id:'two',maker:'制作人乙',title:'标题二',date:'2026-01-01'}]); renderFilters();`);
  assert.match(f.elements.get('makerSelect').innerHTML,/制作人甲/);
  assert.match(f.elements.get('makerSelect').innerHTML,/制作人乙/);
  assert.equal(f.run(`state.maker='制作人乙'; getFilteredAssets().map(asset=>asset.id).join()`),'two');
  f.run('renderFilters()');
  assert.equal(f.elements.get('makerSelect').value,'制作人乙');
});

test('Drive HTML is never an image candidate, successful fallback is reused and failures stop retrying',async()=>{
  const f=frontend();
  f.run(`setAssets([normalizeAsset({id:'one',fileId:'abcdefghijk123456',mediaType:'video',viewUrl:'https://drive.google.com/file/d/abcdefghijk123456/view',previewUrl:'https://example.com/thumb.jpg'})]);`);
  assert.equal(f.run(`getPreviewCandidates(assets[0]).some(url=>url.endsWith('/view'))`),false);
  assert.equal(f.run(`previewOverrideById.set('one','https://example.com/working.jpg'); getAssetImageSrc(assets[0])`),'https://example.com/working.jpg');
  f.context.img={tagName:'IMG',dataset:{assetImg:'one',previewIndex:'99'},closest:()=>null};
  await f.run('handleImageError({target:img})');
  assert.equal(f.run(`getAssetImageSrc(assets[0])===PREVIEW_PLACEHOLDER`),true);
  f.run('handleImageLoad({target:img})');
  assert.equal(f.run(`previewBlockedIds.has('one')`),true);
});

test('initial loading combines directory/metadata with list and visible polling makes only one request',async()=>{
  const f=frontend();
  // Isolate network orchestration from DOM rendering; these are unit tests, not browser screenshots.
  f.run(`renderNav=renderFilters=renderGallery=renderGalleryPreservingPosition=()=>{}; hideLogin=()=>{};`);
  const actions=[];
  f.context.fetch=async(_url,request)=>{
    const body=JSON.parse(request.body);actions.push(body.action);
    return {ok:true,status:200,text:async()=>JSON.stringify({ok:true,assets:[{id:'one',date:'2026-01-01',maker:'甲'}],catalogs:[],cacheMeta:{cacheKey:'current',total:1},total:1,hasMore:false,nextOffset:null})};
  };
  await f.run('loadRemoteData()');
  assert.deepEqual(actions,['list']);
  actions.length=0;
  await f.run('lastCacheCheck=0; checkVisibleCache()');
  assert.deepEqual(actions,['cacheMeta']);
  actions.length=0;
  await f.run('document.hidden=true; lastCacheCheck=0; checkVisibleCache()');
  assert.deepEqual(actions,[]);
});

test('old Drive view links download originals through the native frame with no Worker or file proxy request',async()=>{
  const f=frontend(),clicked=[];
  f.context.document.createElement=()=>({click(){clicked.push({href:this.href,target:this.target,download:this.download});},remove(){}});
  f.context.document.body={appendChild(){}};
  f.context.fetch=()=>{throw new Error('Drive download must not call the gallery or buffer media');};
  f.run(`statsClient=()=> '1'.repeat(32); apiPost=async()=>({ok:true,downloads:1,favorites:0}); syncAssetViews=()=>{}; setAssets([{id:'drive',title:'原图',downloadUrl:'https://drive.google.com/file/d/abcdefghijk123456/view?resourcekey=key-one',viewUrl:'https://drive.google.com/file/d/abcdefghijk123456/view'}]);`);
  await f.run(`downloadAsset('drive')`);
  assert.equal(clicked.length,1);
  assert.match(clicked[0].href,/export=download/);
  assert.match(clicked[0].href,/resourcekey=key-one/);
  assert.equal(clicked[0].target,'assetDownloadFrame');
  assert.equal(f.run(`toDriveDownloadUrl('https://example.com/image.png?id=abcdefghijk123456')`),'');
  assert.match(f.run(`assetCardHtml(assets[0])`),/target="assetDownloadFrame" download/);
});

test('home page displays the material grid beneath compact category navigation',()=>{
  const f=frontend();
  f.run(`renderHomeView=renderActivePath=updateStats=syncNavState=()=>{};
    renderGalleryCards=items=>{els.gallery.innerHTML=items.map(item=>item.id).join(',');};
    setAssets([{id:'first',date:'2026-01-01'}]); assetsLoaded=true; renderGallery();`);
  assert.equal(f.elements.get('gallery').hidden,false);
  assert.equal(f.elements.get('gallery').innerHTML,'first');
});

test('large galleries render another batch only as requested, not thousands of cards at startup',()=>{
  const f=frontend();
  f.run(`setAssets(Array.from({length:2000},(_,i)=>({id:'asset-'+i,date:'2026-01-01',title:'素材'}))); renderGalleryCards(assets);`);
  const count=()=> (f.elements.get('gallery').innerHTML.match(/<article /g)||[]).length;
  assert.equal(count(),90);
  assert.equal(f.elements.get('galleryMoreBtn').hidden,false);
  f.elements.get('galleryMoreBtn').onclick();
  assert.equal(count(),180);
});

test('ordinary file hosts download through a browser Blob, and CORS failures never navigate to an image',async()=>{
  const f=frontend(),clicked=[],requests=[];
  f.context.URL=class extends URL {static createObjectURL(){return 'blob:local-fixture';}static revokeObjectURL(){}};
  f.context.setTimeout=()=>1;
  f.context.document.createElement=()=>({click(){clicked.push({href:this.href,download:this.download});},remove(){}});
  f.context.document.body={appendChild(){}};
  f.context.fetch=async url=>{requests.push(url);return {ok:true,headers:{get:()=> 'image/jpeg'},blob:async()=>({})};};
  f.run(`statsClient=()=> '1'.repeat(32); apiPost=async()=>({ok:true,downloads:1,favorites:0}); syncAssetViews=()=>{}; setAssets([{id:'other',title:'原图',viewUrl:'https://files.example.com/image.jpg'}]);`);
  await f.run(`downloadAsset('other')`);
  assert.deepEqual(requests,['https://files.example.com/image.jpg']);
  assert.deepEqual(clicked,[{href:'blob:local-fixture',download:'原图.jpg'}]);
  f.context.fetch=async()=>{throw new Error('CORS');};
  await assert.rejects(f.run(`downloadAsset('other')`),/不允许网页下载/);
  assert.equal(clicked.length,1);
});

test('favorites use server totals and failed writes preserve local state',async()=>{
  const f=frontend();
  f.run(`setAssets([{id:'one',favorites:4,downloads:7}]); statsClient=()=> '1'.repeat(32);
    syncAssetViews=saveState=()=>{}; apiPost=async()=>({favorites:5,downloads:7});`);
  await f.run("toggleFavorite('one')");
  assert.equal(f.run('assets[0].favorites'),5);
  assert.equal(f.run("state.favorites.has('one')"),true);
  f.run(`apiPost=async()=>{throw new Error('offline');};`);
  await assert.rejects(f.run("toggleFavorite('one')"),/offline/);
  assert.equal(f.run('assets[0].favorites'),5);
  assert.equal(f.run("state.favorites.has('one')"),true);
});
test('statistics clear stale counts without clearing assignee data',()=>{
  const f=frontend();
  f.run(`setAssets([{id:'one',favorites:4,downloads:7,assignee:'示例'}]);applyStatsMap({});`);
  assert.equal(f.run('assets[0].favorites'),0);
  assert.equal(f.run('assets[0].downloads'),0);
  assert.equal(f.run('assets[0].assignee'),'示例');
});

test('opening a cached gallery refreshes server statistics even when the build is unchanged',async()=>{
  const first=frontend(),storage=localDatabase(first);cacheApi(first);
  await first.run('loadRemoteData()');await first.run('browserCacheWrite');
  const next=frontend();localDatabase(next,storage);const calls=[];
  next.run('renderNav=renderFilters=renderGalleryPreservingPosition=syncAssetViews=()=>{};');
  next.context.fetch=async(_url,request)=>{
    const body=JSON.parse(request.body);calls.push(body.action);
    const data=body.action==='stats'?{ok:true,stats:{one:{downloads:9,favorites:3}}}:
      {ok:true,cacheMeta:{statistics:true,cacheKey:'build:2026-10-30',total:1}};
    return {ok:true,text:async()=>JSON.stringify(data)};
  };
  await next.run('loadRemoteData()');
  assert.deepEqual(calls,['cacheMeta','stats']);
  assert.equal(next.run('assets[0].downloads'),9);
  assert.equal(next.run('assets[0].favorites'),3);
});

test('first-page statistics render immediately and survive later pages without waiting for full loading',async()=>{
  const f=frontend();let releaseSecond;const rendered=[];
  f.run(`renderNav=renderFilters=()=>{}; renderGalleryPreservingPosition=()=>{};`);
  f.context.capture=()=>rendered.push(f.run('assets.map(a=>({id:a.id,favorites:a.favorites,downloads:a.downloads}))'));
  f.run('renderGalleryPreservingPosition=capture;');
  f.context.fetch=async(_url,request)=>{
    const body=JSON.parse(request.body);
    const meta={statistics:true,cacheKey:'same-build',total:2};
    if(body.cursor) await new Promise(resolve=>{releaseSecond=resolve;});
    const data=body.cursor?{ok:true,assets:[{id:'two'}],cacheMeta:meta,total:2,hasMore:false,nextOffset:null}:
      {ok:true,assets:[{id:'one'}],stats:{one:{downloads:8,favorites:2},two:{downloads:3,favorites:1}},
       cacheMeta:meta,total:2,hasMore:true,nextOffset:1,nextCursor:'second'};
    return {ok:true,text:async()=>JSON.stringify(data)};
  };
  const loading=f.run('loadAssetsPages()');
  while(!releaseSecond) await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.run('assetsLoaded'),false);
  assert.equal(rendered[0][0].favorites,2);
  assert.equal(rendered[0][0].downloads,8);
  releaseSecond();await loading;
  assert.equal(f.run('assets[1].favorites'),1);
  assert.equal(f.run('assets[1].downloads'),3);
});
