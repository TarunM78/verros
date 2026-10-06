/* Gear Train Designer: UI thread. Python work happens in worker.js (Pyodide). */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- worker RPC
  const worker = new Worker('worker.js');
  const pending = new Map();
  let nextId = 1;

  function call(cmd, payload = {}) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      worker.postMessage({ id, cmd, ...payload });
    });
  }

  worker.onmessage = (ev) => {
    const msg = ev.data;
    if (msg.type === 'status') {
      $('loading-text').textContent = msg.text;
      if (msg.progress != null) $('loading-bar').style.width = `${Math.round(msg.progress * 100)}%`;
      setStatus(msg.text);
      return;
    }
    if (msg.type === 'ready') { onReady(msg.specs); return; }
    if (msg.type === 'error') { showFatal(msg.text); return; }
    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    msg.ok ? p.resolve(msg) : p.reject(new Error(msg.error));
  };

  worker.onerror = (e) => {
    const text = e.message || 'the background worker failed to load (is the Pyodide CDN reachable?)';
    if (!$('loading').hidden) showFatal(text); else setStatus(`Worker error: ${text}`, true);
  };

  function showFatal(text) {
    $('loading').hidden = false;
    $('loading').querySelector('.overlay-card').classList.add('error');
    $('loading').querySelector('.spinner').style.display = 'none';
    $('loading-text').textContent = `Failed to start: ${text}`;
    setStatus(`Failed to start: ${text}`, true);
  }

  // ---------------------------------------------------------------- state
  const state = {
    specs: [],
    spec: null,
    result: null,        // last build result
    frame: null,         // {meta, data}
    scene: null,         // angular contact scene
    phase: 0,
    playing: false,
    frameInFlight: false,
    building: false,
    rebuildQueued: false,
    recorder: null,
  };

  const canvas = $('canvas');
  const ctx = canvas.getContext('2d');

  function setStatus(text, isError = false) {
    const el = $('status');
    el.textContent = text;
    el.classList.toggle('error', isError);
  }

  // ---------------------------------------------------------------- catalogue UI
  function onReady(specs) {
    state.specs = specs;
    const sel = $('spec-select');
    sel.innerHTML = '';
    for (const s of specs) {
      const o = document.createElement('option');
      o.value = s.name; o.textContent = s.name;
      sel.appendChild(o);
    }
    const fromHash = parseHash();
    selectSpec(fromHash?.spec || specs[1]?.name || specs[0].name, fromHash);
    $('loading').hidden = true;
    resizeCanvas();
  }

  function selectSpec(name, restore = null) {
    const spec = state.specs.find((s) => s.name === name) || state.specs[0];
    state.spec = spec;
    $('spec-select').value = spec.name;
    $('spec-description').textContent = spec.description;

    // kinematics
    const memberOptions = spec.members.map(([k, label]) => ({ value: k, text: `${k}  (${label})` }));
    for (const key of ['input', 'output', 'fixed']) fillSelect($(`kin-${key}`), memberOptions);
    $('kin-fixed-field').hidden = !spec.has_fixed;
    if (spec.extra_kin) {
      $('kin-extra-field').hidden = false;
      $('kin-extra-label').textContent = spec.extra_kin.label;
      fillSelect($('kin-extra'), spec.extra_kin.choices.map((c) => ({ value: c, text: c })));
      $('kin-extra').value = spec.extra_kin.default;
    } else {
      $('kin-extra-field').hidden = true;
    }

    // params
    const grid = $('params');
    grid.innerHTML = '';
    for (const p of spec.params) {
      if (p.kind === 'bool') {
        const wrap = document.createElement('label');
        wrap.className = 'check';
        const cb = document.createElement('input');
        cb.type = 'checkbox'; cb.id = `param-${p.key}`; cb.checked = !!p.default;
        wrap.appendChild(cb);
        wrap.appendChild(document.createTextNode(p.label));
        grid.appendChild(wrap);
        cb.addEventListener('change', scheduleBuild);
        continue;
      }
      const label = document.createElement('label');
      label.textContent = p.label; label.htmlFor = `param-${p.key}`;
      grid.appendChild(label);
      let input;
      if (p.kind === 'choice') {
        input = document.createElement('select');
        fillSelect(input, p.choices.map((c) => ({ value: c, text: c })));
        input.value = p.default;
      } else {
        input = document.createElement('input');
        input.type = 'number';
        input.step = p.kind === 'int' ? '1' : 'any';
        if (p.minimum != null) input.min = String(p.minimum);
        input.value = String(p.default);
      }
      input.id = `param-${p.key}`;
      input.addEventListener('change', scheduleBuild);
      grid.appendChild(input);
    }

    // presets
    const presetNames = Object.keys(spec.presets);
    fillSelect($('preset-select'), [{ value: '', text: '— custom —' }, ...presetNames.map((n) => ({ value: n, text: n }))]);

    $('play-btn').disabled = !spec.animatable;
    $('step-btn').disabled = !spec.animatable;
    $('rec-btn').disabled = !spec.animatable;
    $('phase').disabled = !spec.animatable;
    $('export-btn').disabled = !spec.exportable;
    $('step-btn').disabled = !spec.exportable;
    $('export-log').hidden = true;
    $('step-log').hidden = true;

    if (restore && restore.spec === spec.name) {
      applyValues(restore.kin, restore.params, restore.fuse);
      $('preset-select').value = '';
    } else if (presetNames.length) {
      $('preset-select').value = presetNames[0];
      applyPreset(presetNames[0]);
    }
    build();
  }

  function fillSelect(sel, options) {
    sel.innerHTML = '';
    for (const o of options) {
      const el = document.createElement('option');
      el.value = o.value; el.textContent = o.text;
      sel.appendChild(el);
    }
  }

  function applyPreset(name) {
    const preset = state.spec.presets[name];
    if (!preset) return;
    const { kin, fuse, ...params } = preset;
    applyValues(kin, params, fuse);
  }

  function applyValues(kin, params, fuse) {
    params = params && typeof params === 'object' ? params : {};
    if (Array.isArray(kin) && kin.length >= 2) {
      $('kin-input').value = kin[0];
      $('kin-output').value = kin[1];
      if (state.spec.has_fixed && kin[2]) $('kin-fixed').value = kin[2];
    }
    if (fuse && state.spec.extra_kin) $('kin-extra').value = fuse;
    for (const p of state.spec.params) {
      if (!(p.key in params)) continue;
      const el = $(`param-${p.key}`);
      if (!el) continue;
      if (p.kind === 'bool') el.checked = !!params[p.key];
      else el.value = String(params[p.key]);
    }
  }

  function readKin() {
    const kin = [$('kin-input').value, $('kin-output').value];
    if (state.spec.has_fixed) kin.push($('kin-fixed').value);
    if (state.spec.extra_kin) kin.push($('kin-extra').value);
    return kin;
  }

  function readParams() {
    const params = {};
    for (const p of state.spec.params) {
      const el = $(`param-${p.key}`);
      if (p.kind === 'bool') params[p.key] = el.checked;
      else if (p.kind === 'choice') params[p.key] = el.value;
      else params[p.key] = el.value === '' ? p.default : Number(el.value);
    }
    return params;
  }

  // ---------------------------------------------------------------- build
  let buildTimer = null;
  function scheduleBuild() {
    $('preset-select').value = '';
    clearTimeout(buildTimer);
    buildTimer = setTimeout(build, 150);
  }

  async function build() {
    if (state.building) { state.rebuildQueued = true; return; }
    state.building = true;
    stopAnimation();
    setStatus('Building geometry…');
    const kin = readKin();
    const params = readParams();
    try {
      const { result } = await call('build', { spec: state.spec.name, kin, params });
      if (!result.ok) {
        setStatus(result.error, true);
        showWarnings([result.error], true);
        return;
      }
      state.result = result;
      state.phase = 0;
      $('phase').value = '0';
      showResults(result);
      showWarnings(result.warnings, false);
      writeHash(kin, params);
      state.scene = null;
      state.frame = null;
      if (result.animatable) {
        await requestFrame(0);
      } else {
        const { scene } = await call('scene');
        state.scene = scene;
        state.frame = null;
        draw();
      }
      setStatus(`${state.spec.name}: ratio ${fmtRatio(result.ratio)} : 1`);
    } catch (e) {
      setStatus(String(e.message || e), true);
    } finally {
      state.building = false;
      if (state.rebuildQueued) { state.rebuildQueued = false; build(); }
    }
  }

  function fmtRatio(r) {
    if (!isFinite(r)) return String(r);
    const a = Math.abs(r);
    if (a >= 1000) return r.toFixed(0);
    if (a >= 100) return r.toFixed(1);
    if (a >= 10) return r.toFixed(2);
    return r.toPrecision(4);
  }

  function showResults(r) {
    $('results').hidden = false;
    $('ratio-value').textContent = fmtRatio(r.ratio);
    const label = (k) => { const m = state.spec.members.find(([key]) => key === k); return m ? `${k} (${m[1]})` : k; };
    $('res-io').textContent = `${label(r.input)} → ${label(r.output)}${r.fixed ? `,  fixed ${label(r.fixed)}` : ''}`;
    $('res-symbolic').textContent = r.symbolic;
    const table = $('res-table');
    table.innerHTML = '';
    for (const k of Object.keys(r.ratios).sort()) {
      const tr = document.createElement('tr');
      const td1 = document.createElement('td'); td1.textContent = label(k);
      const td2 = document.createElement('td'); td2.textContent = formatSigned(r.ratios[k]);
      tr.append(td1, td2);
      table.appendChild(tr);
    }
    $('canvas-title').textContent = r.title;
  }

  function formatSigned(v) {
    const s = Math.abs(v) >= 1e4 || (Math.abs(v) > 0 && Math.abs(v) < 1e-3) ? v.toExponential(4) : v.toPrecision(5);
    return (v >= 0 ? '+' : '') + s;
  }

  function showWarnings(list, isError) {
    const el = $('warnings');
    const stage = $('stage-warn');
    if (!list || !list.length) { el.hidden = true; stage.hidden = true; return; }
    el.hidden = false;
    el.textContent = list.join('\n');
    el.style.color = isError ? 'var(--danger)' : '';
    stage.hidden = false;
    stage.textContent = list.join('  ·  ');
  }

  // ---------------------------------------------------------------- frames & drawing
  async function requestFrame(phase) {
    if (state.frameInFlight) return;
    state.frameInFlight = true;
    try {
      const msg = await call('frame', { phase });
      state.frame = { meta: msg.meta, data: msg.data };
      state.phase = msg.phase;
      $('phase').value = String(mod2pi(msg.phase));
      $('phase-label').textContent = `${mod2pi(msg.phase).toFixed(3)} rad`;
      draw();
    } catch (e) {
      setStatus(String(e.message || e), true);
      stopAnimation();
    } finally {
      state.frameInFlight = false;
    }
  }

  function mod2pi(x) { const t = 2 * Math.PI; return ((x % t) + t) % t; }

  function resizeCanvas() {
    const stage = canvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    const w = stage.clientWidth, h = stage.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    draw();
  }

  function viewTransform() {
    const limit = state.result?.limit || 1;
    const w = canvas.width, h = canvas.height;
    const pad = 0.90;
    const topInset = 36 * (window.devicePixelRatio || 1);
    const s = Math.min(w, h - topInset) / (2 * limit) * pad;
    return { s, cx: w / 2, cy: (h + topInset) / 2 };
  }

  function draw() {
    const w = canvas.width, h = canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim() || '#fff';
    ctx.fillRect(0, 0, w, h);
    if (!state.result) return;
    const { s, cx, cy } = viewTransform();
    const dpr = window.devicePixelRatio || 1;
    ctx.lineWidth = Math.max(1, 1.1 * dpr);
    ctx.lineCap = 'round';

    if (state.result.animatable && state.frame) {
      const { meta, data } = state.frame;
      for (const g of meta.groups) {
        ctx.strokeStyle = g.color;
        ctx.beginPath();
        const start = g.start * 4, end = (g.start + g.count) * 4;
        for (let i = start; i < end; i += 4) {
          ctx.moveTo(cx + data[i] * s, cy - data[i + 1] * s);
          ctx.lineTo(cx + data[i + 2] * s, cy - data[i + 3] * s);
        }
        ctx.stroke();
      }
    } else if (state.scene) {
      drawScene(state.scene, s, cx, cy, dpr);
    }
  }

  function drawScene(scene, s, cx, cy, dpr) {
    ctx.lineWidth = 1.5 * dpr;
    for (const pl of scene.polylines) {
      ctx.strokeStyle = pl.color;
      ctx.beginPath();
      pl.points.forEach(([x, y], i) => (i ? ctx.lineTo(cx + x * s, cy - y * s) : ctx.moveTo(cx + x * s, cy - y * s)));
      ctx.stroke();
    }
    for (const p of scene.points) {
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(cx + p.x * s, cy - p.y * s, 4 * dpr, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text').trim() || '#000';
    ctx.font = `${12 * dpr}px system-ui, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (const t of scene.texts) {
      ctx.save();
      ctx.translate(cx + t.x * s, cy - t.y * s);
      ctx.rotate(-(t.rotation || 0) * Math.PI / 180);
      ctx.fillText(t.text, 0, 0);
      ctx.restore();
    }
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim() || '#666';
    ctx.fillText(scene.xlabel || '', cx, cy + 1.25 * s);
    ctx.save(); ctx.translate(cx - 1.25 * s, cy); ctx.rotate(-Math.PI / 2); ctx.fillText(scene.ylabel || '', 0, 0); ctx.restore();
  }

  // ---------------------------------------------------------------- animation
  function speedFactor() { return Math.pow(10, Number($('speed').value)); }

  function animationStep() {
    if (!state.playing) return;
    if (!state.frameInFlight && state.result?.animatable) {
      const next = state.phase + state.result.anim_scale * speedFactor();
      requestFrame(next);
    }
    requestAnimationFrame(animationStep);
  }

  function startAnimation() {
    if (!state.result?.animatable) return;
    state.playing = true;
    $('play-btn').textContent = 'Pause';
    requestAnimationFrame(animationStep);
  }

  function stopAnimation() {
    state.playing = false;
    $('play-btn').textContent = 'Play';
  }

  function toggleAnimation() { state.playing ? stopAnimation() : startAnimation(); }

  // ---------------------------------------------------------------- downloads
  function downloadBlob(blob, filename) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  function suggestName(ext) {
    const base = state.spec.name.toLowerCase().split(' ')[0].replace(/[^a-z]/g, '');
    const ints = state.spec.params.filter((p) => p.kind === 'int').map((p) => $(`param-${p.key}`).value);
    return `${base}${ints.length ? '_' + ints.join('_') : ''}.${ext}`;
  }

  function savePng() {
    if (!state.result) return;
    // compose the title into the image
    const out = document.createElement('canvas');
    out.width = canvas.width; out.height = canvas.height;
    const octx = out.getContext('2d');
    octx.drawImage(canvas, 0, 0);
    const dpr = window.devicePixelRatio || 1;
    octx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
    octx.font = `${11 * dpr}px ui-monospace, Consolas, monospace`;
    octx.textAlign = 'center'; octx.textBaseline = 'top';
    octx.fillText(state.result.title, out.width / 2, 10 * dpr, out.width - 32 * dpr);
    out.toBlob((blob) => downloadBlob(blob, suggestName('png')), 'image/png');
  }

  async function recordWebm() {
    if (!state.result?.animatable || state.recorder) return;
    const seconds = Math.max(1, Math.min(60, Number($('rec-seconds').value) || 6));
    const stream = canvas.captureStream(30);
    const mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((m) => MediaRecorder.isTypeSupported(m));
    if (!mime) { $('rec-status').textContent = 'This browser cannot record WebM.'; return; }
    const chunks = [];
    const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 6_000_000 });
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = () => {
      state.recorder = null;
      $('rec-btn').disabled = false;
      $('rec-status').textContent = 'Saved.';
      downloadBlob(new Blob(chunks, { type: 'video/webm' }), suggestName('webm'));
    };
    state.recorder = rec;
    $('rec-btn').disabled = true;
    const wasPlaying = state.playing;
    if (!wasPlaying) startAnimation();
    rec.start(200);
    let left = seconds;
    const tick = setInterval(() => {
      left -= 1;
      $('rec-status').textContent = `Recording… ${left}s`;
      if (left <= 0) {
        clearInterval(tick);
        rec.stop();
        if (!wasPlaying) stopAnimation();
      }
    }, 1000);
    $('rec-status').textContent = `Recording… ${left}s`;
  }

  function exportSettings() {
    return {
      target_diameter_mm: Number($('exp-diam').value) || 70,
      thickness_mm: Number($('exp-thick').value) || 10,
      helix_angle_deg: Number($('exp-helix').value) || 0,
      gear_type: $('exp-type').value,
    };
  }

  async function exportCad() {
    if (!state.result?.exportable) return;
    const btn = $('export-btn');
    btn.disabled = true;
    setStatus('Generating CAD curves…');
    try {
      const settings = exportSettings();
      const [{ bytes }, { report }] = await Promise.all([
        call('export', { settings }),
        call('export_report', { settings }),
      ]);
      downloadBlob(new Blob([bytes], { type: 'application/zip' }), suggestName('zip'));
      const log = $('export-log');
      log.hidden = false;
      log.textContent = [`Scale factor ${report.scale.toFixed(6)}`, ...report.messages, '', 'Files:', ...report.files.map((f) => '  ' + f)].join('\n');
      setStatus(`Exported ${report.files.length} curve files`);
    } catch (e) {
      setStatus(`Export failed: ${e.message || e}`, true);
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- STEP export (OpenCascade in a second worker)
  let cadWorker = null;
  const cadPending = new Map();
  let cadNextId = 1;

  function ensureCadWorker() {
    if (cadWorker) return cadWorker;
    cadWorker = new Worker('step/cad-worker.js', { type: 'module' });
    cadWorker.onmessage = (ev) => {
      const msg = ev.data;
      if (msg.type === 'progress') {
        setStatus(`STEP: ${msg.text}`);
        const bar = $('step-progress');
        bar.hidden = false;
        if (typeof msg.fraction === 'number') bar.value = msg.fraction;
        return;
      }
      const p = cadPending.get(msg.id);
      if (!p) return;
      cadPending.delete(msg.id);
      msg.ok ? p.resolve(msg) : p.reject(new Error(msg.error));
    };
    cadWorker.onerror = (e) => {
      const err = new Error(e.message || 'the CAD worker failed to load');
      for (const p of cadPending.values()) p.reject(err);
      cadPending.clear();
      cadWorker.terminate();
      cadWorker = null;
    };
    return cadWorker;
  }

  function cadCall(cmd, payload = {}) {
    return new Promise((resolve, reject) => {
      const id = cadNextId++;
      cadPending.set(id, { resolve, reject });
      ensureCadWorker().postMessage({ id, cmd, ...payload });
    });
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`could not load ${src}`));
      document.head.appendChild(s);
    });
  }

  async function loadJSZip() {
    if (!window.JSZip) await loadScript('https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js');
    return window.JSZip;
  }

  async function exportStep() {
    if (!state.result?.exportable) return;
    const btn = $('step-btn');
    const log = $('step-log');
    btn.disabled = true;
    $('step-progress').hidden = false;
    $('step-progress').value = 0;
    try {
      setStatus('STEP: preparing solid model…');
      const settings = { ...exportSettings(), layer_gap_mm: Number($('exp-gap').value) || 0 };
      const { model } = await call('solid_model', { settings });
      const options = { parts: true, assembly: $('step-assembly').checked, curves: $('step-curves').value };
      const t0 = performance.now();
      const res = await cadCall('build', { model, options });
      const JSZip = await loadJSZip();
      const zip = new JSZip();
      for (const f of res.files) zip.file(f.name, f.data);
      zip.file('README.txt', [
        'pygeartrain STEP export', '',
        `Gear train: ${state.spec.name}`, `Title: ${state.result.title}`,
        `Settings: ${JSON.stringify(settings)}`, `Curves: ${options.curves}`, '',
        'Each <part>.step is centred on its own axis with the mid-plane at Z=0.',
        'assembly.step places every part as in the animation at phase 0; stacked stages are',
        `offset along Z by thickness + ${settings.layer_gap_mm} mm. Units: mm.`,
      ].join('\n'));
      const blob = await zip.generateAsync({ type: 'blob' });
      downloadBlob(blob, suggestName('step.zip'));
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      log.hidden = false;
      log.textContent = [`Built ${res.files.length} STEP files in ${secs} s`, '',
        ...res.files.map((f) => `  ${f.name}  (${(f.data.byteLength / 1024).toFixed(0)} KB)`)].join('\n');
      setStatus(`STEP export done: ${res.files.length} files`);
    } catch (e) {
      setStatus(`STEP export failed: ${e.message || e}`, true);
      log.hidden = false;
      log.textContent = `STEP export failed: ${e.message || e}`;
    } finally {
      btn.disabled = false;
      $('step-progress').hidden = true;
    }
  }

  // ---------------------------------------------------------------- URL state
  function writeHash(kin, params) {
    const obj = { spec: state.spec.name, kin, params };
    if (state.spec.extra_kin) obj.fuse = $('kin-extra').value;
    try { history.replaceState(null, '', '#' + encodeURIComponent(JSON.stringify(obj))); } catch { /* ignore */ }
  }

  function parseHash() {
    if (!location.hash || location.hash.length < 2) return null;
    try {
      const obj = JSON.parse(decodeURIComponent(location.hash.slice(1)));
      return obj && obj.spec ? obj : null;
    } catch { return null; }
  }

  // ---------------------------------------------------------------- wiring
  $('spec-select').addEventListener('change', (e) => selectSpec(e.target.value));
  $('preset-select').addEventListener('change', (e) => { if (e.target.value) { applyPreset(e.target.value); build(); } });
  for (const id of ['kin-input', 'kin-output', 'kin-fixed', 'kin-extra']) $(id).addEventListener('change', scheduleBuild);
  $('update-btn').addEventListener('click', build);
  document.querySelector('.sidebar').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.matches('input')) { e.preventDefault(); build(); }
  });

  document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => {
      t.classList.toggle('active', t === tab);
      t.setAttribute('aria-selected', String(t === tab));
    });
    document.querySelectorAll('.tab-panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${tab.dataset.tab}`));
  }));

  $('play-btn').addEventListener('click', toggleAnimation);
  $('step-btn').addEventListener('click', () => { stopAnimation(); requestFrame(state.phase + (state.result?.anim_scale || 0.01) * speedFactor()); });
  $('reset-btn').addEventListener('click', () => { stopAnimation(); requestFrame(0); });
  $('speed').addEventListener('input', () => { $('speed-label').textContent = `${speedFactor().toFixed(2).replace(/\.?0+$/, '')}×`; });
  $('phase').addEventListener('input', () => { if (!state.playing) requestFrame(Number($('phase').value)); });
  $('png-btn').addEventListener('click', savePng);
  $('rec-btn').addEventListener('click', recordWebm);
  $('export-btn').addEventListener('click', exportCad);
  $('step-btn').addEventListener('click', exportStep);

  window.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !e.target.matches('input, select, textarea, button, summary, a')) { e.preventDefault(); toggleAnimation(); }
  });
  window.addEventListener('resize', resizeCanvas);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
  new ResizeObserver(resizeCanvas).observe(canvas.parentElement);
})();
