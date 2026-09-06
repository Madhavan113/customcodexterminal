(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const bridge = window.webkit?.messageHandlers?.terminal;
  const preview = !bridge && new URLSearchParams(location.search).has('preview');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const defaults = { scene: 'dragon', palette: 'noir', energy: 'auto', intensity: .75, glass: .70, fontSize: 14, motion: !reducedMotion.matches };
  const enums = { scene: ['dragon', 'aurora', 'embers', 'void'], palette: ['noir', 'violet', 'ember', 'ice'], energy: ['auto', 'idle', 'working', 'max', 'ultra'] };
  const palettes = {
    noir: { accent: '#8af6ff', secondary: '#f290d4', rgb: '138,246,255', blue: '#8faeff', magenta: '#bd9dff' },
    violet: { accent: '#c0a0ff', secondary: '#f493e7', rgb: '192,160,255', blue: '#a3a8ff', magenta: '#df9dff' },
    ember: { accent: '#ffbb82', secondary: '#ef7194', rgb: '255,187,130', blue: '#9aaaff', magenta: '#ee94bb' },
    ice: { accent: '#a0eaff', secondary: '#7ca6ff', rgb: '160,234,255', blue: '#8dbdff', magenta: '#b0aaff' }
  };
  function validated(values) {
    const result = {};
    if (!values || typeof values !== 'object') return result;
    for (const [key, allowed] of Object.entries(enums)) if (allowed.includes(values[key])) result[key] = values[key];
    for (const [key, low, high] of [['intensity', 0, 1], ['glass', .2, 1], ['fontSize', 10, 24]]) {
      if (typeof values[key] === 'number' && Number.isFinite(values[key])) result[key] = Math.max(low, Math.min(high, values[key]));
    }
    if (typeof values.motion === 'boolean') result.motion = values.motion;
    return result;
  }
  let prefs = { ...defaults };
  try { prefs = { ...prefs, ...validated(JSON.parse(localStorage.getItem('dragon-appearance') || '{}')) }; } catch (_) { /* Native preferences are authoritative. */ }
  let alive = true, activity = 'idle', focusMode = false, sessionMode = 'codex', detectionTimer = 0, prefTimer = 0;
  let lastState = '', lastPalette = '', lastFont = 0, outputChunks = 0;
  const send = message => { if (bridge) bridge.postMessage(message); };
  function theme() {
    const p = palettes[prefs.palette];
    return {
      background: '#090a1000', foreground: '#e2dfec', cursor: p.accent, cursorAccent: '#090a10',
      selectionBackground: p.magenta + '55', selectionInactiveBackground: p.magenta + '33',
      black: '#171820', red: '#f17f9d', green: '#86dec4', yellow: '#f0c574', blue: p.blue,
      magenta: p.magenta, cyan: p.accent, white: '#e2dfec', brightBlack: '#7c7f97',
      brightRed: '#ffa2b9', brightGreen: '#abf3da', brightYellow: '#ffe0a3', brightBlue: '#b6c9ff',
      brightMagenta: p.secondary, brightCyan: '#bcfbff', brightWhite: '#f7f3ff'
    };
  }
  const term = new Terminal({
    allowTransparency: true, fontFamily: 'Menlo, Monaco, monospace', fontSize: prefs.fontSize,
    lineHeight: 1.16, fontWeight: '400', cursorBlink: prefs.motion, cursorStyle: 'bar',
    cursorInactiveStyle: 'outline', scrollback: 10000, minimumContrastRatio: 4.5,
    macOptionIsMeta: true, rightClickSelectsWord: true, theme: theme(),
    linkHandler: { activate: (_event, uri) => { if (/^https?:\/\//i.test(uri)) { send({ type: 'copy', text: uri }); flash('Link copied. Paste it into your browser.'); } } }
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($('terminal'));
  const scene = new DragonScene($('atmosphere'));
  function fitTerminal() {
    fit.fit();
    $('terminal-size').textContent = `${term.cols} × ${term.rows}`;
  }
  term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }));
  term.onData(data => { if (alive) send({ type: 'input', data }); });
  term.onBinary(data => { if (alive) send({ type: 'inputBytes', data: btoa(data) }); });
  term.onTitleChange(title => { $('session-name').textContent = title.slice(0, 70) || sessionMode.toUpperCase(); });
  new ResizeObserver(() => { fitTerminal(); scene.resize(); }).observe($('terminal'));
  let flashTimer;
  function flash(message) {
    clearTimeout(flashTimer);
    $('status-message').textContent = message;
    flashTimer = setTimeout(() => { $('status-message').textContent = prefs.energy === 'auto' ? 'Atmosphere follows Codex.' : 'Visual energy is set manually.'; }, 3500);
  }
  function persist() {
    try { localStorage.setItem('dragon-appearance', JSON.stringify(prefs)); } catch (_) { /* Native bridge persists independently. */ }
    clearTimeout(prefTimer);
    prefTimer = setTimeout(() => send({ type: 'preferences', values: prefs }), 180);
  }
  function effectiveState() { return prefs.energy === 'auto' ? activity : prefs.energy; }
  function updateAppearance(save = false) {
    const p = palettes[prefs.palette];
    const effectiveMotion = prefs.motion && !reducedMotion.matches;
    document.documentElement.style.setProperty('--accent', p.accent);
    document.documentElement.style.setProperty('--secondary', p.secondary);
    document.documentElement.style.setProperty('--accent-rgb', p.rgb);
    document.documentElement.style.setProperty('--shade', String(prefs.glass));
    document.body.classList.toggle('motion-off', !effectiveMotion);
    document.querySelectorAll('[data-scene]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.scene === prefs.scene)));
    document.querySelectorAll('[data-palette]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.palette === prefs.palette)));
    $('energy').value = prefs.energy;
    $('intensity').value = Math.round(prefs.intensity * 100);
    $('intensity-value').value = `${Math.round(prefs.intensity * 100)}%`;
    $('glass').value = Math.round(prefs.glass * 100);
    $('glass-value').value = `${Math.round(prefs.glass * 100)}%`;
    $('motion').checked = prefs.motion;
    $('font-size').value = String(prefs.fontSize);
    if (lastPalette !== prefs.palette) { term.options.theme = theme(); lastPalette = prefs.palette; }
    if (lastFont !== prefs.fontSize) { term.options.fontSize = prefs.fontSize; lastFont = prefs.fontSize; fitTerminal(); }
    term.options.cursorBlink = effectiveMotion;
    const state = effectiveState();
    scene.setOptions({ scene: prefs.scene, palette: prefs.palette, intensity: prefs.intensity, motion: effectiveMotion && !focusMode, state });
    const labels = { idle: 'AMBIENT', working: 'WORKING', max: 'MAX', ultra: 'ULTRA' };
    $('energy-badge').lastElementChild.textContent = labels[state];
    $('energy-badge').dataset.state = state;
    const captions = {
      dragon: { idle: 'The dragon\nis listening.', working: 'Wings up.\nIdeas taking flight.', max: 'Into the\nviolet hour.', ultra: 'Let the\nnight catch fire.' },
      aurora: { idle: 'A little light\nin the darkness.', working: 'Follow the\ncurrent.', max: 'Electric\nafterglow.', ultra: 'The sky\nis awake.' },
      embers: { idle: 'Keep a\nlittle fire.', working: 'Something\nis sparking.', max: 'Feed the\nflame.', ultra: 'Bring on\nthe inferno.' },
      void: { idle: 'Space\nto think.', working: 'Quietly\nin motion.', max: 'Deep\nconcentration.', ultra: 'Into\nthe deep.' }
    };
    $('scene-title').textContent = captions[prefs.scene][state];
    $('connection-status').textContent = preview ? 'VISUAL PREVIEW' : alive ? (state === 'idle' ? 'CONNECTED' : 'CONNECTED / ' + labels[state]) : 'SESSION ENDED';
    if (state !== lastState) { document.body.dataset.energy = state; lastState = state; }
    if (save) persist();
  }
  function visibleOutput() {
    const buffer = term.buffer.active;
    const lines = [];
    for (let y = buffer.baseY; y < Math.min(buffer.length, buffer.baseY + term.rows); y++) lines.push(buffer.getLine(y)?.translateToString(true) || '');
    return lines.join('\n');
  }
  function detectActivity() {
    detectionTimer = 0;
    const output = visibleOutput();
    let next = 'idle';
    const rails = [...output.matchAll(/^\s*─{3,}\s+(WORKING|MAX|ULTRA)\s+─{3,}\s*$/gm)];
    if (rails.length) next = rails[rails.length - 1][1].toLowerCase();
    else if (/^[ •◦·⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]*Working\s*\(.*esc to interrupt/m.test(output)) next = 'working';
    if (!alive) next = 'idle';
    if (next !== activity) { activity = next; updateAppearance(); }
  }
  function scheduleDetection() { if (!detectionTimer) detectionTimer = setTimeout(detectActivity, 120); }
  function change(values) { prefs = { ...prefs, ...validated(values) }; updateAppearance(true); }
  function newSession(mode) { if (bridge) send({ type: 'newSession', mode }); else flash('Open Dragon Terminal to start a live session.'); }
  function copySelection() { const text = term.getSelection(); if (text) send({ type: 'copy', text }); }
  function adjustFont(delta) { change({ fontSize: prefs.fontSize + delta }); }
  function toggleFocus() {
    focusMode = !focusMode;
    document.body.classList.toggle('focus-mode', focusMode);
    $('focus-mode').setAttribute('aria-pressed', String(focusMode));
    updateAppearance(); fitTerminal(); term.focus();
  }
  window.DragonNative = {
    receive(message) {
      if (!message || typeof message !== 'object') return;
      if (message.type === 'data' && typeof message.data === 'string') {
        try {
          const bytes = Uint8Array.from(atob(message.data), character => character.charCodeAt(0));
          term.write(bytes, () => { outputChunks++; send({ type: 'ack' }); scheduleDetection(); });
        } catch (_) { send({ type: 'ack' }); flash('Could not read a terminal frame.'); }
      } else if (message.type === 'session') {
        sessionMode = message.mode === 'shell' ? 'shell' : 'codex';
        $('session-name').textContent = sessionMode.toUpperCase();
        const cwd = typeof message.cwd === 'string' ? message.cwd : '';
        $('directory').textContent = cwd;
        $('directory').title = cwd;
        alive = true; $('session-ended').hidden = true; updateAppearance(); term.focus();
      } else if (message.type === 'preferences') {
        prefs = { ...defaults, ...validated(message.values) }; updateAppearance();
      } else if (message.type === 'exit') {
        alive = false; activity = 'idle'; $('session-ended').hidden = false; updateAppearance();
        flash(message.code === 0 ? 'Session finished.' : `Session exited (${Number(message.code) || 1}).`);
      } else if (message.type === 'error') {
        alive = false; activity = 'idle'; $('session-ended').hidden = false; $('session-ended').querySelector('strong').textContent = 'Unable to start this session.';
        $('session-ended').querySelector('span').textContent = typeof message.message === 'string' ? message.message : 'Open a new window to retry.';
        updateAppearance();
      }
    },
    copySelection, paste: text => { if (alive && typeof text === 'string') { term.paste(text); term.focus(); } },
    selectAll: () => term.selectAll(), adjustFont, toggleFocus,
    snapshot: () => ({ preferences: { ...prefs }, activity, state: effectiveState(), renderer: scene.stats, alive, cols: term.cols, rows: term.rows, outputChunks, text: visibleOutput() })
  };
  document.querySelectorAll('[data-scene]').forEach(button => button.addEventListener('click', () => change({ scene: button.dataset.scene })));
  document.querySelectorAll('[data-palette]').forEach(button => button.addEventListener('click', () => change({ palette: button.dataset.palette })));
  $('energy').addEventListener('change', event => change({ energy: event.target.value }));
  $('intensity').addEventListener('input', event => change({ intensity: Number(event.target.value) / 100 }));
  $('glass').addEventListener('input', event => change({ glass: Number(event.target.value) / 100 }));
  $('motion').addEventListener('change', event => change({ motion: event.target.checked }));
  $('font-smaller').addEventListener('click', () => adjustFont(-1));
  $('font-larger').addEventListener('click', () => adjustFont(1));
  $('focus-mode').addEventListener('click', toggleFocus);
  $('reset-settings').addEventListener('click', () => { prefs = { ...defaults }; updateAppearance(true); flash('Appearance reset.'); });
  $('new-codex').addEventListener('click', () => newSession('codex'));
  $('new-shell').addEventListener('click', () => newSession('shell'));
  document.addEventListener('keydown', event => {
    if (!event.metaKey || event.type !== 'keydown') return;
    let handled = true;
    if (['+', '='].includes(event.key)) adjustFont(1);
    else if (event.key === '-') adjustFont(-1);
    else if (event.key.toLowerCase() === 'f' && event.shiftKey) toggleFocus();
    else if (event.key.toLowerCase() === 'n') newSession(event.shiftKey ? 'shell' : 'codex');
    else if (event.key.toLowerCase() === 'c' && term.hasSelection()) copySelection();
    else handled = false;
    if (handled) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  reducedMotion.addEventListener('change', () => updateAppearance());
  updateAppearance(); fitTerminal(); scene.resize();
  if (bridge) { send({ type: 'ready' }); send({ type: 'resize', cols: term.cols, rows: term.rows }); term.focus(); }
  else if (preview) {
    document.body.classList.add('preview-mode');
    term.options.disableStdin = true;
    term.write('\x1b[38;2;138;246;255m❯ codex-noir\x1b[0m\r\n\r\n\x1b[1mOpenAI Codex\x1b[0m\r\n\x1b[38;2;124;127;151mA preview of your after-hours workspace.\x1b[0m\r\n\r\n\x1b[38;2;189;157;255m›\x1b[0m Build something extraordinary.\r\n\r\n  Your real shell and Codex live here.\r\n  The atmosphere is yours to change.\r\n\r\n\x1b[38;2;138;246;255m  ──────────────── ULTRA ────────────────\x1b[0m\r\n\x1b[38;2;189;157;255m»\x1b[0m \x1b[38;2;124;127;151mAsk Codex to do anything\x1b[0m\r\n', () => { detectActivity(); });
  } else { alive = false; updateAppearance(); flash('Open the Dragon Terminal application to connect your shell.'); }
})();
