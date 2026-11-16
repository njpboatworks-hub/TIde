/* SailIQ Wind frontend. Plain JavaScript, no build step. */
"use strict";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const $ = (s) => document.querySelector(s);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtSigned = (x, unit = "°") => (x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x) + unit);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let VENUES = {}, venueId = "newport", venue = null;
let map, layers = {}, charts = {};
let planResult = null, selectedDay = null;
let currentTrack = null, colorMode = "lift";

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

/* ---------------- Map ---------------- */
function initMap() {
  map = L.map("map", { zoomControl: true }).setView(venue.center, venue.zoom);
  map.fitBounds(L.latLngBounds(Object.values(venue.stations).map((s) => [s.lat, s.lon])), { padding: [60, 60] });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  layers.stations = L.layerGroup().addTo(map);
  layers.track = L.layerGroup().addTo(map);
  layers.lift = L.layerGroup().addTo(map);
  drawStations({});
}

function arrowIcon(dir, speed, label) {
  // Arrow points where the wind blows TO (dir + 180).
  const rot = dir == null ? 0 : (dir + 180) % 360;
  const arrow = dir == null
    ? `<circle cx="14" cy="14" r="5" fill="${css("--muted")}"/>`
    : `<g transform="rotate(${rot} 14 14)"><path d="M14 3 L20 17 L14 14 L8 17 Z" fill="${css("--accent")}" stroke="#fff" stroke-width="1.5"/><line x1="14" y1="14" x2="14" y2="25" stroke="${css("--accent")}" stroke-width="3" stroke-linecap="round"/></g>`;
  const txt = dir == null ? "no data" : `${dir}° ${speed ?? "–"} kt`;
  return L.divIcon({
    className: "station-icon", iconSize: [28, 28], iconAnchor: [14, 14],
    html: `<svg width="28" height="28" viewBox="0 0 28 28" role="img" aria-label="${esc(label)} ${txt}">${arrow}</svg><span class="lab">${txt}</span>`,
  });
}

function drawStations(values) {
  layers.stations.clearLayers();
  for (const [sid, s] of Object.entries(venue.stations)) {
    const v = values[sid] || {};
    L.marker([s.lat, s.lon], { icon: arrowIcon(v.dir ?? null, v.speed ?? null, s.name) })
      .bindTooltip(`<b>${esc(s.name)}</b>` + (v.dir != null ? `<br>From ${v.dir}° at ${v.speed} kt` : ""), { direction: "top", offset: [0, -12] })
      .addTo(layers.stations);
  }
}

function divergingColor(x, max, neg, pos) {
  // Two hues with a neutral gray midpoint.
  const t = Math.max(-1, Math.min(1, x / max));
  const mid = hex(css("--neutral"));
  const end = hex(t < 0 ? neg : pos);
  const a = Math.abs(t);
  const c = mid.map((m, i) => Math.round(m + (end[i] - m) * a));
  return `rgb(${c.join(",")})`;
}
function hex(h) { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); }

function setLegend(html) {
  const el = $("#map-legend");
  if (!html) { el.hidden = true; return; }
  el.innerHTML = html; el.hidden = false;
}
function divergingLegend(title, leftLabel, rightLabel, negVar, posVar) {
  return `<b>${title}</b><div class="bar" style="background:linear-gradient(90deg, ${css(negVar)}, ${css("--neutral")}, ${css(posVar)})"></div><div class="ends"><span>${leftLabel}</span><span>${rightLabel}</span></div>`;
}

/* ---------------- Charts ---------------- */
function chartDefaults() {
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = false;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.tooltip.mode = "index";
  Chart.defaults.plugins.tooltip.intersect = false;
  Chart.defaults.interaction = { mode: "index", intersect: false };
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 4;
}
function makeChart(id, cfg) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), cfg);
  return charts[id];
}
const minLabel = (m) => `+${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;

/* ---------------- Tabs ---------------- */
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
  layers.track.clearLayers(); layers.lift.clearLayers(); setLegend(null);
  $("#liftmap-btn").textContent = "Show venue lift map";
  if (name === "plan" && planResult) updateSlider();
  else if (name !== "plan") drawStations({});
  if (name === "debrief") { refreshTrackList(); if (currentTrack) drawTrack(); }
  if (name === "live") loadLive();
  if (name === "model") loadModel();
  setTimeout(() => map.invalidateSize(), 50);
}

/* ---------------- Planner ---------------- */
function initPlanner() {
  const box = $("#months");
  const cur = new Date().getMonth() + 1;
  const def = venue.season_months.includes(cur) ? [cur] : [7, 8];
  MONTHS.forEach((m, i) => {
    const n = i + 1;
    box.insertAdjacentHTML("beforeend", `<label><input type="checkbox" value="${n}" ${def.includes(n) ? "checked" : ""}>${m}</label>`);
  });
  const cm = $("#clim-month");
  MONTHS.forEach((m, i) => cm.insertAdjacentHTML("beforeend", `<option value="${i + 1}">${m}</option>`));
  cm.value = def[0];
  cm.addEventListener("change", loadClimatology);
  $("#plan-form").addEventListener("submit", (e) => { e.preventDefault(); runPlan(); });
  $("#time-slider").addEventListener("input", updateSlider);
  loadClimatology();
}

async function runPlan() {
  const months = [...document.querySelectorAll("#months input:checked")].map((i) => +i.value);
  if (!months.length) { alert("Pick at least one month."); return; }
  const [h, m] = $("#start").value.split(":").map(Number);
  const body = {
    months, start_hour: h + m / 60, duration_h: +$("#duration").value,
    direction: +$("#dir").value % 360, speed: +$("#spd").value,
    dir_tol: +$("#dirtol").value, spd_tol: +$("#spdtol").value,
  };
  const btn = $("#plan-form button.primary");
  btn.disabled = true; btn.textContent = "Analyzing…";
  try {
    planResult = await api(`/api/venue/${venueId}/plan`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    selectedDay = null;
    renderPlan();
  } catch (e) { alert(e.message); }
  finally { btn.disabled = false; btn.textContent = "Analyze history"; }
}

function renderPlan() {
  const r = planResult;
  $("#plan-out").hidden = false;
  if (!r.n_analogs) {
    $("#plan-tiles").innerHTML = `<div class="tile hero"><div class="k">No matching days</div><div class="v">0 of ${r.candidates}</div><div class="s">Widen the tolerance or add months.</div></div>`;
    ["#plan-split", "#plan-advice", "#days-table tbody"].forEach((s) => ($(s).innerHTML = ""));
    return;
  }
  const s = r.summary;
  const side = s.pct_persistent_right >= 50 && s.pct_persistent_right > 2 * s.pct_persistent_left ? "Right side"
    : s.pct_persistent_left >= 50 && s.pct_persistent_left > 2 * s.pct_persistent_right ? "Left side" : "Play the shifts";
  $("#plan-tiles").innerHTML = `
    <div class="tile hero"><div class="k">Historical edge (upwind, looking at the wind)</div><div class="v">${side}</div>
      <div class="s">Based on ${r.n_analogs} similar days out of ${r.candidates} in the selected months</div></div>
    <div class="tile"><div class="k">Typical shift by finish</div><div class="v">${fmtSigned(s.median_end_shift)}</div><div class="s">median, + = right</div></div>
    <div class="tile"><div class="k">Trend</div><div class="v">${fmtSigned(s.median_trend_deg_per_h)}/h</div><div class="s">persistent part</div></div>
    <div class="tile"><div class="k">Oscillation</div><div class="v">±${s.median_osc_amplitude ?? "–"}°</div><div class="s">${s.median_osc_period_min ? `~${s.median_osc_period_min} min cycle` : "cycle unclear"}</div></div>
    <div class="tile"><div class="k">Speed change</div><div class="v">${fmtSigned(s.median_speed_change, " kt")}</div><div class="s">start to finish</div></div>`;
  const seg = (cls, p, lbl) => (p > 0 ? `<div class="${cls}" style="flex:${p}" title="${lbl}: ${p}%">${p >= 12 ? p + "%" : ""}</div>` : "");
  $("#plan-split").innerHTML = seg("l", s.pct_persistent_left, "Went left") + seg("o", s.pct_oscillating, "Oscillated") + seg("r", s.pct_persistent_right, "Went right");
  $("#plan-split").insertAdjacentHTML("afterend", "");
  const legend = document.getElementById("split-legend") || Object.assign(document.createElement("div"), { id: "split-legend", className: "split-legend" });
  legend.innerHTML = `<span><i class="swatch" style="background:var(--left)"></i>Went left (backed &gt;10°) ${s.pct_persistent_left}%</span>
    <span><i class="swatch" style="background:var(--neutral)"></i>Stayed within ±10° ${s.pct_oscillating}%</span>
    <span><i class="swatch" style="background:var(--right)"></i>Went right (veered &gt;10°) ${s.pct_persistent_right}%</span>`;
  $("#plan-split").after(legend);
  $("#plan-advice").innerHTML = r.advice.map((a) => `<li>${esc(a)}</li>`).join("");

  renderEnvelope();
  const labels = r.times_min.map(minLabel);
  const e = r.envelope;
  makeChart("spd-chart", {
    type: "line",
    data: { labels, datasets: [
      { label: "75th pct", data: e.speed_p75, borderWidth: 0, backgroundColor: css("--band2"), fill: "+1" },
      { label: "25th pct", data: e.speed_p25, borderWidth: 0, fill: false },
      { label: "Median speed", data: e.speed_p50, borderColor: css("--accent") },
    ] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y} kt` } } },
      scales: { y: { title: { display: true, text: "knots" } } } },
  });

  const sl = $("#time-slider");
  sl.max = r.times_min.length - 1; sl.value = 0;
  updateSlider();

  $("#days-count").textContent = `(${r.days.length})`;
  $("#days-table tbody").innerHTML = r.days.map((d, i) =>
    `<tr data-i="${i}"><td>${d.date}</td><td>${d.start_dir}° ${d.start_speed} kt</td><td>${fmtSigned(d.end_shift)}</td><td>${d.osc_amp != null ? "±" + d.osc_amp + "°" : "–"}</td><td>${d.end_speed} kt</td></tr>`).join("");
  document.querySelectorAll("#days-table tbody tr").forEach((tr) => tr.addEventListener("click", () => {
    const i = +tr.dataset.i;
    selectedDay = selectedDay === i ? null : i;
    document.querySelectorAll("#days-table tbody tr").forEach((x) => x.classList.toggle("sel", +x.dataset.i === selectedDay));
    renderEnvelope();
  }));
}

function renderEnvelope() {
  const r = planResult, e = r.envelope;
  const labels = r.times_min.map(minLabel);
  const ds = [
    { label: "90th pct", data: e.p90, borderWidth: 0, backgroundColor: css("--band1"), fill: "+1" },
    { label: "10th pct", data: e.p10, borderWidth: 0, fill: false },
    { label: "75th pct", data: e.p75, borderWidth: 0, backgroundColor: css("--band2"), fill: "+1" },
    { label: "25th pct", data: e.p25, borderWidth: 0, fill: false },
    { label: "Median", data: e.p50, borderColor: css("--accent") },
  ];
  if (selectedDay != null) {
    const d = r.days[selectedDay];
    ds.push({ label: d.date, data: d.trace, borderColor: css("--header"), borderDash: [4, 3], spanGaps: true });
  }
  makeChart("env-chart", {
    type: "line", data: { labels, datasets: ds },
    options: {
      plugins: {
        legend: { labels: { filter: (i) => ["Median"].includes(i.text) || i.datasetIndex >= 5 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmtSigned(c.parsed.y)}` } },
      },
      scales: {
        y: { title: { display: true, text: "← left · degrees · right →" },
          grid: { color: (c) => (c.tick.value === 0 ? css("--axis") : css("--grid")) } },
        x: { title: { display: true, text: "time after start (h:mm)" } },
      },
    },
  });
}

function updateSlider() {
  if (!planResult || !planResult.stations) return;
  const i = +$("#time-slider").value;
  const vals = {};
  for (const [sid, st] of Object.entries(planResult.stations)) vals[sid] = { dir: st.dir[i], speed: st.speed[i] };
  drawStations(vals);
  const q = planResult.query;
  const t = q.start_hour * 60 + planResult.times_min[i];
  $("#time-label").textContent = `${String(Math.floor(t / 60) % 24).padStart(2, "0")}:${String(Math.round(t % 60)).padStart(2, "0")} local (${minLabel(planResult.times_min[i])} after start), vector average of ${planResult.n_analogs} days`;
}

async function loadClimatology() {
  const m = +$("#clim-month").value;
  const c = await api(`/api/venue/${venueId}/climatology?month=${m}`);
  const labels = c.hours.map((h) => `${h}:00`);
  makeChart("clim-dir", {
    type: "line",
    data: { labels, datasets: [{ label: "Median direction", data: c.dir, borderColor: css("--accent"), pointRadius: 3 }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: (x) => `From ${x.parsed.y}°` } } },
      scales: { y: { title: { display: true, text: "wind from (°)" } } } },
  });
  makeChart("clim-spd", {
    type: "line",
    data: { labels, datasets: [{ label: "Median speed", data: c.spd, borderColor: css("--accent"), pointRadius: 3 }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: (x) => `${x.parsed.y} kt` } } },
      scales: { y: { title: { display: true, text: "knots" }, beginAtZero: true } } },
  });
}

/* ---------------- Venue lift map ---------------- */
async function toggleLiftMap() {
  if (layers.lift.getLayers().length) { layers.lift.clearLayers(); setLegend(null); $("#liftmap-btn").textContent = "Show venue lift map"; return; }
  const dir = planResult ? planResult.query.direction : null;
  const q = dir != null ? `?direction=${dir}&tol=30` : "";
  const lm = await api(`/api/venue/${venueId}/liftmap${q}`);
  if (!lm.cells.length) {
    alert("No saved debrief tracks match these conditions yet. Upload GPX tracks (or load the demo tracks) in Coach Debrief to build the venue lift map.");
    return;
  }
  const max = Math.max(4, ...lm.cells.map((c) => Math.abs(c.shift)));
  for (const c of lm.cells) {
    L.rectangle([[c.lat0, c.lon0], [c.lat1, c.lon1]], {
      stroke: false, fillOpacity: 0.55, fillColor: divergingColor(c.shift, max, css("--left"), css("--right")),
    }).bindTooltip(`Wind here averages ${fmtSigned(c.shift)} vs. the session mean<br>${c.n_sessions} session(s), ${c.n_points} points`).addTo(layers.lift);
  }
  setLegend(divergingLegend(`Venue lift map (${lm.sessions_used} sessions${dir != null ? `, wind ${dir}° ±30°` : ""})`,
    `left/backed −${Math.round(max)}°`, `right/veered +${Math.round(max)}°`, "--left", "--right") +
    `<div class="ends" style="margin-top:4px;color:var(--muted)">From boats compared with each other in the same race. The pattern is more reliable than the exact numbers, which tend to understate the shift.</div>`);
  $("#liftmap-btn").textContent = "Hide venue lift map";
  map.fitBounds(layers.lift.getLayers().reduce((b, l) => b.extend(l.getBounds()), L.latLngBounds([])), { padding: [80, 80] });
}

/* ---------------- Live ---------------- */
async function loadLive() {
  const out = $("#live-out");
  out.innerHTML = `<p class="hint">Loading live NOAA data…</p>`;
  try {
    const d = await api(`/api/venue/${venueId}/live`);
    drawStations(d.stations);
    const p = d.prediction;
    let html = d.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join("");
    if (p) {
      const dirWord = Math.abs(p.shift_median) < 3 ? "little net change" : p.shift_median > 0 ? "a shift RIGHT (veer)" : "a shift LEFT (back)";
      html += `<div class="tiles">
        <div class="tile hero"><div class="k">AI forecast: next ${p.horizon_h} h at Newport harbor</div>
          <div class="v">${fmtSigned(p.shift_median)} — ${dirWord}</div>
          <div class="s">80% range ${fmtSigned(p.shift_p10)} to ${fmtSigned(p.shift_p90)} · from ${p.current_dir}° at ${p.current_speed} kt as of ${p.as_of_local}</div></div>
      </div>
      <p class="hint">On the 2024 test season the model's average error was ${d.metrics.mae_model}° vs ${d.metrics.mae_persistence}° for assuming no shift. See the Model Accuracy tab.</p>
      <button id="live-plan">Run the planner with these conditions</button>`;
    }
    html += `<h2>Latest station readings</h2><div class="table-wrap"><table><thead><tr><th>Station</th><th>Time</th><th>From</th><th>Speed</th><th>Gust</th></tr></thead><tbody>${
      Object.values(d.stations).map((s) => `<tr><td>${esc(s.name)}</td><td>${s.time_local.slice(11)}</td><td>${s.dir}°</td><td>${s.speed} kt</td><td>${s.gust ?? "–"} kt</td></tr>`).join("")
    }</tbody></table></div>`;
    out.innerHTML = html;
    const lp = $("#live-plan");
    if (lp) lp.addEventListener("click", () => {
      const now = new Date();
      document.querySelectorAll("#months input").forEach((i) => (i.checked = +i.value === now.getMonth() + 1));
      const mins = Math.round((now.getHours() * 60 + now.getMinutes()) / 10) * 10;
      $("#start").value = `${String(Math.floor(mins / 60) % 24).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;
      $("#dir").value = p.current_dir; $("#spd").value = Math.round(p.current_speed);
      showTab("plan"); runPlan();
    });
  } catch (e) {
    out.innerHTML = `<div class="warn">${esc(e.message)}</div>`;
  }
}

/* ---------------- Debrief ---------------- */
function initDebrief() {
  $("#upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = $("#gpx").files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f); fd.append("name", $("#gpx-name").value || f.name); fd.append("venue", venueId);
    if ($("#twd-hint").value !== "") fd.append("twd_hint", $("#twd-hint").value);
    const btn = $("#upload-form button.primary");
    btn.disabled = true; btn.textContent = "Analyzing…";
    try { currentTrack = await api("/api/debrief", { method: "POST", body: fd }); renderTrack(); refreshTrackList(); }
    catch (err) { alert(err.message); }
    finally { btn.disabled = false; btn.textContent = "Analyze track"; }
  });
  $("#load-samples").addEventListener("click", async () => {
    const r = await api(`/api/debrief/load-samples?venue=${venueId}`, { method: "POST" });
    await refreshTrackList();
    if (!r.added) alert("Demo tracks are already loaded.");
  });
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    colorMode = b.dataset.color;
    document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("active", x === b));
    drawTrack();
  }));
}

async function refreshTrackList() {
  const list = await api(`/api/debriefs?venue=${venueId}`);
  const box = $("#debrief-list");
  if (!list.length) { box.innerHTML = `<p class="hint" style="margin-top:14px">No tracks yet. Upload one, or load the demo tracks.</p>`; return; }
  box.innerHTML = `<h2>Saved tracks</h2><div class="list">${list.map((t) =>
    `<div class="item ${currentTrack && currentTrack.id === t.id ? "sel" : ""}" data-id="${t.id}"><div><div>${esc(t.name)}</div><div class="meta">${t.start_local} · wind ~${t.inferred_twd}°</div></div><button class="del" data-del="${t.id}" aria-label="Delete ${esc(t.name)}">Delete</button></div>`).join("")}</div>`;
  box.querySelectorAll(".item").forEach((el) => el.addEventListener("click", async (e) => {
    if (e.target.dataset.del) {
      if (!confirm("Delete this track?")) return;
      await api(`/api/debrief/${e.target.dataset.del}`, { method: "DELETE" });
      if (currentTrack && currentTrack.id === e.target.dataset.del) { currentTrack = null; $("#debrief-out").hidden = true; layers.track.clearLayers(); setLegend(null); }
      return refreshTrackList();
    }
    currentTrack = await api(`/api/debrief/${el.dataset.id}`);
    renderTrack(); refreshTrackList();
  }));
}

function renderTrack() {
  const t = currentTrack, s = t.summary;
  $("#debrief-out").hidden = false;
  $("#debrief-title").textContent = t.name;
  const hdr = s.n_tacks ? Math.round((100 * s.n_tacks_on_header) / s.n_tacks) : null;
  $("#debrief-tiles").innerHTML = `
    <div class="tile"><div class="k">Start</div><div class="v">${s.start_local.slice(11)}</div><div class="s">${s.start_local.slice(0, 10)} · ${s.duration_min} min</div></div>
    <div class="tile"><div class="k">Inferred wind</div><div class="v">${s.inferred_twd}°</div><div class="s">tacking angle ${Math.round(s.half_tack_angle * 2)}°${s.station_median_dir != null ? ` · station ${s.station_median_dir}°` : ""}</div></div>
    <div class="tile"><div class="k">Upwind on lifted tack</div><div class="v">${s.pct_upwind_lifted ?? "–"}%</div><div class="s">of upwind time</div></div>
    <div class="tile"><div class="k">Tacks on headers</div><div class="v">${s.n_tacks_on_header}/${s.n_tacks}</div><div class="s">${hdr != null ? hdr + "%" : ""}</div></div>
    <div class="tile"><div class="k">Upwind VMG</div><div class="v">${s.avg_upwind_vmg ?? "–"} kt</div><div class="s">vs. inferred wind</div></div>`;
  $("#debrief-insights").innerHTML = t.insights.map((x) => `<li>${esc(x)}</li>`).join("");

  const labels = t.twd_series.map((x) => x[0]);
  const ds = [{ label: "GPS-inferred wind", data: t.twd_series.map((x) => x[1]), borderColor: css("--accent"), spanGaps: false }];
  if (t.station) {
    const m = Object.fromEntries(t.station.series.map((x) => [x[0], x[1]]));
    ds.push({ label: "Newport harbor station", data: labels.map((l) => m[l] ?? null), borderColor: css("--header"), spanGaps: true, pointRadius: 3, showLine: true, borderDash: [4, 3] });
  }
  makeChart("twd-chart", {
    type: "line", data: { labels, datasets: ds },
    options: { plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y}°` } } },
      scales: { y: { title: { display: true, text: "wind from (°)" } }, x: { ticks: { maxTicksLimit: 10 } } } },
  });
  drawTrack(true);
}

function drawTrack(fit = false) {
  layers.track.clearLayers();
  if (!currentTrack) return;
  const pts = currentTrack.points; // [lat, lon, time, sog, cog, twd_est, lift, upwind]
  const twd0 = currentTrack.summary.inferred_twd;
  const maxv = 12;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    let color = css("--muted"), val = null;
    if (b[7] && b[6] != null) {
      if (colorMode === "lift") { val = b[6]; color = divergingColor(val, maxv, css("--header"), css("--lift")); }
      else { val = ((b[5] - twd0 + 540) % 360) - 180; color = divergingColor(val, maxv, css("--left"), css("--right")); }
    }
    const tip = `${b[2]} · ${b[3]} kt · COG ${b[4]}°` + (b[7] && b[6] != null
      ? `<br>${b[6] > 0 ? "Lifted" : "Headed"} ${Math.abs(b[6])}° · wind est. ${b[5]}°` : "<br>Off the wind");
    L.polyline([[a[0], a[1]], [b[0], b[1]]], { color, weight: 4, opacity: 0.95 }).bindTooltip(tip, { sticky: true }).addTo(layers.track);
  }
  for (const tk of currentTrack.tacks) {
    L.circleMarker([tk.lat, tk.lon], { radius: 5, color: "#fff", weight: 2, fillOpacity: 1,
      fillColor: tk.on_header ? css("--lift") : css("--header") })
      .bindTooltip(`Tack at ${tk.time_local}: ${tk.on_header ? "on a header (good)" : "on a lift"} · wind ${fmtSigned(tk.shift_vs_mean)} vs 10-min mean`).addTo(layers.track);
  }
  if (colorMode === "lift") setLegend(divergingLegend("Upwind: lifted vs. headed", `headed ${maxv}°`, `lifted ${maxv}°`, "--header", "--lift") +
    `<div style="margin-top:6px"><i class="swatch" style="background:var(--lift);border-radius:50%"></i>tack on a header (good)</div><div><i class="swatch" style="background:var(--header);border-radius:50%"></i>tack on a lift</div><div><i class="swatch" style="background:var(--muted)"></i>off the wind (not scored)</div>`);
  else setLegend(divergingLegend(`Wind vs. race mean (${twd0}°)`, `left −${maxv}°`, `right +${maxv}°`, "--left", "--right"));
  if (fit) map.fitBounds(L.latLngBounds(pts.map((p) => [p[0], p[1]])), { padding: [30, 30] });
}

/* ---------------- Model ---------------- */
async function loadModel() {
  const m = await api(`/api/venue/${venueId}/model`);
  const imp = Math.round((1 - m.mae_model / m.mae_persistence) * 100);
  $("#model-out").innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="k">Average error (AI)</div><div class="v">${m.mae_model}°</div><div class="s">${imp}% better than "no shift"</div></div>
      <div class="tile"><div class="k">Calls the side of big shifts</div><div class="v">${Math.round(m.big_shift_direction_accuracy * 100)}%</div><div class="s">climatology: ${Math.round(m.big_shift_direction_accuracy_climatology * 100)}%</div></div>
      <div class="tile"><div class="k">80% range contains truth</div><div class="v">${Math.round(m.interval_80_coverage * 100)}%</div><div class="s">target 80%</div></div>
    </div>
    <div class="table-wrap"><table class="metrics"><thead><tr><th>Measure (test year ${m.test_year})</th><th>Value</th></tr></thead><tbody>
      <tr><td>Mean absolute error, AI model</td><td>${m.mae_model}°</td></tr>
      <tr><td>Mean absolute error, persistence (assume no shift)</td><td>${m.mae_persistence}°</td></tr>
      <tr><td>Mean absolute error, climatology (typical for month and hour)</td><td>${m.mae_climatology}°</td></tr>
      <tr><td>Big shifts (≥10°) in test set</td><td>${m.n_big_shifts_test}</td></tr>
      <tr><td>Side called correctly on big shifts, AI</td><td>${(m.big_shift_direction_accuracy * 100).toFixed(1)}%</td></tr>
      <tr><td>Side called correctly on big shifts, climatology</td><td>${(m.big_shift_direction_accuracy_climatology * 100).toFixed(1)}%</td></tr>
      <tr><td>80% prediction interval coverage</td><td>${(m.interval_80_coverage * 100).toFixed(1)}%</td></tr>
      <tr><td>Training samples (${m.train_years.join(", ")})</td><td>${m.n_train.toLocaleString()}</td></tr>
      <tr><td>Test samples (${m.test_year})</td><td>${m.n_test.toLocaleString()}</td></tr>
    </tbody></table></div>
    <ul class="advice">${m.notes.map((n) => `<li>${esc(n)}</li>`).join("")}
      <li>What this means: the model gives a real but modest edge on the size of a shift, and a clearer edge on which way a big shift will go. It forecasts the harbor station, not every spot on the course.</li>
      ${m.interval_80_coverage < 0.77 ? `<li>The 80% range is slightly too narrow (it caught ${(m.interval_80_coverage * 100).toFixed(0)}% of outcomes). Read it as a rough range.</li>` : ""}
    </ul>`;
}

/* ---------------- Boot ---------------- */
async function boot() {
  chartDefaults();
  VENUES = await api("/api/venues");
  const sel = $("#venue");
  for (const [k, v] of Object.entries(VENUES)) sel.insertAdjacentHTML("beforeend", `<option value="${k}">${esc(v.name)}</option>`);
  venue = VENUES[venueId];
  initMap(); initPlanner(); initDebrief();
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("#liftmap-btn").addEventListener("click", toggleLiftMap);
  runPlan();
}
boot();
