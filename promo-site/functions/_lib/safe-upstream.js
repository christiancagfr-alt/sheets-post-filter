/** Google media/cache fetches cannot become an arbitrary URL proxy. */
export function allowedUpstream(value, env = {}) {
  try {
    const url=new URL(value);
    if(url.protocol!=='https:' || url.username || url.password || (url.port && url.port!=='443')) return false;
    const host=url.hostname.toLowerCase();
    if(['drive.google.com','docs.google.com','drive.usercontent.google.com'].includes(host) || host==='googleusercontent.com' || host.endsWith('.googleusercontent.com')) return true;
    const configured=['PUBLIC_JSON_CACHE_URL','Q_GALLERY_PUBLIC_CACHE_URL','DRIVE_CACHE_MANIFEST_URL','PUBLIC_CACHE_BASE','PUBLIC_JSON_CACHE_BASE'];
    return configured.some(key=>{
      try {const trusted=new URL(env[key]);return trusted.protocol==='https:' && trusted.origin===url.origin;}
      catch {return false;}
    });
  } catch {return false;}
}

export async function safeUpstreamFetch(value,env,options={}) {
  let url=String(value);
  for(let hop=0;hop<6;hop++) {
    if(!allowedUpstream(url,env)) throw new Error('上游地址不在已配置的 HTTPS 来源中');
    const response=await fetch(url,{...options,redirect:'manual'});
    if(![301,302,303,307,308].includes(response.status)) return response;
    const location=response.headers.get('location');
    if(!location) throw new Error('无效的上游跳转');
    await response.body?.cancel();
    url=new URL(location,url).href;
  }
  throw new Error('上游跳转次数过多');
}
