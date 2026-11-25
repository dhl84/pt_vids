// usage: node captions.mjs VIDEO_ID OUT.srt
// Saves YouTube's auto captions (English) of a private or unlisted video as SRT, via the
// signed-in Brave debug browser (authuser=1). The timedtext URL needs a per-session token,
// so the script switches the player's caption track on and captures the response the player gets.
import { writeFileSync } from 'node:fs';
const PORT = process.env.CDP_PORT || 9222;
const [VID, OUT] = process.argv.slice(2);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(`https://www.youtube.com/watch?v=${VID}&authuser=${process.env.YT_AUTHUSER ?? 1}`)}`, { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map(); const timedtext = [];
ws.onmessage = m => { const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { const { resolve, reject } = pending.get(msg.id); pending.delete(msg.id); msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result); }
  else if (msg.method === 'Network.responseReceived' && /\/api\/timedtext/.test(msg.params.response.url)) timedtext.push(msg.params.requestId); };
const send = (method, params = {}) => new Promise((resolve, reject) => { const n = ++id; pending.set(n, { resolve, reject }); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async expr => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.text); return r.result.value; };
await send('Network.enable'); await send('Runtime.enable');
let tracks = null;
for (let i = 0; i < 30 && !tracks; i++) { await sleep(1000); tracks = await evaluate(`(() => {
  const meta = window.ytInitialPlayerResponse?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
  if (meta && meta.length) return meta.map(t => ({ languageCode: t.languageCode, kind: t.kind, displayName: t.name?.simpleText || t.name?.runs?.[0]?.text }));
  const p = document.getElementById('movie_player'); if (!p || !p.loadModule) return null;
  try { p.loadModule('captions'); const t = p.getOption('captions', 'tracklist'); return t && t.length ? t : null; } catch (e) { return null; } })()`); }
if (!tracks) { console.log('no caption tracks for', VID, '| title:', await evaluate('document.title')); ws.close(); await fetch(`http://127.0.0.1:${PORT}/json/close/${tab.id}`).catch(() => {}); process.exit(1); }
const track = tracks.find(t => t.languageCode === 'en' && t.kind === 'asr') || tracks.find(t => (t.languageCode || '').startsWith('en')) || tracks[0];
console.log('track:', track.languageCode, track.kind || 'manual', track.displayName || '');
await evaluate(`(() => { const p = document.getElementById('movie_player'); p.loadModule('captions'); p.playVideo(); return true; })()`);
await sleep(2500);
await evaluate(`(() => { const p = document.getElementById('movie_player'); p.setOption('captions', 'track', ${JSON.stringify({ languageCode: track.languageCode, ...(track.kind ? { kind: track.kind } : {}) })}); const b = document.querySelector('.ytp-subtitles-button'); if (b && b.getAttribute('aria-pressed') === 'false') b.click(); return true; })()`);
let body = null;
for (let i = 0; i < 30 && !body; i++) { await sleep(1000); for (const rid of timedtext.splice(0)) { try { const r = await send('Network.getResponseBody', { requestId: rid }); if (r.body && r.body.length > 100) body = r.body; } catch (e) {} } }
await evaluate(`document.getElementById('movie_player').pauseVideo()`).catch(() => {});
if (!body) { console.log('no timedtext response captured for', VID); ws.close(); await fetch(`http://127.0.0.1:${PORT}/json/close/${tab.id}`).catch(() => {}); process.exit(1); }
const tc = ms => { const s = Math.floor(ms / 1000); return `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')},${String(ms % 1000).padStart(3, '0')}`; };
let cues;
if (body.trim().startsWith('{')) { const json = JSON.parse(body); cues = (json.events || []).filter(e => e.segs && e.segs.some(s => s.utf8.trim())).map(e => ({ start: e.tStartMs, dur: e.dDurationMs ?? 2000, text: e.segs.map(s => s.utf8).join('').replace(/\n/g, ' ').trim() })); }
else { cues = [...body.matchAll(/<p t="(\d+)" d="(\d+)"[^>]*>([\s\S]*?)<\/p>/g)].map(m => ({ start: +m[1], dur: +m[2], text: m[3].replace(/<[^>]+>/g, '').replace(/&#39;/g, "'").replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/\n/g, ' ').trim() })).filter(q => q.text); }
writeFileSync(OUT, cues.map((q, i) => `${i + 1}\n${tc(q.start)} --> ${tc(q.start + q.dur)}\n${q.text}\n`).join('\n'));
console.log('wrote', OUT, cues.length, 'cues');
ws.close(); await fetch(`http://127.0.0.1:${PORT}/json/close/${tab.id}`).catch(() => {}); process.exit(0);
