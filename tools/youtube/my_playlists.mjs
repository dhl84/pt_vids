// usage: node my_playlists.mjs -- lists ALL the signed-in account's playlists (private included) from the Library feed
const { newTab, connect } = await import(process.env.CDP_MJS ?? `file:///Users/davidlee/.claude/plugins/cache/ledgerrocket-plugins/ledgerrocket-workflows/1.72.0/skills/authenticated-browser/scripts/cdp.mjs`);
const tab = await newTab('https://www.youtube.com/feed/playlists?authuser=1');
const c = connect(tab.webSocketDebuggerUrl); await c.ready; await c.send('Runtime.enable');
await new Promise(r => setTimeout(r, 7000));
console.log('href:', await c.eval('location.href'));
console.log(await c.eval(`(() => {
  const found = [];
  const walk = (o) => { if (!o || typeof o !== 'object') return;
    if (o.playlistId && (o.title?.simpleText || o.title?.runs)) found.push([o.playlistId, o.title.simpleText || o.title.runs.map(r=>r.text).join('')]);
    if (o.lockupViewModel) { const l=o.lockupViewModel; found.push([l.contentId, l.metadata?.lockupMetadataViewModel?.title?.content]); }
    for (const k in o) walk(o[k]); };
  walk(window.ytInitialData);
  const dom = [...document.querySelectorAll('a[href*="list="]')].map(a => [new URL(a.href).searchParams.get('list'), a.innerText.trim()]).filter(x => x[1]);
  return JSON.stringify({ data: [...new Map(found.map(f=>[f[0],f[1]])).entries()], dom: [...new Map(dom.map(f=>[f[0],f[1]])).entries()] }, null, 1);
})()`));
await fetch(`http://127.0.0.1:9222/json/close/${tab.id}`).catch(()=>{}); process.exit(0);
