"use strict";

// Kinetic Trading — GDELT Intelligence Dashboard (vanilla JS, no build step).
//
// Fetches the committed sample JSON (which mirrors the BigQuery reporting-view
// output schema) and renders KPI cards, a daily-volume chart, top-source and
// top-theme bars, and a data-quality panel. All charts are inline SVG/CSS so the
// page has zero external dependencies and works fully offline.

const DATA_DIR = "public/sample_data";
const SOURCES = {
  volume: `${DATA_DIR}/daily_event_volume.json`,
  sources: `${DATA_DIR}/top_sources.json`,
  themes: `${DATA_DIR}/top_themes_or_entities.json`,
  quality: `${DATA_DIR}/data_quality_summary.json`,
};

const numberFmt = new Intl.NumberFormat("en-US");

async function loadJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${url} -> HTTP ${res.status}`);
  }
  return res.json();
}

function showLoadError(err) {
  const el = document.getElementById("load-error");
  el.hidden = false;
  el.innerHTML =
    "<strong>Could not load sample data.</strong> Open the dashboard through a " +
    "local web server (browsers block <code>fetch</code> from <code>file://</code>):" +
    '<br /><br /><code>cd apps/reporting_dashboard &amp;&amp; python3 -m http.server 8000</code>' +
    "<br />then visit <code>http://localhost:8000</code>." +
    `<br /><br /><span style="opacity:0.75">Details: ${err.message}</span>`;
}

// ── renderers ───────────────────────────────────────────────────────────────

function renderKpis(quality) {
  const q = Array.isArray(quality) ? quality[0] : quality;
  const cards = [
    { label: "Total rows", value: numberFmt.format(q.total_rows), cls: "" },
    {
      label: "Missing required fields",
      value: numberFmt.format(q.missing_required_field_count),
      cls: q.missing_required_field_count > 0 ? "warn" : "good",
      meta: pct(q.missing_required_field_count, q.total_rows) + " of rows",
    },
    {
      label: "Duplicate records",
      value: numberFmt.format(q.duplicate_count),
      cls: q.duplicate_count > 0 ? "warn" : "good",
      meta: pct(q.duplicate_count, q.total_rows) + " of rows",
    },
    {
      label: "Latest record",
      value: formatTimestamp(q.latest_record_timestamp),
      cls: "",
      small: true,
      meta: "as of check " + q.check_date,
    },
  ];

  const container = document.getElementById("kpi-cards");
  container.innerHTML = cards
    .map(
      (c) => `
      <div class="card">
        <div class="label">${c.label}</div>
        <div class="value ${c.small ? "small" : ""} ${c.cls}">${c.value}</div>
        ${c.meta ? `<div class="meta">${c.meta}</div>` : ""}
      </div>`
    )
    .join("");
}

function renderVolumeChart(rows) {
  const width = 900;
  const height = 260;
  const pad = { top: 16, right: 16, bottom: 28, left: 48 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  const counts = rows.map((r) => r.record_count);
  const maxV = Math.max(...counts, 1);
  const stepX = rows.length > 1 ? innerW / (rows.length - 1) : innerW;

  const x = (i) => pad.left + i * stepX;
  const y = (v) => pad.top + innerH - (v / maxV) * innerH;

  const linePts = rows.map((r, i) => `${x(i)},${y(r.record_count)}`).join(" ");
  const areaPts =
    `${pad.left},${pad.top + innerH} ` +
    linePts +
    ` ${pad.left + innerW},${pad.top + innerH}`;

  // Y gridlines at 0, 50%, 100%.
  const yTicks = [0, 0.5, 1].map((f) => {
    const val = Math.round(maxV * f);
    return { yy: y(val), val };
  });

  // X labels: first, middle, last.
  const idxs = rows.length > 2 ? [0, Math.floor(rows.length / 2), rows.length - 1] : [0, rows.length - 1];

  const svg = `
    <svg viewBox="0 0 ${width} ${height}" role="img"
         aria-label="Daily event volume line chart">
      <defs>
        <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="rgba(76,141,255,0.35)" />
          <stop offset="100%" stop-color="rgba(76,141,255,0.02)" />
        </linearGradient>
      </defs>
      ${yTicks
        .map(
          (t) => `
        <line x1="${pad.left}" y1="${t.yy}" x2="${pad.left + innerW}" y2="${t.yy}"
              stroke="#2a313c" stroke-width="1" />
        <text class="axis-label" x="${pad.left - 8}" y="${t.yy + 4}"
              text-anchor="end">${numberFmt.format(t.val)}</text>`
        )
        .join("")}
      <polygon points="${areaPts}" fill="url(#areaFill)" />
      <polyline points="${linePts}" fill="none" stroke="#4c8dff" stroke-width="2.5"
                stroke-linejoin="round" stroke-linecap="round" />
      ${idxs
        .map(
          (i) => `
        <circle cx="${x(i)}" cy="${y(rows[i].record_count)}" r="3.2" fill="#4c8dff" />
        <text class="axis-label" x="${x(i)}" y="${height - 8}"
              text-anchor="middle">${rows[i].event_date}</text>`
        )
        .join("")}
    </svg>`;

  document.getElementById("volume-chart").innerHTML = svg;
}

function renderBars(elementId, rows, labelKey, valueKey) {
  const maxV = Math.max(...rows.map((r) => r[valueKey]), 1);
  const html = rows
    .map((r) => {
      const w = (r[valueKey] / maxV) * 100;
      return `
        <div class="bar-row">
          <span class="bar-label" title="${r[labelKey]}">${r[labelKey]}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div>
          <span class="bar-value">${numberFmt.format(r[valueKey])}</span>
        </div>`;
    })
    .join("");
  document.getElementById(elementId).innerHTML = html;
}

function renderDataQuality(quality) {
  const q = Array.isArray(quality) ? quality[0] : quality;
  const items = [
    {
      label: "Row count > 0",
      value: numberFmt.format(q.total_rows) + " rows",
      status: q.total_rows > 0 ? "pass" : "fail",
    },
    {
      label: "Missing required field count",
      value: numberFmt.format(q.missing_required_field_count),
      status: q.missing_required_field_count === 0 ? "pass" : "warn",
    },
    {
      label: "Duplicate record count",
      value: numberFmt.format(q.duplicate_count),
      status: q.duplicate_count === 0 ? "pass" : "warn",
    },
    {
      label: "Latest record timestamp",
      value: formatTimestamp(q.latest_record_timestamp),
      status: "pass",
    },
  ];

  document.getElementById("dq-panel").innerHTML = items
    .map(
      (it) => `
      <div class="dq-item">
        <span class="dq-label">${it.label}</span>
        <span class="dq-value">
          <span class="status-pill ${it.status}"></span>${it.value}
        </span>
      </div>`
    )
    .join("");
}

// ── helpers ──────────────────────────────────────────────────────────────────

function pct(part, total) {
  if (!total) return "0%";
  const p = (part / total) * 100;
  return (p < 0.01 && p > 0 ? "<0.01" : p.toFixed(2)) + "%";
}

function formatTimestamp(ts) {
  if (!ts) return "n/a";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

// ── boot ─────────────────────────────────────────────────────────────────────

async function main() {
  try {
    const [volume, sources, themes, quality] = await Promise.all([
      loadJson(SOURCES.volume),
      loadJson(SOURCES.sources),
      loadJson(SOURCES.themes),
      loadJson(SOURCES.quality),
    ]);

    renderKpis(quality);
    renderVolumeChart(volume);
    renderBars("sources-chart", sources, "source_domain", "record_count");
    renderBars("themes-chart", themes, "entity_or_theme", "record_count");
    renderDataQuality(quality);
  } catch (err) {
    showLoadError(err);
  }
}

main();
