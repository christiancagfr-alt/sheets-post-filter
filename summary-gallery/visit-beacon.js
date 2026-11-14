/* One anonymous record per foreground visit; no heartbeat or R2 writes. */
(() => {
  if (navigator.doNotTrack === '1' || navigator.globalPrivacyControl) return;
  const randomId = () => crypto.randomUUID().replaceAll('-', '');
  let visit = null;
  function begin() {
    if (document.visibilityState !== 'visible' || visit) return;
    const start = Date.now(), day = new Date(start).toISOString().slice(0,10);
    let visitor = randomId();
    try {
      const stored = JSON.parse(localStorage.getItem('gallery-visitor-day') || 'null');
      if (stored?.day === day && /^[a-f0-9]{32}$/.test(stored.id)) visitor = stored.id;
      else localStorage.setItem('gallery-visitor-day', JSON.stringify({day,id:visitor}));
    } catch {}
    visit = {visitor,session:randomId(),started:start/1000,clock:performance.now()};
  }
  function finish() {
    if (!visit) return;
    const value = visit; visit = null;
    const seconds = Math.max(0, Math.min(28800,(performance.now()-value.clock)/1000));
    let token = '';
    try { token = sessionStorage.getItem('summary-gallery-token-v1') || ''; } catch {}
    const body = JSON.stringify({visitor:value.visitor,session:value.session,started:value.started,seconds,token});
    navigator.sendBeacon('/api/visit', new Blob([body],{type:'text/plain;charset=UTF-8'}));
  }
  document.addEventListener('visibilitychange', () => document.visibilityState === 'hidden' ? finish() : begin());
  window.addEventListener('pagehide', finish);
  window.addEventListener('pageshow', begin);
  begin();
})();
