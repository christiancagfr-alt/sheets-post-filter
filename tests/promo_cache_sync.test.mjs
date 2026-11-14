import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const source=await fs.readFile(new URL('../promo-site/assets/app.js',import.meta.url),'utf8');

test('protected same-origin R2 manifest requests include the site login cookie',async()=>{
  const calls=[];
  const start=source.indexOf('async function fetchCacheSnapshotFromUrl');
  const end=source.indexOf('async function fetchCacheSnapshotViaProxy',start);
  const context=vm.createContext({
    URL,
    window:{location:{href:'https://gallery-example.pages.dev/',origin:'https://gallery-example.pages.dev'}},
    normalizeCacheUrl:value=>value,appendStatusLog:()=>{},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({chunks:[]})};}
  });
  vm.runInContext(source.slice(start,end),context);
  await vm.runInContext('fetchCacheSnapshotFromUrl("https://gallery-example.pages.dev/cdn/promo/manifest.json")',context);
  await vm.runInContext('fetchCacheSnapshotFromUrl("https://drive.google.com/uc?id=example")',context);
  assert.equal(calls[0].options.credentials,'same-origin');
  assert.equal(calls[1].options.credentials,'omit');
});

test('refresh JSON cache reads the existing R2 manifest and never calls the legacy mirror endpoint',async()=>{
  const calls=[];
  const context=vm.createContext({
    els:{refreshCdnBtn:{disabled:false}},
    setStatus:()=>{},appendStatusLog:()=>{},
    syncSharedCache:async options=>{calls.push(options);return true;}
  });
  const start=source.indexOf('async function refreshCdnCache');
  const end=source.indexOf('async function syncSharedCache',start);
  vm.runInContext(source.slice(start,end),context);
  await vm.runInContext('refreshCdnCache()',context);
  assert.equal(calls.length,1);
  assert.equal(calls[0].reset,true);
  assert.equal(calls[0].maxPages,Infinity);
  assert.equal(context.els.refreshCdnBtn.disabled,false);
});
