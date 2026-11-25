// usage: node studio_download.mjs VIDEO_ID OUT_DIR -- Studio "Download" of one video (authuser=1) into OUT_DIR via the browser's downloader
const { newTab, connect } = await import(process.env.CDP_MJS ?? `file:///Users/davidlee/.claude/plugins/cache/ledgerrocket-plugins/ledgerrocket-workflows/1.72.0/skills/authenticated-browser/scripts/cdp.mjs`);
const [VID, OUT] = process.argv.slice(2); const sleep = ms => new Promise(r => setTimeout(r, ms));
const vis = `e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }`;
const t = await newTab(`https://studio.youtube.com/video/${VID}/edit?authuser=${process.env.YT_AUTHUSER ?? 1}`);
const c = connect(t.webSocketDebuggerUrl); await c.ready; await c.send('Runtime.enable'); await c.send('Page.enable');
await c.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: OUT, eventsEnabled: true }).catch(e => console.log('setDownloadBehavior:', e.message));
await sleep(8000);
console.log('href:', await c.eval('location.href'));
const click = async (expr, wait = 2000) => { const b = await c.eval(`(() => { const e = ${expr}; if (!e) return null; e.scrollIntoView({block:'center'}); const r = e.getBoundingClientRect(); return {x: r.x + r.width/2, y: r.y + r.height/2}; })()`); if (!b) return null; await c.send('Input.dispatchMouseEvent', {type:'mouseMoved', x: b.x, y: b.y}); await sleep(300); for (const type of ['mousePressed','mouseReleased']) await c.send('Input.dispatchMouseEvent', {type, x: b.x, y: b.y, button: 'left', clickCount: 1}); await sleep(wait); return b; };
console.log("menu:", !!await click(`document.querySelector("ytcp-icon-button#overflow-menu-button")`, 2000));
console.log('items:', await c.eval(`JSON.stringify([...document.querySelectorAll('tp-yt-paper-item, [role=menuitem]')].filter(${vis}).map(e => e.innerText.replace(/\\s+/g,' ').trim()).slice(0,12))`));
console.log('download:', !!await click(`[...document.querySelectorAll('tp-yt-paper-item, [role=menuitem]')].filter(${vis}).find(e => /^Download/i.test(e.innerText.trim()))`, 3000));
await sleep(5000);
console.log('dialog:', await c.eval(`(document.querySelector('ytcp-dialog, tp-yt-paper-dialog')?.innerText||'').replace(/\\s+/g,' ').slice(0,200)`));
await fetch(`http://127.0.0.1:9222/json/close/${t.id}`).catch(()=>{}); process.exit(0);
