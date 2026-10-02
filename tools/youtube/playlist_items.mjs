// usage: node playlist_items.mjs PLAYLIST_ID -- prints id|title|length for every video in the signed-in account's playlist (authuser=1)
const { newTab, connect } = await import(process.env.CDP_MJS ?? `file:///Users/davidlee/.claude/plugins/cache/ledgerrocket-plugins/ledgerrocket-workflows/1.72.0/skills/authenticated-browser/scripts/cdp.mjs`);
const [PL] = process.argv.slice(2); const sleep = ms => new Promise(r => setTimeout(r, ms));
const tab = await newTab(`https://www.youtube.com/playlist?list=${PL}&authuser=1`);
const c = connect(tab.webSocketDebuggerUrl); await c.ready; await c.send('Runtime.enable');
await sleep(7000);
for (let i = 0; i < 15; i++) { await c.eval('window.scrollTo(0, document.documentElement.scrollHeight)'); await sleep(1200); }
console.log(await c.eval(`(() => {
  const rows = [...document.querySelectorAll('ytd-playlist-video-renderer')].map(r => {
    const a = r.querySelector('a#video-title'); const id = a ? new URL(a.href).searchParams.get('v') : null;
    const len = r.querySelector('ytd-thumbnail-overlay-time-status-renderer, badge-shape, .yt-badge-shape__text')?.innerText?.trim();
    const meta = r.querySelector('#video-info, #metadata-line')?.innerText?.replace(/\\s+/g,' ').trim();
    return [id, a?.innerText?.trim(), len, meta].join('|'); });
  const head = document.querySelector('yt-dynamic-sizing-formatted-string, #title, yt-formatted-string.ytd-playlist-header-renderer')?.innerText?.trim();
  return ['playlist: ' + head + ' | ' + location.href, ...rows].join('\\n');
})()`));
await fetch(`http://127.0.0.1:9222/json/close/${tab.id}`).catch(()=>{}); process.exit(0);
