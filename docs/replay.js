/* Replay player: plays back the run the grader recorded for a board entry.
 *
 * Nothing is simulated here. The referee records the graded run (grader bundle
 * replay_recorder.py, format v1), the builder publishes the best run's copy as
 * data/<row.replay>, and this draws it on a canvas. The board script only calls
 * RRReplay.attach(boardElement, doc) after it renders a table.
 *
 * It is a race: one clock, zero when every car crosses the start line, running
 * until the slowest car has finished, then starting over. By default each car
 * drives all its timed laps (three on the lap boards), each over exactly its
 * official time; "Best lap" races the ranked laps alone, in the board's order.
 * The cars wait on the line for a second first (nothing before it is drawn) and
 * park where they finish. The watched car drives in colour, the rest of the
 * board greyed out, the TA reference always picked out. Hover a car to name it,
 * click it (or pick it from the list) to watch that one instead; the clock and
 * the view stay put.
 *
 * Compare (the board's button, or the menu in the player) races 2 to 5 cars
 * alone: the board's top 3 or top 5, or cars picked from its list. Each drives
 * in its own colour with its name on it, a standings box keeps their order, and
 * a click on a car or its row puts that car in the readout.
 *
 * A recording is numbers only; every name shown comes from the board and is
 * set with textContent or drawn with fillText.
 */
(function () {
  "use strict";
  const CAR = { length: 0.58, width: 0.31, ahead: 0.165 };  // the gym's contact box; the pose is the rear axle
  const PRE_ROLL = 1.0;                                     // seconds every car holds on the start line first
  const TAIL = 1.0;                                         // ... and after the last car finishes
  const LOOP_PAUSE = 1500;                                  // ms the finished race stays up before it restarts
  const SPEEDS = [0.25, 0.5, 1, 2, 4, 8];
  const PATHS_KEY = "rr-replay-paths";                     // the viewer's Paths choice, kept across visits
  const PICK_RADIUS = 18;                                   // CSS px around a car that counts as pointing at it
  const MAX_CARS = 5;                                       // a comparison races 2 to this many cars
  const cache = new Map();                                  // url -> Promise<run>
  let maps = null, dialog = null, ui = null, state = null;
  let where = "";                                           // the board on show (a comparison's subtitle)

  // a recording's URL carries its content hash (?v=), so any cached copy of it is the right one
  const fetchJSON = (url, mode = "force-cache") => fetch(url, { cache: mode }).then((r) => {
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  });
  // ... the track index has no hash and gains tracks (a copy cached before Spielberg was added
  // hid it for good), so it is revalidated like the page's other files, `fresh` past any cache,
  // and a failed fetch is forgotten so the next Watch tries again
  function loadMaps(fresh) {
    const p = fetchJSON("assets/maps/maps.json", fresh ? "no-cache" : "default");
    p.catch(() => { if (maps === p) maps = null; });
    return (maps = p);
  }

  // The instant, to a fraction of a sample, a car crosses its track's start/finish line (maps.json
  // `line`: a point and the track's direction through it) near sample `b`, a recorded lap boundary.
  // A recording rounds its laps to its samples, which leaves every car a few centimetres to a few
  // decimetres past the line at its start. No crossing within LINE_WINDOW samples (a lap not timed
  // from that line, like the obstacle course's): b.
  const LINE_WINDOW = 4;
  const LINE_REACH = 4;          // m along the line from its point: its far extension across another hallway is not it
  function onLine(run, line, b) {
    if (!line) return b / run.hz;
    const along = (i) => (run.x[i] - line.x) * line.dx + (run.y[i] - line.y) * line.dy;
    let best = null;
    for (let i = Math.max(0, b - LINE_WINDOW); i < Math.min(run.n - 1, b + LINE_WINDOW); i++) {
      const a = along(i), c = along(i + 1);
      if ((a < 0) === (c < 0)) continue;
      const f = i + a / (a - c), k = f - i;
      const across = (run.y[i] + k * (run.y[i + 1] - run.y[i]) - line.y) * line.dx
                   - (run.x[i] + k * (run.x[i + 1] - run.x[i]) - line.x) * line.dy;
      if (Math.abs(f - b) <= LINE_WINDOW && Math.abs(across) <= LINE_REACH && (best === null || Math.abs(f - b) < Math.abs(best - b))) best = f;
    }
    // ... the last lap closes on the final sample, which the recorder takes as the lap closes, often
    // a fraction of a sample before the car is on the line: carry its last step on to the line
    // (poseAt carries it on the same way), or a finished car would park up to a few decimetres short
    if (best === null && b === run.n - 1) {
      const a = along(b - 1), c = along(b), f = b + c / (a - c), k = f - b;
      const across = (run.y[b] + k * (run.y[b] - run.y[b - 1]) - line.y) * line.dx
                   - (run.x[b] + k * (run.x[b] - run.x[b - 1]) - line.x) * line.dy;
      if (k > 0 && k <= LINE_WINDOW && Math.abs(across) <= LINE_REACH) best = f;
    }
    return (best === null ? b : best) / run.hz;
  }

  // `index` (maps.json) is optional: it only puts the laps' ends on the track's line
  function decode(doc, index) {
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
    const given = index && index[run.map] && index[run.map].line, norm = given ? Math.hypot(given.dx, given.dy) : 0;
    const line = norm > 0 && [given.x, given.y].every(Number.isFinite)
      ? { x: given.x, y: given.y, dx: given.dx / norm, dy: given.dy / norm } : null;
    run.laps = (Array.isArray(doc.laps) ? doc.laps : [])
      .filter((l) => Array.isArray(l) && l[0] >= 0 && l[1] < run.n && l[1] > l[0])
      .map((l, k) => ({ t0: onLine(run, line, l[0]), t1: onLine(run, line, l[1]), ms: (doc.lap_ms || [])[k], i0: l[0] }));
    run.best = Number.isInteger(doc.best) && run.laps[doc.best] ? doc.best : (run.laps.length ? 0 : -1);
    // colour scale: the speed range of the timed laps (a standing start would
    // stretch it to zero and leave a fast lap one flat colour)
    const first = run.laps.length ? run.laps[0].i0 : Math.min(run.n - 1, Math.round(2 * run.hz));
    run.vmin = Infinity; run.vmax = -Infinity;
    for (let i = first; i < run.n; i++) { run.vmin = Math.min(run.vmin, run.v[i]); run.vmax = Math.max(run.vmax, run.v[i]); }
    if (!(run.vmax - run.vmin > 0.2)) { run.vmin = 0; run.vmax = Math.max(run.vmax, 0.2); }
    return run;
  }

  function load(path) {
    const url = (window.RR_DATA_BASE || "data/") + path;      // an archived term keeps its recordings beside its boards
    if (!cache.has(url)) {
      const p = Promise.all([fetchJSON(url), maps || loadMaps()])
        .then(([doc, index]) => decode(doc, index));
      p.catch(() => cache.delete(url));
      cache.set(url, p);
    }
    return cache.get(url);
  }

  // A car's race in `mode`: which laps of its recording it drives, and when. Each lap is a
  // segment of race clock [r0, r1] played over recorded time [t0, t1], so a line crossing falls on
  // the cumulative official time (lap_ms): sample indices round to 1/hz s, enough to swap the
  // finishing order of two cars a few hundredths apart, and on the index range alone the car would
  // sit a sample short of, or past, the line as it "finished". "laps": every timed lap, zero as it
  // starts the first, finishing on their sum. "best": the ranked lap alone, finishing on the time the
  // board ranks it by. A recording with no timed lap plays whole.
  function timeline(run, mode) {
    const picked = !run.laps.length ? [] : mode === "best" ? [run.best] : run.laps.map((_, k) => k);
    const segs = [];
    let at = 0;
    for (const k of picked) {
      const l = run.laps[k], d = l.ms > 0 ? l.ms / 1000 : l.t1 - l.t0;
      segs.push({ lap: k, r0: at, r1: at + d, t0: l.t0, t1: l.t1 });
      at += d;
    }
    if (!segs.length) segs.push({ lap: -1, r0: 0, r1: run.duration, t0: 0, t1: run.duration });
    return { run, mode, segs, start: segs[0].t0, finish: segs[segs.length - 1].r1 };
  }

  // the lap (index into segs) a car is driving at race time `rel`: -1 holding on the line, segs.length finished
  function lapOn(tl, rel) {
    if (!(rel > 0)) return -1;
    if (rel >= tl.finish) return tl.segs.length;
    let k = 0;
    while (k < tl.segs.length - 1 && rel >= tl.segs[k].r1) k++;
    return k;
  }

  // the recorded time a car is at, at race time `rel`: until the clock starts it holds on the line
  // (the run-up and the laps before are never shown), and once it has finished it parks there
  function timeOf(tl, rel) {
    const k = lapOn(tl, rel), segs = tl.segs;
    if (k < 0) return segs[0].t0;
    if (k >= segs.length) return segs[segs.length - 1].t1;
    const s = segs[k];
    return s.t0 + ((rel - s.r0) / (s.r1 - s.r0)) * (s.t1 - s.t0);
  }

  // what the readout says of a car at race time `rel`: its clock, the lap it is on and the laps it has closed
  function readout(tl, rel) {
    const run = tl.run, segs = tl.segs, k = lapOn(tl, rel), done = k >= segs.length;
    const out = { done, clock: rel < 0 ? "on the line" : done ? "finished · " + fmtTime(tl.finish) : fmtTime(rel), lap: "", splits: "" };
    if (run.laps.length < 2) return out;
    if (tl.mode === "best") { out.lap = `lap ${run.best + 1} of ${run.laps.length} · best`; return out; }
    out.lap = done ? `${segs.length} laps · best: lap ${run.best + 1}` : `lap ${Math.max(0, k) + 1} of ${segs.length}`;
    out.splits = segs.slice(0, Math.max(0, k)).map((s) => `L${s.lap + 1} ${fmtTime(s.r1 - s.r0)}`).join(" · ");
    return out;
  }

  // the distance a car has driven from the start of its recording to recorded time t
  function distAt(run, t) {
    if (!run.dist) {
      run.dist = new Float64Array(run.n);
      for (let i = 1; i < run.n; i++) run.dist[i] = run.dist[i - 1] + Math.hypot(run.x[i] - run.x[i - 1], run.y[i] - run.y[i - 1]);
    }
    const f = Math.max(0, t * run.hz), i = Math.min(Math.floor(f), run.n - 2);
    return run.dist[i] + (f - i) * (run.dist[i + 1] - run.dist[i]);
  }

  // How far into its race a car is at race time `rel`: the laps it has closed plus the share of
  // the current lap's distance it has driven, so a whole number exactly as it crosses the line.
  // Two cars' lines differ by a few per cent, so between two crossings the order is that close.
  function progress(tl, rel) {
    const k = lapOn(tl, rel);
    if (k < 0 || k >= tl.segs.length) return Math.max(0, k);
    const s = tl.segs[k], d0 = distAt(tl.run, s.t0), d1 = distAt(tl.run, s.t1);
    return k + (d1 > d0 ? Math.min(1, (distAt(tl.run, timeOf(tl, rel)) - d0) / (d1 - d0)) : 0);
  }

  // the cars ({ tl }) in race order at `rel`: furthest first, finished ones by their finishing time,
  // ties (every car on the line) in the order given
  function standings(cars, rel) {
    return cars.map((c, i) => ({ c, i, p: progress(c.tl, rel) }))
      .sort((a, b) => b.p - a.p || (rel >= a.c.tl.finish && rel >= b.c.tl.finish ? a.c.tl.finish - b.c.tl.finish : 0) || a.i - b.i)
      .map((s) => s.c);
  }

  // how far behind the leader a car was where it last crossed the line (a lap closing): exact at
  // every crossing, null before its first
  function gapOf(c, lead, rel) {
    const j = Math.max(0, lapOn(c.tl, rel)) - 1;
    return j < 0 || !lead.tl.segs[j] ? null : c.tl.segs[j].r1 - lead.tl.segs[j].r1;
  }

  // the board's top n that have a recording: ranked rows only (never the TA reference or a run
  // graded late), in board order, fewer when fewer have one
  const top = (field, n) => field.filter((e) => !e.isRef && !e.late).slice(0, n);
  // compare=<name>,<name>,...: a comma or a backslash inside a name is escaped with a backslash
  const joinNames = (entries) => entries.map((e) => e.name.replace(/[\\,]/g, "\\$&")).join(",");
  const splitNames = (s) => (s.match(/(?:\\.|[^\\,])+/g) || []).map((n) => n.replace(/\\(.)/g, "$1"));

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
  const set = (node, text) => { if (node.textContent !== text) node.textContent = text; };
  // how a comparison names a car: the TA row's full name is a mouthful on a car
  const short = (e) => (e.isRef ? "TA reference" : e.name);
  // Compare's icon: three cars on their lines, one ahead
  const RACE_ICON = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h9M3 12h13M3 18h6"/><circle cx="15.5" cy="6" r="2"/><circle cx="19.5" cy="12" r="2"/><circle cx="12.5" cy="18" r="2"/></svg>';
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
    ui.splits = el("span", "lap");                  // the official time of each lap closed so far
    ui.who = el("span", "who");                     // a comparison: whose readout it is
    ui.who.hidden = true;
    ui.whoSwatch = el("i");
    ui.whoName = el("span");
    ui.who.append(ui.whoSwatch, ui.whoName);
    ui.hud.append(ui.who, ui.speed, ui.clock, ui.lap, ui.splits);
    // a comparison's standings: a row per car, kept in race order (a click puts that car in the readout)
    ui.legend = el("div", "rr-legend");
    ui.legend.hidden = true;
    ui.rows = el("ol");
    ui.rows.id = "rr-standings";
    ui.legendToggle = button("rr-legend-toggle", "Standings");
    ui.legendToggle.setAttribute("aria-controls", ui.rows.id);
    ui.legendToggle.setAttribute("aria-expanded", "true");
    ui.legend.append(ui.legendToggle, ui.rows);
    ui.msg = el("p", "rr-msg");
    stage.append(ui.canvas, ui.hud, ui.legend, ui.msg);

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
    // every timed lap or the ranked one alone; offered only where a run on show has several, and
    // never remembered: the default view is always the full race
    ui.race = el("select", "rr-race");
    ui.race.setAttribute("aria-label", "Race");
    ui.race.title = "Race every timed lap, or each car's best lap only";
    [["laps", "3 laps"], ["best", "Best lap"]].forEach(([value, text]) => { const o = el("option", "", text); o.value = value; ui.race.append(o); });
    ui.race.hidden = true;
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
    try { ui.paths.checked = localStorage.getItem(PATHS_KEY) === "1"; } catch (e) { /* storage blocked */ }
    ui.pathsLabel.title = "Draw where every car has driven so far";
    ui.pathsLabel.append(ui.paths, document.createTextNode(" Paths"));
    ui.share = button("rr-share", "Copy link");
    ui.cmp = button("rr-cmp", null);
    ui.cmp.innerHTML = RACE_ICON + "<span>Compare</span>";     // static markup: no name goes in here
    ui.cmp.title = "Race several cars on one track";
    ui.cmp.setAttribute("aria-label", "Compare cars");
    ui.cmp.setAttribute("aria-haspopup", "dialog");
    ui.cmp.setAttribute("aria-expanded", "false");
    ui.bar.append(ui.play, ui.seek, ui.rate, ui.race, ui.pick, ui.cmp, ui.fieldLabel, ui.pathsLabel, ui.share);

    // the Compare menu: the board's top 3 or top 5 in one press, or 2 to 5 cars from its list
    ui.menu = el("div", "rr-menu");
    ui.menu.id = "rr-menu";
    ui.menu.hidden = true;
    ui.menu.setAttribute("role", "dialog");
    ui.menu.setAttribute("aria-label", "Compare cars");
    ui.cmp.setAttribute("aria-controls", ui.menu.id);
    const tops = el("div", "tops"), foot = el("div", "foot");
    ui.top3 = button("", "Top 3");
    ui.top5 = button("", "Top 5");
    tops.append(ui.top3, ui.top5);
    ui.hint = el("p", "hint");
    ui.hint.id = "rr-menu-hint";
    ui.list = el("div", "list");
    ui.list.setAttribute("role", "group");
    ui.list.setAttribute("aria-labelledby", ui.hint.id);
    ui.one = button("", "Watch one car");
    ui.go = button("rr-go", "Race");
    foot.append(ui.one, ui.go);
    ui.menu.append(tops, ui.hint, ui.list, foot);

    // the Fullscreen API refuses <dialog> itself, so everything lives in a frame that can take it
    ui.frame.append(ui.head, stage, ui.bar, ui.menu);
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
    ui.race.addEventListener("change", () => { if (state) setMode(ui.race.value); });
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
    ui.cmp.addEventListener("click", () => menu(ui.menu.hidden));
    ui.top3.addEventListener("click", () => { menu(false, true); compare(top(state.fieldEntries, 3), "top3", state.fieldEntries, state); });
    ui.top5.addEventListener("click", () => { menu(false, true); compare(top(state.fieldEntries, 5), "top5", state.fieldEntries, state); });
    ui.list.addEventListener("change", picked);
    ui.go.addEventListener("click", () => {
      const on = new Set([...ui.list.querySelectorAll("input:checked")].map((b) => b.value));
      menu(false, true);
      compare(state.fieldEntries.filter((e) => on.has(e.name)), null, state.fieldEntries, state);
    });
    ui.one.addEventListener("click", () => { menu(false, true); watch(focused().entry); });
    // the menu closes on a press anywhere else, or when the keyboard leaves it
    dialog.addEventListener("pointerdown", (e) => { if (!ui.menu.hidden && !ui.menu.contains(e.target) && !ui.cmp.contains(e.target)) menu(false); });
    ui.menu.addEventListener("focusout", (e) => {
      if (e.relatedTarget && !ui.menu.contains(e.relatedTarget) && !ui.cmp.contains(e.relatedTarget)) menu(false);
    });
    ui.legendToggle.addEventListener("click", () => {
      ui.legendToggle.setAttribute("aria-expanded", String(!ui.legend.classList.toggle("shut")));
      if (state) { layout(); draw(); }
    });
    dialog.addEventListener("keydown", (e) => {
      // Escape shuts the Compare menu first, the player only after
      if (e.key === "Escape" && !ui.menu.hidden) { e.preventDefault(); menu(false, true); return; }
      if (!state || ui.menu.contains(e.target) || e.target === ui.seek || e.target.tagName === "SELECT") return;
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
    // full screen: everything the header, the readout and standings strips and the controls leave over
    const strips = [ui.hud, ui.legend].reduce((h, n) => h + (!n.hidden && getComputedStyle(n).position === "static" ? n.offsetHeight : 0), 0);
    const around = ui.head.offsetHeight + ui.bar.offsetHeight + strips;
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
    state.layer = layer;
    if (state.compare) return;                       // a comparison shows the cars it races and nothing else
    g.lineWidth = 1.5 * dpr; g.strokeStyle = css("--line-strong") || "#c9cddc"; g.lineJoin = "round";
    // the laps it races only, line to line, like every other path
    const t0 = state.tl.start, t1 = timeOf(state.tl, state.tl.finish), a = poseAt(run, t0), b = poseAt(run, t1);
    g.beginPath();
    g.moveTo(v.px(a.x), v.py(a.y));
    for (let i = Math.floor(t0 * run.hz) + 1; i <= Math.min(run.n - 1, Math.floor(t1 * run.hz)); i++) g.lineTo(v.px(run.x[i]), v.py(run.y[i]));
    g.lineTo(v.px(b.x), v.py(b.y));
    g.stroke();
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

  // a name plate above a car: solid, theme-aware, ringed so it reads over anything. `taken`: the
  // plates already placed (and whatever floats over the canvas), which this one stacks clear of
  function tag(g, pose, text, back, ink, big, taken) {
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
    // cars side by side (every car of a comparison on the start line): the names stack up, then
    // below the car once the top of the canvas is reached, instead of piling on one another
    if (taken) {
      const clash = () => taken.find((r) => left < r.left + r.w && left + w > r.left && top < r.top + r.h && top + h > r.top);
      let up = true;
      for (let r, k = 0; k < 12 && (r = clash()); k++) {
        top = up ? r.top - h - 2 * v.dpr : r.top + r.h + 2 * v.dpr;
        if (up && top < 3) { up = false; top = v.py(pose.y) + 16 * v.dpr; }
      }
      taken.push({ left, top, w, h });
    }
    g.beginPath(); g.roundRect(left, top, w, h, 4 * v.dpr);
    g.fillStyle = back; g.fill();
    g.lineWidth = 2 * v.dpr; g.strokeStyle = css("--surface") || "#fff"; g.stroke();
    g.fillStyle = ink; g.textBaseline = "middle"; g.textAlign = "center";
    g.fillText(text, left + w / 2, top + h / 2 + v.dpr * 0.5);
    g.restore();
  }

  // The race lasts until the slowest car on show has finished; the Race choice appears once a
  // run on show has laps to choose from.
  function race() {
    let end = state.tl.finish, laps = state.run.laps.length;
    for (const o of state.others) {
      if (state.compare || ui.field.checked || o.entry.isRef) end = Math.max(end, o.tl.finish);
      laps = Math.max(laps, o.run.laps.length);
    }
    state.raceEnd = end + TAIL;
    state.rel = Math.min(state.rel, state.raceEnd);
    ui.race.options[0].textContent = laps + " laps";
    ui.race.hidden = laps < 2;
  }

  // every car's race in the current mode (the recordings are shared, the timelines are the view's)
  function retime() {
    state.tl = timeline(state.run, state.mode);
    for (const o of state.others) o.tl = timeline(o.run, state.mode);
    race();
  }

  // another race over the same cars and the same view, from the line
  function setMode(mode) {
    state.mode = ui.race.value = mode === "best" ? "best" : "laps";
    retime();
    state.rel = -PRE_ROLL;
    state.loopAt = 0;
    address(state.entry, state.mode, state.compare);
    layout();
    draw();
  }

  // the view in the address bar: what Copy link shares and a reload opens again
  function address(entry, mode, compare) {
    const url = new URL(location.href);
    if (compare) {
      url.searchParams.set("compare", compare.key || joinNames(compare.entries));
      url.searchParams.delete("watch");
    } else {
      url.searchParams.set("watch", entry.name);
      url.searchParams.delete("compare");
    }
    if (mode === "best") url.searchParams.set("race", "best");
    else url.searchParams.delete("race");
    url.search = url.search.replace(/%2C/gi, ",");      // compare=Team+8,Team+10 reads as it is
    history.replaceState(null, "", url);
  }

  // where a car has driven so far: from its race's start, on the line, to its pose at race
  // time `rel`, never ahead of it
  function drawPath(g, tl, rel, color, alpha, width) {
    const run = tl.run, v = state.view, t = timeOf(tl, rel);
    if (!(t > tl.start)) return;
    const upto = Math.min(run.n - 1, Math.floor(t * run.hz));
    const begin = poseAt(run, tl.start), end = poseAt(run, t);
    g.save();
    g.globalAlpha = alpha; g.strokeStyle = color; g.lineWidth = width * v.dpr; g.lineJoin = g.lineCap = "round";
    g.beginPath();
    g.moveTo(v.px(begin.x), v.py(begin.y));
    for (let i = Math.floor(tl.start * run.hz) + 1; i <= upto; i++) g.lineTo(v.px(run.x[i]), v.py(run.y[i]));
    g.lineTo(v.px(end.x), v.py(end.y));
    g.stroke();
    g.restore();
  }

  // the trail behind a car: its last `secs` seconds fading out, or with Paths on all it has driven
  // so far; never from before its race's start (its first piece starts on the line itself)
  function trail(g, tl, colorAt, secs) {
    const run = tl.run, v = state.view, t = timeOf(tl, state.rel), whole = ui.paths.checked;
    const upto = Math.min(run.n - 1, Math.floor(t * run.hz)), i0 = Math.floor(tl.start * run.hz);
    const from = whole ? i0 : Math.max(i0, upto - Math.round(secs * run.hz));
    const begin = poseAt(run, tl.start);
    g.save();
    g.lineWidth = 3 * v.dpr; g.lineCap = "round";
    for (let i = from; i < upto; i++) {
      g.globalAlpha = whole ? 1 : 0.25 + 0.75 * ((i - from) / Math.max(1, upto - from));
      g.strokeStyle = colorAt(i);
      const a = i === i0 ? begin : { x: run.x[i], y: run.y[i] };
      g.beginPath(); g.moveTo(v.px(a.x), v.py(a.y)); g.lineTo(v.px(run.x[i + 1]), v.py(run.y[i + 1])); g.stroke();
    }
    g.restore();
  }

  function draw() {
    const g = ui.canvas.getContext("2d");
    g.clearRect(0, 0, ui.canvas.width, ui.canvas.height);
    g.drawImage(state.layer, 0, 0);
    if (state.compare) return drawRace(g);
    const run = state.run, tl = state.tl, t = timeOf(tl, state.rel);

    // the rest of the field's paths sit under everything else
    const grey = css("--ink-3") || "#62677f", ref = css("--ref") || "#c026d3", accent = css("--accent") || "#7c3aed";
    const surface = css("--surface") || "#fff", ink = css("--ink") || "#0b0c14";
    if (ui.paths.checked) {
      for (const o of state.others) {
        if (o.entry.isRef || o === state.hover || !ui.field.checked) continue;
        drawPath(g, o.tl, state.rel, grey, 0.35, 1.25);
      }
      const taCar = state.others.find((o) => o.entry.isRef);
      if (taCar && taCar !== state.hover) drawPath(g, taCar.tl, state.rel, ref, 0.6, 1.5);
    }
    // the car under the pointer shows where it has been, Paths on or off
    if (state.hover) drawPath(g, state.hover.tl, state.rel, state.hover.entry.isRef ? ref : ink, 0.85, 2);

    // the watched car's trail, coloured by speed
    trail(g, tl, (i) => speedColor(run, run.v[i]), 6);

    // the rest of the field, greyed out; the TA car and the one under the pointer stand out
    const field = state.others.map((o) => ({ o, pose: poseAt(o.run, timeOf(o.tl, state.rel)) }));
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
    tell(tl, pose);
  }

  // the readout: the watched car, or in a comparison the one picked on the track or in the standings
  function tell(tl, pose) {
    const said = readout(tl, state.rel);
    ui.speed.textContent = pose.v.toFixed(1) + " m/s";
    ui.clock.textContent = said.clock;
    ui.lap.textContent = said.lap;
    ui.splits.textContent = said.splits;
    ui.seek.value = Math.round(((state.rel + PRE_ROLL) / (state.raceEnd + PRE_ROLL)) * 1000);
  }

  const focused = () => state.others.find((o) => o.entry.name === state.focus) || state.others[0];
  function focusOn(car) {
    state.focus = car.entry.name;
    if (!state.playing) draw();
  }

  // A comparison: the cars it races and nothing else, each in its own colour and always named;
  // the one in the readout drawn on top, ringed in ink, its name first to find a place
  function drawRace(g) {
    const surface = css("--surface") || "#fff", ink = css("--ink") || "#0b0c14", focus = focused(), v = state.view;
    const cars = state.others.map((o) => ({ o, pose: poseAt(o.run, timeOf(o.tl, state.rel)),
      color: css(`--car-${o.slot + 1}`) || ink, text: css(`--car-${o.slot + 1}-ink`) || surface }));
    state.shown = cars;
    for (const c of cars) trail(g, c.o.tl, () => c.color, 3);        // shorter: five of them share the track
    const hovered = !ui.paths.checked && cars.find((c) => c.o === state.hover);
    if (hovered) drawPath(g, hovered.o.tl, state.rel, hovered.color, 0.85, 2);
    cars.sort((a, b) => (a.o === focus) - (b.o === focus));
    for (const c of cars) drawCar(g, c.pose, c.color, c.o === focus ? ink : surface, { width: c.o === focus ? 2.25 : 1.5, dashed: c.o.entry.isRef });
    // the readout floats over the canvas's corner on wide screens: names keep clear of it
    const taken = [ui.hud, ui.legend].filter((n) => !n.hidden && getComputedStyle(n).position === "absolute").map((n) => (
      { left: (n.offsetLeft - 6) * v.dpr, top: (n.offsetTop - 6) * v.dpr, w: (n.offsetWidth + 12) * v.dpr, h: (n.offsetHeight + 12) * v.dpr }));
    for (const c of cars.slice().reverse()) tag(g, c.pose, c.o.entry.isRef ? "TA" : c.o.entry.name, c.color, c.text, c.o === focus, taken);

    // the standings: rows keep their place in the list and move by CSS order, so the keyboard
    // focus stays on the row it is on
    const order = standings(state.others, state.rel), lead = order[0];
    order.forEach((c, k) => {
      const r = c.row, j = lapOn(c.tl, state.rel), gap = k ? gapOf(c, lead, state.rel) : null;
      r.li.style.order = k;
      set(r.pos, String(k + 1));
      set(r.st, state.rel >= c.tl.finish ? "finished · " + fmtTime(c.tl.finish) : c.tl.segs.length > 1 ? `lap ${Math.max(0, j) + 1}/${c.tl.segs.length}` : "");
      set(r.gap, !k ? "—" : gap === null ? "" : (gap < 0 ? "−" : "+") + Math.abs(gap).toFixed(2));
      r.b.setAttribute("aria-pressed", String(c === focus));
    });
    set(ui.whoName, short(focus.entry));
    ui.whoSwatch.style.background = `var(--car-${focus.slot + 1})`;
    tell(focus.tl, poseAt(focus.run, timeOf(focus.tl, state.rel)));
  }

  // the standings box: a row per car of the comparison
  function legend() {
    ui.rows.textContent = "";
    ui.legend.hidden = ui.who.hidden = !state.compare;
    if (!state.compare) return;
    for (const o of state.others) {
      const b = button("", null), swatch = el("i");
      swatch.style.background = `var(--car-${o.slot + 1})`;
      o.row = { li: el("li"), b, pos: el("span", "pos"), st: el("span", "st"), gap: el("span", "gap") };
      b.title = o.entry.label;
      b.append(o.row.pos, swatch, el("span", "name", short(o.entry)), o.row.st, o.row.gap);
      b.addEventListener("click", () => focusOn(o));
      o.row.li.append(b);
      ui.rows.append(o.row.li);
    }
  }

  // hover names a car, a click watches it
  function point(e, click) {
    if (!state || !state.shown) return;
    const box = ui.canvas.getBoundingClientRect(), v = state.view;
    const x = (e.clientX - box.left) * v.dpr, y = (e.clientY - box.top) * v.dpr;
    let best = null, bestD = PICK_RADIUS * v.dpr;
    for (const c of state.shown) {
      if (!state.compare && !ui.field.checked && !c.o.entry.isRef) continue;
      const d = Math.hypot(v.px(c.pose.x) - x, v.py(c.pose.y) - y);
      if (d < bestD) { bestD = d; best = c.o; }
    }
    // a click acts on the car that is lit up: a click event's whole-pixel position can
    // fall just outside the radius its fractional pointer position was inside of. In a
    // comparison it puts that car in the readout.
    if (click) {
      const target = state.hover || best;
      if (target && state.compare) focusOn(target);
      else if (target) watch(target.entry);
      return;
    }
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
    dialog.classList.remove("rr-max", "rr-comparing");
    ui.full.textContent = "Full screen";
    menu(false);
    const url = new URL(location.href), view = ["watch", "race", "compare"];
    if (view.some((k) => url.searchParams.has(k))) {
      view.forEach((k) => url.searchParams.delete(k));
      history.replaceState(null, "", url);
    }
  }

  // The Compare menu: opens over the track above the controls, as tall as the player leaves room
  // for (its list scrolls), on the first thing to press; `back` returns the keyboard to its button
  function menu(show, back) {
    if (show && state) {
      fill();
      ui.menu.hidden = false;
      ui.menu.style.bottom = ui.bar.offsetHeight + "px";
      const room = ui.bar.getBoundingClientRect().top - Math.max(0, ui.frame.getBoundingClientRect().top) - 8;
      ui.menu.style.maxHeight = Math.max(160, room) + "px";
      (ui.top3.hidden ? ui.list.querySelector("input") : ui.top3).focus();
    } else if (!ui.menu.hidden) {
      ui.menu.hidden = true;
      if (back) ui.cmp.focus();
    }
    ui.cmp.setAttribute("aria-expanded", String(!ui.menu.hidden));
  }

  // the menu for the board on show: its Top buttons say how many they race (fewer ranked rows with
  // a recording than 3 or 5), the list starts from the cars on the track
  function fill() {
    const field = state.fieldEntries, on = new Set((state.compare ? state.compare.entries : [state.entry]).map((e) => e.name));
    const n3 = top(field, 3).length, n5 = top(field, 5).length;
    ui.top3.textContent = "Top " + n3;
    ui.top3.hidden = n3 < 2;
    ui.top5.textContent = "Top " + n5;
    ui.top5.hidden = n5 <= n3;
    ui.list.textContent = "";
    for (const e of field) {
      const row = el("label", e.isRef ? "ref" : ""), box = el("input");
      box.type = "checkbox";
      box.value = e.name;
      box.checked = on.has(e.name);
      row.append(box, el("span", "", e.label));
      ui.list.append(row);
    }
    ui.one.hidden = !state.compare;
    picked();
  }

  // 2 to 5 cars: the other boxes lock once five are ticked, Race waits for a second one
  function picked() {
    const boxes = [...ui.list.querySelectorAll("input")], n = boxes.filter((b) => b.checked).length;
    boxes.forEach((b) => { b.disabled = !b.checked && n >= MAX_CARS; });
    ui.go.disabled = n < 2;
    ui.go.textContent = n < 2 ? "Race" : `Race ${n} cars`;
    ui.hint.textContent = `${ui.top3.hidden ? "Pick" : "Or pick"} 2 to ${MAX_CARS} cars (${n} picked)`;
  }

  /* Race 2 to 5 of the board's cars alone on the track; `key` (top3 / top5) when they are its top N,
     which is what the link then says. A new race starts from the line. */
  function compare(entries, key, fieldEntries, keep, mode) {
    entries = entries.slice(0, MAX_CARS);
    // a car keeps its colour while others join and leave: the colour follows the car, never its place
    const was = keep && keep.compare ? keep.compare.slots : new Map(), slots = new Map();
    for (const e of entries) if (was.has(e.name)) slots.set(e.name, was.get(e.name));
    for (const e of entries) {
      if (slots.has(e.name)) continue;
      let s = 0;
      while ([...slots.values()].includes(s)) s++;
      slots.set(e.name, s);
    }
    return open(entries[0], fieldEntries, keep, mode, { key, entries, slots });
  }

  /* Watch another car of the same race: the clock, the race, the view and the cars already loaded all stay. */
  function watch(entry) {
    if (!state) return open(entry, []);
    open(entry, state.fieldEntries, state);
  }

  // a new view races every timed lap unless `mode` (a deep link's race=best) says otherwise;
  // `compare` ({ key, entries, slots }, from compare()) races those cars alone, `entry` the first
  async function open(entry, fieldEntries, keep, mode, compare) {
    if (!dialog) build();
    if (state) cancelAnimationFrame(state.raf);
    menu(false);
    mode = keep ? keep.mode : mode === "best" ? "best" : "laps";
    const cars = compare ? compare.entries : [];
    ui.title.textContent = !compare ? entry.name : compare.key ? "Top " + cars.length
      : cars.length === 2 ? cars.map(short).join(" vs ") : cars.length + " cars";
    ui.sub.textContent = compare ? where : entry.summary || "";
    ui.canvas.setAttribute("aria-label", compare ? `Top-down replay racing ${cars.map(short).join(", ")}` : `Top-down replay of ${entry.name}'s graded run`);
    if (!keep) { ui.msg.textContent = "Loading the run…"; ui.msg.hidden = false; ui.hud.hidden = ui.race.hidden = true; }
    ui.race.value = mode;
    ui.pick.textContent = "";
    fieldEntries.forEach((e) => { const o = el("option", "", e.label); o.value = e.name; ui.pick.append(o); });
    ui.pick.value = entry.name;
    ui.pathsLabel.hidden = ui.cmp.hidden = fieldEntries.length < 2;
    ui.pick.hidden = ui.fieldLabel.hidden = fieldEntries.length < 2 || !!compare;
    if (!dialog.open) dialog.showModal();
    address(entry, mode, compare);

    const token = (open.token = (open.token || 0) + 1);
    try {
      const run = await load(entry.replay);
      let index = await (maps || loadMaps());
      // a track published since this page read the index: read it again, once
      if (!index[run.map]) index = await loadMaps(true);
      if (token !== open.token || !dialog.open) return;
      const map = index[run.map];
      if (!map) throw new Error("no track image for " + run.map);
      const same = keep && keep.map === map;
      const image = (same && keep.mapImage) || await new Promise((ok) => {
        const im = new Image(); im.onload = () => ok(im); im.onerror = () => ok(null); im.src = "assets/maps/" + map.file;
      });
      if (token !== open.token || !dialog.open) return;
      let others;
      if (compare) {
        // the cars compared, this one among them, every one loaded before the race starts so that
        // they leave the line together
        const runs = await Promise.all(cars.map((e) => (e === entry ? run : load(e.replay).catch(() => null))));
        if (token !== open.token || !dialog.open) return;
        others = cars.map((e, i) => ({ entry: e, run: runs[i], slot: compare.slots.get(e.name) })).filter((o) => o.run && o.run.map === run.map);
      } else {
        // the car that was being watched rejoins the field (after a comparison it is in it already);
        // the newly watched one leaves it
        others = same ? keep.others.filter((o) => o.entry.replay !== entry.replay) : [];
        if (same && keep.entry.replay !== entry.replay && !others.some((o) => o.entry.replay === keep.entry.replay)) others.push({ entry: keep.entry, run: keep.run });
      }
      state = { entry, fieldEntries, run, map, mapImage: image, box: same ? keep.box : boxOf(run), mode,
                rel: same && !compare ? keep.rel : -PRE_ROLL, loopAt: 0,
                rate: Number(ui.rate.value), playing: false, others, hover: null, shown: [],
                compare: compare || null, focus: compare && keep && compare.slots.has(keep.focus) ? keep.focus : entry.name };
      dialog.classList.toggle("rr-comparing", !!compare);
      retime();
      legend();
      ui.msg.hidden = true;
      ui.hud.hidden = false;
      layout();
      draw();
      // the rest of the field arrives car by car and joins in as it loads
      const have = new Set(others.map((o) => o.entry.replay).concat(entry.replay));
      if (!compare) fieldEntries.filter((e) => !have.has(e.replay)).forEach((e) => {
        load(e.replay).then((other) => {
          if (token !== open.token || !state || other.map !== run.map) return;
          state.others.push({ entry: e, run: other, tl: timeline(other, state.mode) });
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
     recording carry a .watch button with data-replay / data-name / data-summary;
     `tools` (beside the board's title) gets the board's Compare button. */
  function attach(board, doc, tools) {
    const buttons = [...board.querySelectorAll("button.watch")];
    const entryOf = (b) => ({ name: b.dataset.name, replay: b.dataset.replay, summary: b.dataset.summary,
                              isRef: !!b.closest("tr.ref"), late: !!b.closest("tr.late"), label: b.dataset.label || b.dataset.name });
    const field = buttons.map(entryOf);
    where = (doc && doc.board_title) || "";
    buttons.forEach((b, i) => {
      const warm = () => load(b.dataset.replay).catch(() => {});
      b.addEventListener("pointerenter", warm, { once: true });
      b.addEventListener("focus", warm, { once: true });
      b.addEventListener("click", () => open(field[i], field));
    });
    // the board's way in to a comparison: its top 3, or while fewer than two ranked rows have a
    // recording, the first entries that do (the TA's, a late run's)
    const first = top(field, 3).length >= 2 ? { key: "top3", entries: top(field, 3) } : { key: null, entries: field.slice(0, MAX_CARS) };
    if (tools) {
      tools.textContent = "";
      if (field.length >= 2) {
        const b = button("compare", null);
        b.innerHTML = RACE_ICON + "<span>Compare</span>";           // static markup: no name goes in here
        b.title = first.key ? `Race the top ${first.entries.length} on one track` : "Race these runs on one track";
        b.addEventListener("click", () => compare(first.entries, first.key, field));
        tools.append(b);
      }
    }
    const params = new URLSearchParams(location.search), wanted = params.get("watch"), versus = params.get("compare");
    if ((wanted || versus) && !(dialog && dialog.open)) {
      // compare=top3 | top5 | names, any case; names the board does not have are skipped
      const t = versus && /^top([35])$/i.exec(versus.trim()), names = new Set(versus ? splitNames(versus).map((n) => n.trim().toLowerCase()) : []);
      const cars = !versus ? [] : t ? top(field, Number(t[1])) : field.filter((e) => names.has(e.name.toLowerCase()));
      const i = field.findIndex((e) => e.name.toLowerCase() === (wanted || "").toLowerCase());
      if (cars.length >= 2) compare(cars, t ? "top" + t[1] : null, field, null, params.get("race"));
      else if (i >= 0) open(field[i], field, null, params.get("race"));
    }
    // the podium and the reference are what most visitors open first
    const idle = window.requestIdleCallback || ((f) => setTimeout(f, 800));
    idle(() => buttons.slice(0, 4).forEach((b) => load(b.dataset.replay).catch(() => {})));
  }

  window.RRReplay = { attach, decode, timeline, timeOf, lapOn, readout, progress, standings, top, joinNames, splitNames };
})();
