/* Replay player: plays back the run the grader recorded for a board entry.
 *
 * Nothing is simulated here. The referee records the graded run (grader bundle
 * replay_recorder.py, format v1), the builder publishes the best run's copy as
 * data/<row.replay>, and this draws it on a canvas. The board script only calls
 * RRReplay.attach(boardElement, doc) after it renders a table.
 *
 * It is a race: one clock, zero when every car crosses the start line of its
 * ranked lap, running until the slowest car has finished, then starting over.
 * The watched car drives in colour, the rest of the board greyed out, the TA
 * reference always picked out. Hover a car to name it, click it (or pick it
 * from the list) to watch that one instead; the clock and the view stay put.
 *
 * A recording is numbers only; every name shown comes from the board and is
 * set with textContent or drawn with fillText.
 */
(function () {
  "use strict";
  const CAR = { length: 0.58, width: 0.31, ahead: 0.165 };  // the gym's contact box; the pose is the rear axle
  const PRE_ROLL = 1.0;                                     // seconds of race clock before the start line
  const TAIL = 1.0;                                         // ... and after the last car finishes
  const LOOP_PAUSE = 1500;                                  // ms the finished race stays up before it restarts
  const SPEEDS = [0.25, 0.5, 1, 2, 4, 8];
  const PATHS_KEY = "rr-replay-paths";                     // the viewer's Paths choice, kept across visits
  const PICK_RADIUS = 18;                                   // CSS px around a car that counts as pointing at it
  const cache = new Map();                                  // url -> Promise<run>
  let maps = null, dialog = null, ui = null, state = null;

  const fetchJSON = (url) => fetch(url, { cache: "force-cache" }).then((r) => {
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  });

  function decode(doc) {
    if (!doc || doc.v !== 1 || !(doc.hz > 0) || !Array.isArray(doc.x)) throw new Error("unknown recording format");
    const column = (values, scale) => {
      const out = new Float64Array(values.length);
      let total = 0;
      for (let i = 0; i < values.length; i++) { total += values[i]; out[i] = total / scale; }
      return out;
    };
    const run = { map: String(doc.map), hz: doc.hz, x: column(doc.x, 100), y: column(doc.y, 100),
                  yaw: column(doc.yaw, 100), v: column(doc.s, 100), rerun: doc.rerun === 1 };
    run.n = Math.min(run.x.length, run.y.length, run.yaw.length, run.v.length);
    if (run.n < 2) throw new Error("empty recording");
    for (let i = 0; i < run.n; i++) run.yaw[i] *= Math.PI / 180;
    run.duration = (run.n - 1) / run.hz;
    run.laps = (Array.isArray(doc.laps) ? doc.laps : [])
      .filter((l) => Array.isArray(l) && l[0] >= 0 && l[1] < run.n && l[1] > l[0])
      .map((l, k) => ({ t0: l[0] / run.hz, t1: l[1] / run.hz, ms: (doc.lap_ms || [])[k] }));
    run.best = Number.isInteger(doc.best) && run.laps[doc.best] ? doc.best : (run.laps.length ? 0 : -1);
    // the race clock is zero at `start`; the car has finished `finish` seconds later
    run.start = run.best >= 0 ? run.laps[run.best].t0 : 0;
    // ... by the official lap time when the recording carries it: sample indices round to
    // 1/hz s, enough to swap the finishing order of two cars a few hundredths apart
    const ranked = run.best >= 0 ? run.laps[run.best] : null;
    run.finish = !ranked ? run.duration : ranked.ms > 0 ? ranked.ms / 1000 : ranked.t1 - run.start;
    // colour scale: the speed range of the timed laps (a standing start would
    // stretch it to zero and leave a fast lap one flat colour)
    const first = run.laps.length ? Math.round(run.laps[0].t0 * run.hz) : Math.min(run.n - 1, Math.round(2 * run.hz));
    run.vmin = Infinity; run.vmax = -Infinity;
    for (let i = first; i < run.n; i++) { run.vmin = Math.min(run.vmin, run.v[i]); run.vmax = Math.max(run.vmax, run.v[i]); }
    if (!(run.vmax - run.vmin > 0.2)) { run.vmin = 0; run.vmax = Math.max(run.vmax, 0.2); }
    return run;
  }

  function load(path) {
    const url = (window.RR_DATA_BASE || "data/") + path;      // an archived term keeps its recordings beside its boards
    if (!cache.has(url)) {
      const p = Promise.all([fetchJSON(url), maps || (maps = fetchJSON("assets/maps/maps.json"))])
        .then(([doc]) => decode(doc));
      p.catch(() => cache.delete(url));
      cache.set(url, p);
    }
    return cache.get(url);
  }

  // a car waits at its first sample before its recording begins and rests at its last one after it ends
  const timeOf = (run, rel) => Math.min(run.duration, Math.max(0, run.start + rel));
  function poseAt(run, t) {
    const f = t * run.hz;
    const i = Math.min(Math.floor(f), run.n - 2), a = f - i;
    return { x: run.x[i] + a * (run.x[i + 1] - run.x[i]), y: run.y[i] + a * (run.y[i + 1] - run.y[i]),
             yaw: run.yaw[i] + a * (run.yaw[i + 1] - run.yaw[i]), v: run.v[i] + a * (run.v[i + 1] - run.v[i]) };
  }

  // slow -> fast runs through the logo gradient: cyan, violet, magenta
  const STOPS = [[0, 209, 218], [124, 58, 237], [252, 0, 255]];
  const LUT = Array.from({ length: 48 }, (_, k) => {
    const f = (k / 47) * (STOPS.length - 1), i = Math.min(Math.floor(f), STOPS.length - 2), a = f - i;
    return "rgb(" + STOPS[i].map((c, j) => Math.round(c + a * (STOPS[i + 1][j] - c))).join(",") + ")";
  });
  const speedColor = (run, v) => LUT[Math.min(LUT.length - 1, Math.max(0, Math.round(((v - run.vmin) / (run.vmax - run.vmin)) * (LUT.length - 1))))];

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtTime = (s) => s.toFixed(2) + " s";
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const button = (cls, text) => { const b = el("button", cls, text); b.type = "button"; return b; };
  // Full screen is the real thing where the browser offers it for an element, and the player
  // filling the whole viewport where it does not (iPhones only ever full-screen a video).
  const isFull = () => document.fullscreenElement === ui.frame || dialog.classList.contains("rr-max");
  function setMax(on) {
    dialog.classList.toggle("rr-max", on);
    ui.full.textContent = isFull() ? "Exit full screen" : "Full screen";
    if (state) { layout(); draw(); }
  }

  function build() {
    dialog = el("dialog", "rr-player");
    dialog.setAttribute("aria-labelledby", "rr-player-title");
    ui = { frame: el("div", "rr-frame"), head: el("header"), title: el("h2"), sub: el("p", "sub") };
    const titles = el("div");
    ui.title.id = "rr-player-title";
    titles.append(ui.title, ui.sub);
    const actions = el("div", "rr-actions");
    ui.full = button("rr-full", "Full screen");
    ui.close = button("rr-close", "Close");
    actions.append(ui.full, ui.close);
    ui.head.append(titles, actions);

    const stage = el("div", "rr-stage");
    ui.canvas = el("canvas");
    ui.canvas.setAttribute("role", "img");
    ui.hud = el("div", "rr-hud");
    ui.speed = el("strong");
    ui.clock = el("span");
    ui.lap = el("span", "lap");
    ui.hud.append(ui.speed, ui.clock, ui.lap);
    ui.msg = el("p", "rr-msg");
    stage.append(ui.canvas, ui.hud, ui.msg);

    ui.bar = el("div", "rr-bar");
    ui.play = button("rr-play", "Play");
    ui.play.autofocus = true;                       // Space/Enter work the moment it opens
    ui.seek = el("input");
    Object.assign(ui.seek, { type: "range", min: 0, max: 1000, step: 1, value: 0 });
    ui.seek.setAttribute("aria-label", "Position in the race");
    ui.rate = el("select");
    ui.rate.setAttribute("aria-label", "Playback speed");
    SPEEDS.forEach((s) => { const o = el("option", "", s + "×"); o.value = s; ui.rate.append(o); });
    ui.rate.value = 1;
    ui.pick = el("select", "rr-pick");              // the field, for keyboards and touch screens
    ui.pick.setAttribute("aria-label", "Car to watch");
    ui.fieldLabel = el("label", "rr-ghost");
    ui.field = el("input");
    ui.field.type = "checkbox";
    ui.field.checked = true;
    ui.fieldLabel.append(ui.field, document.createTextNode(" Other cars"));
    ui.pathsLabel = el("label", "rr-ghost");
    ui.paths = el("input");
    ui.paths.type = "checkbox";
    ui.paths.checked = true;                                 // on unless this viewer turned it off
    try { ui.paths.checked = localStorage.getItem(PATHS_KEY) !== "0"; } catch (e) { /* storage blocked */ }
    ui.pathsLabel.title = "Draw where every car has driven so far";
    ui.pathsLabel.append(ui.paths, document.createTextNode(" Paths"));
    ui.share = button("rr-share", "Copy link");
    ui.bar.append(ui.play, ui.seek, ui.rate, ui.pick, ui.fieldLabel, ui.pathsLabel, ui.share);

    // the Fullscreen API refuses <dialog> itself, so everything lives in a frame that can take it
    ui.frame.append(ui.head, stage, ui.bar);
    dialog.append(ui.frame);
    document.body.append(dialog);

    ui.close.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", onClose);
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
    ui.play.addEventListener("click", toggle);
    ui.seek.addEventListener("input", () => {
      if (!state) return;
      state.rel = -PRE_ROLL + (ui.seek.value / 1000) * (state.raceEnd + PRE_ROLL);
      state.loopAt = 0;
      draw();
    });
    ui.rate.addEventListener("change", () => { if (state) state.rate = Number(ui.rate.value); });
    ui.field.addEventListener("change", () => { if (state) { race(); draw(); } });
    ui.paths.addEventListener("change", () => {
      try { localStorage.setItem(PATHS_KEY, ui.paths.checked ? "1" : "0"); } catch (e) { /* storage blocked */ }
      if (state && !state.playing) draw();
    });
    ui.pick.addEventListener("change", () => {
      const entry = state && state.fieldEntries.find((e) => e.name === ui.pick.value);
      if (entry) watch(entry);
    });
    ui.full.addEventListener("click", () => {
      if (document.fullscreenElement) return document.exitFullscreen();
      if (dialog.classList.contains("rr-max")) return setMax(false);
      const request = ui.frame.requestFullscreen || ui.frame.webkitRequestFullscreen;
      if (!request) return setMax(true);
      Promise.resolve(request.call(ui.frame)).catch(() => setMax(true));
    });
    document.addEventListener("fullscreenchange", () => {
      ui.full.textContent = isFull() ? "Exit full screen" : "Full screen";
      if (state) { layout(); draw(); }
    });
    ui.share.addEventListener("click", () => {
      const done = () => { ui.share.textContent = "Copied"; setTimeout(() => (ui.share.textContent = "Copy link"), 1500); };
      if (navigator.clipboard) navigator.clipboard.writeText(location.href).then(done, () => {});
    });
    dialog.addEventListener("keydown", (e) => {
      if (!state || e.target === ui.seek || e.target.tagName === "SELECT") return;
      if (e.key === " " && e.target.tagName !== "BUTTON") { e.preventDefault(); toggle(); }
      if (e.key === "f" || e.key === "F") ui.full.click();
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        state.rel = Math.min(state.raceEnd, Math.max(-PRE_ROLL, state.rel + (e.key === "ArrowLeft" ? -1 : 1)));
        state.loopAt = 0;
        draw();
      }
    });
    ui.canvas.addEventListener("pointermove", (e) => point(e, false));
    ui.canvas.addEventListener("pointerleave", () => { if (state && state.hover) { state.hover = null; ui.canvas.style.cursor = ""; draw(); } });
    ui.canvas.addEventListener("click", (e) => point(e, true));
    window.addEventListener("resize", () => { if (state) { layout(); draw(); } });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state) { layout(); draw(); } });
  }

  // The part of the track the first watched car covers, with a margin. It is kept
  // while cars are switched, so the track never moves under the viewer.
  function boxOf(run) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < run.n; i++) {
      x0 = Math.min(x0, run.x[i]); x1 = Math.max(x1, run.x[i]);
      y0 = Math.min(y0, run.y[i]); y1 = Math.max(y1, run.y[i]);
    }
    // room for the walls beside the driving line: a fixed few metres suit a hallway loop
    // and leave a 100 m circuit pressed against the edge, so the margin grows with the track
    const pad = Math.max(2.5, 0.06 * Math.max(x1 - x0, y1 - y0));
    return { x0: x0 - pad, x1: x1 + pad, y0: y0 - pad, y1: y1 + pad };
  }

  function layout() {
    const run = state.run, m = state.map, { x0, x1, y0, y1 } = state.box;
    const cssW = ui.canvas.parentElement.clientWidth;
    // full screen: everything the header, the readout strip and the controls leave over
    const strip = getComputedStyle(ui.hud).position === "static" && !ui.hud.hidden ? ui.hud.offsetHeight : 0;
    const around = ui.head.offsetHeight + ui.bar.offsetHeight + strip;
    const cssH = isFull() ? Math.max(120, ui.frame.clientHeight - around)
                          : Math.max(200, Math.min(cssW * (y1 - y0) / (x1 - x0), window.innerHeight * 0.58));
    const dpr = window.devicePixelRatio || 1;
    ui.canvas.style.height = cssH + "px";
    ui.canvas.width = Math.round(cssW * dpr);
    ui.canvas.height = Math.round(cssH * dpr);
    const scale = Math.min(ui.canvas.width / (x1 - x0), ui.canvas.height / (y1 - y0));
    const ox = (ui.canvas.width - scale * (x1 - x0)) / 2, oy = (ui.canvas.height - scale * (y1 - y0)) / 2;
    state.view = { scale, dpr, px: (x) => ox + (x - x0) * scale, py: (y) => ui.canvas.height - oy - (y - y0) * scale };

    // static layer: tinted walls + the watched car's whole line, faint
    const layer = document.createElement("canvas");
    layer.width = ui.canvas.width; layer.height = ui.canvas.height;
    const g = layer.getContext("2d"), v = state.view;
    if (state.mapImage) {
      const tint = document.createElement("canvas");
      tint.width = m.w; tint.height = m.h;
      const tg = tint.getContext("2d");
      tg.drawImage(state.mapImage, 0, 0);
      tg.globalCompositeOperation = "source-in";
      tg.fillStyle = css("--ink-2") || "#3b3f55";
      tg.fillRect(0, 0, m.w, m.h);
      g.imageSmoothingEnabled = true;
      // a large map drawn small turns its one-pixel walls into hairlines: draw it a few times,
      // nudged, so a wall is never thinner than about a pixel and a half on screen
      const grow = Math.max(0, (1.6 * dpr - m.res * scale) / 2);
      const nudges = grow > 0.05 ? [[0, 0], [grow, 0], [-grow, 0], [0, grow], [0, -grow], [grow, grow], [-grow, -grow], [grow, -grow], [-grow, grow]] : [[0, 0]];
      for (const [dx, dy] of nudges) {
        g.drawImage(tint, v.px(m.x0) + dx, v.py(m.y0 + m.h * m.res) + dy, m.w * m.res * scale, m.h * m.res * scale);
      }
    }
    g.lineWidth = 1.5 * dpr; g.strokeStyle = css("--line-strong") || "#c9cddc"; g.lineJoin = "round";
    g.beginPath();
    for (let i = 0; i < run.n; i++) g[i ? "lineTo" : "moveTo"](v.px(run.x[i]), v.py(run.y[i]));
    g.stroke();
    state.layer = layer;
  }

  function drawCar(g, pose, fill, stroke, opts) {
    const v = state.view, L = CAR.length * v.scale, W = CAR.width * v.scale;
    g.save();
    g.globalAlpha = (opts && opts.alpha) || 1;
    g.translate(v.px(pose.x), v.py(pose.y));
    g.rotate(-pose.yaw);
    g.translate(CAR.ahead * v.scale, 0);
    // never smaller than a fingertip-visible mark, whatever the zoom
    const k = Math.max(1, (9 * v.dpr) / L);
    g.scale(k, k);
    g.beginPath();
    g.rect(-L / 2, -W / 2, L, W);
    if (fill) { g.fillStyle = fill; g.fill(); }
    g.lineWidth = (((opts && opts.width) || 1.5) * v.dpr) / k; g.strokeStyle = stroke;
    if (opts && opts.dashed) g.setLineDash([3 * v.dpr / k, 2 * v.dpr / k]);
    g.stroke();
    g.setLineDash([]);
    g.beginPath();                                   // nose: which way it points
    g.moveTo(L / 2, 0); g.lineTo(L / 6, -W / 2); g.lineTo(L / 6, W / 2); g.closePath();
    g.fillStyle = stroke; g.fill();
    g.restore();
  }

  // a name plate above a car: solid, theme-aware, ringed so it reads over anything
  function tag(g, pose, text, back, ink, big) {
    const v = state.view, x = v.px(pose.x), y = v.py(pose.y) - 16 * v.dpr;
    g.save();
    g.font = `700 ${(big ? 13 : 11) * v.dpr}px ${css("--body") || "sans-serif"}`;
    const w = g.measureText(text).width + (big ? 16 : 10) * v.dpr, h = (big ? 24 : 17) * v.dpr;
    const left = Math.min(Math.max(x - w / 2, 3), ui.canvas.width - w - 3);
    let top = Math.max(y - h, 3);
    // the speed readout floats over the canvas's corner: a hover name that would land under it goes
    // below the car. Only the hover name: the TA tag stays above its car wherever it drives, or it
    // flips under and back as the TA car rounds the corner by the readout.
    if (big && getComputedStyle(ui.hud).position === "absolute" && !ui.hud.hidden) {
      const hl = (ui.hud.offsetLeft - 6) * v.dpr, ht = (ui.hud.offsetTop - 6) * v.dpr;
      const hr = (ui.hud.offsetLeft + ui.hud.offsetWidth + 6) * v.dpr, hb = (ui.hud.offsetTop + ui.hud.offsetHeight + 6) * v.dpr;
      if (left < hr && left + w > hl && top < hb && top + h > ht) top = v.py(pose.y) + 16 * v.dpr;
    }
    g.beginPath(); g.roundRect(left, top, w, h, 4 * v.dpr);
    g.fillStyle = back; g.fill();
    g.lineWidth = 2 * v.dpr; g.strokeStyle = css("--surface") || "#fff"; g.stroke();
    g.fillStyle = ink; g.textBaseline = "middle"; g.textAlign = "center";
    g.fillText(text, left + w / 2, top + h / 2 + v.dpr * 0.5);
    g.restore();
  }

  // The race lasts until the slowest car on show has finished its lap.
  function race() {
    let end = state.run.finish;
    for (const o of state.others) if (ui.field.checked || o.entry.isRef) end = Math.max(end, o.run.finish);
    state.raceEnd = end + TAIL;
    state.rel = Math.min(state.rel, state.raceEnd);
  }

  function currentLap(run, t) {
    for (let k = 0; k < run.laps.length; k++) if (t >= run.laps[k].t0 && t <= run.laps[k].t1) return k;
    return -1;
  }

  // where a car has driven so far: from the run-up to its pose at race time `rel`, never ahead of it
  function drawPath(g, run, rel, color, alpha, width) {
    const v = state.view, t = timeOf(run, rel), from = Math.floor(timeOf(run, -PRE_ROLL) * run.hz);
    const upto = Math.min(run.n - 1, Math.floor(t * run.hz));
    if (upto <= from) return;
    const end = poseAt(run, t);
    g.save();
    g.globalAlpha = alpha; g.strokeStyle = color; g.lineWidth = width * v.dpr; g.lineJoin = g.lineCap = "round";
    g.beginPath();
    g.moveTo(v.px(run.x[from]), v.py(run.y[from]));
    for (let i = from + 1; i <= upto; i++) g.lineTo(v.px(run.x[i]), v.py(run.y[i]));
    g.lineTo(v.px(end.x), v.py(end.y));
    g.stroke();
    g.restore();
  }

  function draw() {
    const run = state.run, v = state.view, g = ui.canvas.getContext("2d"), t = timeOf(run, state.rel);
    g.clearRect(0, 0, ui.canvas.width, ui.canvas.height);
    g.drawImage(state.layer, 0, 0);

    // the rest of the field's paths sit under everything else
    const grey = css("--ink-3") || "#62677f", ref = css("--ref") || "#c026d3", accent = css("--accent") || "#7c3aed";
    const surface = css("--surface") || "#fff", ink = css("--ink") || "#0b0c14";
    if (ui.paths.checked) {
      for (const o of state.others) {
        if (o.entry.isRef || o === state.hover || !ui.field.checked) continue;
        drawPath(g, o.run, state.rel, grey, 0.35, 1.25);
      }
      const taCar = state.others.find((o) => o.entry.isRef);
      if (taCar && taCar !== state.hover) drawPath(g, taCar.run, state.rel, ref, 0.6, 1.5);
      if (state.hover) drawPath(g, state.hover.run, state.rel, state.hover.entry.isRef ? ref : ink, 0.85, 2);
    }

    // the trail, coloured by speed: the last few seconds, or the whole run so far with Paths on
    const upto = Math.min(run.n - 1, Math.floor(t * run.hz));
    const from = ui.paths.checked ? Math.floor(timeOf(run, -PRE_ROLL) * run.hz) : Math.max(0, upto - Math.round(6 * run.hz));
    g.lineWidth = 3 * v.dpr; g.lineCap = "round";
    for (let i = from; i < upto; i++) {
      g.globalAlpha = ui.paths.checked ? 1 : 0.25 + 0.75 * ((i - from) / Math.max(1, upto - from));
      g.strokeStyle = speedColor(run, run.v[i]);
      g.beginPath(); g.moveTo(v.px(run.x[i]), v.py(run.y[i])); g.lineTo(v.px(run.x[i + 1]), v.py(run.y[i + 1])); g.stroke();
    }
    g.globalAlpha = 1;

    // the rest of the field, greyed out; the TA car and the one under the pointer stand out
    const field = state.others.map((o) => ({ o, pose: poseAt(o.run, timeOf(o.run, state.rel)) }));
    state.shown = field;
    for (const c of field) {
      if (c.o.entry.isRef || c.o === state.hover || !ui.field.checked) continue;
      drawCar(g, c.pose, null, grey, { alpha: 0.5, width: 1 });
    }
    const ta = field.find((c) => c.o.entry.isRef);
    if (ta && ta.o !== state.hover) drawCar(g, ta.pose, null, ref, { dashed: true, width: 1.75 });
    const pose = poseAt(run, t);
    drawCar(g, pose, surface, state.entry.isRef ? ref : accent, { width: 1.75 });
    if (ta && ta.o !== state.hover) tag(g, ta.pose, "TA", ref, css("--ref-ink") || "#fff", false);
    const hovered = field.find((c) => c.o === state.hover);
    if (hovered) {                                   // last, so nothing covers the name
      const isRef = hovered.o.entry.isRef;
      drawCar(g, hovered.pose, surface, isRef ? ref : ink, { width: 2 });
      tag(g, hovered.pose, hovered.o.entry.label, isRef ? ref : ink, isRef ? css("--ref-ink") || "#fff" : css("--bg") || "#fff", true);
    }

    const lap = currentLap(run, t), done = state.rel >= run.finish;
    ui.speed.textContent = pose.v.toFixed(1) + " m/s";
    ui.clock.textContent = state.rel < 0 ? "run-up" : done ? "finished" : fmtTime(state.rel);
    const k = done ? run.best : lap;
    ui.lap.textContent = k < 0 ? "" : (run.laps.length > 1 ? `lap ${k + 1} of ${run.laps.length} · ` : "lap · ")
      + (run.laps[k].ms ? fmtTime(run.laps[k].ms / 1000) : "") + (run.laps.length > 1 && k === run.best ? " · ranked" : "");
    ui.seek.value = Math.round(((state.rel + PRE_ROLL) / (state.raceEnd + PRE_ROLL)) * 1000);
  }

  // hover names a car, a click watches it
  function point(e, click) {
    if (!state || !state.shown) return;
    const box = ui.canvas.getBoundingClientRect(), v = state.view;
    const x = (e.clientX - box.left) * v.dpr, y = (e.clientY - box.top) * v.dpr;
    let best = null, bestD = PICK_RADIUS * v.dpr;
    for (const c of state.shown) {
      if (!ui.field.checked && !c.o.entry.isRef) continue;
      const d = Math.hypot(v.px(c.pose.x) - x, v.py(c.pose.y) - y);
      if (d < bestD) { bestD = d; best = c.o; }
    }
    // a click acts on the car that is lit up: a click event's whole-pixel position can
    // fall just outside the radius its fractional pointer position was inside of
    if (click) { const target = state.hover || best; if (target) watch(target.entry); return; }
    if (best !== state.hover) {
      state.hover = best;
      ui.canvas.style.cursor = best ? "pointer" : "";
      if (!state.playing) draw();
    }
  }

  function frame(now) {
    if (!state || !state.playing) return;
    if (state.loopAt) {                              // the finished race stays up a moment, then starts over
      if (now >= state.loopAt) { state.loopAt = 0; state.rel = -PRE_ROLL; }
    } else {
      state.rel += ((now - state.last) / 1000) * state.rate;
      if (state.rel >= state.raceEnd) { state.rel = state.raceEnd; state.loopAt = now + LOOP_PAUSE; }
    }
    state.last = now;
    draw();
    state.raf = requestAnimationFrame(frame);
  }

  function setPlaying(on) {
    state.playing = on;
    ui.play.textContent = on ? "Pause" : "Play";
    cancelAnimationFrame(state.raf);
    if (on) { state.last = performance.now(); state.raf = requestAnimationFrame(frame); }
  }

  function toggle() {
    if (!state) return;
    if (!state.playing && state.rel >= state.raceEnd) { state.rel = -PRE_ROLL; state.loopAt = 0; }
    setPlaying(!state.playing);
  }

  function onClose() {
    if (state) cancelAnimationFrame(state.raf);
    state = null;
    if (document.fullscreenElement) document.exitFullscreen();
    dialog.classList.remove("rr-max");
    ui.full.textContent = "Full screen";
    const url = new URL(location.href);
    if (url.searchParams.has("watch")) { url.searchParams.delete("watch"); history.replaceState(null, "", url); }
  }

  /* Watch another car of the same race: the clock, the view and the cars already loaded all stay. */
  function watch(entry) {
    if (!state) return open(entry, []);
    open(entry, state.fieldEntries, state);
  }

  async function open(entry, fieldEntries, keep) {
    if (!dialog) build();
    if (state) cancelAnimationFrame(state.raf);
    ui.title.textContent = entry.name;
    ui.sub.textContent = entry.summary || "";
    ui.canvas.setAttribute("aria-label", `Top-down replay of ${entry.name}'s graded run`);
    if (!keep) { ui.msg.textContent = "Loading the run…"; ui.msg.hidden = false; ui.hud.hidden = true; }
    ui.pick.textContent = "";
    fieldEntries.forEach((e) => { const o = el("option", "", e.label); o.value = e.name; ui.pick.append(o); });
    ui.pick.value = entry.name;
    ui.pick.hidden = ui.fieldLabel.hidden = ui.pathsLabel.hidden = fieldEntries.length < 2;
    if (!dialog.open) dialog.showModal();
    const url = new URL(location.href);
    url.searchParams.set("watch", entry.name);
    history.replaceState(null, "", url);

    const token = (open.token = (open.token || 0) + 1);
    try {
      const run = await load(entry.replay);
      const index = await maps;
      if (token !== open.token || !dialog.open) return;
      const map = index[run.map];
      if (!map) throw new Error("no track image for " + run.map);
      const same = keep && keep.map === map;
      const image = (same && keep.mapImage) || await new Promise((ok) => {
        const im = new Image(); im.onload = () => ok(im); im.onerror = () => ok(null); im.src = "assets/maps/" + map.file;
      });
      if (token !== open.token || !dialog.open) return;
      // the car that was being watched rejoins the field; the newly watched one leaves it
      const others = same ? keep.others.filter((o) => o.entry.replay !== entry.replay) : [];
      if (same && keep.entry.replay !== entry.replay) others.push({ entry: keep.entry, run: keep.run });
      state = { entry, fieldEntries, run, map, mapImage: image, box: same ? keep.box : boxOf(run),
                rel: same ? keep.rel : -PRE_ROLL, raceEnd: run.finish + TAIL, loopAt: 0,
                rate: Number(ui.rate.value), playing: false, others, hover: null, shown: [] };
      race();
      ui.msg.hidden = true;
      ui.hud.hidden = false;
      layout();
      draw();
      // the rest of the field arrives car by car and joins in as it loads
      const have = new Set(others.map((o) => o.entry.replay).concat(entry.replay));
      fieldEntries.filter((e) => !have.has(e.replay)).forEach((e) => {
        load(e.replay).then((other) => {
          if (token !== open.token || !state || other.map !== run.map) return;
          state.others.push({ entry: e, run: other });
          race();
          if (!state.playing) draw();
        }, () => {});
      });
      const play = keep ? keep.playing : !matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (play) setPlaying(true);
      else ui.play.textContent = "Play";
    } catch (err) {
      if (token !== open.token) return;
      ui.msg.textContent = "This run's recording could not be loaded. Try again in a few minutes.";
      ui.msg.hidden = false;
    }
  }

  /* Called by the board script after it renders a table. Rows that have a
     recording carry a .watch button with data-replay / data-name / data-summary. */
  function attach(board) {
    const buttons = [...board.querySelectorAll("button.watch")];
    const entryOf = (b) => ({ name: b.dataset.name, replay: b.dataset.replay, summary: b.dataset.summary,
                              isRef: !!b.closest("tr.ref"), label: b.dataset.label || b.dataset.name });
    const field = buttons.map(entryOf);
    buttons.forEach((b, i) => {
      const warm = () => load(b.dataset.replay).catch(() => {});
      b.addEventListener("pointerenter", warm, { once: true });
      b.addEventListener("focus", warm, { once: true });
      b.addEventListener("click", () => open(field[i], field));
    });
    const wanted = new URLSearchParams(location.search).get("watch");
    if (wanted && !(dialog && dialog.open)) {
      const i = field.findIndex((e) => e.name.toLowerCase() === wanted.toLowerCase());
      if (i >= 0) open(field[i], field);
    }
    // the podium and the reference are what most visitors open first
    const idle = window.requestIdleCallback || ((f) => setTimeout(f, 800));
    idle(() => buttons.slice(0, 4).forEach((b) => load(b.dataset.replay).catch(() => {})));
  }

  window.RRReplay = { attach, decode };
})();
