import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import worker, {today,releaseDate,isReleased,listPage} from '../summary-gallery/gallery-worker.js';
if (!globalThis.crypto) globalThis.crypto=webcrypto;

class Bucket {
  objects=new Map();
  async put(key,value,options={}) {this.objects.set(key,{value,options});}
  async get(key) {const object=this.objects.get(key);return object?{json:async()=>JSON.parse(object.value)}:null;}
  async list({prefix='',limit=1000,include=[],cursor}={}) {
    const all=[...this.objects.keys()].filter(key=>key.startsWith(prefix)).sort();
    const start=Number(cursor)||0;
    const objects=all.slice(start,start+limit).map(key=>({key,customMetadata:this.objects.get(key).options.customMetadata||{}}));
    return {objects,truncated:start+limit<all.length,cursor:String(start+limit)};
  }
}
const streamA='a'.repeat(24),streamB='b'.repeat(24),build='c'.repeat(32);
const metadata=(kind='image',days=4)=>({mediaType:kind,delayDays:days,timezone:'Europe/Lisbon',releaseCounts:{'2026-10-24':{'分类':1}}});
const asset=(date='2026-10-20',kind='image')=>({id:'d'.repeat(32),date,mediaType:kind,sourceUrl:'https://example.com/a',viewUrl:'https://example.com/a',type:'分类'});
const env=()=>({GALLERY_CACHE:new Bucket(),CACHE_PUBLISH_SECRET:'test-only-secret',ASSETS:{fetch:async()=>new Response('page')}});

test('oversized declared or streamed request bodies stop before JSON parsing',async()=>{
  const e=env();
  const declared=await worker.fetch(new Request('https://gallery.example.com/api/summary-publish',{
    method:'POST',headers:{'x-publish-secret':e.CACHE_PUBLISH_SECRET,'content-length':String(4*1024*1024)},body:'{}'}),e);
  assert.equal(declared.status,413);
  const streamed=await worker.fetch(new Request('https://gallery.example.com/api/gallery',{
    method:'POST',body:JSON.stringify({action:'login',password:'示例'.repeat(12000)})}),e);
  assert.equal(streamed.status,413);
});
async function publish(e,body,secret=e.CACHE_PUBLISH_SECRET) {
  const response=await worker.fetch(new Request('https://gallery.example.com/api/summary-publish',{method:'POST',headers:{'x-publish-secret':secret},body:JSON.stringify(body)}),e);
  return {status:response.status,...await response.json()};
}
async function api(e,body) {
  const response=await worker.fetch(new Request('https://gallery.example.com/api/gallery',{method:'POST',body:JSON.stringify(body)}),e);
  return {status:response.status,...await response.json()};
}

test('20th images release exactly at 24th midnight, videos on 27th; configurable timezone',()=>{
  assert.equal(releaseDate('2026-10-20',4),'2026-10-24');
  assert.equal(releaseDate('2026-10-20',7),'2026-10-27');
  assert.equal(isReleased(asset(),metadata(),new Date('2026-10-23T22:59:59Z')),false);
  assert.equal(isReleased(asset(),metadata(),new Date('2026-10-23T23:00:00Z')),true);
  assert.equal(isReleased(asset('2026-10-20','video'),metadata('video',7),new Date('2026-10-26T23:59:59Z')),false);
  assert.equal(isReleased(asset('2026-10-20','video'),metadata('video',7),new Date('2026-10-27T00:00:00Z')),true);
  assert.equal(today('Asia/Shanghai',new Date('2026-10-23T16:00:00Z')),'2026-10-24');
});

test('missing/wrong publish secret rejected and protocol handshake reports correct service',async()=>{
  const e=env();
  assert.equal((await publish(e,{action:'hello'},'wrong')).status,401);
  assert.equal((await publish(e,{action:'hello'})).service,'summary-gallery/v1');
  assert.equal(e.GALLERY_CACHE.objects.size,0);
});

test('independent image/video streams survive publishing and incomplete builds never replace committed data',async()=>{
  const e=env();
  for(const [stream,kind,days] of [[streamA,'image',4],[streamB,'video',7]]) {
    assert.equal((await publish(e,{action:'put-chunk',stream,buildId:build,index:1,assets:[asset('2026-10-20',kind)]})).ok,true);
    const meta=metadata(kind,days);
    meta.releaseCounts={[releaseDate('2026-10-20',days)]:{'分类':1}};
    assert.equal((await publish(e,{action:'finalize',stream,buildId:build,chunkCount:1,totalRows:1,metadata:meta})).ok,true);
  }
  const key='summary/streams/'+streamA+'.json';
  const original=e.GALLERY_CACHE.objects.get(key).value;
  const failed=await publish(e,{action:'finalize',stream:streamA,buildId:'e'.repeat(32),chunkCount:2,totalRows:2,metadata:metadata()});
  assert.equal(failed.ok,false);
  assert.equal(e.GALLERY_CACHE.objects.get(key).value,original);
  assert.ok(e.GALLERY_CACHE.objects.has('summary/streams/'+streamB+'.json'));
  const info=await api(e,{action:'catalogs'});
  assert.deepEqual(info.catalogs.map(x=>x.name).sort(),['KB视频','图片']);
});

test('future items never returned, pagination crosses unreleased chunks without losing eligible items',async()=>{
  const bucket=new Bucket();
  for(let i=0;i<12;i++)await bucket.put('chunk'+i,JSON.stringify({assets:[asset(i<10?'2026-10-23':'2026-10-20')]}));
  const streams=[{stream:streamA,buildId:build,metadata:metadata(),chunks:Array.from({length:12},(_,i)=>'chunk'+i)}];
  const first=await listPage(streams,bucket,{limit:1},new Date('2026-10-24T12:00:00Z'));
  assert.equal(first.assets.length,0);assert.equal(first.hasMore,true);
  let page=first;
  const result=[];
  while(page.hasMore) {
    page=await listPage(streams,bucket,{limit:1,cursor:page.nextCursor},new Date('2026-10-24T12:00:00Z'));
    result.push(...page.assets);
  }
  assert.equal(result.length,2);
  assert.ok(result.every(asset=>asset.date==='2026-10-20'));
});

test('large pages create at most one timezone formatter rather than one per asset',async()=>{
  const bucket=new Bucket();
  const values=Array.from({length:500},(_,i)=>({...asset(),id:i.toString(16).padStart(32,'0')}));
  await bucket.put('large',JSON.stringify({assets:values}));
  const stream={stream:streamA,metadata:{...metadata(),timezone:'Pacific/Auckland'},chunks:['large']};
  const original=Intl.DateTimeFormat;let constructors=0;
  Intl.DateTimeFormat=function(...args){constructors++;return new original(...args);};
  try {
    const page=await listPage([stream],bucket,{limit:200},new Date('2026-10-30T12:00:00Z'));
    assert.equal(page.assets.length,200);
    assert.ok(constructors<=1,`created ${constructors} timezone formatters`);
  } finally {Intl.DateTimeFormat=original;}
});

test('pagination reads one R2 data chunk per request and preserves all eligible records in order',async()=>{
  const bucket=new Bucket();const chunks=[];const expected=[];
  for(let part=0;part<5;part++) {
    const values=Array.from({length:500},(_,i)=>({...asset(i%3===0?'2030-01-01':'2026-10-20'),id:(part*500+i).toString(16).padStart(32,'0')}));
    const key='large-'+part;chunks.push(key);await bucket.put(key,JSON.stringify({assets:values}));
    expected.push(...values.filter(value=>isReleased(value,metadata(),new Date('2026-10-30T12:00:00Z'))).map(value=>value.id));
  }
  const streams=[{stream:streamA,metadata:metadata(),chunks}];
  const get=bucket.get.bind(bucket);let reads=0;
  bucket.get=async key=>{reads++;return get(key);};
  const actual=[];let cursor;
  do {
    reads=0;
    const page=await listPage(streams,bucket,{limit:200,cursor},new Date('2026-10-30T12:00:00Z'));
    assert.ok(reads<=1);
    actual.push(...page.assets.map(value=>value.id));
    cursor=page.nextCursor;
    if(!page.hasMore)break;
  }while(true);
  assert.deepEqual(actual,expected);
});

test('staging data and CDN routes are private, optional site password gates all gallery data',async()=>{
  const e=env();e.ACCESS_PASSWORD='view-test';
  for(const path of ['/cdn/summary/streams/a.json','/summary/builds/a.json']) {
    const response=await worker.fetch(new Request('https://gallery.example.com'+path),e);
    assert.equal(response.status,404);
  }
  assert.equal((await api(e,{action:'catalogs'})).status,401);
  assert.equal((await api(e,{action:'login',password:'wrong'})).status,401);
  const login=await api(e,{action:'login',password:'view-test'});
  assert.equal(login.ok,true);
  assert.equal((await api(e,{action:'catalogs',token:login.token})).ok,true);
});

test('all Worker responses carry noindex, including private routes and publish errors',async()=>{
  const e=env();
  for(const path of ['/','/api/gallery','/api/summary-publish','/summary/private']) {
    const response=await worker.fetch(new Request('https://gallery.example.com'+path),e);
    assert.match(response.headers.get('X-Robots-Tag'),/\bnoindex\b/);
  }
});

test('KB and effects video catalogs remain separate even though both use video playback',async()=>{
  const e=env();
  for(const [stream,id,name] of [[streamA,'kb_videos','KB视频'],[streamB,'effect_videos','效果视频']]) {
    await publish(e,{action:'put-chunk',stream,buildId:build,index:1,assets:[asset('2026-10-20','video')]});
    assert.equal((await publish(e,{action:'finalize',stream,buildId:build,chunkCount:1,totalRows:1,metadata:{...metadata('video',7),releaseCounts:{'2026-10-27':{'分类':1}},catalogId:id,catalogName:name}})).ok,true);
  }
  assert.deepEqual((await api(e,{action:'catalogs'})).catalogs.map(item=>item.name),['KB视频','效果视频']);
  const first=await api(e,{action:'list',limit:500});
  const next=await api(e,{action:'list',limit:500,cursor:first.nextCursor});
  assert.deepEqual([...first.assets,...next.assets].map(asset=>asset.group),['KB视频','效果视频']);
});

test('an explicit empty finalized stream removes its old material while other catalogs survive',async()=>{
  const e=env();
  for(const stream of [streamA,streamB]) {
    await publish(e,{action:'put-chunk',stream,buildId:build,index:1,assets:[asset()]});
    await publish(e,{action:'finalize',stream,buildId:build,chunkCount:1,totalRows:1,metadata:metadata()});
  }
  const original=(await api(e,{action:'cacheMeta'})).cacheMeta;
  const result=await publish(e,{action:'finalize',stream:streamA,buildId:'9'.repeat(32),chunkCount:0,totalRows:0,chunkHashes:[],metadata:{...metadata(),releaseCounts:{},excludedCategories:['分类'],excludedRows:1}});
  assert.equal(result.ok,true);
  const info=await api(e,{action:'cacheMeta'});
  assert.equal(info.cacheMeta.total,1);assert.notEqual(info.cacheMeta.cacheKey,original.cacheKey);
  assert.equal((await api(e,{action:'list',limit:500})).assets.length,1);
});

test('content-addressed chunks resume and remain isolated; rules change without re-uploading assets',async()=>{
  const e=env(),hash='1'.repeat(32),nextBuild='2'.repeat(32);
  const send=body=>publish(e,{stream:streamA,buildId:build,...body});
  assert.deepEqual((await send({action:'status'})).chunkHashes,[]);
  await send({action:'put-chunk',index:1,chunkHash:hash,assets:[asset()]});
  assert.deepEqual((await send({action:'status'})).chunkHashes,[hash]);
  assert.deepEqual((await publish(e,{action:'status',stream:streamB,buildId:build})).chunkHashes,[]);
  assert.equal((await send({action:'finalize',chunkCount:1,totalRows:1,chunkHashes:[hash],metadata:metadata()})).ok,true);
  assert.equal((await send({action:'status'})).buildId,build);
  // A rule-only update references the same chunk but immediately hides its data.
  const changed={...metadata('image',365),releaseCounts:{'2027-10-20':{'分类':1}}};
  assert.equal((await send({action:'finalize',buildId:nextBuild,chunkCount:1,totalRows:1,chunkHashes:[hash],metadata:changed})).ok,true);
  const data=await api(e,{action:'list',limit:500});
  assert.equal(data.assets.length,0);
  const old=e.GALLERY_CACHE.objects.get('summary/streams/'+streamA+'.json').value;
  assert.equal((await send({action:'finalize',chunkCount:1,totalRows:1,chunkHashes:['9'.repeat(32)],metadata:metadata()})).ok,false);
  assert.equal(e.GALLERY_CACHE.objects.get('summary/streams/'+streamA+'.json').value,old);
});

test('immutable chunk cache saves repeat R2 reads while changed release rules are always applied',async()=>{
  const bucket=new Bucket();await bucket.put('immutable',JSON.stringify({assets:[asset()]}));
  let reads=0;const get=bucket.get.bind(bucket);bucket.get=async key=>{reads++;return get(key);};
  const stream={stream:streamA,metadata:metadata(),chunks:['immutable']};
  const now=new Date('2026-10-30T12:00:00Z');
  assert.equal((await listPage([stream],bucket,{limit:500},now)).assets.length,1);
  assert.equal((await listPage([stream],bucket,{limit:500},now)).assets.length,1);
  assert.equal(reads,1);
  stream.metadata={...stream.metadata,delayDays:365};
  assert.equal((await listPage([stream],bucket,{limit:500},now)).assets.length,0);
});
