'use strict';
/* Misty console. Vanilla JS, one file. Talks to server.py (same origin). */

// ---------- helpers ----------
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const uid = () => Math.random().toString(36).slice(2, 9);
const num = (v, d = 0) => (Number.isFinite(+v) ? +v : d);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== 'list') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  el.append(...kids.flat().filter(k => k != null && k !== false));
  return el;
}

// Case-insensitive property lookup (the robot is inconsistent about casing).
function pick(o, ...keys) {
  if (!o || typeof o !== 'object') return undefined;
  const m = {};
  for (const k in o) m[k.toLowerCase()] = o[k];
  for (const k of keys) if (m[k.toLowerCase()] !== undefined) return m[k.toLowerCase()];
  return undefined;
}

function throttled(fn, ms) {
  let last = 0, to = 0, args = [];
  const run = () => { to = 0; last = performance.now(); fn(...args); };
  const f = (...a) => { args = a; if (to) return; const w = ms - (performance.now() - last); w <= 0 ? run() : (to = setTimeout(run, w)); };
  f.now = (...a) => { if (a.length) args = a; clearTimeout(to); run(); };
  return f;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const ICON = {
  stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
  play: '<path d="M8 5v14l11-7z" fill="currentColor"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  up: '<path d="M6 15l6-6 6 6"/>', down: '<path d="M6 9l6 6 6-6"/>',
  trash: '<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="M4 20l1-4L16 5l3 3L8 19z"/>', reset: '<path d="M4 12a8 8 0 1 0 3-6.2M4 4v4h4"/>',
};
const icon = n => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${ICON[n]}</svg>`;
const iconEl = n => { const s = h('span', { 'data-ic': n }); s.innerHTML = icon(n); return s; };

// ---------- state ----------
const S = { ip: '', behaviour: null, behaviours: [], head: { pitch: 0, yaw: 0, roll: 0 }, arms: { l: 90, r: 90 }, images: [], clips: [], restOk: null };
let cfg = {};
const FEEL_DEFAULT = { maxDrive: 25, maxTurn: 30, headDur: 0.5 }; // matches approach_face.py MAX_SPEED / MAX_TURN
const feel = () => cfg.feel || FEEL_DEFAULT;
const locked = () => !!S.behaviour;
const BEH = {
  main: ['Approach and greet', 'Scans for a face, drives up to ~2 m, waves, compliments, asks to be petted.'],
  idle_mode: ['Idle scan', 'Looks around and greets faces.'],
  follow_face: ['Follow face', 'Tracks a face with the head and describes what it sees.'],
  approach_face: ['Approach only', 'Drives to and holds distance from a face.'],
};
const behLabel = n => (BEH[n] ? BEH[n][0] : n.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase()));

// ---------- network ----------
async function api(url, method = 'GET', body) {
  const isRobot = url.startsWith('/misty/');
  let r;
  try {
    r = await fetch(url, { method, headers: body !== undefined ? { 'content-type': 'application/json' } : undefined, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch (e) {
    if (isRobot) setRest(false);
    throw new Error('Network error: ' + e.message);
  }
  let j = null;
  try { j = await r.json(); } catch { /* empty body */ }
  if (isRobot) setRest(r.status !== 502);
  if (!r.ok) throw new Error((j && (j.error || j.message)) || `${r.status} ${r.statusText}`);
  return j;
}
const post = (p, b) => api('/misty/' + p, 'POST', b ?? {});

let toastT;
function toast(msg, err) {
  const t = $('#toast');
  t.textContent = msg; t.dataset.err = err ? 1 : 0; t.classList.add('on');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('on'), err ? 5000 : 1800);
}
const fail = e => toast(e.message || String(e), true);

async function act(btn, fn) {
  if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
  try { return await fn(); } catch (e) { fail(e); } finally { if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); } }
}

// ---------- connection chips ----------
function chip(id, s, tip) { const c = $('#chip-' + id); c.dataset.s = s; if (tip) c.title = tip; }
function setRest(ok) {
  const was = S.restOk; S.restOk = ok;
  chip('rest', ok ? 'ok' : 'bad', ok ? 'Robot REST reachable' : 'Robot REST unreachable');
  if (ok && was === false) { loadAssets(); camKick(); }
}
const setWs = open => { sens.open = open; chip('ws', open ? 'ok' : 'bad', open ? 'Sensor socket open' : 'Sensor socket closed, retrying'); renderSensors(); };

// ---------- config ----------
const saveCfg = debounce(() => api('/api/config', 'PUT', cfg).catch(e => toast('Could not save settings: ' + e.message, true)), 600);

function defaultMacros() {
  const mk = (name, hotkey, color, steps) => ({ id: uid(), name, hotkey, color, steps });
  return [
    mk('Wave hello', 'h', '#3ddc97', [
      { type: 'eyes', name: 'e_Joy.jpg' }, { type: 'say', text: 'Hello there!' },
      { type: 'arms', left: 90, right: -29 }, { type: 'wait', seconds: 0.5 },
      { type: 'arms', left: 90, right: 10 }, { type: 'wait', seconds: 0.4 },
      { type: 'arms', left: 90, right: -29 }, { type: 'wait', seconds: 0.4 },
      { type: 'arms', left: 90, right: 90 }, { type: 'eyes', name: 'e_DefaultContent.jpg' },
    ]),
    mk('Happy', 'j', '#f2a93b', [
      { type: 'led', color: '#ffd23f' }, { type: 'eyes', name: 'e_Joy.jpg' },
      { type: 'head', pitch: -15, yaw: 0, roll: 10 }, { type: 'say', text: 'I am so happy to see you!' },
      { type: 'arms', left: -29, right: -29 }, { type: 'wait', seconds: 1.5 },
      { type: 'arms', left: 90, right: 90 }, { type: 'head', pitch: 0, yaw: 0, roll: 0 },
    ]),
    mk('Sleepy', 'k', '#5b8cff', [
      { type: 'led', color: '#1b2a6b' }, { type: 'eyes', name: 'e_Sleepy.jpg' },
      { type: 'head', pitch: 22, yaw: 0, roll: -8 }, { type: 'arms', left: 90, right: 90 },
      { type: 'say', text: 'I am getting sleepy.' }, { type: 'wait', seconds: 2 },
      { type: 'eyes', name: 'e_DefaultContent.jpg' }, { type: 'head', pitch: 0, yaw: 0, roll: 0 },
    ]),
  ];
}

async function loadCfg() {
  try { cfg = (await api('/api/config')) || {}; } catch (e) { cfg = {}; fail(e); }
  if (typeof cfg !== 'object' || Array.isArray(cfg)) cfg = {};
  cfg.feel = { ...FEEL_DEFAULT, ...(cfg.feel || {}) };
  cfg.phrases = Array.isArray(cfg.phrases) ? cfg.phrases : [];
  let seeded = false;
  if (!Array.isArray(cfg.macros)) { cfg.macros = defaultMacros(); seeded = true; }
  if (seeded) saveCfg();
  initFeel(); renderPhrases(); renderMacros();
}

// ---------- STOP ----------
function stopAll() {
  releaseAll();
  if (run) run.cancel();
  Promise.allSettled([post('drive/stop'), api('/api/behaviour/stop', 'POST', {})]).then(rs => {
    const f = rs.find(r => r.status === 'rejected');
    toast(f ? 'Stop: ' + f.reason.message : 'Stopped', !!f);
  });
}

// ---------- drive ----------
const drive = { keys: new Set(), inflight: false, moving: false, backoff: 0, last: '' };
const stick = { x: 0, y: 0, id: null };
const KEYMAP = { KeyW: [1, 0], ArrowUp: [1, 0], KeyS: [-1, 0], ArrowDown: [-1, 0], KeyA: [0, 1], ArrowLeft: [0, 1], KeyD: [0, -1], ArrowRight: [0, -1] };

function driveTick() {
  let lin = 0, ang = 0;
  if (!locked()) {
    let l = stick.y, a = -stick.x; // misty: positive angular = turn left
    for (const k of drive.keys) { l += KEYMAP[k][0]; a += KEYMAP[k][1]; }
    lin = Math.round(clamp(l, -1, 1) * feel().maxDrive);
    ang = Math.round(clamp(a, -1, 1) * feel().maxTurn);
  }
  const txt = `${lin} / ${ang}`;
  if (txt !== drive.last) { drive.last = txt; $('#drive-read').textContent = txt; }
  if (lin || ang) {
    drive.moving = true;
    if (drive.inflight || performance.now() < drive.backoff) return; // never queue
    drive.inflight = true;
    // TimeMs 400 is a deliberate dead-man: the robot stops itself if we stop talking.
    post('drive/time', { LinearVelocity: lin, AngularVelocity: ang, TimeMs: 400 })
      .catch(e => { drive.backoff = performance.now() + 1500; fail(e); })
      .finally(() => { drive.inflight = false; });
  } else if (drive.moving) {
    drive.moving = false;
    post('drive/stop').catch(fail);
  }
}

function releaseAll() {
  drive.keys.clear(); stick.x = stick.y = 0; stick.id = null;
  $('#nub').style.transform = ''; $('#stick').classList.remove('drag');
  driveTick();
}

function bindStick() {
  const el = $('#stick'), nub = $('#nub');
  const curve = v => Math.sign(v) * Math.abs(v) ** 1.5; // finer control near the centre
  const move = e => {
    const r = el.getBoundingClientRect(), R = r.width / 2 - nub.offsetWidth / 2;
    let dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
    const m = Math.hypot(dx, dy);
    if (m > R) { dx *= R / m; dy *= R / m; }
    nub.style.transform = `translate(${dx}px, ${dy}px)`;
    const x = dx / R, y = -dy / R;
    const dead = Math.hypot(x, y) < 0.12;
    stick.x = dead ? 0 : curve(x); stick.y = dead ? 0 : curve(y);
    driveTick();
  };
  const end = e => {
    if (e.pointerId !== stick.id) return;
    stick.id = null; stick.x = stick.y = 0; nub.style.transform = ''; el.classList.remove('drag'); driveTick();
  };
  el.addEventListener('pointerdown', e => {
    if (locked() || stick.id != null) return; // one finger only
    stick.id = e.pointerId; el.setPointerCapture(e.pointerId); el.classList.add('drag'); move(e);
  });
  el.addEventListener('pointermove', e => { if (e.pointerId === stick.id) move(e); });
  el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end); el.addEventListener('lostpointercapture', end);
}

// ---------- head ----------
const HR = { pitch: [-40, 26], yaw: [-81, 81], roll: [-40, 40] };
const YAW_SIGN = -1; // misty: positive yaw = head turns left, so "right" on pad/image is negative
const headSend = throttled(() => {
  const H = S.head;
  post('head', { Pitch: H.pitch, Yaw: H.yaw, Roll: H.roll, Duration: feel().headDur, Units: 'degrees' }).catch(fail);
}, 125);

function setHead(p, final) {
  for (const k of ['pitch', 'yaw', 'roll']) if (p[k] != null) S.head[k] = clamp(num(p[k]), ...HR[k]);
  renderHead();
  final ? headSend.now() : headSend();
}
function renderHead() {
  const { pitch, yaw, roll } = S.head, pad = $('#pad');
  const fx = (YAW_SIGN * yaw / 81 + 1) / 2, fy = (pitch - HR.pitch[0]) / (HR.pitch[1] - HR.pitch[0]);
  $('#pad-dot').style.transform = `translate(${fx * pad.clientWidth}px, ${fy * pad.clientHeight}px)`;
  $('#roll').value = roll; $('#roll-out').textContent = Math.round(roll) + '°';
  $('#head-read').textContent = `yaw ${Math.round(yaw)}° pitch ${Math.round(pitch)}°`;
}
function bindHead() {
  const pad = $('#pad'); let id = null;
  const at = (e, final) => {
    const r = pad.getBoundingClientRect();
    const fx = clamp((e.clientX - r.left) / r.width, 0, 1), fy = clamp((e.clientY - r.top) / r.height, 0, 1);
    setHead({ yaw: YAW_SIGN * (fx * 2 - 1) * 81, pitch: HR.pitch[0] + fy * (HR.pitch[1] - HR.pitch[0]) }, final);
  };
  pad.addEventListener('pointerdown', e => { if (id != null) return; id = e.pointerId; pad.setPointerCapture(id); pad.classList.add('drag'); at(e); });
  pad.addEventListener('pointermove', e => { if (e.pointerId === id) at(e); });
  const end = e => { if (e.pointerId !== id) return; id = null; pad.classList.remove('drag'); if (e.type === 'pointerup') at(e, true); };
  pad.addEventListener('pointerup', end); pad.addEventListener('pointercancel', end);
  pad.addEventListener('keydown', e => {
    const st = e.shiftKey ? 10 : 3, d = { ArrowLeft: [-YAW_SIGN * st, 0], ArrowRight: [YAW_SIGN * st, 0], ArrowUp: [0, -st], ArrowDown: [0, st] }[e.key];
    if (!d) return;
    e.preventDefault(); e.stopPropagation(); // keep arrows from driving the robot
    setHead({ yaw: S.head.yaw + d[0], pitch: S.head.pitch + d[1] });
  });
  $('#roll').addEventListener('input', e => setHead({ roll: e.target.value }));
  $('#roll').addEventListener('change', () => headSend.now());
  $('#head-centre').addEventListener('click', () => setHead({ pitch: 0, yaw: 0, roll: 0 }, true));
  new ResizeObserver(renderHead).observe(pad);
}

// ---------- arms / expression ----------
const armsSend = throttled(() => post('arms/set', { LeftArmPosition: S.arms.l, RightArmPosition: S.arms.r, LeftArmVelocity: 60, RightArmVelocity: 60, Units: 'degrees' }).catch(fail), 125);
function setArms(l, r, final = false) { // final: true = send now, false = throttled, null = UI only
  if (l != null) S.arms.l = clamp(num(l, 90), -29, 90);
  if (r != null) S.arms.r = clamp(num(r, 90), -29, 90);
  $('#arm-l').value = S.arms.l; $('#arm-r').value = S.arms.r;
  $('#arm-l-out').textContent = S.arms.l + '°'; $('#arm-r-out').textContent = S.arms.r + '°';
  if (final === true) armsSend.now(); else if (final === false) armsSend();
}
const hex2rgb = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16) || 0);
const sendLed = color => { const [r, g, b] = hex2rgb(color); return post('led', { Red: r, Green: g, Blue: b }); };
const ledSend = throttled(c => sendLed(c).catch(fail), 150);
const volSend = throttled(v => post('audio/volume', { Volume: +v }).catch(fail), 200);
const showEyes = name => post('images/display', { FileName: name, Alpha: 1 });
const speakRaw = text => post('tts/speak', { Text: text, Flush: true });

function bindExpression() {
  $('#arm-l').addEventListener('input', e => setArms(e.target.value, null, false));
  $('#arm-r').addEventListener('input', e => setArms(null, e.target.value, false));
  for (const id of ['#arm-l', '#arm-r']) $(id).addEventListener('change', () => armsSend.now());
  $('#wave').addEventListener('click', () => runMacro({ id: 'wave', steps: [
    { type: 'arms', left: 90, right: -29 }, { type: 'wait', seconds: 0.45 },
    { type: 'arms', left: 90, right: 12 }, { type: 'wait', seconds: 0.4 },
    { type: 'arms', left: 90, right: -29 }, { type: 'wait', seconds: 0.4 },
    { type: 'arms', left: 90, right: 12 }, { type: 'wait', seconds: 0.4 },
    { type: 'arms', left: 90, right: 90 }] }));
  $('#led').addEventListener('input', e => { $('#led-out').textContent = e.target.value; ledSend(e.target.value); });
  $('#led').addEventListener('change', e => ledSend.now(e.target.value));
  $('#eyes').addEventListener('change', e => { if (e.target.value) showEyes(e.target.value).catch(fail); });
  $('#vol').addEventListener('input', e => { $('#vol-out').textContent = e.target.value; volSend(e.target.value); });
  $('#vol').addEventListener('change', e => volSend.now(e.target.value));
}

const listNames = j => ((j && j.result) || []).map(x => (typeof x === 'string' ? x : pick(x, 'name', 'fileName'))).filter(Boolean).sort((a, b) => a.localeCompare(b));
function fillSelect(sel, names, none) {
  const cur = sel.value;
  sel.replaceChildren(h('option', { value: '' }, names.length ? none : 'Nothing found'), ...names.map(n => h('option', { value: n }, n)));
  sel.disabled = !names.length; sel.value = names.includes(cur) ? cur : '';
}
async function loadAssets() {
  const [im, au] = await Promise.allSettled([api('/misty/images/list'), api('/misty/audio/list')]);
  if (im.status === 'fulfilled') S.images = listNames(im.value);
  if (au.status === 'fulfilled') S.clips = listNames(au.value);
  fillSelect($('#eyes'), S.images, 'Choose an image');
  fillSelect($('#clip'), S.clips, 'Choose a clip');
  $('#clip-btn').disabled = !S.clips.length;
  $('#dl-images').replaceChildren(...S.images.map(n => h('option', { value: n })));
  $('#dl-audio').replaceChildren(...S.clips.map(n => h('option', { value: n })));
  if (im.status === 'rejected' && !S.assetErr) { S.assetErr = true; toast('Could not load images or audio: ' + im.reason.message, true); }
  if (im.status === 'fulfilled') S.assetErr = false;
}

// ---------- speak ----------
function renderPhrases() {
  $('#phrases').replaceChildren(...cfg.phrases.map(p => h('li', {}, h('button', { type: 'button', title: 'Speak again: ' + p, onclick: e => act(e.currentTarget, () => speakRaw(p)) }, p))));
}
function bindSpeak() {
  $('#speak-form').addEventListener('submit', e => {
    e.preventDefault();
    const inp = $('#say'), text = inp.value.trim();
    if (!text) return;
    act($('#say-btn'), async () => {
      await speakRaw(text);
      cfg.phrases = [text, ...cfg.phrases.filter(p => p !== text)].slice(0, 10);
      saveCfg(); renderPhrases(); inp.value = '';
    });
  });
  $('#clip-btn').addEventListener('click', e => { const n = $('#clip').value; if (n) act(e.currentTarget, () => post('audio/play', { FileName: n })); else toast('Choose a clip first'); });
}

// ---------- camera ----------
const cam = { on: true, busy: false, timer: 0, url: '', blob: null };
function camMsg(t, tag, s) { $('#cam-msg').textContent = t; const g = $('#cam-tag'); g.textContent = tag; g.dataset.s = s; }
async function camFrame() {
  if (!cam.on || document.hidden || cam.busy) return;
  cam.busy = true;
  const t0 = performance.now(), ac = new AbortController(), to = setTimeout(() => ac.abort(), 5000);
  let delay = 500;
  try {
    const r = await fetch(`/misty/cameras/rgb?base64=false&width=640&height=480&_=${Date.now()}`, { signal: ac.signal });
    setRest(r.status !== 502);
    if (!r.ok) {
      let m = ''; try { m = (await r.json()).error; } catch { /* not json */ }
      throw new Error(m || `Camera ${r.status}`);
    }
    const blob = await r.blob();
    if (!blob.type.startsWith('image')) throw new Error('Camera returned no image');
    const u = URL.createObjectURL(blob), old = cam.url;
    $('#cam-img').src = u; cam.url = u; cam.blob = blob;
    if (old) URL.revokeObjectURL(old);
    camMsg('', 'LIVE', 'live'); $('#cam-snap').disabled = false;
  } catch (e) {
    delay = 1500;
    if (e.name === 'AbortError') camMsg('Camera timed out', 'NO SIGNAL', 'bad');
    else { camMsg(e.message, 'NO SIGNAL', 'bad'); if (e instanceof TypeError) setRest(false); }
  } finally {
    clearTimeout(to); cam.busy = false;
    cam.timer = setTimeout(camFrame, Math.max(0, delay - (performance.now() - t0)));
  }
}
function camKick() { clearTimeout(cam.timer); camFrame(); }

function bindCamera() {
  $('#cam-toggle').addEventListener('click', () => {
    cam.on = !cam.on;
    $('#cam-toggle').setAttribute('aria-pressed', cam.on);
    $('#cam-toggle').firstElementChild.innerHTML = icon(cam.on ? 'pause' : 'play');
    $('#cam-toggle-l').textContent = cam.on ? 'Pause' : 'Resume';
    if (cam.on) camKick(); else camMsg('', 'PAUSED', 'wait');
  });
  $('#cam-snap').addEventListener('click', () => {
    if (!cam.blob) return;
    const ext = (cam.blob.type.split('/')[1] || 'jpg').replace('jpeg', 'jpg').replace(/\+.*/, '');
    const a = h('a', { href: cam.url, download: `misty-${new Date().toISOString().replace(/[:.]/g, '-')}.${ext}` });
    document.body.append(a); a.click(); a.remove();
  });
  $('#cam-view').addEventListener('click', e => {
    if (!cam.blob) return;
    const r = $('#cam-view').getBoundingClientRect(), fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
    const ret = $('#cam-ret');
    ret.hidden = true; ret.style.left = fx * 100 + '%'; ret.style.top = fy * 100 + '%'; void ret.offsetWidth; ret.hidden = false;
    // ~60 deg horizontal, ~45 deg vertical field of view, relative to the last commanded pose.
    setHead({ yaw: S.head.yaw + YAW_SIGN * (fx - 0.5) * 60, pitch: S.head.pitch + (fy - 0.5) * 45 }, true);
  });
  $('#desc-btn').addEventListener('click', e => {
    const out = $('#desc-out');
    act(e.currentTarget, async () => {
      out.hidden = false; out.dataset.s = 'loading'; out.textContent = 'Looking';
      try {
        const j = await api('/api/describe', 'POST', {});
        out.dataset.s = ''; out.textContent = j.text || 'No description returned.';
        if ($('#desc-say').checked && j.text) await speakRaw(j.text);
      } catch (err) { out.dataset.s = 'err'; out.textContent = err.message; throw err; }
    });
  });
}

// ---------- sensors (direct websocket to the robot) ----------
const sens = { ws: null, open: false, tries: 0, timer: 0, tof: {}, bump: {}, touch: {}, face: null, act: {}, raf: 0 };
const SUBS = [['TimeOfFlight', 250], ['TouchSensor', 100], ['BumpSensor', 100], ['BatteryCharge', 5000], ['FaceRecognition', 200], ['ActuatorPosition', 250]];
const TOF = { left: 'l', frontleft: 'l', center: 'c', frontcenter: 'c', right: 'r', frontright: 'r', back: 'b' };
const BUMP = { bfl: 'fl', frontleft: 'fl', bfr: 'fr', frontright: 'fr', brl: 'bl', backleft: 'bl', brr: 'br', backright: 'br' };
const norm = v => String(v ?? '').toLowerCase().replace(/[^a-z]/g, '');

function wsConnect() {
  clearTimeout(sens.timer);
  if (!S.ip) return;
  let ws;
  try { ws = new WebSocket(`ws://${S.ip}/pubsub`); } catch { return wsRetry(); }
  sens.ws = ws;
  ws.onopen = () => {
    sens.tries = 0; setWs(true);
    for (const [Type, ms] of SUBS) ws.send(JSON.stringify({ Operation: 'subscribe', Type, DebounceMs: ms, EventName: 'ui-' + Type, ReturnProperty: null }));
  };
  ws.onmessage = e => { try { onSensor(JSON.parse(e.data)); } catch { /* ignore junk */ } };
  ws.onclose = () => { if (sens.ws !== ws) return; sens.ws = null; sens.tof = {}; setWs(false); wsRetry(); };
  ws.onerror = () => {};
}
function wsRetry() { sens.tries++; clearTimeout(sens.timer); sens.timer = setTimeout(wsConnect, Math.min(15000, 1000 * 2 ** Math.min(sens.tries, 4))); }

function onSensor(d) {
  const name = pick(d, 'eventName'), m = pick(d, 'message');
  if (typeof name !== 'string' || !m || typeof m !== 'object') return;
  const now = Date.now();
  switch (name.replace(/^ui-/, '')) {
    case 'TimeOfFlight': {
      const k = TOF[norm(pick(m, 'sensorPosition'))], dist = pick(m, 'distanceInMeters'), st = pick(m, 'status');
      if (k) sens.tof[k] = (st === 0 || st == null) && typeof dist === 'number' ? dist : null;
      break;
    }
    case 'TouchSensor': sens.touch[norm(pick(m, 'sensorPosition'))] = !!pick(m, 'isContacted'); break;
    case 'BumpSensor': { const k = BUMP[norm(pick(m, 'sensorPosition', 'sensorId'))]; if (k) sens.bump[k] = !!pick(m, 'isContacted'); break; }
    case 'FaceRecognition': {
      const b = pick(m, 'bearing');
      if (b != null) sens.face = { bearing: num(b), elev: pick(m, 'elevation'), dist: pick(m, 'distance'), t: now };
      break;
    }
    case 'BatteryCharge': {
      let p = pick(m, 'chargePercent'); if (typeof p === 'number') { if (p <= 1) p *= 100; setBatt(p, pick(m, 'isCharging')); }
      break;
    }
    case 'ActuatorPosition': { const id = norm(pick(m, 'sensorId')), v = pick(m, 'value'); if (id && typeof v === 'number') sens.act[id] = v; break; }
  }
  if (!sens.raf) sens.raf = requestAnimationFrame(() => { sens.raf = 0; renderSensors(); });
}

function setBatt(p, charging) {
  const b = $('#batt'); b.textContent = `${charging ? '+' : ''}${Math.round(p)}%`;
  b.title = charging ? 'Battery, charging' : 'Battery'; b.dataset.low = p < 20 && !charging ? 1 : 0;
}

function renderSensors() {
  const on = sens.open;
  $('#sens-body').dataset.off = on ? 0 : 1;
  $('#sens-state').textContent = on ? 'live' : 'offline';
  for (const k of ['l', 'c', 'r', 'b']) {
    const d = on ? sens.tof[k] : null, ln = $('#t-' + k), v = $('#v-' + k);
    const len = d == null ? 0 : 4 + 28 * clamp(d / 1.5, 0, 1), back = k === 'b';
    const y0 = back ? 168 : 56; ln.setAttribute('y2', back ? y0 + len : y0 - len);
    ln.dataset.l = d == null ? '' : d < 0.3 ? 'near' : d < 0.7 ? 'mid' : 'far';
    v.textContent = d == null ? '-' : d.toFixed(2) + ' m';
  }
  for (const k of ['fl', 'fr', 'bl', 'br']) $('#b-' + k).classList.toggle('on', on && !!sens.bump[k]);
  let anyHead = false;
  $$('#touch li').forEach(li => { const o = on && !!sens.touch[li.dataset.k]; li.classList.toggle('on', o); if (o && li.dataset.k !== 'chin') anyHead = true; });
  $('#s-head').classList.toggle('on', anyHead);
  const f = sens.face, seen = on && f && Date.now() - f.t < 2000;
  $('#k-face').textContent = seen ? `seen, bearing ${f.bearing.toFixed(0)}°${f.dist != null ? ', dist ' + (+f.dist).toFixed(0) : ''}${f.elev != null ? ', elev ' + (+f.elev).toFixed(0) + '°' : ''}` : on ? 'none (detection must be running)' : 'n/a';
  const a = sens.act, g = k => (on && typeof a[k] === 'number' ? Math.round(a[k]) + '°' : '-');
  $('#k-head').textContent = on ? `p ${g('ahp')} y ${g('ahy')} r ${g('ahr')}` : 'n/a';
  $('#k-arms').textContent = on ? `L ${g('ala')} R ${g('ara')}` : 'n/a';
}

// ---------- server state, behaviours, log ----------
let since = 0, logLines = [], behKey = '';
async function poll() {
  try {
    const d = await api('/api/state?since=' + since);
    chip('server', 'ok', 'Control server reachable');
    if (d.misty_ip !== S.ip) {
      S.ip = d.misty_ip || ''; $('#ip').textContent = S.ip || 'no address';
      const old = sens.ws; sens.ws = null; if (old) old.close();
      setWs(false); sens.tries = 0; wsConnect();
    }
    S.behaviours = d.behaviours || [];
    const prev = S.behaviour; S.behaviour = d.behaviour || null;
    if (prev !== S.behaviour) { releaseAll(); renderLock(); renderKnobBar(); }
    const key = S.behaviours.join() + '|' + S.behaviour;
    if (key !== behKey) { behKey = key; renderBehaviours(); }
    if (typeof d.next === 'number') {
      if (d.next < since) { logLines = []; renderLog(); }
      since = d.next;
      if (d.log && d.log.length) appendLog(d.log);
    }
  } catch (e) { chip('server', 'bad', 'Control server unreachable: ' + e.message); }
  setTimeout(poll, 1000);
}

function appendLog(lines) {
  logLines.push(...lines);
  if (logLines.length > 500) logLines = logLines.slice(-500);
  renderLog();
}
function renderLog() {
  const el = $('#log'), pin = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
  el.textContent = logLines.join('\n');
  if (pin) el.scrollTop = el.scrollHeight;
}

function renderLock() {
  const lock = $('#drive-lock'), st = $('#stick');
  lock.hidden = !locked();
  if (locked()) lock.textContent = `Manual drive is off while "${behLabel(S.behaviour)}" runs. STOP still works.`;
  st.setAttribute('aria-disabled', locked());
}
function renderBehaviours() {
  $('#beh-state').textContent = S.behaviour ? 'running: ' + behLabel(S.behaviour) : 'idle';
  $('#behs').replaceChildren(...(S.behaviours.length ? S.behaviours : []).map(n => {
    const run = S.behaviour === n, other = S.behaviour && !run;
    const btn = h('button', {
      type: 'button', class: run ? '' : 'primary', disabled: !!other, title: other ? `Stop "${behLabel(S.behaviour)}" first` : '',
      onclick: e => act(e.currentTarget, async () => {
        if (run) await api('/api/behaviour/stop', 'POST', {}); else await api('/api/behaviour/start', 'POST', { name: n });
        S.behaviour = run ? null : n; releaseAll(); renderBehaviours(); renderKnobBar();
      }),
    }, run ? 'Stop' : 'Start');
    return h('article', { class: 'beh-card', 'data-run': run ? 1 : 0 },
      h('h3', {}, behLabel(n), run ? h('span', { class: 'run' }, h('i'), 'Running') : null),
      h('p', {}, (BEH[n] || ['', 'No description.'])[1]), btn);
  }));
  if (!S.behaviours.length) $('#behs').append(h('p', { class: 'hint' }, 'No behaviours reported by the server.'));
  renderLock();
}

// ---------- tuning ----------
const KG = ['Distance', 'Speed', 'Turning', 'Head', 'Collision', 'Other'];
const KRULES = [['Collision', /OBSTACLE|LOOK_UP|BACK_AWAY|COLLISION|BUMP/], ['Head', /PITCH|HEAD|ELEVATION|YAW/], ['Turning', /TURN|BEARING|ANGULAR/], ['Speed', /SPEED|VELOCITY/], ['Distance', /DISTANCE|ESTIMATE|STOP|SLOW|RANGE/]];
const kgroup = n => (KRULES.find(([, re]) => re.test(n)) || ['Other'])[0];
const kn = { list: [], draft: {}, bad: new Set() };

async function loadKnobs() {
  try {
    kn.list = (await api('/api/knobs')).knobs || [];
    kn.draft = Object.fromEntries(kn.list.map(k => [k.name, structuredClone(k.value)]));
    kn.bad.clear(); renderKnobs();
  } catch (e) { $('#tune-sum').textContent = 'unavailable'; $('#knobs').replaceChildren(h('p', { class: 'hint' }, 'Could not load tuning values: ' + e.message)); }
}
function renderKnobs() {
  const rows = {};
  const knobRow = k => {
    const arr = Array.isArray(k.default), row = h('div', { class: 'knob' });
    const inputs = (arr ? kn.draft[k.name] : [kn.draft[k.name]]).map((v, i) => h('input', {
      type: 'number', step: 'any', value: v, 'aria-label': arr ? `${k.name} element ${i + 1}` : k.name,
      oninput: e => {
        const id = k.name + i, x = e.target.valueAsNumber;
        e.target.setAttribute('aria-invalid', Number.isNaN(x));
        if (Number.isNaN(x)) kn.bad.add(id); else { kn.bad.delete(id); if (arr) kn.draft[k.name][i] = x; else kn.draft[k.name] = x; }
        update(k, row); renderKnobBar();
      },
    }));
    const reset = h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Reset ' + k.name + ' to default', title: 'Reset to default', onclick: () => {
      kn.draft[k.name] = structuredClone(k.default); [...kn.bad].filter(x => x.startsWith(k.name)).forEach(x => kn.bad.delete(x));
      (arr ? k.default : [k.default]).forEach((v, i) => { inputs[i].value = v; inputs[i].setAttribute('aria-invalid', false); });
      update(k, row); renderKnobBar();
    } }); reset.innerHTML = icon('reset');
    row.append(h('div', { class: 'n' }, k.name, h('span', { class: 'tagm', hidden: true }, 'modified'), h('span', { class: 'tagd', hidden: true }, 'unsaved')),
      h('div', { class: 'ctl' }, ...inputs, reset), h('div', { class: 'h' }, k.help || ''));
    rows[k.name] = row; update(k, row);
    return row;
  };
  const update = (k, row) => {
    const mod = !same(kn.draft[k.name], k.default), dirty = !same(kn.draft[k.name], k.value);
    row.classList.toggle('mod', mod); row.querySelector('.tagm').hidden = !mod; row.querySelector('.tagd').hidden = !dirty;
    row.querySelector('.icon-btn').disabled = !mod;
  };
  kn.update = update;
  $('#knobs').replaceChildren(...KG.map(g => {
    const ks = kn.list.filter(k => kgroup(k.name) === g);
    return ks.length ? h('section', { class: 'kgroup' }, h('h3', {}, g), h('div', { class: 'kgrid' }, ks.map(knobRow))) : null;
  }));
  kn.rows = rows; renderKnobBar();
}
const knobsDirty = () => kn.list.filter(k => !same(kn.draft[k.name], k.value)).length;
function renderKnobBar() {
  const n = knobsDirty(), mods = kn.list.filter(k => !same(kn.draft[k.name], k.default)).length;
  $('#tune-sum').textContent = kn.list.length ? `${mods} modified${n ? `, ${n} unsaved` : ''}` : 'loading';
  $('#k-save').disabled = !n || kn.bad.size > 0; $('#k-save').textContent = n ? `Save (${n})` : 'Save';
  $('#k-restart').disabled = !S.behaviour || kn.bad.size > 0;
  $('#k-restart').title = S.behaviour ? `Save, stop and restart "${behLabel(S.behaviour)}"` : 'Needs a running behaviour';
  $('#k-resetall').disabled = !kn.list.some(k => !same(kn.draft[k.name], k.default));
  for (const k of kn.list) kn.rows && kn.rows[k.name] && kn.update(k, kn.rows[k.name]);
}
async function saveKnobs() {
  const values = {};
  for (const k of kn.list) if (!same(kn.draft[k.name], k.default)) values[k.name] = kn.draft[k.name];
  const r = await api('/api/knobs', 'PUT', { values });
  kn.list = r.knobs || kn.list;
  kn.draft = Object.fromEntries(kn.list.map(k => [k.name, structuredClone(k.value)]));
  renderKnobs();
}
function bindTuning() {
  $('#k-resetall').addEventListener('click', () => {
    kn.draft = Object.fromEntries(kn.list.map(k => [k.name, structuredClone(k.default)])); kn.bad.clear(); renderKnobs();
  });
  $('#k-save').addEventListener('click', e => act(e.currentTarget, async () => { await saveKnobs(); toast('Saved. Applies the next time a behaviour starts.'); }));
  $('#k-restart').addEventListener('click', e => act(e.currentTarget, async () => {
    const name = S.behaviour; await saveKnobs();
    await api('/api/behaviour/stop', 'POST', {});
    try { await api('/api/behaviour/start', 'POST', { name }); } catch { await new Promise(r => setTimeout(r, 700)); await api('/api/behaviour/start', 'POST', { name }); }
    S.behaviour = name; renderBehaviours(); renderKnobBar(); toast(`Saved and restarted "${behLabel(name)}"`);
  }));
}

// ---------- macros ----------
let run = null;
const macros = () => cfg.macros || [];
const STEP_TYPES = [['say', 'Say'], ['eyes', 'Eyes'], ['led', 'LED'], ['arms', 'Arms'], ['head', 'Head'], ['audio', 'Audio'], ['wait', 'Wait']];
const defStep = t => ({ say: { type: t, text: '' }, eyes: { type: t, name: '' }, led: { type: t, color: '#ffffff' }, arms: { type: t, left: 90, right: 90 }, head: { type: t, pitch: 0, yaw: 0, roll: 0 }, audio: { type: t, name: '' }, wait: { type: t, seconds: 1 } })[t];

async function execStep(s) {
  switch (s.type) {
    case 'say': if (s.text) await speakRaw(s.text); break;
    case 'eyes': if (s.name) await showEyes(s.name); break;
    case 'led': { const c = String(s.color || '#000000'); $('#led').value = c; $('#led-out').textContent = c; await sendLed(c); break; }
    case 'arms': setArms(num(s.left, 90), num(s.right, 90), true); break;
    case 'head': setHead({ pitch: num(s.pitch), yaw: num(s.yaw), roll: num(s.roll) }, true); break;
    case 'audio': if (s.name) await post('audio/play', { FileName: s.name }); break;
  }
}
async function runMacro(m) {
  if (run) run.cancel();
  const r = run = { id: m.id, stop: false, cancel() { this.stop = true; this.wake && this.wake(); } };
  renderMacros();
  try {
    for (const s of m.steps) {
      if (r.stop) break;
      if (s.type === 'wait') await new Promise(res => { const t = setTimeout(res, Math.max(0, num(s.seconds)) * 1000); r.wake = () => { clearTimeout(t); res(); }; });
      else { try { await execStep(s); } catch (e) { fail(e); } }
    }
  } finally { if (run === r) run = null; renderMacros(); }
}

function renderMacros() {
  const box = $('#macros');
  box.replaceChildren(...macros().map(m => {
    const go = h('button', { type: 'button', class: 'go', title: m.name, onclick: () => runMacro(m) }, h('span', {}, m.name), m.hotkey ? h('kbd', {}, m.hotkey.toUpperCase()) : null);
    const ed = h('button', { type: 'button', class: 'edit icon-btn', 'aria-label': 'Edit ' + m.name, onclick: () => openEditor(m) }); ed.innerHTML = icon('edit');
    return h('div', { class: 'macro', 'data-run': run && run.id === m.id ? 1 : 0, style: m.color ? `--mc:${m.color}` : null }, go, ed);
  }));
  if (!macros().length) box.append(h('p', { class: 'none' }, 'No actions yet. Use New to build one from speech, eyes, LED, arm, head and audio steps.'));
}

let ed = null;
function openEditor(m) {
  ed = m ? structuredClone(m) : { id: uid(), name: '', hotkey: '', color: '', steps: [] };
  ed.isNew = !m;
  $('#dlg-title').textContent = m ? 'Edit action' : 'New action';
  $('#ed-name').value = ed.name; $('#ed-key').value = ed.hotkey; $('#ed-color').value = ed.color || '#3ddc97';
  $('#ed-del').hidden = ed.isNew; $('#ed-err').hidden = true;
  renderSteps(); $('#dlg').showModal(); $('#ed-name').focus();
}
function renderSteps() {
  const swap = (i, j) => { [ed.steps[i], ed.steps[j]] = [ed.steps[j], ed.steps[i]]; renderSteps(); };
  const mkNum = (o, k, label, min, max, step) => h('label', {}, label, h('input', { type: 'number', min, max, step, value: o[k], 'aria-label': label, oninput: e => { o[k] = e.target.valueAsNumber; } }));
  $('#ed-steps').replaceChildren(...ed.steps.map((s, i) => {
    const f = h('div', { class: 'f' });
    if (s.type === 'say') f.append(h('input', { type: 'text', value: s.text, placeholder: 'What to say', 'aria-label': 'Text to say', oninput: e => { s.text = e.target.value; } }));
    if (s.type === 'eyes' || s.type === 'audio') f.append(h('input', { type: 'text', list: s.type === 'eyes' ? 'dl-images' : 'dl-audio', value: s.name, placeholder: s.type === 'eyes' ? 'Image file name' : 'Clip file name', 'aria-label': s.type === 'eyes' ? 'Image name' : 'Clip name', oninput: e => { s.name = e.target.value; } }));
    if (s.type === 'led') f.append(h('input', { type: 'color', value: s.color, 'aria-label': 'LED colour', oninput: e => { s.color = e.target.value; } }));
    if (s.type === 'arms') f.append(mkNum(s, 'left', 'L', -29, 90, 1), mkNum(s, 'right', 'R', -29, 90, 1));
    if (s.type === 'head') f.append(mkNum(s, 'pitch', 'P', -40, 26, 1), mkNum(s, 'yaw', 'Y', -81, 81, 1), mkNum(s, 'roll', 'R', -40, 40, 1));
    if (s.type === 'wait') f.append(mkNum(s, 'seconds', 'Seconds', 0, 60, 0.1));
    const sel = h('select', { 'aria-label': 'Step type', onchange: e => { ed.steps[i] = defStep(e.target.value); renderSteps(); } }, STEP_TYPES.map(([v, l]) => h('option', { value: v, selected: v === s.type }, l)));
    const b = (ic, label, fn, dis) => { const x = h('button', { type: 'button', class: 'icon-btn', 'aria-label': label, title: label, disabled: dis, onclick: fn }); x.innerHTML = icon(ic); return x; };
    return h('li', { class: 'step' }, sel, f, h('div', { class: 'btns' }, b('up', 'Move up', () => swap(i, i - 1), i === 0), b('down', 'Move down', () => swap(i, i + 1), i === ed.steps.length - 1), b('x', 'Remove step', () => { ed.steps.splice(i, 1); renderSteps(); })));
  }));
}
function validateEd() {
  const name = $('#ed-name').value.trim(), key = $('#ed-key').value.trim().toLowerCase();
  if (!name) return 'Give the action a name.';
  if (key) {
    if (!/^[a-z0-9]$/.test(key)) return 'Hotkey must be a single letter or digit.';
    if ('wasd'.includes(key)) return 'W, A, S and D are used for driving. Pick another hotkey.';
    const clash = macros().find(m => m.hotkey === key && m.id !== ed.id);
    if (clash) return `"${key.toUpperCase()}" is already used by "${clash.name}".`;
  }
  for (const [i, s] of ed.steps.entries()) {
    const bad = ['left', 'right', 'pitch', 'yaw', 'roll', 'seconds'].find(k => k in s && !Number.isFinite(s[k]));
    if (bad) return `Step ${i + 1}: "${bad}" needs a number.`;
  }
  return '';
}
function collectEd() { ed.name = $('#ed-name').value.trim(); ed.hotkey = $('#ed-key').value.trim().toLowerCase(); }
function bindEditor() {
  const dlg = $('#dlg');
  $('#ed-type').replaceChildren(...STEP_TYPES.map(([v, l]) => h('option', { value: v }, l)));
  $('#macro-new').addEventListener('click', () => openEditor(null));
  $('#ed-add').addEventListener('click', () => { ed.steps.push(defStep($('#ed-type').value)); renderSteps(); });
  $('#ed-color').addEventListener('input', e => { ed.color = e.target.value; });
  $('#ed-nocolor').addEventListener('click', () => { ed.color = ''; $('#ed-color').value = '#3ddc97'; toast('Colour removed'); });
  $('#ed-x').addEventListener('click', () => dlg.close());
  $('#ed-cancel').addEventListener('click', () => dlg.close());
  $('#ed-test').addEventListener('click', () => { const e = validateEd(); $('#ed-err').hidden = !e; $('#ed-err').textContent = e; if (!e) { collectEd(); runMacro(ed); } });
  $('#ed-del').addEventListener('click', () => {
    if (run && run.id === ed.id) run.cancel();
    cfg.macros = macros().filter(m => m.id !== ed.id); saveCfg(); renderMacros(); dlg.close();
  });
  $('#ed-form').addEventListener('submit', e => {
    e.preventDefault();
    const err = validateEd(); $('#ed-err').hidden = !err; $('#ed-err').textContent = err;
    if (err) return;
    collectEd();
    const { isNew, ...m } = ed, i = macros().findIndex(x => x.id === m.id);
    if (i >= 0) cfg.macros[i] = m; else cfg.macros.push(m);
    saveCfg(); renderMacros(); dlg.close();
  });
}

// ---------- control feel ----------
function initFeel() {
  const f = cfg.feel, defs = [['#f-drive', 'maxDrive', v => v + '%'], ['#f-turn', 'maxTurn', v => v + '%'], ['#f-head', 'headDur', v => (+v).toFixed(1) + ' s']];
  for (const [sel, key, fmt] of defs) {
    const el = $(sel), out = $(sel + '-out');
    el.value = f[key]; out.textContent = fmt(f[key]);
    el.oninput = () => { cfg.feel[key] = +el.value; out.textContent = fmt(el.value); saveCfg(); };
  }
}

// ---------- keyboard ----------
const typing = t => !!(t && t.closest && t.closest('input, textarea, select, dialog, [contenteditable="true"]'));
addEventListener('keydown', e => {
  if (e.key === 'Escape') { stopAll(); return; }
  if (typing(e.target)) return;
  if (e.code === 'Space') { e.preventDefault(); if (!e.repeat) stopAll(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.code in KEYMAP) {
    if (e.code.startsWith('Arrow')) e.preventDefault();
    if (!e.repeat) { drive.keys.add(e.code); driveTick(); }
    return;
  }
  const m = !e.repeat && e.key.length === 1 && macros().find(x => x.hotkey && x.hotkey === e.key.toLowerCase());
  if (m) runMacro(m);
});
addEventListener('keyup', e => { if (drive.keys.delete(e.code)) driveTick(); });
addEventListener('blur', releaseAll);
document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(); else camKick(); });
document.addEventListener('pointerup', e => { if (e.target.matches && e.target.matches('input[type=range]')) e.target.blur(); }); // hand WASD back after a slider drag

// ---------- boot ----------
async function pollBattery() {
  if (!sens.open) { try { const r = (await api('/misty/battery')).result; const p = pick(r, 'chargePercent'); if (typeof p === 'number') setBatt(p <= 1 ? p * 100 : p, pick(r, 'isCharging')); } catch { /* optional */ } }
  setTimeout(pollBattery, 20000);
}
async function pingRobot() {
  try { await api('/misty/device'); } catch { /* api() already updated the Robot chip */ }
  setTimeout(pingRobot, 5000);
}

$$('[data-ic]').forEach(e => { e.innerHTML = icon(e.dataset.ic); });
$('#stop').addEventListener('click', stopAll);
$('#log-clear').addEventListener('click', () => { logLines = []; renderLog(); });
bindStick(); bindHead(); bindExpression(); bindSpeak(); bindCamera(); bindTuning(); bindEditor();
setArms(90, 90, null); renderHead(); renderSensors(); renderLock();
setInterval(driveTick, 125);
setInterval(renderSensors, 500); // also ages out the face indicator
loadCfg().then(() => { loadKnobs(); loadAssets(); poll(); camKick(); pingRobot(); pollBattery(); });
