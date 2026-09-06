/* A small, dependency-free atmospheric canvas renderer for Dragon Terminal. */
(() => {
  'use strict';

  const TAU = Math.PI * 2;
  const FRAME_MS = 1000 / 30;
  const PALETTES = {
    noir: {
      background: [6, 8, 14], primary: [138, 246, 255], secondary: [225, 118, 236],
      ember: [255, 181, 113], body: [17, 37, 57], wing: [77, 40, 108],
    },
    violet: {
      background: [9, 7, 17], primary: [182, 164, 255], secondary: [253, 124, 213],
      ember: [244, 199, 255], body: [35, 26, 61], wing: [99, 43, 121],
    },
    ember: {
      background: [13, 8, 10], primary: [255, 176, 98], secondary: [255, 94, 125],
      ember: [255, 223, 155], body: [58, 29, 29], wing: [110, 37, 50],
    },
    ice: {
      background: [5, 10, 16], primary: [151, 249, 255], secondary: [127, 166, 255],
      ember: [211, 243, 255], body: [19, 39, 63], wing: [39, 70, 113],
    },
  };
  const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, value));
  const color = (rgb, alpha = 1) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${clamp(alpha)})`;
  const mix = (a, b, weight) => a.map((v, i) => Math.round(v + (b[i] - v) * weight));
  const fract = value => value - Math.floor(value);
  const point = (x, y) => ({ x, y });
  const lerpPoint = (a, b, t) => point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);

  function seededRandom(seed) {
    return () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
  }

  function cubic(a, b, c, d, t) {
    const u = 1 - t;
    return point(
      u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t * t * t * d.x,
      u * u * u * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t * t * t * d.y,
    );
  }

  function strokeCurve(ctx, points) {
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length - 1; i++) {
      const midpoint = lerpPoint(points[i], points[i + 1], 0.5);
      ctx.quadraticCurveTo(points[i].x, points[i].y, midpoint.x, midpoint.y);
    }
    const last = points[points.length - 1];
    ctx.lineTo(last.x, last.y);
    ctx.stroke();
  }

  class DragonScene {
    constructor(canvas) {
      if (!canvas || typeof canvas.getContext !== 'function') {
        throw new TypeError('DragonScene needs a canvas element.');
      }
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d', { alpha: false });
      if (!this.ctx) throw new Error('A 2D canvas context is unavailable.');
      this.options = { scene: 'dragon', intensity: 0.7, motion: true, palette: 'noir', state: 'idle' };
      this.width = 1;
      this.height = 1;
      this.dpr = 1;
      this._elapsed = 0;
      this._frames = 0;
      this._raf = null;
      this._lastTime = null;
      this._lastPaint = -Infinity;
      this._destroyed = false;
      this._motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
      this._visibilityChanged = () => this._syncAnimation();
      this._reducedMotionChanged = () => this._syncAnimation();
      this._tick = this._tick.bind(this);
      const random = seededRandom(0xD4A60F1);
      this._stars = Array.from({ length: 110 }, () => ({
        x: random(), y: random(), radius: 0.35 + random() * 1.15,
        depth: 0.25 + random() * 0.75, phase: random() * TAU, speed: 0.35 + random() * 0.65,
      }));
      this._embers = Array.from({ length: 78 }, () => ({
        x: random(), y: random(), radius: 0.5 + random() * 1.4,
        phase: random() * TAU, speed: 0.35 + random() * 0.65, drift: random() - 0.5,
      }));
      this._breath = Array.from({ length: 34 }, () => ({
        phase: random(), spread: random() - 0.5, radius: 0.002 + random() * 0.007,
        speed: 0.6 + random() * 0.4, wave: random() * TAU,
      }));
      document.addEventListener('visibilitychange', this._visibilityChanged);
      this._motionQuery.addEventListener('change', this._reducedMotionChanged);
      this.resize();
      this._syncAnimation(false);
    }

    setOptions(next = {}) {
      if (this._destroyed || !next || typeof next !== 'object') return;
      const previous = { ...this.options };
      if (['dragon', 'aurora', 'embers', 'void'].includes(next.scene)) this.options.scene = next.scene;
      if (Object.prototype.hasOwnProperty.call(PALETTES, next.palette)) this.options.palette = next.palette;
      if (['idle', 'working', 'max', 'ultra'].includes(next.state)) this.options.state = next.state;
      if (typeof next.motion === 'boolean') this.options.motion = next.motion;
      if (typeof next.intensity === 'number' && Number.isFinite(next.intensity)) {
        this.options.intensity = clamp(next.intensity);
      }
      if (Object.keys(this.options).every(key => this.options[key] === previous[key])) return;
      this._render();
      this._syncAnimation(false);
    }

    resize() {
      if (this._destroyed) return;
      const rect = this.canvas.getBoundingClientRect();
      this.width = Math.max(1, Math.round(rect.width || this.canvas.clientWidth || window.innerWidth));
      this.height = Math.max(1, Math.round(rect.height || this.canvas.clientHeight || window.innerHeight));
      this.dpr = clamp(window.devicePixelRatio || 1, 1, 2);
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      this._render();
    }

    destroy() {
      this._destroyed = true;
      if (this._raf !== null) cancelAnimationFrame(this._raf);
      this._raf = null;
      document.removeEventListener('visibilitychange', this._visibilityChanged);
      this._motionQuery.removeEventListener('change', this._reducedMotionChanged);
    }

    get stats() {
      return {
        ...this.options, frameCount: this._frames, elapsed: this._elapsed,
        width: this.width, height: this.height, dpr: this.dpr,
        animating: this._raf !== null, reducedMotion: this._motionQuery.matches,
      };
    }

    _canAnimate() {
      return !this._destroyed && this.options.motion && !this._motionQuery.matches
        && !document.hidden && this.options.scene !== 'void' && this.options.intensity > 0;
    }

    _syncAnimation(redraw = true) {
      if (this._destroyed) return;
      if (this._canAnimate()) {
        if (this._raf === null) {
          this._lastTime = null;
          this._lastPaint = -Infinity;
          this._raf = requestAnimationFrame(this._tick);
        }
      } else {
        if (this._raf !== null) cancelAnimationFrame(this._raf);
        this._raf = null;
        this._lastTime = null;
        if (redraw && !document.hidden) this._render();
      }
    }

    _tick(now) {
      this._raf = null;
      if (!this._canAnimate()) return;
      if (this._lastTime !== null) this._elapsed += Math.min((now - this._lastTime) / 1000, 0.1);
      this._lastTime = now;
      if (now - this._lastPaint >= FRAME_MS - 0.5) {
        this._render();
        this._lastPaint = now;
      }
      this._raf = requestAnimationFrame(this._tick);
    }

    _render() {
      if (this._destroyed) return;
      const ctx = this.ctx;
      const { width: w, height: h } = this;
      const p = PALETTES[this.options.palette];
      const intensity = this.options.intensity;
      const state = this.options.state;
      const energy = state === 'ultra' ? 1 : state === 'max' ? 0.72 : state === 'working' ? 0.46 : 0.14;
      const t = this._elapsed;
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
      ctx.shadowBlur = 0;
      ctx.fillStyle = this.options.scene === 'void' ? '#050609' : color(p.background);
      ctx.fillRect(0, 0, w, h);
      this._frames++;
      if (this.options.scene === 'void' || intensity === 0) return;

      this._atmosphere(ctx, p, t, intensity, energy);
      if (this.options.scene !== 'embers') this._aurora(ctx, p, t, intensity, energy);
      this._starfield(ctx, p, t, intensity, energy);
      if (this.options.scene === 'dragon') this._dragon(ctx, p, t, intensity, energy);
      if (this.options.scene === 'embers' || energy > 0.2) {
        this._floatingEmbers(ctx, p, t, intensity, energy);
      }
      this._vignette(ctx, w, h);
    }

    _atmosphere(ctx, p, t, intensity, energy) {
      const { width: w, height: h } = this;
      const glows = [
        [0.82, 0.39, 0.52, 0.058 + energy * 0.035, p.primary],
        [0.89, 0.16, 0.37, 0.075 + energy * 0.025, p.secondary],
        [0.53, 0.90, 0.55, 0.034, p.secondary],
      ];
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      for (const [x, y, radius, strength, tint] of glows) {
        const gradient = ctx.createRadialGradient(w * x, h * y, 0, w * x, h * y, Math.max(w, h) * radius);
        gradient.addColorStop(0, color(tint, strength * intensity));
        gradient.addColorStop(0.42, color(tint, strength * intensity * 0.38));
        gradient.addColorStop(1, color(tint, 0));
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, w, h);
      }
      // Elliptical pools of fog move independently of the stars and the rig.
      ctx.translate(w * (0.71 + Math.sin(t * 0.045) * 0.03), h * 0.84);
      ctx.scale(1, 0.19);
      const mist = ctx.createRadialGradient(0, 0, 0, 0, 0, w * 0.51);
      mist.addColorStop(0, color(p.primary, intensity * (0.038 + energy * 0.018)));
      mist.addColorStop(0.5, color(p.secondary, intensity * 0.026));
      mist.addColorStop(1, color(p.primary, 0));
      ctx.fillStyle = mist;
      ctx.fillRect(-w, -h * 4, w * 2, h * 8);
      ctx.restore();
    }

    _aurora(ctx, p, t, intensity, energy) {
      const { width: w, height: h } = this;
      const isAurora = this.options.scene === 'aurora';
      const amplitude = isAurora ? 1.85 : 1;
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      for (let i = 0; i < 4; i++) {
        const phase = t * (0.085 + i * 0.012) + i * 1.9;
        const offset = i * h * 0.039;
        const wave = Math.sin(phase) * h * 0.035;
        const gradient = ctx.createLinearGradient(w * 0.34, h * 0.04, w, h * 0.58);
        gradient.addColorStop(0, color(p.secondary, 0));
        gradient.addColorStop(0.35, color(i % 2 ? p.primary : p.secondary, 0.017 * intensity * amplitude));
        gradient.addColorStop(0.75, color(i % 2 ? p.secondary : p.primary, (0.031 + energy * 0.018) * intensity * amplitude));
        gradient.addColorStop(1, color(p.primary, 0));
        ctx.beginPath();
        ctx.moveTo(w * 0.31, -h * 0.04 + offset);
        ctx.bezierCurveTo(w * 0.51, h * (0.05 + i * 0.006) + wave, w * 0.62, h * 0.44 + offset, w * 1.05, h * 0.16 + offset);
        ctx.bezierCurveTo(w * 0.91, h * 0.47 + offset, w * 0.61, h * 0.32 + wave, w * 0.31, -h * 0.04 + offset);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(w * 0.30, -h * 0.05 + offset);
        ctx.bezierCurveTo(w * 0.53, h * 0.05 + wave, w * 0.63, h * 0.44 + offset, w * 1.04, h * 0.16 + offset);
        ctx.strokeStyle = color(i % 2 ? p.primary : p.secondary, intensity * (0.034 + energy * 0.025) * amplitude);
        ctx.lineWidth = 0.8 + i * 0.45;
        ctx.stroke();
      }
      ctx.restore();
    }

    _starfield(ctx, p, t, intensity, energy) {
      const { width: w, height: h } = this;
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      for (let i = 0; i < this._stars.length; i++) {
        const star = this._stars[i];
        const x = fract(star.x + t * star.depth * 0.0009) * w;
        const y = fract(star.y - t * star.depth * 0.00035) * h;
        const rightBias = 0.21 + Math.pow(x / w, 1.8) * 0.79;
        const twinkle = 0.60 + Math.sin(t * star.speed + star.phase) * 0.25;
        const alpha = intensity * rightBias * twinkle * (0.33 + energy * 0.14);
        ctx.fillStyle = color(i % 4 ? p.primary : p.secondary, alpha);
        ctx.beginPath();
        ctx.arc(x, y, star.radius * (0.65 + star.depth * 0.5), 0, TAU);
        ctx.fill();
        if (i % 17 === 0 && x > w * 0.58) {
          const r = 2.4 + star.radius;
          ctx.strokeStyle = color(p.primary, alpha * 0.55);
          ctx.lineWidth = 0.6;
          ctx.beginPath();
          ctx.moveTo(x - r, y);
          ctx.lineTo(x + r, y);
          ctx.moveTo(x, y - r);
          ctx.lineTo(x, y + r);
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    _floatingEmbers(ctx, p, t, intensity, energy) {
      const { width: w, height: h } = this;
      const fullScene = this.options.scene === 'embers';
      const count = fullScene ? this._embers.length : Math.round(16 + energy * 35);
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      for (let i = 0; i < count; i++) {
        const ember = this._embers[i];
        const progress = fract(ember.y + t * (0.012 + energy * 0.009) * ember.speed);
        const y = (1.08 - progress * 1.20) * h;
        const x = (fullScene ? ember.x : 0.58 + ember.x * 0.43) * w
          + Math.sin(t * 0.22 + ember.phase) * (12 + ember.drift * 17);
        const fade = Math.sin(progress * Math.PI);
        const alpha = fade * intensity * (fullScene ? 0.57 : 0.32) * (0.65 + Math.sin(t * 2 + ember.phase) * 0.2);
        const tint = i % 3 ? p.ember : p.secondary;
        ctx.fillStyle = color(tint, alpha);
        ctx.beginPath();
        ctx.ellipse(x, y, ember.radius * 0.58, ember.radius * (1.1 + energy * 0.8), 0.35, 0, TAU);
        ctx.fill();
        if (i % 5 === 0) {
          ctx.strokeStyle = color(tint, alpha * 0.35);
          ctx.lineWidth = 0.7;
          ctx.beginPath();
          ctx.moveTo(x, y + 1);
          ctx.lineTo(x + 1.2, y + 5 + energy * 3);
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    _dragon(ctx, p, t, intensity, energy) {
      const { width: w, height: h } = this;
      const scale = Math.min(w * 0.173, h * 0.286);
      const x = w * 0.795;
      const y = h * 0.395 + Math.sin(t * 0.67) * scale * 0.032;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(Math.sin(t * 0.39) * 0.012 - 0.025);
      ctx.scale(scale, scale);
      ctx.globalAlpha = intensity;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';

      // A faint halo puts the dragon in a volume of air, without a hard disk.
      const halo = ctx.createRadialGradient(0.1, -0.15, 0.06, 0.1, -0.15, 1.38);
      halo.addColorStop(0, color(p.primary, 0.025 + energy * 0.025));
      halo.addColorStop(0.4, color(p.secondary, 0.027 + energy * 0.021));
      halo.addColorStop(1, color(p.primary, 0));
      ctx.fillStyle = halo;
      ctx.fillRect(-1.4, -1.65, 3, 3.2);

      this._wing(ctx, p, t, energy, 1, false);
      this._tail(ctx, p, t, energy);
      this._leg(ctx, p, t, false, true);
      this._wing(ctx, p, t, energy, -1, true);
      this._body(ctx, p, t, energy);
      this._leg(ctx, p, t, false, false);
      this._leg(ctx, p, t, true, false);
      this._head(ctx, p, t, energy);
      if (this.options.state !== 'idle') this._dragonBreath(ctx, p, t, energy);
      this._orbitingMotes(ctx, p, t, energy);
      ctx.restore();
    }

    _wing(ctx, p, t, energy, side, near) {
      const phase = t * (1.32 + energy * 0.34) + (near ? 0 : 0.32);
      const flap = Math.sin(phase);
      const spread = (near ? 0.90 : 0.91) + flap * (near ? 0.10 : 0.07);
      const lift = Math.cos(phase) * (0.09 + energy * 0.026);
      const deform = (x, y) => point(x * spread, y + Math.abs(x) * lift);
      const root = point(0.055, 0.025);
      const elbow = deform(side * 0.29, -0.35);
      const wrist = deform(side * 0.51, -0.73);
      const tip = deform(side * 1.06, -1.105);
      const fingers = [deform(side * 1.03, -0.39), deform(side * 0.75, -0.015), deform(side * 0.41, 0.20)];
      const valleys = [deform(side * 0.70, -0.64), deform(side * 0.46, -0.32), deform(side * 0.25, -0.025)];
      const tint = near ? p.secondary : p.primary;
      ctx.save();
      ctx.globalAlpha *= near ? 1 : 0.68;
      const membrane = ctx.createLinearGradient(tip.x, tip.y, root.x, root.y + 0.15);
      membrane.addColorStop(0, color(tint, 0.15 + energy * 0.07));
      membrane.addColorStop(0.36, color(p.wing, 0.73));
      membrane.addColorStop(0.70, color(mix(p.wing, p.primary, 0.15), 0.34));
      membrane.addColorStop(1, color(p.body, 0.75));
      ctx.beginPath();
      ctx.moveTo(root.x, root.y);
      ctx.quadraticCurveTo(elbow.x, elbow.y, wrist.x, wrist.y);
      ctx.lineTo(tip.x, tip.y);
      for (let i = 0; i < fingers.length; i++) {
        ctx.quadraticCurveTo(valleys[i].x, valleys[i].y, fingers[i].x, fingers[i].y);
      }
      ctx.quadraticCurveTo(side * 0.22, 0.01, root.x, root.y);
      ctx.closePath();
      ctx.fillStyle = membrane;
      ctx.fill();
      ctx.strokeStyle = color(tint, 0.38 + energy * 0.16);
      ctx.lineWidth = 0.007;
      ctx.stroke();

      // The spars articulate at the elbow and wrist; the membranes follow them.
      ctx.shadowColor = color(tint, 0.4);
      ctx.shadowBlur = 5 + energy * 5;
      ctx.strokeStyle = color(mix(tint, p.primary, 0.33), 0.60);
      ctx.lineWidth = near ? 0.017 : 0.012;
      ctx.beginPath();
      ctx.moveTo(root.x, root.y);
      ctx.quadraticCurveTo(elbow.x, elbow.y, wrist.x, wrist.y);
      ctx.lineTo(tip.x, tip.y);
      ctx.stroke();
      ctx.shadowBlur = 0;
      for (let i = 0; i < fingers.length; i++) {
        const finger = fingers[i];
        ctx.strokeStyle = color(tint, near ? 0.48 : 0.36);
        ctx.lineWidth = 0.008 - i * 0.0012;
        ctx.beginPath();
        ctx.moveTo(wrist.x, wrist.y);
        ctx.quadraticCurveTo((wrist.x + finger.x) * 0.54, (wrist.y + finger.y) * 0.53, finger.x, finger.y);
        ctx.stroke();
        // A few fine veins give the translucent skin a scale beyond its outline.
        for (let vein = 1; vein < 4; vein++) {
          const start = lerpPoint(wrist, finger, vein / 4);
          const end = lerpPoint(valleys[i], finger, 0.22 + vein * 0.16);
          ctx.strokeStyle = color(tint, 0.10);
          ctx.lineWidth = 0.003;
          ctx.beginPath();
          ctx.moveTo(start.x, start.y);
          ctx.quadraticCurveTo(end.x * 0.92, start.y * 0.3 + end.y * 0.7, end.x, end.y);
          ctx.stroke();
        }
      }
      // A hooked thumb at each wrist makes the wings read as bat-like anatomy.
      ctx.beginPath();
      ctx.moveTo(wrist.x - side * 0.018, wrist.y + 0.018);
      ctx.quadraticCurveTo(wrist.x - side * 0.08, wrist.y - 0.105, wrist.x - side * 0.004, wrist.y - 0.155);
      ctx.quadraticCurveTo(wrist.x - side * 0.03, wrist.y - 0.08, wrist.x + side * 0.025, wrist.y - 0.01);
      ctx.fillStyle = color(mix(tint, [220, 229, 243], 0.25), 0.70);
      ctx.fill();
      ctx.restore();
    }

    _tail(ctx, p, t, energy) {
      const sway = Math.sin(t * 0.83 - 0.7) * 0.038;
      const segments = [
        [point(0.12, 0.30), point(0.24, 0.58), point(0.54, 0.81 + sway), point(0.81, 0.71 + sway)],
        [point(0.81, 0.71 + sway), point(1.08, 0.61), point(1.10, 0.31 - sway), point(0.85, 0.32 - sway)],
        [point(0.85, 0.32 - sway), point(0.67, 0.30 - sway), point(0.60, 0.44), point(0.68, 0.49 + sway)],
      ];
      const spine = [];
      for (let segment = 0; segment < segments.length; segment++) {
        for (let i = 0; i <= 16; i++) {
          if (segment && i === 0) continue;
          spine.push(cubic(...segments[segment], i / 16));
        }
      }
      const edgeA = [];
      const edgeB = [];
      for (let i = 0; i < spine.length; i++) {
        const previous = spine[Math.max(0, i - 1)];
        const next = spine[Math.min(spine.length - 1, i + 1)];
        const dx = next.x - previous.x;
        const dy = next.y - previous.y;
        const length = Math.hypot(dx, dy) || 1;
        const thickness = 0.065 * Math.pow(1 - i / (spine.length - 1), 1.25) + 0.002;
        edgeA.push(point(spine[i].x - dy / length * thickness, spine[i].y + dx / length * thickness));
        edgeB.push(point(spine[i].x + dy / length * thickness, spine[i].y - dx / length * thickness));
      }
      const gradient = ctx.createLinearGradient(0.12, 0.3, 0.92, 0.78);
      gradient.addColorStop(0, color(p.body));
      gradient.addColorStop(0.54, color(mix(p.body, p.primary, 0.19)));
      gradient.addColorStop(1, color(mix(p.body, p.secondary, 0.13)));
      ctx.beginPath();
      ctx.moveTo(edgeA[0].x, edgeA[0].y);
      for (const v of edgeA.slice(1)) ctx.lineTo(v.x, v.y);
      for (const v of edgeB.reverse()) ctx.lineTo(v.x, v.y);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
      ctx.strokeStyle = color(p.primary, 0.44 + energy * 0.10);
      ctx.lineWidth = 0.005;
      ctx.stroke();
      ctx.strokeStyle = color(p.secondary, 0.35);
      ctx.lineWidth = 0.004;
      strokeCurve(ctx, spine);
      for (let i = 6; i < 32; i += 3) {
        const a = spine[i];
        const b = spine[i + 1];
        const size = 0.039 * (1 - i / spine.length);
        ctx.fillStyle = color(i % 2 ? p.primary : p.secondary, 0.45);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y - size * 0.55);
        ctx.lineTo(a.x - size * 0.75, a.y - size * 2);
        ctx.lineTo(b.x, b.y - size * 0.45);
        ctx.closePath();
        ctx.fill();
      }
      const tip = spine[spine.length - 1];
      ctx.beginPath();
      ctx.moveTo(tip.x - 0.015, tip.y - 0.022);
      ctx.lineTo(tip.x + 0.08, tip.y + 0.015);
      ctx.lineTo(tip.x + 0.032, tip.y + 0.018);
      ctx.lineTo(tip.x + 0.03, tip.y + 0.077);
      ctx.lineTo(tip.x - 0.025, tip.y + 0.015);
      ctx.closePath();
      ctx.fillStyle = color(p.secondary, 0.56);
      ctx.fill();
      ctx.strokeStyle = color(p.primary, 0.57);
      ctx.lineWidth = 0.003;
      ctx.stroke();
    }

    _body(ctx, p, t, energy) {
      // Raised dorsal plates follow the S of the neck into the back.
      const plates = [
        [-0.207, -0.347, 0.070], [-0.193, -0.27, 0.07], [-0.153, -0.20, 0.077],
        [-0.071, -0.15, 0.085], [0.04, -0.10, 0.082], [0.13, -0.019, 0.067],
        [0.189, 0.075, 0.061], [0.205, 0.17, 0.053], [0.188, 0.26, 0.044],
      ];
      for (const [x, y, size] of plates) {
        ctx.beginPath();
        ctx.moveTo(x - 0.012, y + 0.03);
        ctx.lineTo(x + size, y - size * 0.85);
        ctx.quadraticCurveTo(x + size * 0.7, y + 0.024, x + 0.005, y + 0.054);
        ctx.closePath();
        ctx.fillStyle = color(mix(p.body, p.secondary, 0.35), 0.98);
        ctx.fill();
        ctx.strokeStyle = color(p.secondary, 0.60);
        ctx.lineWidth = 0.004;
        ctx.stroke();
      }
      const skin = ctx.createLinearGradient(-0.25, -0.25, 0.23, 0.30);
      skin.addColorStop(0, color(mix(p.body, p.primary, 0.24)));
      skin.addColorStop(0.38, color(p.body));
      skin.addColorStop(0.74, color(mix(p.body, p.primary, 0.20)));
      skin.addColorStop(1, color(p.body));
      ctx.beginPath();
      ctx.moveTo(0.12, 0.35);
      ctx.bezierCurveTo(0.30, 0.20, 0.20, -0.04, 0.04, -0.13);
      ctx.bezierCurveTo(-0.045, -0.19, -0.22, -0.17, -0.20, -0.32);
      ctx.bezierCurveTo(-0.17, -0.43, -0.24, -0.50, -0.36, -0.47);
      ctx.lineTo(-0.42, -0.397);
      ctx.bezierCurveTo(-0.24, -0.37, -0.39, -0.23, -0.235, -0.073);
      ctx.bezierCurveTo(-0.16, 0.022, -0.074, 0.095, -0.088, 0.224);
      ctx.bezierCurveTo(-0.112, 0.393, 0.016, 0.449, 0.12, 0.35);
      ctx.closePath();
      ctx.fillStyle = skin;
      ctx.fill();
      ctx.strokeStyle = color(p.primary, 0.58 + energy * 0.11);
      ctx.lineWidth = 0.008;
      ctx.stroke();

      // The belly is a separate ribbon with articulated overlapping plates.
      ctx.beginPath();
      ctx.moveTo(-0.349, -0.391);
      ctx.bezierCurveTo(-0.215, -0.347, -0.346, -0.237, -0.190, -0.064);
      ctx.bezierCurveTo(-0.088, 0.039, -0.026, 0.193, 0.061, 0.347);
      ctx.strokeStyle = color(p.primary, 0.20);
      ctx.lineWidth = 0.036;
      ctx.stroke();
      const belly = [
        [-0.286, -0.335, -0.237, -0.326], [-0.286, -0.275, -0.224, -0.27],
        [-0.265, -0.212, -0.192, -0.216], [-0.227, -0.151, -0.143, -0.162],
        [-0.180, -0.088, -0.098, -0.115], [-0.134, -0.021, -0.044, -0.059],
        [-0.095, 0.051, 0.002, 0.017], [-0.064, 0.125, 0.042, 0.097],
        [-0.029, 0.204, 0.078, 0.173], [0.013, 0.284, 0.110, 0.246],
      ];
      ctx.strokeStyle = color(p.primary, 0.40);
      ctx.lineWidth = 0.005;
      for (const [x1, y1, x2, y2] of belly) {
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.quadraticCurveTo((x1 + x2) / 2, y1 + 0.009, x2, y2);
        ctx.stroke();
      }
      ctx.strokeStyle = color(p.secondary, 0.31);
      ctx.lineWidth = 0.004;
      for (let row = 0; row < 6; row++) {
        for (let column = 0; column < 3; column++) {
          const x = 0.012 + column * 0.04 + Math.sin(row * 0.52) * 0.01;
          const y = -0.035 + row * 0.049 + (column % 2) * 0.019;
          ctx.beginPath();
          ctx.moveTo(x - 0.012, y);
          ctx.quadraticCurveTo(x + 0.008, y + 0.022, x + 0.021, y);
          ctx.stroke();
        }
      }
    }

    _leg(ctx, p, t, front, far) {
      const sway = Math.sin(t * 0.73 + (front ? 0.7 : -0.5)) * 0.016;
      ctx.save();
      if (far) {
        ctx.translate(0.064, -0.018);
        ctx.globalAlpha *= 0.48;
      }
      const outline = front
        ? [point(-0.07, 0.04), point(-0.225, 0.20 + sway), point(-0.25, 0.29 + sway), point(-0.355, 0.28 + sway)]
        : [point(0.095, 0.245), point(0.287, 0.39 + sway), point(0.198, 0.523 + sway), point(0.095, 0.524 + sway)];
      ctx.strokeStyle = color(p.primary, 0.50);
      ctx.lineWidth = front ? 0.052 : 0.086;
      strokeCurve(ctx, outline);
      ctx.strokeStyle = color(mix(p.body, p.primary, 0.09));
      ctx.lineWidth = front ? 0.038 : 0.069;
      strokeCurve(ctx, outline);
      ctx.strokeStyle = color(p.secondary, 0.35);
      ctx.lineWidth = 0.005;
      strokeCurve(ctx, outline.map(v => point(v.x + 0.008, v.y - 0.008)));
      const foot = outline[outline.length - 1];
      ctx.strokeStyle = color(mix(p.primary, [243, 233, 243], 0.32), 0.78);
      ctx.lineWidth = 0.007;
      for (let claw = 0; claw < 3; claw++) {
        ctx.beginPath();
        ctx.moveTo(foot.x + claw * 0.025, foot.y - 0.006);
        ctx.quadraticCurveTo(foot.x - 0.028 + claw * 0.025, foot.y + 0.016, foot.x - 0.022 + claw * 0.025, foot.y + 0.037);
        ctx.stroke();
      }
      ctx.restore();
    }

    _head(ctx, p, t, energy) {
      ctx.save();
      ctx.translate(-0.292, -0.414);
      ctx.rotate(Math.sin(t * 0.63 + 0.2) * 0.025);
      // Horns curve backwards from the brow; the nearer one catches the rim light.
      const horns = [
        [0.012, -0.095, 0.185, -0.275, 0.087, -0.082],
        [-0.112, -0.105, -0.035, -0.280, -0.035, -0.097],
      ];
      for (let i = 0; i < horns.length; i++) {
        const [x1, y1, x2, y2, x3, y3] = horns[i];
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.quadraticCurveTo(x2 * 0.83, y2 * 0.72, x2, y2);
        ctx.quadraticCurveTo(x2 * 0.61, y2 * 0.67, x3, y3);
        ctx.closePath();
        ctx.fillStyle = color(mix(p.body, i ? p.primary : p.secondary, 0.42));
        ctx.fill();
        ctx.strokeStyle = color(i ? p.primary : p.secondary, 0.77);
        ctx.lineWidth = 0.005;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.moveTo(-0.18, -0.093);
      ctx.lineTo(-0.33, -0.021);
      ctx.lineTo(-0.385, 0.026);
      ctx.quadraticCurveTo(-0.40, 0.058, -0.328, 0.082);
      ctx.lineTo(-0.122, 0.105);
      ctx.lineTo(-0.023, 0.113);
      ctx.lineTo(0.071, 0.034);
      ctx.lineTo(0.014, -0.095);
      ctx.lineTo(-0.070, -0.149);
      ctx.closePath();
      const face = ctx.createLinearGradient(-0.21, -0.12, -0.10, 0.12);
      face.addColorStop(0, color(mix(p.body, p.primary, 0.34)));
      face.addColorStop(0.48, color(mix(p.body, p.primary, 0.12)));
      face.addColorStop(1, color(p.body));
      ctx.fillStyle = face;
      ctx.fill();
      ctx.strokeStyle = color(p.primary, 0.78);
      ctx.lineWidth = 0.006;
      ctx.stroke();

      // Cheek fins and the angular brow preserve a readable silhouette at small sizes.
      ctx.fillStyle = color(mix(p.body, p.secondary, 0.33));
      ctx.strokeStyle = color(p.secondary, 0.68);
      ctx.lineWidth = 0.004;
      for (let fin = 0; fin < 3; fin++) {
        ctx.beginPath();
        ctx.moveTo(0.015, -0.040 + fin * 0.040);
        ctx.lineTo(0.152 - fin * 0.017, -0.035 + fin * 0.067);
        ctx.lineTo(0.047, 0.017 + fin * 0.030);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.moveTo(-0.337, 0.058);
      ctx.quadraticCurveTo(-0.22, 0.070, -0.085, 0.061);
      ctx.strokeStyle = color(p.primary, 0.71);
      ctx.lineWidth = 0.004;
      ctx.stroke();
      for (let tooth = 0; tooth < 3; tooth++) {
        const x = -0.26 + tooth * 0.061;
        ctx.beginPath();
        ctx.moveTo(x, 0.060);
        ctx.lineTo(x + 0.011, 0.085 - tooth * 0.003);
        ctx.lineTo(x + 0.020, 0.061);
        ctx.closePath();
        ctx.fillStyle = color(mix(p.primary, [240, 240, 250], 0.55), 0.76);
        ctx.fill();
      }
      ctx.fillStyle = color(p.primary, 0.52 + energy * 0.18);
      ctx.beginPath();
      ctx.ellipse(-0.335, 0.011, 0.012, 0.007, -0.32, 0, TAU);
      ctx.fill();

      // The eye gets a compact halo, a swept lid, and a vertical dark pupil.
      const eyeX = -0.179;
      const eyeY = -0.033;
      const eyeGlow = ctx.createRadialGradient(eyeX, eyeY, 0, eyeX, eyeY, 0.075);
      eyeGlow.addColorStop(0, color(p.primary, 0.52 + energy * 0.25));
      eyeGlow.addColorStop(0.25, color(p.primary, 0.22));
      eyeGlow.addColorStop(1, color(p.primary, 0));
      ctx.fillStyle = eyeGlow;
      ctx.fillRect(eyeX - 0.08, eyeY - 0.08, 0.16, 0.16);
      ctx.beginPath();
      ctx.moveTo(eyeX - 0.041, eyeY + 0.004);
      ctx.quadraticCurveTo(eyeX, eyeY - 0.019, eyeX + 0.029, eyeY - 0.005);
      ctx.quadraticCurveTo(eyeX, eyeY + 0.025, eyeX - 0.041, eyeY + 0.004);
      ctx.fillStyle = color(mix(p.primary, [250, 253, 255], 0.45));
      ctx.shadowColor = color(p.primary);
      ctx.shadowBlur = 8 + energy * 7;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = color(p.body);
      ctx.beginPath();
      ctx.ellipse(eyeX - 0.002, eyeY + 0.003, 0.003, 0.014, 0.14, 0, TAU);
      ctx.fill();
      ctx.strokeStyle = color(p.secondary, 0.74);
      ctx.lineWidth = 0.007;
      ctx.beginPath();
      ctx.moveTo(-0.238, -0.050);
      ctx.lineTo(-0.142, -0.078);
      ctx.lineTo(-0.088, -0.035);
      ctx.stroke();
      ctx.restore();
    }

    _dragonBreath(ctx, p, t, energy) {
      const length = 0.35 + energy * 0.28;
      const cadence = 0.62 + Math.sin(t * 0.9) * 0.22;
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      // Thin vapor ribbons join the otherwise independent luminous embers.
      for (let i = 0; i < 3; i++) {
        const wave = Math.sin(t * 1.6 + i) * 0.018;
        const gradient = ctx.createLinearGradient(-0.66, -0.365, -0.66 - length, -0.43);
        gradient.addColorStop(0, color(p.primary, (0.10 + energy * 0.16) * cadence));
        gradient.addColorStop(0.25, color(i % 2 ? p.secondary : p.primary, 0.10 * cadence));
        gradient.addColorStop(1, color(p.secondary, 0));
        ctx.beginPath();
        ctx.moveTo(-0.67, -0.365 + i * 0.006);
        ctx.bezierCurveTo(-0.80, -0.375 + wave, -0.85 - length * 0.2, -0.47 - i * 0.016, -0.67 - length, -0.43 + wave);
        ctx.strokeStyle = gradient;
        ctx.lineWidth = 0.012 + i * 0.012;
        ctx.stroke();
      }
      const count = Math.round(12 + energy * 22);
      for (let i = 0; i < count; i++) {
        const spark = this._breath[i];
        const age = fract(t * (0.37 + energy * 0.1) * spark.speed + spark.phase);
        const x = -0.665 - age * length;
        const y = -0.367 - age * 0.09 + spark.spread * age * 0.12 + Math.sin(t * 2 + spark.wave) * age * 0.015;
        const alpha = Math.sin(age * Math.PI) * (0.52 + energy * 0.33) * cadence;
        ctx.fillStyle = color(i % 3 ? p.primary : p.secondary, alpha);
        ctx.beginPath();
        ctx.arc(x, y, spark.radius * (1 - age * 0.45), 0, TAU);
        ctx.fill();
      }
      ctx.restore();
    }

    _orbitingMotes(ctx, p, t, energy) {
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      const count = this.options.state === 'ultra' ? 15 : 7;
      for (let i = 0; i < count; i++) {
        const phase = t * (0.09 + i * 0.003) + i * 2.39996;
        const x = 0.17 + Math.cos(phase) * (0.85 + (i % 3) * 0.11);
        const y = 0.05 + Math.sin(phase) * (0.67 + (i % 4) * 0.075);
        const alpha = (0.14 + energy * 0.25) * (0.6 + Math.sin(t * 1.3 + i) * 0.25);
        ctx.fillStyle = color(i % 2 ? p.primary : p.secondary, alpha);
        ctx.beginPath();
        ctx.arc(x, y, 0.003 + (i % 3) * 0.0014, 0, TAU);
        ctx.fill();
      }
      ctx.restore();
    }

    _vignette(ctx, w, h) {
      ctx.save();
      const vignette = ctx.createRadialGradient(w * 0.62, h * 0.42, Math.min(w, h) * 0.22, w * 0.60, h * 0.43, Math.max(w, h) * 0.74);
      vignette.addColorStop(0, 'rgba(0,0,0,0)');
      vignette.addColorStop(0.70, 'rgba(0,0,0,0.10)');
      vignette.addColorStop(1, 'rgba(0,0,0,0.65)');
      ctx.fillStyle = vignette;
      ctx.fillRect(0, 0, w, h);
      // Preserve contrast in the part of the terminal where most lines begin.
      const readable = ctx.createLinearGradient(0, 0, w * 0.68, 0);
      readable.addColorStop(0, 'rgba(3,5,9,0.36)');
      readable.addColorStop(0.58, 'rgba(3,5,9,0.16)');
      readable.addColorStop(1, 'rgba(3,5,9,0)');
      ctx.fillStyle = readable;
      ctx.fillRect(0, 0, w * 0.68, h);
      ctx.restore();
    }
  }

  window.DragonScene = DragonScene;
})();
