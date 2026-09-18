/* Replay player: plays back the run the grader recorded for a board entry.
 *
 * Nothing is simulated here. The referee records the graded run (grader bundle
 * replay_recorder.py, format v1), the builder publishes the best run's copy as
 * data/<row.replay>, and this draws it on a canvas. The board script only calls
 * RRReplay.attach(boardElement, doc) after it renders a table.
 *
 * The run being watched drives in colour; every other entry of the board drives
 * along greyed out, all of them crossing their ranked lap's start line at the
 * same moment, and the TA reference is always picked out. Hover a car to name
 * it, click it (or pick it from the list) to watch that one instead.
 *
 * A recording is numbers only; every name shown comes from the board and is
 * set with textContent or drawn with fillText.
 */
(function () {
  "use strict";
  const CAR = { length: 0.58, width: 0.31, ahead: 0.165 };  // the gym's contact box; the pose is the rear axle
  const PRE_ROLL = 1.0;                                     // seconds shown before the ranked lap starts
  const SPEEDS = [0.25, 0.5, 1, 2, 4, 8];
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
    run.start = run.best >= 0 ? run.laps[run.best].t0 : 0;  // every car is lined up on this moment
    // colour scale: the speed range of the timed laps (a standing start would
    // stretch it to zero and leave a fast lap one flat colour)
    const first = run.laps.length ? Math.round(run.laps[0].t0 * run.hz) : Math.min(run.n - 1, Math.round(2 * run.hz));
    run.vmin = Infinity; run.vmax = -Infinity;
    for (let i = first; i < run.n; i++) { run.vmin = Math.min(run.vmin, run.v[i]); run.vmax = Math.max(run.vmax, run.v[i]); }
    if (!(run.vmax - run.vmin > 0.2)) { run.vmin = 0; run.vmax = Math.max(run.vmax, 0.2); }
    return run;
  }

  function load(path) {
    const url = "data/" + path;
    if (!cache.has(url)) {
      const p = Promise.all([fetchJSON(url), maps || (maps = fetchJSON("assets/maps/maps.json"))])
        .then(([doc]) => decode(doc));
      p.catch(() => cache.delete(url));
      cache.set(url, p);
    }
    return cache.get(url);
  }

  function poseAt(run, t) {
    const f = Math.min(Math.max(t, 0), run.duration) * run.hz;
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

  function build() {
    dialog = el("dialog", "rr-player");
    dialog.setAttribute("aria-labelledby", "rr-player-title");
    const head = el("header");
    const titles = el("div");
    ui = { title: el("h2"), sub: el("p", "sub") };
    ui.title.id = "rr-player-title";
    titles.append(ui.title, ui.sub);
    const actions = el("div", "rr-actions");
    ui.full = button("rr-full", "Full screen");
    ui.close = button("rr-close", "Close");
    actions.append(ui.full, ui.close);
    head.append(titles, actions);

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

    const bar = el("div", "rr-bar");
    ui.play = button("rr-play", "Play");
    ui.play.autofocus = true;                       // Space/Enter work the moment it opens
    ui.seek = el("input");
    Object.assign(ui.seek, { type: "range", min: 0, max: 1000, step: 1, value: 0 });
    ui.seek.setAttribute("aria-label", "Position in the run");
    ui.rate = el("select");
    ui.rate.setAttribute("aria-label", "Playback speed");
    SPEEDS.forEach((s) => { const o = el("option", "", s + "×"); o.value = s; ui.rate.append(o); });
    ui.rate.value = 1;
    ui.pick = el("select", "rr-pick");              // the field, for keyboards and touch screens
    ui.pick.setAttribute("aria-label", "Car to watch");
    const fieldLabel = el("label", "rr-ghost");
    ui.field = el("input");
    ui.field.type = "checkbox";
    ui.field.checked = true;
    fieldLabel.append(ui.field, document.createTextNode(" Other cars"));
    ui.fieldLabel = fieldLabel;
    ui.share = button("rr-share", "Copy link");
    bar.append(ui.play, ui.seek, ui.rate, ui.pick, fieldLabel, ui.share);
    ui.note = el("p", "rr-note");

    dialog.append(head, stage, bar, ui.note);
    document.body.append(dialog);

    ui.close.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", onClose);
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
    ui.play.addEventListener("click", toggle);
    ui.seek.addEventListener("input", () => { if (state) { state.t = (ui.seek.value / 1000) * state.run.duration; draw(); } });
    ui.rate.addEventListener("change", () => { if (state) state.rate = Number(ui.rate.value); });
    ui.field.addEventListener("change", () => { if (state) draw(); });
    ui.pick.addEventListener("change", () => {
      const entry = state && state.fieldEntries.find((e) => e.name === ui.pick.value);
      if (entry) watch(entry);
    });
    ui.full.addEventListener("click", () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (dialog.requestFullscreen) dialog.requestFullscreen().catch(() => {});
    });
    document.addEventListener("fullscreenchange", () => {
      ui.full.textContent = document.fullscreenElement === dialog ? "Exit full screen" : "Full screen";
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
        state.t = Math.min(state.run.duration, Math.max(0, state.t + (e.key === "ArrowLeft" ? -1 : 1)));
        draw();
      }
    });
    ui.canvas.addEventListener("pointermove", (e) => point(e, false));
    ui.canvas.addEventListener("pointerleave", () => { if (state && state.hover) { state.hover = null; ui.canvas.style.cursor = ""; draw(); } });
    ui.canvas.addEventListener("click", (e) => point(e, true));
    window.addEventListener("resize", () => { if (state) { layout(); draw(); } });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state) { layout(); draw(); } });
  }

  function layout() {
    // fit the run (not the whole building) so the car is as large as the screen allows
    const run = state.run, m = state.map, pad = 2.5;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < run.n; i++) {
      x0 = Math.min(x0, run.x[i]); x1 = Math.max(x1, run.x[i]);
      y0 = Math.min(y0, run.y[i]); y1 = Math.max(y1, run.y[i]);
    }
    x0 -= pad; x1 += pad; y0 -= pad; y1 += pad;
    const full = document.fullscreenElement === dialog;
    const cssW = ui.canvas.parentElement.clientWidth;
    // full screen: everything the header and the controls leave over
    const around = dialog.scrollHeight - ui.canvas.offsetHeight;
    const cssH = full ? Math.max(200, window.innerHeight - around - 2)
                      : Math.max(200, Math.min(cssW * (y1 - y0) / (x1 - x0), window.innerHeight * 0.58));
    const dpr = window.devicePixelRatio || 1;
    ui.canvas.style.height = cssH + "px";
    ui.canvas.width = Math.round(cssW * dpr);
    ui.canvas.height = Math.round(cssH * dpr);
    const scale = Math.min(ui.canvas.width / (x1 - x0), ui.canvas.height / (y1 - y0));
    const ox = (ui.canvas.width - scale * (x1 - x0)) / 2, oy = (ui.canvas.height - scale * (y1 - y0)) / 2;
    state.view = { scale, dpr, px: (x) => ox + (x - x0) * scale, py: (y) => ui.canvas.height - oy - (y - y0) * scale };

    // static layer: tinted walls + the whole line, faint
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
      g.drawImage(tint, v.px(m.x0), v.py(m.y0 + m.h * m.res), m.w * m.res * scale, m.h * m.res * scale);
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

  function tag(g, pose, text, colour) {
    const v = state.view, x = v.px(pose.x), y = v.py(pose.y) - 14 * v.dpr;
    g.save();
    g.font = `600 ${11 * v.dpr}px ${css("--body") || "sans-serif"}`;
    const w = g.measureText(text).width + 10 * v.dpr, h = 17 * v.dpr;
    const left = Math.min(Math.max(x - w / 2, 2), ui.canvas.width - w - 2), top = Math.max(y - h, 2);
    g.fillStyle = colour; g.beginPath(); g.roundRect(left, top, w, h, 3 * v.dpr); g.fill();
    g.fillStyle = "#fff"; g.textBaseline = "middle"; g.fillText(text, left + 5 * v.dpr, top + h / 2 + v.dpr * 0.5);
    g.restore();
  }

  // where each other car is right now: lined up on its own ranked lap's start
  function fieldNow() {
    const rel = state.t - state.run.start, out = [];
    for (const o of state.others) {
      const t = o.run.start + rel;
      if (t < 0 || t > o.run.duration) continue;
      out.push({ o, pose: poseAt(o.run, t) });
    }
    return out;
  }

  function currentLap(run, t) {
    for (let k = 0; k < run.laps.length; k++) if (t >= run.laps[k].t0 && t <= run.laps[k].t1) return k;
    return -1;
  }

  function draw() {
    const run = state.run, v = state.view, g = ui.canvas.getContext("2d");
    g.clearRect(0, 0, ui.canvas.width, ui.canvas.height);
    g.drawImage(state.layer, 0, 0);

    // the trail: the last few seconds, coloured by speed
    const upto = Math.min(run.n - 1, Math.floor(state.t * run.hz)), from = Math.max(0, upto - Math.round(6 * run.hz));
    g.lineWidth = 3 * v.dpr; g.lineCap = "round";
    for (let i = from; i < upto; i++) {
      g.globalAlpha = 0.25 + 0.75 * ((i - from) / Math.max(1, upto - from));
      g.strokeStyle = speedColor(run, run.v[i]);
      g.beginPath(); g.moveTo(v.px(run.x[i]), v.py(run.y[i])); g.lineTo(v.px(run.x[i + 1]), v.py(run.y[i + 1])); g.stroke();
    }
    g.globalAlpha = 1;

    // the rest of the field, greyed out; the TA car and the one under the pointer stand out
    const grey = css("--ink-3") || "#62677f", ref = css("--ref") || "#c026d3", accent = css("--accent") || "#7c3aed";
    const field = fieldNow();
    state.shown = field;
    for (const c of field) {
      if (c.o.entry.isRef || c.o === state.hover || !ui.field.checked) continue;
      drawCar(g, c.pose, null, grey, { alpha: 0.5, width: 1 });
    }
    for (const c of field) if (c.o.entry.isRef && c.o !== state.hover) {
      drawCar(g, c.pose, null, ref, { dashed: true, width: 1.75 });
      tag(g, c.pose, "TA", ref);
    }
    const pose = poseAt(run, state.t);
    drawCar(g, pose, css("--surface") || "#fff", state.entry.isRef ? ref : accent, { width: 1.75 });
    const hovered = field.find((c) => c.o === state.hover);
    if (hovered) {
      drawCar(g, hovered.pose, css("--surface") || "#fff", hovered.o.entry.isRef ? ref : css("--ink") || "#0b0c14", { width: 2 });
      tag(g, hovered.pose, hovered.o.entry.label, hovered.o.entry.isRef ? ref : css("--ink") || "#0b0c14");
    }

    const lap = currentLap(run, state.t);
    ui.speed.textContent = pose.v.toFixed(1) + " m/s";
    ui.clock.textContent = lap >= 0 ? fmtTime(state.t - run.laps[lap].t0) : (state.t < run.start ? "run-up" : fmtTime(state.t));
    ui.lap.textContent = lap < 0 ? "" : (run.laps.length > 1 ? `lap ${lap + 1} of ${run.laps.length} · ` : "lap · ")
      + (run.laps[lap].ms ? fmtTime(run.laps[lap].ms / 1000) : "") + (run.laps.length > 1 && lap === run.best ? " · ranked" : "");
    ui.seek.value = Math.round((state.t / run.duration) * 1000);
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
    state.t += ((now - state.last) / 1000) * state.rate;
    state.last = now;
    if (state.t >= state.run.duration) { state.t = state.run.duration; setPlaying(false); }
    draw();
    if (state.playing) state.raf = requestAnimationFrame(frame);
  }

  function setPlaying(on) {
    state.playing = on;
    ui.play.textContent = on ? "Pause" : (state.t >= state.run.duration ? "Replay" : "Play");
    cancelAnimationFrame(state.raf);
    if (on) { state.last = performance.now(); state.raf = requestAnimationFrame(frame); }
  }

  function toggle() {
    if (!state) return;
    if (!state.playing && state.t >= state.run.duration) state.t = Math.max(0, state.run.start - PRE_ROLL);
    setPlaying(!state.playing);
  }

  function onClose() {
    if (state) cancelAnimationFrame(state.raf);
    state = null;
    if (document.fullscreenElement === dialog) document.exitFullscreen();
    const url = new URL(location.href);
    if (url.searchParams.has("watch")) { url.searchParams.delete("watch"); history.replaceState(null, "", url); }
  }

  /* Watch another car of the same field, keeping the moment of the lap. */
  function watch(entry) {
    if (!state) return open(entry, []);
    const rel = state.t - state.run.start, playing = state.playing;
    open(entry, state.fieldEntries, { rel, playing });
  }

  async function open(entry, fieldEntries, keep) {
    if (!dialog) build();
    if (state) cancelAnimationFrame(state.raf);
    const previous = state;
    ui.title.textContent = entry.name;
    ui.sub.textContent = entry.summary || "";
    ui.canvas.setAttribute("aria-label", `Top-down replay of ${entry.name}'s graded run`);
    if (!previous) { ui.msg.textContent = "Loading the run…"; ui.msg.hidden = false; ui.hud.hidden = true; }
    ui.note.hidden = true;
    ui.pick.textContent = "";
    fieldEntries.forEach((e) => { const o = el("option", "", e.label); o.value = e.name; ui.pick.append(o); });
    ui.pick.value = entry.name;
    ui.pick.hidden = ui.fieldLabel.hidden = fieldEntries.length < 2;
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
      const image = (previous && previous.map === map && previous.mapImage) || await new Promise((ok) => {
        const im = new Image(); im.onload = () => ok(im); im.onerror = () => ok(null); im.src = "assets/maps/" + map.file;
      });
      if (token !== open.token || !dialog.open) return;
      const t = keep ? run.start + keep.rel : run.start - PRE_ROLL;
      state = { entry, fieldEntries, run, map, mapImage: image, t: Math.min(run.duration, Math.max(0, t)),
                rate: Number(ui.rate.value), playing: false, others: [], hover: null, shown: [] };
      ui.msg.hidden = true;
      ui.hud.hidden = false;
      ui.note.textContent = run.rerun
        ? "This run was graded before runs were recorded, so this is a re-run of the same submitted code in the same grading simulator. The board's time is from the original graded run; a re-run is never identical, and its lap can differ by a few tenths of a second."
        : "";
      ui.note.hidden = !run.rerun;
      layout();
      draw();
      // the rest of the field arrives car by car and joins in as it loads
      fieldEntries.filter((e) => e.replay !== entry.replay).forEach((e) => {
        load(e.replay).then((other) => {
          if (token !== open.token || !state || other.map !== run.map) return;
          state.others.push({ entry: e, run: other });
          if (!state.playing) draw();
        }, () => {});
      });
      const play = keep ? keep.playing : !matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (play && state.t < run.duration) setPlaying(true);
      else ui.play.textContent = state.t >= run.duration ? "Replay" : "Play";
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
