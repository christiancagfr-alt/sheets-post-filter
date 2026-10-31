import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {webcrypto,timingSafeEqual} from 'node:crypto';
if(!globalThis.crypto)globalThis.crypto=webcrypto;
if(!crypto.subtle.timingSafeEqual)crypto.subtle.timingSafeEqual=(a,b)=>timingSafeEqual(new Uint8Array(a),new Uint8Array(b));
const moduleFrom=async path=>import('data:text/javascript;base64,'+Buffer.from(await fs.readFile(new URL(path,import.meta.url),'utf8')).toString('base64'));
const {default:worker}=await moduleFrom('../promo-site/gallery-worker.js');
const auth=await moduleFrom('../promo-site/functions/_lib/auth.js');
const upstream=await moduleFrom('../promo-site/functions/_lib/safe-upstream.js');
const env={ACCESS_PASSWORD:'example-password',CACHE_PUBLISH_SECRET:'example-secret',ASSETS:{fetch:async()=>new Response('example')}};

test('cache proxy validates configured origins and every redirect destination',async()=>{
  assert.equal(upstream.allowedUpstream('http://127.0.0.1/'),false);
  assert.equal(upstream.allowedUpstream('https://example.com/private'),false);
  assert.equal(upstream.allowedUpstream('https://cache.example.com/data',{PUBLIC_JSON_CACHE_URL:'https://cache.example.com/index'}),true);
  const original=globalThis.fetch;
  let calls=0;
  try {
    globalThis.fetch=async()=>{calls++;return new Response(null,{status:302,headers:{location:'http://127.0.0.1/private'}});};
    await assert.rejects(upstream.safeUpstreamFetch('https://drive.google.com/uc?id=example',{}));
    assert.equal(calls,1);
  } finally {globalThis.fetch=original;}
});

test('legacy password gates CDN data and image endpoints before any fetch',async()=>{
  for(const path of ['/cdn/promo/manifest.json','/api/drive-image?id=example']) {
    const response=await worker.fetch(new Request('https://gallery.example.com'+path),env);
    assert.equal(response.status,401);
    assert.equal(response.headers.get('Cache-Control'),'no-store');
  }
});

test('legacy sessions have a signed server-side expiry and reject old static cookies',async()=>{
  const header=await auth.authCookieHeader(env);
  const cookie=header.split(';')[0];
  assert.equal(await auth.isAuthenticated(new Request('https://gallery.example.com',{headers:{cookie}}),env),true);
  const expired='1.'+await auth.sign('session|1|'+env.ACCESS_PASSWORD,env);
  assert.equal(await auth.isAuthenticated(new Request('https://gallery.example.com',{headers:{cookie:'q_gallery_auth='+expired}}),env),false);
  const old=await auth.sign(env.ACCESS_PASSWORD,env);
  assert.equal(await auth.isAuthenticated(new Request('https://gallery.example.com',{headers:{cookie:'q_gallery_auth='+old}}),env),false);
});
