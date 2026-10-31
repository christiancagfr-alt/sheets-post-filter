/** Pages Advanced Mode: private R2 builds, server-side release dates, no Apps Script. */
const SERVICE = 'summary-gallery/v1';
const STREAMS = 'summary/streams/';
const BUILDS = 'summary/builds/';
const CHUNKS = 'summary/chunks/';
const MAX_CHUNK_READS_PER_PAGE = 1;
// Cache immutable chunks and ETag-validated manifests within one isolate only.
// Nothing is public-cached and release/password checks still run on every request.
const bucketCaches = new WeakMap();
function bucketCache(bucket) {
  if(!bucketCaches.has(bucket)) bucketCaches.set(bucket,{manifests:new Map(),chunks:new Map(),bytes:0});
  return bucketCaches.get(bucket);
}
// Initialize the common clocks during Worker startup, not inside the first
// CPU-limited list request. Additional configured zones are cached lazily.
const dateFormatters = new Map(['UTC','Europe/Lisbon','Asia/Shanghai'].map(zone=>[
  zone,new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}),
]));
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                    'X-Robots-Tag':'noindex, nofollow, noarchive, nosnippet, noimageindex'},
});
const idOK = value => /^[a-f0-9]{24,32}$/.test(String(value || ''));
const isoOK = value => /^\d{4}-\d{2}-\d{2}$/.test(String(value || '')) && Number.isFinite(Date.parse(value + 'T00:00:00Z'));

async function boundedJson(request, limit) {
  const declared=Number(request.headers.get('content-length'));
  const fail=()=>Object.assign(new Error('请求内容过大'),{status:413});
  if(declared>limit) throw fail();
  if(!request.body) return {};
  const reader=request.body.getReader(), chunks=[];
  let size=0;
  try {
    while(true) {
      const {done,value}=await reader.read();
      if(done) break;
      size+=value.byteLength;
      if(size>limit) {await reader.cancel();throw fail();}
      chunks.push(value);
    }
  } finally {reader.releaseLock();}
  const bytes=new Uint8Array(size);let offset=0;
  for(const chunk of chunks) {bytes.set(chunk,offset);offset+=chunk.byteLength;}
  return size?JSON.parse(new TextDecoder().decode(bytes)):{};
}

async function equal(a, b) {
  const bytes = new TextEncoder();
  const first = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes.encode(String(a))));
  const second = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes.encode(String(b))));
  let difference = 0;
  for (let i=0; i<first.length; i++) difference |= first[i] ^ second[i];
  return difference === 0;
}

export function today(zone, now = new Date()) {
  let formatter = dateFormatters.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-CA', {timeZone: zone, year:'numeric', month:'2-digit', day:'2-digit'});
    if (dateFormatters.size >= 32) dateFormatters.delete(dateFormatters.keys().next().value);
    dateFormatters.set(zone, formatter);
  }
  const parts = formatter.formatToParts(now);
  const values = Object.fromEntries(parts.map(part=>[part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function requestClock(now) {
  const days = new Map();
  return zone => {
    if (!days.has(zone)) days.set(zone, today(zone, now));
    return days.get(zone);
  };
}

export function releaseDate(date, days) {
  if (!isoOK(date)) return '';
  const value = new Date(date + 'T00:00:00Z');
  value.setUTCDate(value.getUTCDate() + Number(days));
  return value.toISOString().slice(0,10);
}

export function isReleased(asset, metadata, now = new Date()) {
  const date = releaseDate(asset.date, metadata.delayDays);
  return Boolean(date && date <= today(metadata.timezone, now));
}

async function readObject(bucket, key) {
  const object = await bucket.get(key);
  return object ? object.json() : null;
}

async function readChunk(bucket,key) {
  const cache=bucketCache(bucket);
  if(cache.chunks.has(key)) {
    const item=cache.chunks.get(key);cache.chunks.delete(key);cache.chunks.set(key,item);
    return item.data;
  }
  const object=await bucket.get(key);
  if(!object)return null;
  const data=await object.json();
  const size=Number(object.size)||JSON.stringify(data).length*2;
  if(size<=1024*1024) {
    while(cache.chunks.size && (cache.chunks.size>=8 || cache.bytes+size>4*1024*1024)) {
      const oldest=cache.chunks.keys().next().value;
      cache.bytes-=cache.chunks.get(oldest).size;cache.chunks.delete(oldest);
    }
    cache.chunks.set(key,{data,size});cache.bytes+=size;
  }
  return data;
}

async function manifests(bucket) {
  let cursor;
  const result = [];
  do {
    const listing = await bucket.list({prefix: STREAMS, limit:1000, ...(cursor ? {cursor} : {})});
    if (result.length + listing.objects.length > 100) throw new Error('Too many gallery streams');
    for (const object of listing.objects) {
      const cache=bucketCache(bucket).manifests;
      const saved=cache.get(object.key);
      const data = object.etag && saved?.etag===object.etag ? saved.data : await readObject(bucket, object.key);
      if(data && object.etag) {
        if(cache.size>=100)cache.delete(cache.keys().next().value);
        cache.set(object.key,{etag:object.etag,data});
      }
      if (data) result.push(data);
    }
    cursor = listing.truncated ? listing.cursor : null;
  } while (cursor);
  return result.sort((a,b)=>a.stream.localeCompare(b.stream));
}

function catalogIdentity(metadata) {
  const oldId=metadata.catalogId||(metadata.mediaType==='video'?'videos':'images');
  return oldId==='videos' ? {id:'kb_videos',name:'KB视频'} :
    {id:oldId,name:metadata.catalogName||(oldId==='effect_videos'?'效果视频':oldId==='kb_videos'?'KB视频':'图片')};
}

function publicInfo(streams, now = new Date(), clock = requestClock(now)) {
  let total = 0;
  const catalogs = new Map();
  for (const stream of streams) {
    const metadata = stream.metadata;
    const cutoff = clock(metadata.timezone);
    const identity=catalogIdentity(metadata), key=identity.id;
    const catalog = catalogs.get(key) || {id:key, name:identity.name, children: new Set()};
    for (const [date, counts] of Object.entries(metadata.releaseCounts || {})) {
      if (date > cutoff) continue;
      for (const [category, count] of Object.entries(counts)) {
        if (Number(count)>0) { total += Number(count); catalog.children.add(category); }
      }
    }
    catalogs.set(key, catalog);
  }
  const cacheKey = 'catalogs-v2|'+(streams.map(stream=>stream.buildId+':'+clock(stream.metadata.timezone)).join('|') || 'empty');
  const order={images:0,kb_videos:1,videos:1,effect_videos:2};
  return {catalogs:[...catalogs.values()].sort((a,b)=>(order[a.id]??9)-(order[b.id]??9)).map(item=>({...item,children:[...item.children].sort()})),
          assignees:[], cacheMeta:{cacheKey, buildId:cacheKey, total, updatedAt:streams.map(s=>s.updatedAt).sort().at(-1)||''}};
}

async function signature(value, secret) {
  const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const bytes=new Uint8Array(await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(value)));
  return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
}

async function authorized(token, env) {
  if (!env.ACCESS_PASSWORD) return true;
  const [expires, mac] = String(token || '').split('.');
  if (!/^\d+$/.test(expires) || Number(expires)<Date.now()) return false;
  return equal(mac, await signature(expires, String(env.CACHE_PUBLISH_SECRET)+':'+env.ACCESS_PASSWORD));
}

async function publish(request, env) {
  const expected=String(env.CACHE_PUBLISH_SECRET||'');
  if (!expected || !(await equal(request.headers.get('x-publish-secret')||'',expected))) return json({ok:false,error:'发布密钥不正确'},401);
  const body=await boundedJson(request,3*1024*1024);
  if (body.action==='hello') return json({ok:true,service:SERVICE,features:['incremental-v1']});
  if (!idOK(body.stream)||!idOK(body.buildId)) throw new Error('Invalid stream/build ID');
  const prefix=`${BUILDS}${body.stream}/${body.buildId}/`;
  const chunkPrefix=`${CHUNKS}${body.stream}/`;
  if (body.action==='status') {
    const current=await readObject(env.GALLERY_CACHE,STREAMS+body.stream+'.json');
    if (current?.buildId===body.buildId) return json({ok:true,buildId:current.buildId});
    // Include staged chunks so an interrupted desktop upload can resume too.
    const chunkHashes=[];let cursor;
    do {
      const page=await env.GALLERY_CACHE.list({prefix:chunkPrefix,limit:1000,...(cursor?{cursor}:{})});
      chunkHashes.push(...page.objects.map(object=>object.key.slice(chunkPrefix.length,-5)));
      cursor=page.truncated?page.cursor:null;
      if(chunkHashes.length>10000) break; // Older chunks may be uploaded again, never omitted.
    } while(cursor);
    return json({ok:true,buildId:current?.buildId||'',chunkHashes});
  }
  if (body.action==='put-chunk') {
    if (!Number.isInteger(body.index)||body.index<1||body.index>2000||!Array.isArray(body.assets)||body.assets.length>500) throw new Error('Invalid chunk');
    const assets=body.assets;
    for (const asset of assets) {
      if (!idOK(asset.id)||!isoOK(asset.date)||!['image','video'].includes(asset.mediaType)) throw new Error('Invalid asset');
      for (const field of ['sourceUrl','viewUrl','videoUrl','previewUrl','downloadUrl']) {
        if (asset[field] && !/^https?:\/\//i.test(asset[field])) throw new Error('Invalid asset URL');
      }
    }
    if(body.chunkHash && !idOK(body.chunkHash)) throw new Error('Invalid chunk hash');
    const key=body.chunkHash?chunkPrefix+body.chunkHash+'.json':prefix+String(body.index).padStart(4,'0')+'.json';
    await env.GALLERY_CACHE.put(key,JSON.stringify({assets}),
                                {httpMetadata:{contentType:'application/json'},customMetadata:{rows:String(assets.length)}});
    return json({ok:true});
  }
  if (body.action!=='finalize') throw new Error('Unknown publish action');
  if (!Number.isInteger(body.chunkCount)||body.chunkCount<0||body.chunkCount>2000) throw new Error('Invalid chunk count');
  const metadata=body.metadata||{};
  if(metadata.catalogId && !['images','videos','kb_videos','effect_videos'].includes(metadata.catalogId)) throw new Error('Invalid gallery catalog');
  if(metadata.catalogName && (typeof metadata.catalogName!=='string'||metadata.catalogName.length>80)) throw new Error('Invalid catalog name');
  if (!['image','video'].includes(metadata.mediaType)||!Number.isInteger(metadata.delayDays)||metadata.delayDays<0||metadata.delayDays>3650) throw new Error('Invalid display rule');
  today(metadata.timezone); // Validate the configured IANA timezone before committing.
  const chunks=[];
  let cursor, rows=0;
  const hashed=Array.isArray(body.chunkHashes);
  if(hashed && (body.chunkHashes.length!==body.chunkCount || body.chunkHashes.some(hash=>!idOK(hash)))) throw new Error('Invalid chunk hashes');
  const wanted=hashed?new Set(body.chunkHashes.map(hash=>chunkPrefix+hash+'.json')):null;
  do {
    const list=await env.GALLERY_CACHE.list({prefix:hashed?chunkPrefix:prefix,limit:1000,include:['customMetadata'],...(cursor?{cursor}:{})});
    chunks.push(...list.objects.filter(object=>!wanted||wanted.has(object.key)));
    cursor=list.truncated?list.cursor:null;
  } while(cursor);
  if(hashed) {
    const found=new Map(chunks.map(chunk=>[chunk.key,chunk]));
    chunks.splice(0,chunks.length,...body.chunkHashes.map(hash=>found.get(chunkPrefix+hash+'.json')).filter(Boolean));
  } else chunks.sort((a,b)=>a.key.localeCompare(b.key));
  if (chunks.length!==body.chunkCount) throw new Error('上传分片未齐全，旧数据保留');
  chunks.forEach((chunk,index)=>{
    if(!hashed && chunk.key!==prefix+String(index+1).padStart(4,'0')+'.json') throw new Error('上传分片不连续');
    rows+=Number(chunk.customMetadata?.rows||0);
  });
  if(rows!==body.totalRows) throw new Error('上传行数不一致，旧数据保留');
  const counted=Object.values(metadata.releaseCounts||{}).reduce((sum,counts)=>sum+Object.values(counts).reduce((a,b)=>a+Number(b),0),0);
  if(counted!==rows) throw new Error('展示统计与行数不一致');
  const manifest={stream:body.stream,buildId:body.buildId,metadata,totalRows:rows,chunks:chunks.map(chunk=>chunk.key),updatedAt:new Date().toISOString()};
  // Only this menu's manifest is swapped; the other image/video streams remain intact.
  await env.GALLERY_CACHE.put(STREAMS+body.stream+'.json',JSON.stringify(manifest));
  return json({ok:true,totalRows:rows,stream:body.stream});
}

export async function listPage(streams, bucket, body, now = new Date(), clock = requestClock(now)) {
  const assets=[];
  let position={stream:0,chunk:0,row:0};
  if(body.cursor) {
    try { position=JSON.parse(atob(body.cursor)); } catch { throw new Error('Invalid page cursor'); }
  }
  if(!['stream','chunk','row'].every(key=>Number.isInteger(position[key])&&position[key]>=0)) throw new Error('Invalid page cursor');
  const limit=Math.max(1,Math.min(500,Number(body.limit)||200));
  // Cache business-date calculations for this request, not the underlying data.
  // Tightened rules still take effect on the next request without a stale cache.
  const releaseContexts = new Map();
  let reads=0;
  while(position.stream<streams.length && assets.length<limit && reads<MAX_CHUNK_READS_PER_PAGE) {
    const stream=streams[position.stream];
    if(position.chunk>=stream.chunks.length){ position={stream:position.stream+1,chunk:0,row:0}; continue; }
    const data=await readChunk(bucket,stream.chunks[position.chunk]);
    reads++;
    if(!data) throw new Error('Gallery chunk missing; retry later');
    const values=data.assets||[];
    let releaseContext = releaseContexts.get(position.stream);
    if (!releaseContext) {
      releaseContext = {today:clock(stream.metadata.timezone), dates:new Map(),catalog:catalogIdentity(stream.metadata)};
      releaseContexts.set(position.stream, releaseContext);
    }
    while(position.row<values.length && assets.length<limit) {
      const asset=values[position.row++];
      if (!releaseContext.dates.has(asset.date)) releaseContext.dates.set(asset.date, releaseDate(asset.date,stream.metadata.delayDays));
      const released = releaseContext.dates.get(asset.date);
      if(released && released<=releaseContext.today) assets.push({...asset,mediaType:stream.metadata.mediaType,
        catalogId:releaseContext.catalog.id,group:releaseContext.catalog.name,releaseDate:released});
    }
    if(position.row>=values.length) position={stream:position.stream,chunk:position.chunk+1,row:0};
  }
  while(position.stream<streams.length && position.chunk>=streams[position.stream].chunks.length) position={stream:position.stream+1,chunk:0,row:0};
  const hasMore=position.stream<streams.length;
  return {assets,hasMore,nextOffset:hasMore?(Number(body.offset)||0)+assets.length:null,nextCursor:hasMore?btoa(JSON.stringify(position)):null};
}

async function gallery(request,env) {
  const body=await boundedJson(request,64*1024);
  if(body.action==='login') {
    if(env.ACCESS_PASSWORD && !(await equal(body.password||'',env.ACCESS_PASSWORD))) return json({ok:false,error:'访问密码不正确'},401);
    const expires=String(Date.now()+86400000);
    const token=env.ACCESS_PASSWORD?expires+'.'+await signature(expires,String(env.CACHE_PUBLISH_SECRET)+':'+env.ACCESS_PASSWORD):'public';
    return json({ok:true,token});
  }
  if(!(await authorized(body.token,env))) return json({ok:false,error:'登录已过期，请重新输入访问密码'},401);
  if(body.action==='stats') return json({ok:true,stats:{},assignees:[]});
  if(body.action==='refreshStatus') return json({ok:true,status:{state:'idle'}});
  const streams=await manifests(env.GALLERY_CACHE);
  const now=new Date();
  const clock=requestClock(now);
  const info=publicInfo(streams,now,clock);
  if(['catalogs','cacheMeta'].includes(body.action)) return json({ok:true,...info});
  if(body.action==='list') return json({ok:true,...info,total:info.cacheMeta.total,...await listPage(streams,env.GALLERY_CACHE,body,now,clock)});
  return json({ok:false,error:'不支持此操作；源表数据请在桌面软件中汇总并发布'},400);
}

export default {
  async fetch(request,env) {
    try {
      const url=new URL(request.url);
      if(url.pathname==='/api/summary-publish' && request.method==='POST') return await publish(request,env);
      if(url.pathname==='/api/gallery' && request.method==='POST') return await gallery(request,env);
      if(url.pathname.startsWith('/api/')||url.pathname.startsWith('/cdn/')||url.pathname.startsWith('/summary/')) return json({ok:false,error:'Not found'},404);
      const asset=await env.ASSETS.fetch(request);
      const headers=new Headers(asset.headers);
      headers.set('X-Content-Type-Options','nosniff');
      headers.set('Referrer-Policy','strict-origin-when-cross-origin');
      headers.set('X-Robots-Tag','noindex, nofollow, noarchive, nosnippet, noimageindex');
      return new Response(asset.body,{status:asset.status,headers});
    } catch(error) { return json({ok:false,error:error.status===413?'请求内容过大':'请求无效，请检查参数'},error.status===413?413:400); }
  },
};
