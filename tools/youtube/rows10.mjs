const { newTab, connect } = await import(process.env.CDP_MJS ?? `file:///Users/davidlee/.claude/plugins/cache/ledgerrocket-plugins/ledgerrocket-workflows/1.72.0/skills/authenticated-browser/scripts/cdp.mjs`);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const t = await newTab('https://studio.youtube.com/channel/UCMmL2oJrsyYiVlveL1ThoDw/videos/upload?authuser=1');
const c = connect(t.webSocketDebuggerUrl); await c.ready; await c.send('Runtime.enable');
for (let i = 0; i < 20; i++) { await sleep(1500); if (await c.eval(`document.querySelectorAll('ytcp-video-row').length > 0`)) break; }
console.log(await c.eval(`JSON.stringify([...document.querySelectorAll('ytcp-video-row')].slice(0,8).map(r => r.innerText.replace(/\\s+/g,' ').slice(0,90)), null, 1)`));
await fetch(`http://127.0.0.1:9222/json/close/${t.id}`).catch(()=>{}); process.exit(0);
