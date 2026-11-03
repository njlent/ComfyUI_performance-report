// Performance tab for ComfyUI's bottom panel. Mounted by performance_dashboard.js, data from dashboard.py.
import { api } from "../../scripts/api.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const POLL_LIVE_MS = 1000;
const POLL_IDLE_MS = 2500;
const TOP_ROWS = 10;
const HISTORY_RUNS = 30;

// Kept between mounts so closing and reopening the panel keeps the selection
const state = {
  runs: [],              // run summaries, newest first
  live: null,            // the running workflow, or null
  selected: null,        // "live" or a run id
  details: new Map(),    // run id -> full run
  sort: { key: "order", dir: 1 },
  highlight: null,       // node id highlighted across the charts and the table
  pointer: null,         // last pointer position over the timeline, kept across live re-renders
};

let view = null;         // DOM of the mounted tab

// ---------------------------------------------------------------- helpers

function setProps(el, props) {
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "style") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
}

// Strings become text nodes, so workflow content never turns into markup
function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  setProps(el, props);
  return append(el, children);
}

function s(tag, props = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  setProps(el, props);
  return append(el, children);
}

const ICONS = {
  success: ["circle", "M9 12l2 2 4-4"],
  error: ["circle", "M15 9l-6 6", "M9 9l6 6"],
  interrupted: ["circle", "M10 15V9", "M14 15V9"],
  running: ["M21 12a9 9 0 1 1-6.219-8.56"],
  history: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5", "M12 7v5l4 2"],
  down: ["M12 5v14", "M19 12l-7 7-7-7"],
  up: ["M12 19V5", "M5 12l7-7 7 7"],
  chevronUp: ["M18 15l-6-6-6 6"],
  chevronDown: ["M6 9l6 6 6-6"],
};

function icon(name, size = 16, cls = "") {
  return s("svg", { class: `pr-icon ${cls}`, width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
    ICONS[name].map((d) => (d === "circle" ? s("circle", { cx: 12, cy: 12, r: 10 }) : s("path", { d }))));
}

const STATUS = {
  success: "Completed",
  error: "Failed",
  interrupted: "Interrupted",
  running: "Running",
};

function statusIcon(status, size = 16) {
  return icon(status in STATUS ? status : "error", size, `pr-status-${status}${status === "running" ? " pr-spin" : ""}`);
}

const measureContext = document.createElement("canvas").getContext("2d");
let fontFamily = "sans-serif"; // read from the panel on every render

function textWidth(text, size = 11) {
  measureContext.font = `${size}px ${fontFamily}`;
  return measureContext.measureText(text).width;
}

// Longest prefix of text (with an ellipsis) that fits into width, or null when not even a few characters fit
function fitText(text, width, size = 11) {
  if (textWidth(text, size) <= width) return text;
  let low = 0, high = text.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (textWidth(text.slice(0, mid) + "…", size) <= width) low = mid;
    else high = mid - 1;
  }
  return low >= 3 ? text.slice(0, low) + "…" : null;
}

const sum = (values) => values.reduce((total, value) => total + value, 0);
const maxOf = (values) => values.reduce((max, value) => (value != null && value > max ? value : max), 0);

// ---------------------------------------------------------------- formatting

function fmtDuration(seconds) {
  if (seconds == null) return "–";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 10) return `${seconds.toFixed(2)} s`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const total = Math.round(seconds);
  if (total < 3600) return `${Math.floor(total / 60)} m ${String(total % 60).padStart(2, "0")} s`;
  return `${Math.floor(total / 3600)} h ${String(Math.floor((total % 3600) / 60)).padStart(2, "0")} m`;
}

function fmtOffset(seconds) {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(1).padStart(4, "0")}`;
}

function fmtAxisTime(seconds, step) {
  if (seconds === 0) return "0";
  if (step < 60) return `${+seconds.toFixed(step < 1 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

function fmtGB(value) {
  if (value == null) return "–";
  return `${value.toFixed(value >= 10 ? 1 : 2)} GB`;
}

function fmtDeltaGB(value) {
  if (value == null) return "–";
  if (Math.abs(value) < 0.005) return "0";
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(2)} GB`;
}

const fmtPercent = (fraction) => `${Math.round(fraction * 100)}%`;
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function fmtClock(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtDay(timestamp) {
  const date = new Date(timestamp * 1000);
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: date.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function niceTicks(max, count) {
  const raw = Math.max(max, 1e-9) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * power).find((candidate) => candidate >= raw);
  const ticks = [];
  for (let value = 0; value < max + step * 0.999; value += step) ticks.push(+value.toFixed(10));
  return ticks;
}

function timeStep(duration, count = 7) {
  return [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200].find((step) => duration / step <= count) ?? 14400;
}

function luminance(color) {
  measureContext.fillStyle = "#000";
  measureContext.fillStyle = color;
  const hex = measureContext.fillStyle;
  if (!hex.startsWith("#")) return 0;
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// ---------------------------------------------------------------- data

async function getJSON(route) {
  const response = await api.fetchApi(route, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadRuns() {
  state.runs = (await getJSON("/performance-report/runs")).runs;
}

async function ensureDetails(id) {
  if (!state.details.has(id)) state.details.set(id, await getJSON(`/performance-report/runs/${encodeURIComponent(id)}`));
}

function selectedRun() {
  return state.selected === "live" ? state.live : state.details.get(state.selected) ?? null;
}

function following() {
  return state.selected == null || state.selected === "live" || state.selected === state.runs[0]?.id;
}

async function select(id) {
  state.selected = id;
  state.pointer = null;
  renderSidebar();
  if (id && id !== "live" && !state.details.has(id)) {
    view.main.classList.add("pr-loading");
    try {
      await ensureDetails(id);
    } finally {
      view?.main.classList.remove("pr-loading");
    }
  }
  if (view && state.selected === id) renderMain();
}

async function poll() {
  const current = view;
  try {
    const wasFollowing = following();
    const previous = state.live;
    const { run, last_run: lastRun } = await getJSON("/performance-report/live");
    state.live = run;

    const finished = lastRun != null && lastRun !== state.runs[0]?.id;
    if (finished) {
      await loadRuns();
      if (wasFollowing) {
        await ensureDetails(lastRun).catch(() => {});
        state.selected = lastRun;
      }
    }
    const started = run && run.id !== previous?.id;
    if (started && wasFollowing) state.selected = "live";
    if (state.selected === "live" && !run) state.selected = state.runs[0]?.id ?? null;

    if (view === current) {
      renderSidebar();
      if (state.selected === "live" || finished || started) renderMain();
    }
  } catch (error) {
    console.warn("[ExecutionTimeReporter] Dashboard update failed", error);
  }
  if (view === current) current.timer = setTimeout(poll, state.live ? POLL_LIVE_MS : POLL_IDLE_MS);
}

// ---------------------------------------------------------------- tooltip and highlight

function showTooltip(x, y, content) {
  const tooltip = view.tooltip;
  tooltip.replaceChildren();
  append(tooltip, content);
  tooltip.hidden = false;
  const box = tooltip.getBoundingClientRect();
  const left = x + 16 + box.width > innerWidth - 8 ? x - 16 - box.width : x + 16;
  const top = Math.min(Math.max(8, y - box.height / 2), innerHeight - box.height - 8);
  tooltip.style.left = `${Math.max(8, left)}px`;
  tooltip.style.top = `${top}px`;
}

function showTooltipAt(element, content) {
  const box = element.getBoundingClientRect();
  showTooltip(box.right - 24, box.top + box.height / 2, content);
}

function hideTooltip() {
  if (view) view.tooltip.hidden = true;
}

function tooltipRow(value, label, color) {
  return h("div", { class: "pr-tooltip-row" },
    color && h("span", { class: "pr-tooltip-key", style: { background: color } }),
    h("span", { class: "pr-tooltip-value" }, value),
    h("span", { class: "pr-tooltip-label" }, label));
}

function nodeTooltip(node, nodeTime) {
  return [
    h("div", { class: "pr-tooltip-title" }, node.title),
    h("div", { class: "pr-tooltip-head" }, `${node.class_type} · node ${node.id}`),
    tooltipRow(fmtDuration(node.duration), `${fmtPercent(node.duration / nodeTime)} of node time`),
    tooltipRow(fmtOffset(node.start), "started"),
    node.vram_peak_gb != null && tooltipRow(fmtGB(node.vram_peak_gb), "peak VRAM held by PyTorch"),
  ];
}

function setHighlight(id) {
  state.highlight = id;
  for (const el of view.root.querySelectorAll(".pr-hl")) el.classList.remove("pr-hl");
  if (id != null) for (const el of view.root.querySelectorAll(`[data-node="${CSS.escape(id)}"]`)) el.classList.add("pr-hl");
}

function highlightHandlers(id, content) {
  return {
    onpointerenter: (event) => { setHighlight(id); showTooltip(event.clientX, event.clientY, content()); },
    onpointermove: (event) => showTooltip(event.clientX, event.clientY, content()),
    onpointerleave: () => { setHighlight(null); hideTooltip(); },
    onfocus: (event) => { setHighlight(id); showTooltipAt(event.currentTarget, content()); },
    onblur: () => { setHighlight(null); hideTooltip(); },
  };
}

// ---------------------------------------------------------------- run list

function renderSidebar() {
  const items = [];
  if (state.live) {
    const run = state.live;
    const node = run.current_node;
    const step = node?.progress && node.progress.max > 1 ? ` · ${Math.round(node.progress.value)}/${Math.round(node.progress.max)}` : "";
    items.push(h("div", { class: "pr-run-group" }, "Now"));
    items.push(h("button", { class: `pr-run-item${state.selected === "live" ? " pr-selected" : ""}`, onclick: () => select("live") },
      statusIcon("running"),
      h("div", { class: "pr-run-item-body" },
        h("div", { class: "pr-run-item-row" }, h("span", { class: "pr-run-item-time" }, "Running"), h("span", { class: "pr-run-item-duration" }, fmtDuration(run.duration))),
        h("div", { class: "pr-run-item-meta" }, node ? `${node.title}${step}` : `${plural(run.nodes.length, "node")} done`))));
  }
  let day = null;
  for (const run of state.runs) {
    const runDay = fmtDay(run.started_at);
    if (runDay !== day) {
      items.push(h("div", { class: "pr-run-group" }, runDay));
      day = runDay;
    }
    items.push(h("button", { class: `pr-run-item${state.selected === run.id ? " pr-selected" : ""}`, title: STATUS[run.status] ?? run.status, onclick: () => select(run.id) },
      statusIcon(run.status),
      h("div", { class: "pr-run-item-body" },
        h("div", { class: "pr-run-item-row" }, h("span", { class: "pr-run-item-time" }, fmtClock(run.started_at)), h("span", { class: "pr-run-item-duration" }, fmtDuration(run.duration))),
        h("div", { class: "pr-run-item-meta", title: run.label }, [plural(run.nodes_executed, "node"), run.label].filter(Boolean).join(" · ")))));
  }
  if (!items.length) items.push(h("div", { class: "pr-run-empty" }, "No runs yet"));
  view.runList.replaceChildren(...items);
  view.runCount.textContent = state.runs.length ? String(state.runs.length) : "";
}

// ---------------------------------------------------------------- selected run

function renderMain() {
  // Nothing to lay out while the panel is closed or another tab is in front
  if (!view || !view.main.clientWidth) return;
  const css = getComputedStyle(view.root);
  fontFamily = css.fontFamily;
  const light = luminance(css.getPropertyValue("--pr-surface").trim()) > 0.5;
  view.root.classList.toggle("pr-light", light);
  view.tooltip.classList.toggle("pr-light", light);
  hideTooltip();

  const run = selectedRun();
  if (!run) {
    view.main.replaceChildren(h("div", { class: "pr-empty" },
      icon("history", 28),
      h("div", { class: "pr-empty-title" }, state.runs.length ? "Pick a run" : "No runs yet"),
      h("div", {}, state.runs.length ? "Select a run on the left to see where its time and memory went." : "Queue a workflow and its timings and memory use show up here.")));
    return;
  }

  const width = view.main.clientWidth - 32 - 32;
  const nodeTime = sum(run.nodes.map((node) => node.duration)) || 1e-9;
  const previous = state.runs.find((other) => other.workflow_hash === run.workflow_hash && other.status === "success" && other.started_at < run.started_at);
  const timelineChart = timeline(run, width);

  view.main.replaceChildren(h("div", { class: "pr-run" },
    runHeader(run),
    kpis(run, previous, nodeTime),
    card("Timeline", run.status === "running" ? "Updates live" : "Hover to see any moment of the run", timelineChart),
    h("div", { class: "pr-grid-2" },
      card("Slowest nodes", `${plural(run.nodes.length, "node")} ran`, rankedBars(nodeRows(run), nodeTime)),
      card("Time by node type", "Summed over all nodes of a type", rankedBars(typeRows(run), nodeTime))),
    historyCard(run, width),
    card("All nodes", "Click a column to sort", nodeTable(run, nodeTime))));

  if (state.highlight != null) setHighlight(state.highlight);
  if (state.pointer) timelineChart.updatePointer(state.pointer.x, state.pointer.y);
}

function card(title, subtitle, ...content) {
  return h("section", { class: "pr-card" },
    h("div", { class: "pr-card-header" }, h("h2", { class: "pr-card-title" }, title), subtitle && h("span", { class: "pr-card-sub" }, subtitle)),
    content);
}

function runHeader(run) {
  return h("div", { class: "pr-run-header" },
    h("h1", { class: "pr-run-title" }, run.status === "running" ? "Running now" : `Run at ${fmtClock(run.started_at)}`),
    h("span", { class: "pr-run-day" }, fmtDay(run.started_at)),
    h("span", { class: "pr-chip" }, statusIcon(run.status, 14), STATUS[run.status] ?? run.status),
    run.label && h("span", { class: "pr-chip", title: "First model the workflow loads" }, run.label));
}

function kpi(label, value, sub, extra) {
  return h("div", { class: "pr-kpi" },
    h("div", { class: "pr-kpi-label" }, label),
    h("div", { class: "pr-kpi-value" }, value),
    extra,
    sub && h("div", { class: "pr-kpi-sub" }, sub));
}

function meter(fraction) {
  return h("div", { class: `pr-meter${fraction >= 0.95 ? " pr-warning" : ""}`, role: "meter", "aria-valuenow": Math.round(fraction * 100), "aria-valuemin": 0, "aria-valuemax": 100 },
    h("div", { class: "pr-meter-fill", style: { width: `${Math.min(fraction, 1) * 100}%` } }));
}

function kpis(run, previous, nodeTime) {
  const live = run.status === "running";
  const slowest = run.nodes.reduce((best, node) => (!best || node.duration > best.duration ? node : best), null);
  const peakVram = maxOf(run.samples.vram_used);
  const peakRam = maxOf(run.samples.ram);
  const hasVram = run.vram_total_gb != null;

  let totalSub = h("span", {}, "First run of this workflow");
  if (live) {
    const node = run.current_node;
    totalSub = h("span", {}, node ? `Now: ${node.title}` : "Waiting for the next node");
  } else if (run.status !== "success") {
    totalSub = h("span", {}, "Stopped before the end");
  } else if (previous) {
    const change = (run.duration - previous.duration) / previous.duration;
    if (Math.abs(change) < 0.005) totalSub = h("span", {}, "Same as the last run");
    else {
      const better = change < 0;
      totalSub = [icon(better ? "down" : "up", 14, better ? "pr-delta-better" : "pr-delta-worse"),
        h("span", { class: better ? "pr-delta-better" : "pr-delta-worse", title: `The last run took ${fmtDuration(previous.duration)}` }, `${fmtPercent(Math.abs(change))} ${better ? "faster" : "slower"} than last run`)];
    }
  }

  const progress = run.current_node?.progress;
  return h("div", { class: "pr-kpis" },
    kpi(live ? "Elapsed" : "Total time", fmtDuration(run.duration), totalSub, live && progress && progress.max > 1 && meter(progress.value / progress.max)),
    kpi("Slowest node", slowest ? fmtDuration(slowest.duration) : "–", slowest && h("span", { title: slowest.title }, `${slowest.title} · ${fmtPercent(slowest.duration / nodeTime)}`)),
    kpi("Peak VRAM", hasVram ? fmtGB(peakVram) : "–", hasVram ? `of ${fmtGB(run.vram_total_gb)} on the GPU` : "No CUDA GPU found", hasVram && meter(peakVram / run.vram_total_gb)),
    kpi("Peak RAM", fmtGB(peakRam), `ComfyUI process, of ${fmtGB(run.ram_total_gb)}`, meter(peakRam / run.ram_total_gb)),
    kpi("Nodes run", `${run.nodes.length} of ${run.node_total}`, nodesSub(run)));
}

function nodesSub(run) {
  const missing = run.node_total - run.nodes.length;
  if (run.status === "running") return `${plural(missing, "node")} to go or cached`;
  if (run.status !== "success") return `${plural(missing, "node")} not run`;
  return missing ? `${plural(missing, "node")} cached or skipped` : "Nothing came from the cache";
}

// ---------------------------------------------------------------- timeline

function seriesPath(times, values, x, y) {
  let path = "";
  let open = false;
  for (let i = 0; i < times.length; i++) {
    if (values[i] == null) {
      open = false;
      continue;
    }
    path += `${open ? "L" : "M"}${x(times[i]).toFixed(1)},${y(values[i]).toFixed(1)}`;
    open = true;
  }
  return path;
}

function areaPath(times, values, x, y, baseline) {
  const points = times.map((t, i) => [t, values[i]]).filter(([, value]) => value != null);
  if (points.length < 2) return "";
  return `M${x(points[0][0]).toFixed(1)},${baseline}` + points.map(([t, value]) => `L${x(t).toFixed(1)},${y(value).toFixed(1)}`).join("") + `L${x(points.at(-1)[0]).toFixed(1)},${baseline}Z`;
}

function legend(items, right, y) {
  const group = s("g", {});
  let x = right;
  for (const item of [...items].reverse()) {
    if (item.note) {
      x -= textWidth(item.note);
      group.append(s("text", { class: "pr-note", x, y: y + 4 }, item.note));
    } else {
      x -= textWidth(item.label);
      group.append(s("text", { class: "pr-legend-label", x, y: y + 4 }, item.label));
      x -= 22;
      group.append(s("line", { x1: x, x2: x + 16, y1: y, y2: y, stroke: item.color, "stroke-width": 2, "stroke-linecap": "round" }));
    }
    x -= 16;
  }
  return group;
}

function nearestIndex(times, t) {
  let low = 0, high = times.length - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (times[mid] < t) low = mid + 1;
    else high = mid;
  }
  return low > 0 && t - times[low - 1] < times[low] - t ? low - 1 : low;
}

function timeline(run, width) {
  const live = run.status === "running";
  const samples = run.samples;
  const css = getComputedStyle(view.root);
  const colors = Object.fromEntries(["gpu", "torch", "cpu"].map((name) => [name, css.getPropertyValue(`--pr-series-${name}`).trim()]));
  const hasVram = samples.vram_used.some((value) => value != null);
  const hasGpu = samples.gpu.some((value) => value != null);

  const duration = Math.max(run.duration, samples.t.at(-1) ?? 0, 0.001);
  const left = 48, right = 8;
  const plotWidth = Math.max(width - left - right, 50);
  const x = (t) => left + (t / duration) * plotWidth;
  const end = left + plotWidth;

  const stripTop = 22, stripHeight = 24;
  const memoryHeader = stripTop + stripHeight + 28, memoryTop = memoryHeader + 14, memoryHeight = 130;
  const loadHeader = memoryTop + memoryHeight + 30, loadTop = loadHeader + 14, loadHeight = 56;
  const axisY = loadTop + loadHeight;
  const height = axisY + 22;

  const svg = s("svg", { width, height, role: "img", "aria-label": "Timeline of node execution, memory use and load over the run" });

  // Nodes lane
  svg.append(s("text", { class: "pr-section-label", x: 0, y: 12 }, "Nodes"));
  for (const node of run.nodes) {
    const x0 = x(node.start);
    const segmentWidth = Math.max(x(node.start + node.duration) - x0 - 2, 1);
    svg.append(s("rect", { class: "pr-segment", x: x0, y: stripTop, width: segmentWidth, height: stripHeight, rx: Math.min(4, segmentWidth / 2), dataset: { node: node.id } }));
    const label = segmentWidth > 24 && fitText(node.title, segmentWidth - 12);
    if (label) svg.append(s("text", { class: "pr-segment-label", x: x0 + 6, y: stripTop + stripHeight / 2 + 4 }, label));
  }
  if (live && run.current_node) {
    const x0 = x(run.current_node.start);
    svg.append(s("rect", { class: "pr-segment pr-current", x: x0, y: stripTop, width: Math.max(end - x0, 2), height: stripHeight, rx: 4 }));
    const label = fitText(run.current_node.title, end - x0 - 12);
    if (label) svg.append(s("text", { class: "pr-segment-label", x: x0 + 6, y: stripTop + stripHeight / 2 + 4 }, label));
  }

  // Memory chart
  const memoryTicks = niceTicks(Math.max(maxOf(samples.vram_used), maxOf(samples.ram), 1) * 1.1, 4);
  const yMemory = (value) => memoryTop + memoryHeight - (value / memoryTicks.at(-1)) * memoryHeight;
  svg.append(s("text", { class: "pr-section-label", x: 0, y: memoryHeader + 4 }, "Memory ", s("tspan", { class: "pr-section-unit" }, "GB")));
  svg.append(legend([
    hasVram && { label: "VRAM used", color: colors.gpu },
    hasVram && { label: "PyTorch tensors", color: colors.torch },
    { label: "RAM (ComfyUI)", color: colors.cpu },
  ].filter(Boolean), end, memoryHeader));
  for (const tick of memoryTicks) {
    const y = Math.round(yMemory(tick)) + 0.5;
    svg.append(s("line", { class: tick === 0 ? "pr-baseline" : "pr-gridline", x1: left, x2: end, y1: y, y2: y }));
    svg.append(s("text", { class: "pr-axis-label", x: left - 8, y: y + 4, "text-anchor": "end" }, String(tick)));
  }
  if (hasVram) {
    // The GPU's capacity only gets a line when usage comes close to it, the Peak VRAM tile shows it otherwise
    if (run.vram_total_gb <= memoryTicks.at(-1)) {
      const y = Math.round(yMemory(run.vram_total_gb)) + 0.5;
      svg.append(s("line", { class: "pr-limit-line", x1: left, x2: end, y1: y, y2: y }));
      svg.append(s("text", { class: "pr-axis-label", x: end, y: y - 5, "text-anchor": "end" }, `GPU memory ${fmtGB(run.vram_total_gb)}`));
    }
    svg.append(s("path", { class: "pr-series-wash", d: areaPath(samples.t, samples.vram_used, x, yMemory, memoryTop + memoryHeight), fill: colors.gpu }));
    svg.append(s("path", { class: "pr-series", d: seriesPath(samples.t, samples.vram_allocated, x, yMemory), stroke: colors.torch }));
    svg.append(s("path", { class: "pr-series", d: seriesPath(samples.t, samples.vram_used, x, yMemory), stroke: colors.gpu }));
  }
  svg.append(s("path", { class: "pr-series", d: seriesPath(samples.t, samples.ram, x, yMemory), stroke: colors.cpu }));

  // Load chart
  const yLoad = (value) => loadTop + loadHeight - (value / 100) * loadHeight;
  svg.append(s("text", { class: "pr-section-label", x: 0, y: loadHeader + 4 }, "Load ", s("tspan", { class: "pr-section-unit" }, "%")));
  svg.append(legend([
    hasGpu ? { label: "GPU", color: colors.gpu } : hasVram && { note: "GPU load needs the nvidia-ml-py package" },
    { label: "CPU (system)", color: colors.cpu },
  ].filter(Boolean), end, loadHeader));
  for (const tick of [0, 50, 100]) {
    const y = Math.round(yLoad(tick)) + 0.5;
    svg.append(s("line", { class: tick === 0 ? "pr-baseline" : "pr-gridline", x1: left, x2: end, y1: y, y2: y }));
    svg.append(s("text", { class: "pr-axis-label", x: left - 8, y: y + 4, "text-anchor": "end" }, String(tick)));
  }
  if (hasGpu) svg.append(s("path", { class: "pr-series", d: seriesPath(samples.t, samples.gpu, x, yLoad), stroke: colors.gpu }));
  svg.append(s("path", { class: "pr-series", d: seriesPath(samples.t, samples.cpu, x, yLoad), stroke: colors.cpu }));

  // Time axis
  const step = timeStep(duration, Math.max(2, Math.floor(plotWidth / 90)));
  for (let t = 0; t <= duration + 1e-9; t += step) {
    svg.append(s("text", { class: "pr-axis-label", x: x(t), y: axisY + 16, "text-anchor": t === 0 ? "start" : "middle" }, fmtAxisTime(t, step)));
  }

  // Crosshair, snapped to the nearest sample
  const crosshair = s("line", { class: "pr-crosshair", y1: stripTop - 4, y2: axisY, visibility: "hidden" });
  const dots = [
    hasVram && { values: samples.vram_used, y: yMemory, color: colors.gpu, label: "VRAM used", fmt: fmtGB },
    hasVram && { values: samples.vram_allocated, y: yMemory, color: colors.torch, label: "PyTorch tensors", fmt: fmtGB },
    { values: samples.ram, y: yMemory, color: colors.cpu, label: "RAM (ComfyUI)", fmt: fmtGB },
    hasGpu && { values: samples.gpu, y: yLoad, color: colors.gpu, label: "GPU load", fmt: (v) => `${Math.round(v)}%` },
    { values: samples.cpu, y: yLoad, color: colors.cpu, label: "CPU load", fmt: (v) => `${Math.round(v)}%` },
  ].filter(Boolean).map((series) => ({ ...series, el: s("circle", { class: "pr-dot", r: 4, fill: series.color, visibility: "hidden" }) }));
  svg.append(crosshair, ...dots.map((dot) => dot.el));

  const hit = s("rect", { class: "pr-hit", x: left, y: stripTop - 4, width: plotWidth, height: axisY - stripTop + 4 });
  svg.append(hit);

  function nodeAt(t) {
    const node = run.nodes.find((n) => t >= n.start && t <= n.start + n.duration);
    if (node) return node;
    if (live && run.current_node && t >= run.current_node.start) return run.current_node;
    return null;
  }

  function hide() {
    crosshair.setAttribute("visibility", "hidden");
    for (const dot of dots) dot.el.setAttribute("visibility", "hidden");
    setHighlight(null);
    hideTooltip();
  }

  svg.updatePointer = (clientX, clientY) => {
    const box = svg.getBoundingClientRect();
    const raw = ((clientX - box.left - left) / plotWidth) * duration;
    if (raw < 0 || raw > duration) return hide();
    const index = samples.t.length ? nearestIndex(samples.t, raw) : -1;
    const t = index >= 0 ? samples.t[index] : raw;
    const px = Math.round(x(t)) + 0.5;
    crosshair.setAttribute("x1", px);
    crosshair.setAttribute("x2", px);
    crosshair.setAttribute("visibility", "visible");
    const rows = [];
    for (const dot of dots) {
      const value = index >= 0 ? dot.values[index] : null;
      if (value == null) {
        dot.el.setAttribute("visibility", "hidden");
        continue;
      }
      dot.el.setAttribute("cx", px);
      dot.el.setAttribute("cy", dot.y(value));
      dot.el.setAttribute("visibility", "visible");
      rows.push(tooltipRow(dot.fmt(value), dot.label, dot.color));
    }
    const node = nodeAt(raw);
    setHighlight(node && node !== run.current_node ? node.id : null);
    showTooltip(clientX, clientY, [
      h("div", { class: "pr-tooltip-head" }, fmtOffset(t)),
      node ? h("div", { class: "pr-tooltip-title" }, node.title) : h("div", { class: "pr-tooltip-label" }, "Between nodes"),
      node && h("div", { class: "pr-tooltip-head" }, node === run.current_node ? `${node.class_type} · running` : `${node.class_type} · ${fmtDuration(node.duration)}`),
      rows,
    ]);
  };

  hit.addEventListener("pointermove", (event) => {
    state.pointer = { x: event.clientX, y: event.clientY };
    svg.updatePointer(event.clientX, event.clientY);
  });
  hit.addEventListener("pointerleave", () => {
    state.pointer = null;
    hide();
  });

  return svg;
}

// ---------------------------------------------------------------- ranked bars

function nodeRows(run) {
  const sorted = [...run.nodes].sort((a, b) => b.duration - a.duration);
  const rows = sorted.slice(0, TOP_ROWS).map((node) => ({ node, label: node.title, sub: node.title === node.class_type ? `node ${node.id}` : node.class_type, value: node.duration }));
  const rest = sorted.slice(TOP_ROWS);
  if (rest.length) rows.push({ other: true, label: plural(rest.length, "other node"), sub: "Combined", value: sum(rest.map((node) => node.duration)) });
  return rows;
}

function typeRows(run) {
  const types = new Map();
  for (const node of run.nodes) {
    const entry = types.get(node.class_type) ?? { label: node.class_type, value: 0, count: 0 };
    entry.value += node.duration;
    entry.count += 1;
    types.set(node.class_type, entry);
  }
  const sorted = [...types.values()].sort((a, b) => b.value - a.value);
  const rows = sorted.slice(0, TOP_ROWS).map((type) => ({ ...type, sub: plural(type.count, "node") }));
  const rest = sorted.slice(TOP_ROWS);
  if (rest.length) rows.push({ other: true, label: plural(rest.length, "other type"), sub: "Combined", value: sum(rest.map((type) => type.value)) });
  return rows;
}

function rankedBars(rows, nodeTime) {
  if (!rows.length) return h("div", { class: "pr-card-sub" }, "No nodes have finished yet.");
  const max = Math.max(...rows.map((row) => row.value), 1e-9);
  return h("div", { class: "pr-bars" }, rows.map((row) => {
    const content = () => (row.node ? nodeTooltip(row.node, nodeTime) : [
      h("div", { class: "pr-tooltip-title" }, row.label),
      tooltipRow(fmtDuration(row.value), `${fmtPercent(row.value / nodeTime)} of node time`),
      row.count && tooltipRow(String(row.count), row.count === 1 ? "node" : "nodes"),
    ]);
    return h("div", { class: `pr-bar-row${row.other ? " pr-other" : ""}`, tabindex: 0, dataset: row.node ? { node: row.node.id } : {}, ...highlightHandlers(row.node?.id ?? null, content) },
      h("div", { class: "pr-bar-label" }, h("span", { class: "pr-bar-title" }, row.label), h("span", { class: "pr-bar-sub" }, row.sub)),
      h("div", { class: "pr-bar-track" },
        h("div", { class: "pr-bar-fill", style: { width: `calc((100% - 112px) * ${row.value / max})` } }),
        h("span", { class: "pr-bar-value" }, fmtDuration(row.value), h("span", { class: "pr-bar-share" }, fmtPercent(row.value / nodeTime)))));
  }));
}

// ---------------------------------------------------------------- workflow history

function columnPath(x, y, width, base) {
  const r = Math.min(4, width / 2, base - y);
  return `M${x},${base}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${base}Z`;
}

function historyCard(run, width) {
  const runs = state.runs.filter((other) => other.workflow_hash === run.workflow_hash && other.status === "success").slice(0, HISTORY_RUNS).reverse();
  if (runs.length < 2) return null;

  const left = 48, top = 18, plotHeight = 110;
  const plotWidth = width - left;
  const base = top + plotHeight;
  const longest = maxOf(runs.map((other) => other.duration));
  const step = timeStep(longest * 1.1, 3);
  const ticks = [];
  for (let t = 0; t < longest * 1.1 + step; t += step) ticks.push(t);
  const y = (value) => base - (value / ticks.at(-1)) * plotHeight;
  const band = plotWidth / runs.length;
  const columnWidth = Math.max(Math.min(24, band - 2), 2);

  const svg = s("svg", { width, height: base + 24, role: "img", "aria-label": "Total time of each completed run of this workflow" });
  for (const tick of ticks) {
    const ty = Math.round(y(tick)) + 0.5;
    svg.append(s("line", { class: tick === 0 ? "pr-baseline" : "pr-gridline", x1: left, x2: width, y1: ty, y2: ty }));
    svg.append(s("text", { class: "pr-axis-label", x: left - 8, y: ty + 4, "text-anchor": "end" }, fmtAxisTime(tick, step)));
  }
  runs.forEach((other, i) => {
    const cx = left + band * i + band / 2;
    const columnTop = Math.min(y(other.duration), base - 1);
    const selected = other.id === run.id;
    const content = () => [
      h("div", { class: "pr-tooltip-title" }, `${fmtDay(other.started_at)}, ${fmtClock(other.started_at)}`),
      tooltipRow(fmtDuration(other.duration), "total time"),
      other.peak_vram_gb != null && tooltipRow(fmtGB(other.peak_vram_gb), "peak VRAM"),
      other.slowest && tooltipRow(fmtDuration(other.slowest.duration), other.slowest.title),
    ];
    svg.append(s("rect", { class: "pr-column-hit", x: left + band * i, y: columnTop - 8, width: band, height: base - columnTop + 8, tabindex: 0, role: "button", "aria-label": `Run at ${fmtClock(other.started_at)}, ${fmtDuration(other.duration)}`,
      onclick: () => select(other.id),
      onkeydown: (event) => (event.key === "Enter" || event.key === " ") && select(other.id),
      onpointermove: (event) => showTooltip(event.clientX, event.clientY, content()),
      onpointerleave: hideTooltip,
      onfocus: (event) => showTooltipAt(event.currentTarget, content()),
      onblur: hideTooltip }));
    svg.append(s("path", { class: `pr-column${selected ? " pr-selected" : ""}`, d: columnPath(cx - columnWidth / 2, columnTop, columnWidth, base) }));
    if (selected) svg.append(s("text", { class: "pr-value-label", x: cx, y: columnTop - 6, "text-anchor": "middle" }, fmtDuration(other.duration)));
  });
  svg.append(s("text", { class: "pr-axis-label", x: left, y: base + 16 }, `${fmtDay(runs[0].started_at)}, ${fmtClock(runs[0].started_at)}`));
  svg.append(s("text", { class: "pr-axis-label", x: width, y: base + 16, "text-anchor": "end" }, `${fmtDay(runs.at(-1).started_at)}, ${fmtClock(runs.at(-1).started_at)}`));

  return card("This workflow over time", `Last ${runs.length} completed runs · click one to open it`, svg);
}

// ---------------------------------------------------------------- node table

const COLUMNS = [
  { key: "order", label: "#", num: true },
  { key: "title", label: "Node" },
  { key: "class_type", label: "Type", muted: true },
  { key: "start", label: "Start", num: true, fmt: fmtOffset },
  { key: "duration", label: "Duration", num: true, fmt: fmtDuration },
  { key: "share", label: "Share", num: true },
  { key: "vram_peak_gb", label: "Peak VRAM (PyTorch)", num: true, fmt: fmtGB },
  { key: "vram_delta_gb", label: "Δ VRAM (PyTorch)", num: true, fmt: fmtDeltaGB },
  { key: "ram_delta_gb", label: "Δ RAM", num: true, fmt: fmtDeltaGB },
];

const ASCENDING_FIRST = new Set(["order", "title", "class_type", "start"]);

function nodeTable(run, nodeTime) {
  const { key, dir } = state.sort;
  const value = (node) => (key === "share" ? node.duration : node[key]);
  const rows = [...run.nodes].sort((a, b) => {
    const va = value(a), vb = value(b);
    if (va == null || vb == null) return va == null ? 1 : -1;
    return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
  });

  const sortBy = (column) => {
    state.sort = { key: column, dir: state.sort.key === column ? -state.sort.dir : ASCENDING_FIRST.has(column) ? 1 : -1 };
    renderMain();
  };

  return h("div", { class: "pr-table-wrap" }, h("table", { class: "pr-table" },
    h("thead", {}, h("tr", {}, COLUMNS.map((column) => h("th", { class: `${column.num ? "pr-num" : ""}${column.key === key ? " pr-sorted" : ""}`, "aria-sort": column.key === key ? (dir > 0 ? "ascending" : "descending") : null },
      h("button", { onclick: () => sortBy(column.key) }, column.label, column.key === key && icon(dir > 0 ? "chevronUp" : "chevronDown", 12)))))),
    h("tbody", {}, rows.map((node) => h("tr", { dataset: { node: node.id }, ...highlightHandlers(node.id, () => nodeTooltip(node, nodeTime)) },
      COLUMNS.map((column) => {
        if (column.key === "share") {
          return h("td", { class: "pr-num" }, h("span", { class: "pr-share" },
            h("span", { class: "pr-share-track" }, h("span", { class: "pr-share-fill", style: { width: `${(node.duration / nodeTime) * 100}%` } })),
            fmtPercent(node.duration / nodeTime)));
        }
        const cell = node[column.key];
        return h("td", { class: [column.num && "pr-num", column.key === "title" && "pr-title", column.muted && "pr-muted"].filter(Boolean).join(" "), title: column.key === "title" ? cell : null }, column.fmt ? column.fmt(cell) : cell);
      }))))));
}

// ---------------------------------------------------------------- mounting

export async function mountDashboard(host) {
  if (view?.host === host) return;
  unmountDashboard();

  host.classList.add("pr-host");
  const runCount = h("span", { class: "pr-sidebar-count" });
  const runList = h("div", { class: "pr-run-list" });
  const main = h("div", { class: "pr-main" });
  const root = h("div", { class: "pr-dashboard" },
    h("aside", { class: "pr-sidebar" }, h("div", { class: "pr-sidebar-header" }, h("span", {}, "Runs"), runCount), runList),
    main);
  const tooltip = h("div", { class: "pr-tooltip", role: "tooltip", hidden: true });
  host.replaceChildren(root);
  document.body.append(tooltip);

  let frame = 0;
  const scheduleRender = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(renderMain);
  };
  const resize = new ResizeObserver(scheduleRender);
  // ComfyUI rewrites the theme variables on the root element when the palette changes
  const theme = new MutationObserver(scheduleRender);
  view = { host, root, main, runList, runCount, tooltip, timer: 0, resize, theme };
  resize.observe(main);
  theme.observe(document.documentElement, { attributes: true, attributeFilter: ["style", "class"] });

  const current = view;
  try {
    await loadRuns();
    state.live = (await getJSON("/performance-report/live")).run;
  } catch (error) {
    console.warn("[ExecutionTimeReporter] Loading the dashboard failed", error);
  }
  if (view !== current) return;
  if (!state.selected || (state.selected !== "live" && !state.runs.some((run) => run.id === state.selected)) || (state.selected === "live" && !state.live)) {
    state.selected = state.live ? "live" : state.runs[0]?.id ?? null;
  }
  if (state.selected && state.selected !== "live") await ensureDetails(state.selected).catch(() => { state.selected = null; });
  if (view !== current) return;
  renderSidebar();
  renderMain();
  current.timer = setTimeout(poll, state.live ? POLL_LIVE_MS : POLL_IDLE_MS);
}

export function unmountDashboard() {
  if (!view) return;
  clearTimeout(view.timer);
  view.resize.disconnect();
  view.theme.disconnect();
  view.tooltip.remove();
  view = null;
}
