// usage: node upload_private.mjs FILE TITLE PLAYLIST  -- Studio upload: PRIVATE, not made for kids, added to an existing playlist; waits until the upload transfer completes
const { newTab, connect } = await import(process.env.CDP_MJS ?? `file:///Users/davidlee/.claude/plugins/cache/ledgerrocket-plugins/ledgerrocket-workflows/1.72.0/skills/authenticated-browser/scripts/cdp.mjs`);
const [FILE, TITLE, PLAYLIST] = process.argv.slice(2);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const t = await newTab('https://studio.youtube.com/channel/UCMmL2oJrsyYiVlveL1ThoDw/videos/upload?authuser=1');
const c = connect(t.webSocketDebuggerUrl); await c.ready; await c.send('Runtime.enable'); await c.send('DOM.enable');
for (let i = 0; i < 20; i++) { await sleep(1500); if (await c.eval(`document.querySelectorAll('ytcp-video-row').length > 0`)) break; }
const vis = `e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }`;
const click = async (expr, wait = 1200) => { const b = await c.eval(`(() => { const e = ${expr}; if (!e) return null; e.scrollIntoView({block:'center'}); const r = e.getBoundingClientRect(); return {x: r.x + r.width/2, y: r.y + r.height/2}; })()`); if (!b) return null; await c.send('Input.dispatchMouseEvent', {type:'mouseMoved', x: b.x, y: b.y}); await sleep(300); for (const type of ['mousePressed','mouseReleased']) await c.send('Input.dispatchMouseEvent', {type, x: b.x, y: b.y, button: 'left', clickCount: 1}); await sleep(wait); return b; };
const log = (...a) => console.log(new Date().toTimeString().slice(0,8), ...a);
log('create:', !!await click(`[...document.querySelectorAll('ytcp-button, button')].filter(${vis}).find(e => /create/i.test(e.innerText) || /create/i.test(e.getAttribute('aria-label')||''))`, 1500));
log('upload item:', !!await click(`[...document.querySelectorAll('tp-yt-paper-item, [role=menuitem]')].filter(${vis}).find(e => /Upload videos/i.test(e.innerText))`, 3000));
let has = false; for (let i = 0; i < 10; i++) { has = await c.eval(`!!document.querySelector('input[type=file]')`); if (has) break; await sleep(1000); }
if (!has) { log('no file input'); process.exit(1); }
const doc = await c.send('DOM.getDocument', { depth: -1, pierce: true });
const q = await c.send('DOM.querySelector', { nodeId: doc.root.nodeId, selector: 'input[type=file]' });
await c.send('DOM.setFileInputFiles', { nodeId: q.nodeId, files: [FILE] });
for (let i = 0; i < 30; i++) { await sleep(2000); if (await c.eval(`!!document.querySelector('ytcp-uploads-dialog #title-textarea #textbox')`)) break; }
log('details form:', await c.eval(`!!document.querySelector('ytcp-uploads-dialog #title-textarea #textbox')`));
await click(`document.querySelector('ytcp-uploads-dialog #title-textarea #textbox')`, 500);
await c.send('Input.dispatchKeyEvent', {type:'keyDown', key:'a', code:'KeyA', modifiers:4, commands:['selectAll']}); await c.send('Input.dispatchKeyEvent', {type:'keyUp', key:'a', code:'KeyA', modifiers:4});
await c.send('Input.insertText', {text: TITLE}); await sleep(800);
log('title:', await c.eval(`document.querySelector('ytcp-uploads-dialog #title-textarea #textbox')?.innerText`));
await click(`[...document.querySelectorAll('ytcp-uploads-dialog tp-yt-paper-radio-button')].filter(${vis}).find(r => /not made for kids/i.test(r.innerText))`);
log('kids:', await c.eval(`[...document.querySelectorAll('ytcp-uploads-dialog tp-yt-paper-radio-button')].find(r => /not made for kids/i.test(r.innerText))?.getAttribute('aria-checked')`));
await click(`document.querySelector('ytcp-uploads-dialog ytcp-video-metadata-playlists ytcp-text-dropdown-trigger, ytcp-uploads-dialog ytcp-video-metadata-playlists')`, 2000);
const found = await click(`(() => { const d = document.querySelector('ytcp-playlist-dialog'); if (!d) return null; const leaf = [...d.querySelectorAll('*')].filter(${vis}).find(e => e.children.length === 0 && e.innerText && e.innerText.trim() === ${JSON.stringify(PLAYLIST)}); return leaf; })()`, 1000);
log('playlist row clicked:', !!found);
await click(`[...document.querySelectorAll('ytcp-playlist-dialog ytcp-button, ytcp-playlist-dialog button')].filter(${vis}).find(e => /^Done$/i.test(e.innerText.trim()))`);
log('playlist field:', await c.eval(`document.querySelector('ytcp-uploads-dialog ytcp-video-metadata-playlists')?.innerText?.replace(/\\s+/g,' ').trim().slice(0,40)`));
for (let i = 0; i < 3; i++) await click(`[...document.querySelectorAll('ytcp-uploads-dialog ytcp-button, ytcp-uploads-dialog button')].filter(${vis}).find(e => /^Next$/i.test(e.innerText.trim()))`, 1500);
await click(`[...document.querySelectorAll('ytcp-uploads-dialog tp-yt-paper-radio-button')].filter(${vis}).find(r => /^Private/i.test(r.innerText.trim()))`);
log('visibility:', await c.eval(`JSON.stringify([...document.querySelectorAll('ytcp-uploads-dialog tp-yt-paper-radio-button')].filter(${vis}).map(r => [r.innerText.replace(/\\s+/g,' ').trim().slice(0,12), r.getAttribute('aria-checked')]))`));
log('link:', await c.eval(`document.querySelector('ytcp-uploads-dialog a[href*="youtu.be"]')?.href`));
await click(`[...document.querySelectorAll('ytcp-uploads-dialog ytcp-button, ytcp-uploads-dialog button')].filter(${vis}).find(e => /^Save$/i.test(e.innerText.trim()))`, 3000);
// stay attached until the transfer really ends: progress label says complete AND no upload requests for 3 minutes
let idle = 0;
for (let i = 0; i < 360; i++) {
  const s = JSON.parse(await c.eval(`JSON.stringify({ p: (document.querySelector('ytcp-video-upload-progress')?.innerText || document.querySelector('ytcp-uploads-dialog .progress-label')?.innerText || '').replace(/\\s+/g,' ').trim().slice(0,90), reqs3m: performance.getEntriesByType('resource').filter(r => /upload|resumable/i.test(r.name) && performance.now() - r.responseEnd < 180000).length })`));
  if (i % 3 === 0) log('progress:', s.p, 'reqs3m=' + s.reqs3m);
  const done = /upload complete|processing complete|Checks/i.test(s.p) && !/Uploading/i.test(s.p);
  if (done && s.reqs3m === 0 && i > 2) { log('DONE:', s.p); break; }
  if (s.reqs3m === 0 && /Uploading/i.test(s.p)) { idle++; if (idle >= 12) { log('STALLED at', s.p); process.exit(2); } } else idle = 0;
  await sleep(60000);
}
process.exit(0);
