// usage: node set_private_when_ready.mjs TITLE1 TITLE2 ... -- polls the content list; when a row leaves "Checking", sets its visibility to Private from the row dropdown
const { newTab, connect } = await import(process.env.CDP_MJS ?? `file:///Users/davidlee/.claude/plugins/cache/ledgerrocket-plugins/ledgerrocket-workflows/1.72.0/skills/authenticated-browser/scripts/cdp.mjs`);
const TITLES = process.argv.slice(2); const sleep = ms => new Promise(r => setTimeout(r, ms));
const vis = `e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }`;
const pending = new Set(TITLES);
for (let i = 0; i < 100 && pending.size; i++) {
  const t = await newTab('https://studio.youtube.com/channel/UCMmL2oJrsyYiVlveL1ThoDw/videos/upload?authuser=1');
  const c = connect(t.webSocketDebuggerUrl); await c.ready; await c.send('Runtime.enable');
  for (let k = 0; k < 20; k++) { await sleep(1500); if (await c.eval(`document.querySelectorAll('ytcp-video-row').length > 0`)) break; }
  const click = async (expr, wait = 1200) => { const b = await c.eval(`(() => { const e = ${expr}; if (!e) return null; e.scrollIntoView({block:'center'}); const r = e.getBoundingClientRect(); return {x: r.x + r.width/2, y: r.y + r.height/2}; })()`); if (!b) return null; await c.send('Input.dispatchMouseEvent', {type:'mouseMoved', x: b.x, y: b.y}); await sleep(300); for (const type of ['mousePressed','mouseReleased']) await c.send('Input.dispatchMouseEvent', {type, x: b.x, y: b.y, button: 'left', clickCount: 1}); await sleep(wait); return b; };
  for (const TITLE of [...pending]) {
    const row = `[...document.querySelectorAll('ytcp-video-row')].find(r => (r.querySelector('#video-title')?.innerText||'').includes(${JSON.stringify(TITLE)}))`;
    const txt = await c.eval(`(() => { const r = ${row}; return r ? r.innerText.replace(/\\s+/g,' ').slice(0,120) : 'none'; })()`);
    console.log(new Date().toTimeString().slice(0,8), TITLE, '|', txt);
    if (/Private/.test(txt) && !/Draft/.test(txt)) { pending.delete(TITLE); continue; }
    if (/Checking|Checks still|Processing|Uploading/i.test(txt)) continue;
    // open the row's visibility dropdown
    await click(`(() => { const r = ${row}; return r && [...r.querySelectorAll('ytcp-video-visibility-select, #visibility-select, ytcp-dropdown-trigger, [class*=visibility]')].filter(${vis})[0]; })()`, 1500);
    console.log('  options:', await c.eval(`JSON.stringify([...document.querySelectorAll('tp-yt-paper-radio-button, tp-yt-paper-item, [role=option]')].filter(${vis}).map(e => e.innerText.replace(/\\s+/g,' ').trim().slice(0,20)).filter(Boolean).slice(0,8))`));
    await click(`[...document.querySelectorAll('tp-yt-paper-radio-button, tp-yt-paper-item, [role=option]')].filter(${vis}).find(e => /^Private/i.test(e.innerText.trim()))`);
    await click(`[...document.querySelectorAll('ytcp-button, button')].filter(${vis}).find(e => /^(Save|Done|Publish)$/i.test(e.innerText.trim()))`, 3000);
    console.log('  after:', await c.eval(`(() => { const r = ${row}; return r ? r.innerText.replace(/\\s+/g,' ').slice(0,120) : 'none'; })()`));
  }
  c.close(); await fetch(`http://127.0.0.1:9222/json/close/${t.id}`).catch(()=>{});
  if (pending.size) await sleep(300000);
}
console.log('finished; still pending:', [...pending]);
process.exit(0);
