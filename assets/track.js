/* Track tab player. Dash mounts an empty #track-root; everything inside is
   drawn here so 22 cars can move every frame without a server round trip. */
(function () {
  const SVG = "http://www.w3.org/2000/svg";
  const WIDTH = 1000, HEIGHT = 640, PAD = 40;
  const SPEEDS = [1, 2, 5, 10];
  const PREFETCH_SECONDS = 40;
  const LIVE_POLL_MS = 400;
  const LIVE_EASING = 0.18; // fraction of the way to the newest fix, per frame
  // Only lights-out is recorded. The build-up uses the regulation one-second
  // steps, and the real hold before lights-out varies, so this is a fixed stand-in.
  const LIGHT_STEP = 1, LIGHT_HOLD = 1.2, GANTRY_LEAD = 2, LIGHTS_OUT_SHOWN = 4;

  const state = {
    sessionKey: null, active: false, mode: "replay", meta: null,
    clock: 0, speed: 1, playing: false, buffering: false, lastTick: 0,
    chunks: new Map(), pending: new Set(), project: null, marks: new Map(),
    sectorMarks: new Map(), statusKey: null,
    liveTargets: new Map(), pollTimer: null, frame: null, loadId: 0,
  };

  const el = (id) => document.getElementById(id);

  function clockText(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
  }

  function buildShell(root) {
    root.innerHTML = `
      <div class="track-bar">
        <button id="track-play" class="track-btn" aria-label="Play">Play</button>
        <input id="track-seek" type="range" min="0" max="100" value="0" step="1" aria-label="Session time">
        <span id="track-time" class="track-time">00:00:00</span>
        <span id="track-speeds" class="track-speeds" role="group" aria-label="Playback speed"></span>
        <button id="track-live" class="track-btn" hidden>Watch live</button>
      </div>
      <div id="track-note" class="track-note"></div>
      <div class="track-status">
        <div id="track-flags" class="track-flags" role="status"></div>
        <div id="track-gantry" class="gantry" hidden></div>
      </div>
      <div class="track-layout">
        <div class="card track-stage"><svg id="track-svg" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="Circuit map with car positions"></svg></div>
        <div class="card track-side">
          <h3 id="track-title"></h3>
          <div id="track-lap" class="card-note"></div>
          <ol id="track-order" class="track-order"></ol>
        </div>
      </div>
      <p id="track-message" class="track-message"></p>`;
    SPEEDS.forEach((speed) => {
      const button = document.createElement("button");
      button.className = "track-btn" + (speed === state.speed ? " on" : "");
      button.textContent = `${speed}×`;
      button.onclick = () => {
        state.speed = speed;
        [...el("track-speeds").children].forEach((b) => b.classList.toggle("on", b === button));
      };
      el("track-speeds").append(button);
    });
    el("track-play").onclick = () => setPlaying(!state.playing);
    el("track-seek").oninput = (e) => { state.clock = +e.target.value; render(); };
    el("track-seek").onchange = () => { ensureChunks(); };
    el("track-live").onclick = () => (state.mode === "live" ? startReplay() : startLive());
  }

  function setPlaying(on) {
    state.playing = on;
    state.lastTick = performance.now();
    const button = el("track-play");
    if (button) {
      button.textContent = on ? "Pause" : "Play";
      button.setAttribute("aria-label", on ? "Pause" : "Play");
    }
  }

  function note(text) {
    const target = el("track-note");
    if (target) target.textContent = text || "";
  }

  function drawCircuit(meta) {
    const svg = el("track-svg");
    svg.replaceChildren();
    state.marks.clear();
    const xs = meta.outline.map((p) => p[0]);
    const ys = meta.outline.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const scale = Math.min((WIDTH - 2 * PAD) / (maxX - minX), (HEIGHT - 2 * PAD) / (maxY - minY));
    const offsetX = (WIDTH - (maxX - minX) * scale) / 2;
    const offsetY = (HEIGHT - (maxY - minY) * scale) / 2;
    // SVG's y axis points down; the circuit's points up.
    state.project = (x, y) => [offsetX + (x - minX) * scale, HEIGHT - offsetY - (y - minY) * scale];

    const line = document.createElementNS(SVG, "polygon");
    line.setAttribute("points", meta.outline.map((p) => state.project(p[0], p[1]).join(",")).join(" "));
    line.setAttribute("class", "track-line");
    line.id = "track-line";
    svg.append(line);

    state.sectorMarks = new Map();
    for (const [number, points] of Object.entries(meta.sectors || {})) {
      const projected = points.map((p) => state.project(p[0], p[1]));
      const path = document.createElementNS(SVG, "polyline");
      path.setAttribute("points", projected.map((p) => p.join(",")).join(" "));
      path.setAttribute("class", "sector-line");
      const middle = projected[Math.floor(projected.length / 2)];
      const label = document.createElementNS(SVG, "text");
      label.setAttribute("x", middle[0]);
      label.setAttribute("y", middle[1] - 12);
      label.setAttribute("class", "sector-label");
      label.textContent = `S${number}`;
      path.style.display = label.style.display = "none";
      svg.append(path, label);
      state.sectorMarks.set(number, { path, label });
    }
    state.statusKey = null;
  }

  function pill(kind, text) {
    const item = document.createElement("span");
    item.className = `flag-pill ${kind}`;
    item.textContent = text;
    return item;
  }

  function sectorList(sectors, flag) {
    const numbers = Object.keys(sectors).filter((n) => sectors[n] === flag);
    if (!numbers.length) return "";
    return `${numbers.length > 1 ? "Sectors" : "Sector"} ${numbers.join(", ")}`;
  }

  function showStatus(status) {
    const current = status || { sectors: {} };
    const key = JSON.stringify(current);
    if (key === state.statusKey) return;
    state.statusKey = key;

    const pills = [];
    if (current.red) pills.push(pill("red", "■ Red flag"));
    if (current.safetyCar) {
      const name = current.safetyCar === "VSC" ? "Virtual Safety Car" : "Safety Car";
      pills.push(pill("yellow", `▲ ${name}${current.ending ? " ending" : ""}`));
    }
    if (current.trackYellow) pills.push(pill("yellow", "⚑ Yellow · Whole track"));
    const single = sectorList(current.sectors, "YELLOW");
    const double = sectorList(current.sectors, "DOUBLE YELLOW");
    if (double) pills.push(pill("yellow", `⚑⚑ Double yellow · ${double}`));
    if (single) pills.push(pill("yellow", `⚑ Yellow · ${single}`));
    if (!pills.length) pills.push(pill("clear", "✓ Track clear"));
    el("track-flags").replaceChildren(...pills);

    el("track-line").classList.toggle("red", Boolean(current.red));
    for (const [number, { path, label }] of state.sectorMarks) {
      const flag = current.sectors[number];
      path.style.display = label.style.display = flag ? "" : "none";
      path.classList.toggle("double", flag === "DOUBLE YELLOW");
    }
  }

  function showGantry(lit, out) {
    const gantry = el("track-gantry");
    const visible = lit !== null;
    gantry.hidden = !visible;
    if (!visible) return;
    if (gantry.childElementCount !== 6) {
      gantry.replaceChildren(
        ...Array.from({ length: 5 }, () => document.createElement("span")),
        Object.assign(document.createElement("strong"), { textContent: "Lights out" }));
    }
    [...gantry.children].forEach((node, i) => {
      if (i < 5) node.classList.toggle("lit", i < lit);
    });
    gantry.classList.toggle("out", out);
    gantry.setAttribute("aria-label", out ? "Lights out" : `${lit} of 5 start lights on`);
  }

  function replayGantry() {
    const out = state.meta.lightsOut;
    if (out === null || out === undefined) return showGantry(null, false);
    const firstLight = out - LIGHT_HOLD - 4 * LIGHT_STEP;
    const t = state.clock;
    if (t < firstLight - GANTRY_LEAD || t >= out + LIGHTS_OUT_SHOWN) return showGantry(null, false);
    if (t >= out) return showGantry(0, true);
    const lit = t < firstLight ? 0 : Math.min(5, Math.floor((t - firstLight) / LIGHT_STEP) + 1);
    showGantry(lit, false);
  }

  function mark(number) {
    if (state.marks.has(number)) return state.marks.get(number);
    const who = state.meta.drivers[number] || { code: String(number), colour: "#898781" };
    const group = document.createElementNS(SVG, "g");
    const dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("r", 7);
    dot.setAttribute("fill", who.colour);
    dot.setAttribute("class", "car-dot");
    const label = document.createElementNS(SVG, "text");
    label.setAttribute("x", 11);
    label.setAttribute("y", 4);
    label.setAttribute("class", "car-label");
    label.textContent = who.code;
    group.append(dot, label);
    el("track-svg").append(group);
    const entry = { group, x: null, y: null };
    state.marks.set(number, entry);
    return entry;
  }

  function place(entry, x, y) {
    entry.x = x; entry.y = y;
    entry.group.style.display = "";
    entry.group.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
  }

  function chunkIndex(t) { return Math.floor(t / state.meta.chunkSeconds); }

  async function fetchChunk(index, loadId) {
    if (state.chunks.has(index) || state.pending.has(index)) return;
    if (index * state.meta.chunkSeconds > state.meta.duration) return;
    state.pending.add(index);
    try {
      const response = await fetch(`/api/track/chunk/${state.sessionKey}/${index}`);
      if (loadId !== state.loadId) return;
      if (!response.ok) throw new Error((await response.json()).error);
      state.chunks.set(index, await response.json());
      for (const key of state.chunks.keys()) {
        if (Math.abs(key - index) > 6) state.chunks.delete(key); // keep memory flat
      }
      note("");
      render(); // a paused player has no animation loop to show the new data
    } catch (error) {
      if (loadId === state.loadId) note(error.message || "Couldn't load car positions.");
    } finally {
      state.pending.delete(index);
    }
  }

  function ensureChunks() {
    if (!state.meta || state.mode !== "replay") return;
    const index = chunkIndex(state.clock);
    fetchChunk(index, state.loadId);
    const untilNext = (index + 1) * state.meta.chunkSeconds - state.clock;
    if (untilNext < PREFETCH_SECONDS * state.speed) fetchChunk(index + 1, state.loadId);
  }

  function lastBefore(list, t) {
    let low = 0, high = list.length - 1, found = -1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (list[middle] <= t) { found = middle; low = middle + 1; } else { high = middle - 1; }
    }
    return found;
  }

  function drawCars() {
    const chunk = state.chunks.get(chunkIndex(state.clock));
    state.buffering = !chunk;
    if (!chunk || !chunk.t.length) return;
    const i = lastBefore(chunk.t, state.clock);
    const from = Math.max(i, 0);
    const to = Math.min(from + 1, chunk.t.length - 1);
    const span = chunk.t[to] - chunk.t[from];
    const blend = span > 0 ? Math.min(Math.max((state.clock - chunk.t[from]) / span, 0), 1) : 0;
    const seen = new Set();
    for (const [number, car] of Object.entries(chunk.cars)) {
      const x0 = car.x[from], y0 = car.y[from];
      if (x0 === null || y0 === null) continue;
      const x1 = car.x[to] ?? x0, y1 = car.y[to] ?? y0;
      const [px, py] = state.project(x0 + (x1 - x0) * blend, y0 + (y1 - y0) * blend);
      place(mark(number), px, py);
      seen.add(number);
    }
    for (const [number, entry] of state.marks) {
      if (!seen.has(number)) entry.group.style.display = "none";
    }
  }

  function showOrder(order) {
    const list = el("track-order");
    if (!list) return;
    const rows = Object.entries(order).sort((a, b) => a[1] - b[1]);
    const key = rows.map((r) => r[0]).join();
    if (list.dataset.key === key) return;
    list.dataset.key = key;
    list.replaceChildren(...rows.map(([number, position]) => {
      const who = state.meta.drivers[number] || { code: number, colour: "#898781" };
      const item = document.createElement("li");
      const place = document.createElement("span");
      place.className = "order-pos";
      place.textContent = position;
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = who.colour;
      item.append(place, swatch, who.code);
      return item;
    }));
  }

  function showSidePanel() {
    const meta = state.meta, t = state.clock;
    const order = {};
    for (const [time, number, position] of meta.order) {
      if (time > t) break;
      order[number] = position;
    }
    showOrder(order);
    let lap = 0;
    for (const [time, number] of meta.laps) {
      if (time > t) break;
      lap = number;
    }
    const total = meta.laps.length ? meta.laps[meta.laps.length - 1][1] : 0;
    el("track-lap").textContent = lap ? `Lap ${lap} of ${total}` : "";
    let text = "";
    for (const [time, message] of meta.messages) {
      if (time > t) break;
      text = message;
    }
    el("track-message").textContent = text;
    let status = null;
    for (const [time, snapshot] of meta.status) {
      if (time > t) break;
      status = snapshot;
    }
    showStatus(status);
    replayGantry();
  }

  function render() {
    if (!state.meta || !state.project || !el("track-svg")) return;
    el("track-seek").value = state.clock;
    el("track-time").textContent = clockText(state.clock);
    drawCars();
    showSidePanel();
  }

  function tick(now) {
    state.frame = requestAnimationFrame(tick);
    if (!state.active) return;
    if (state.mode === "live") {
      for (const [number, target] of state.liveTargets) {
        const entry = mark(number);
        const [tx, ty] = state.project(target[0], target[1]);
        place(entry, entry.x === null ? tx : entry.x + (tx - entry.x) * LIVE_EASING,
              entry.y === null ? ty : entry.y + (ty - entry.y) * LIVE_EASING);
      }
      return;
    }
    if (state.playing && state.meta) {
      const elapsed = (now - state.lastTick) / 1000;
      state.lastTick = now;
      ensureChunks();
      if (!state.buffering) state.clock += elapsed * state.speed; // hold while loading
      if (state.clock >= state.meta.duration) {
        state.clock = state.meta.duration;
        setPlaying(false);
      }
      render();
    }
  }

  function replayControls(visible) {
    ["track-play", "track-seek", "track-time", "track-speeds"].forEach((id) => {
      const node = el(id);
      if (node) node.hidden = !visible;
    });
  }

  async function startReplay(resumeAt = 0) {
    stopPolling();
    state.mode = "replay";
    const id = ++state.loadId;
    state.meta = null;
    state.chunks.clear();
    state.pending.clear();
    state.clock = resumeAt;
    setPlaying(false);
    replayControls(true);
    el("track-live").textContent = "Watch live";
    el("track-message").textContent = "";
    el("track-flags").replaceChildren();
    showGantry(null, false);
    if (!state.sessionKey) return;
    note("Loading circuit…");
    try {
      const response = await fetch(`/api/track/meta/${state.sessionKey}`);
      const body = await response.json();
      if (id !== state.loadId) return;
      if (!response.ok) throw new Error(body.error);
      state.meta = body;
      drawCircuit(body);
      el("track-title").textContent = body.title;
      el("track-seek").max = body.duration;
      el("track-order").dataset.key = "";
      note("");
      ensureChunks();
      render();
    } catch (error) {
      if (id === state.loadId) {
        el("track-svg").replaceChildren();
        el("track-order").replaceChildren();
        note(error.message || "Couldn't load this session.");
      }
    }
  }

  function stopPolling() {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
    state.liveTargets.clear();
  }

  async function pollLive(id) {
    if (id !== state.loadId || state.mode !== "live" || !state.active) return;
    try {
      const body = await (await fetch("/api/track/live")).json();
      if (id !== state.loadId) return;
      state.liveTargets = new Map(Object.entries(body.cars));
      showOrder(body.order);
      el("track-message").textContent = body.message;
      showStatus(body.status);
      // The feed only reports lights-out, about 3 s late, so there's no build-up to show.
      const justStarted = body.lightsOutAgo !== null && body.lightsOutAgo < LIGHTS_OUT_SHOWN;
      showGantry(justStarted ? 0 : null, justStarted);
      const waiting = Object.keys(body.cars).length ? "" : "Connected. Waiting for car data…";
      note(body.error || (body.connected ? waiting : "Connecting to OpenF1…"));
    } catch (error) {
      note("Lost contact with the local server.");
    }
    state.pollTimer = setTimeout(() => pollLive(id), LIVE_POLL_MS);
  }

  async function startLive() {
    const id = ++state.loadId;
    setPlaying(false);
    state.mode = "live";
    replayControls(false);
    el("track-live").textContent = "Back to replay";
    note("Loading circuit…");
    try {
      const response = await fetch("/api/track/live/meta");
      const body = await response.json();
      if (id !== state.loadId) return;
      if (!response.ok) throw new Error(body.error);
      state.meta = body;
      drawCircuit(body);
      el("track-title").textContent = body.title;
      el("track-lap").textContent = "Live, about 3 seconds behind";
      el("track-order").dataset.key = "";
      pollLive(id);
    } catch (error) {
      if (id === state.loadId) note(error.message || "Couldn't start live view.");
    }
  }

  async function refreshLiveButton() {
    try {
      const body = await (await fetch("/api/track/status")).json();
      const button = el("track-live");
      if (!button) return;
      button.hidden = !body.canWatchLive && state.mode !== "live";
      const hint = el("track-hint");
      if (hint) hint.textContent = body.hint;
    } catch (error) { /* the button simply stays as it was */ }
  }

  window.f1track = {
    load(sessionKey, tab) {
      state.active = tab === "track";
      if (!state.active) {
        setPlaying(false);
        stopPolling();
        return;
      }
      // dcc.Tabs may mount the tab after this runs, so wait for the root.
      const root = el("track-root");
      if (!root) { requestAnimationFrame(() => this.load(sessionKey, tab)); return; }
      const fresh = !root.firstChild;
      if (fresh) buildShell(root);
      if (state.frame === null) state.frame = requestAnimationFrame(tick);
      refreshLiveButton();
      if (fresh || sessionKey !== state.sessionKey) {
        const sameSession = sessionKey === state.sessionKey;
        state.sessionKey = sessionKey;
        startReplay(sameSession ? state.clock : 0); // returning to the tab keeps your place
      } else if (state.mode === "live") {
        pollLive(state.loadId); // leaving the tab stopped polling
      }
    },
  };
})();
