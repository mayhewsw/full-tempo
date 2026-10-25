const SOURCE = 'musicbrainz';

const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const searchRow = document.getElementById('search-row');
const resultsList = document.getElementById('results-list');
const status = document.getElementById('status');
const chart = document.getElementById('chart');
const unclassified = document.getElementById('unclassified');
const homeLink = document.getElementById('home-link');
const workHeader = document.getElementById('work-header');
const workTitleEl = document.getElementById('work-title');
const backBtn = document.getElementById('back-btn');

let _currentWorkId = null;

function setWorkInUrl(workId, { replace = false } = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set('work', workId);
  if (replace) history.replaceState({ workId }, '', url.toString());
  else history.pushState({ workId }, '', url.toString());
}

function getWorkFromUrl() {
  const workId = (new URL(window.location.href).searchParams.get('work') || '').trim();
  return workId || null;
}

// --- Navigation: home <-> work, with correct Back behavior ---
//
// The work page replaces the search box with a title + back button (set
// via setViewMode) rather than showing both at once.

function setViewMode(mode) {
  const isWork = mode === 'work';
  homeLink.style.display = isWork ? 'none' : '';
  searchRow.style.display = isWork ? 'none' : '';
  workHeader.style.display = isWork ? 'flex' : 'none';
}

function goHome({ pushHistory = false } = {}) {
  if (pushHistory) {
    const homeUrl = new URL(window.location.href);
    homeUrl.searchParams.delete('work');
    history.pushState({ home: true }, '', homeUrl.toString());
  }
  _currentWorkId = null;
  setViewMode('home');
  resultsList.innerHTML = '';
  chart.innerHTML = '';
  if (unclassified) { unclassified.style.display = 'none'; unclassified.innerHTML = ''; }
  setStatus('');
  searchInput.value = '';
  searchInput.focus();
}

homeLink.addEventListener('click', () => {
  if (_currentWorkId !== null) goHome({ pushHistory: true });
});
backBtn.addEventListener('click', () => goHome({ pushHistory: true }));

window.addEventListener('popstate', () => {
  const workId = getWorkFromUrl();
  if (workId) {
    if (workId !== _currentWorkId) loadWork(workId, { updateUrl: false });
  } else {
    goHome({ pushHistory: false });
  }
});

// --- Search ---

searchBtn.addEventListener('click', runSearch);
searchInput.addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });

async function runSearch() {
  const q = searchInput.value.trim();
  if (!q) return;

  resultsList.innerHTML = '';
  chart.innerHTML = '';
  if (unclassified) unclassified.style.display = 'none';
  setStatus('Searching…');
  searchBtn.disabled = true;

  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}&source=${SOURCE}`);
    const { results } = await resp.json();

    if (results.length === 0) {
      setStatus('No results. Try a different query, or paste a work ID directly below.');
      showDirectEntry();
    } else {
      setStatus(`${results.length} result(s) — click one to load its duration data.`);
      results.forEach(w => {
        const el = document.createElement('div');
        el.className = 'result-item';
        const countBadge = w.recording_count === undefined
          ? ''
          : ` <span class="count">${w.recording_count} recording${w.recording_count === 1 ? '' : 's'}</span>`;
        el.innerHTML = `<div class="title">${escHtml(w.title)}${countBadge}</div><div class="id">${escHtml(w.id)}</div>`;
        el.addEventListener('click', () => loadWork(w.id, { updateUrl: true }));
        resultsList.appendChild(el);
      });
    }
  } catch (err) {
    setStatus('Search failed — is the server running?');
  } finally {
    searchBtn.disabled = false;
  }
}

function showDirectEntry() {
  const el = document.createElement('div');
  el.style.cssText = 'display:flex;gap:10px;max-width:560px;margin-top:8px;';
  el.innerHTML = `
    <input id="direct-id" style="flex:1;padding:10px 14px;border-radius:8px;border:1px solid #ddd8d0;background:#fff;color:#1a1a1a;font-size:0.9rem;" placeholder="e.g. fe8612d1-8610-3fba-ba56-2ee110b6eedb (MusicBrainz work ID)" />
    <button id="direct-btn" style="padding:10px 16px;border-radius:8px;border:none;background:#555;color:#fff;cursor:pointer;">Load</button>
  `;
  resultsList.appendChild(el);
  document.getElementById('direct-btn').addEventListener('click', () => {
    const id = document.getElementById('direct-id').value.trim();
    if (id) loadWork(id, { updateUrl: true });
  });
}

// --- Load work ---

async function loadWork(workId, opts = {}) {
  const { updateUrl = false, replaceUrl = false } = opts;
  _currentWorkId = workId;
  if (updateUrl) setWorkInUrl(workId, { replace: replaceUrl });

  setViewMode('work');
  workTitleEl.textContent = workId; // placeholder until the real title resolves
  resultsList.innerHTML = '';
  chart.innerHTML = '';
  if (unclassified) unclassified.style.display = 'none';
  setStatus(`Fetching recordings for "${workId}"… this may take a minute.`);

  try {
    const resp = await fetch(`/api/work/${encodeURIComponent(workId)}?source=${SOURCE}`);
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    const data = await resp.json();
    setStatus('');
    renderChart(data, data._title || workId);
  } catch (err) {
    setStatus(`Failed to load: ${err.message}`);
  }
}

// Auto-load if ?work=... is present. Ensure a "home" entry sits beneath it in
// history first, so pressing Back from a deep-linked work page lands on the
// app's home page instead of leaving the site entirely.
const initialWork = getWorkFromUrl();
if (initialWork) {
  const homeUrl = new URL(window.location.href);
  homeUrl.searchParams.delete('work');
  history.replaceState({ home: true }, '', homeUrl.toString());
  loadWork(initialWork, { updateUrl: true, replaceUrl: false });
}

// --- Movement grouping (mirrors plot_work.py logic) ---

function loadMovements(data) {
  const byIndex = {};

  for (const [key, entries] of Object.entries(data)) {
    const sep = key.indexOf('||');
    if (sep === -1) continue;
    const idx = parseInt(key.slice(0, sep), 10);
    const title = key.slice(sep + 2);

    if (!byIndex[idx]) byIndex[idx] = {};
    if (!byIndex[idx][title]) byIndex[idx][title] = [];
    byIndex[idx][title].push(...entries);
  }

  return Object.keys(byIndex)
    .map(Number)
    .sort((a, b) => a - b)
    .map(idx => {
      const titleGroups = byIndex[idx];
      const bestTitle = Object.keys(titleGroups).reduce((a, b) =>
        titleGroups[a].length >= titleGroups[b].length ? a : b
      );
      const allEntries = Object.values(titleGroups).flat();
      return { idx, title: bestTitle, entries: allEntries };
    });
}

// --- Chart ---
//
// Each movement gets its own small, independent Plotly figure inside a
// responsive flex layout (#movement-grid) rather than one big multi-subplot
// Plotly figure. A single figure can't reflow itself when the viewport is
// too narrow for N fixed columns — that's what caused movements to get
// crushed together — whereas letting the browser's native flex wrapping
// handle layout, with each chart responsive to its own cell, does. Flex
// (not CSS grid's auto-fit/minmax) specifically because minPanelWidth gives
// each panel its own minimum width based on its own recording count —
// grid's minmax() would force every column in a row to share one width.

const COLORS = ['#c0293f', '#2563eb', '#15803d', '#b45309', '#7c3aed', '#0891b2'];

function minPanelWidth(pointCount) {
  // Shared by both panel sections — pointCount is recording count for the
  // duration-distribution scatter, year count for the duration-over-time
  // line chart. Either way: a sparse panel still gets a sizable minimum, a
  // dense one gets meaningfully more room, tapering off (sqrt, not linear)
  // so a single very popular movement doesn't balloon to an unreasonable
  // width. Capped so it never exceeds the usable page width.
  const base = 320;
  const scaled = base + 16 * Math.sqrt(pointCount);
  return Math.min(Math.round(scaled), 700);
}

function _hashToUnitFloat(str) {
  // Deterministic pseudo-random in [0,1) for stable jitter.
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

function renderChart(data, pieceTitle) {
  workTitleEl.textContent = pieceTitle;

  const movementsAll = loadMovements(data);
  const { movements, unclassifiedMovements } = partitionMovements(movementsAll);

  renderUnclassified(unclassifiedMovements);
  chart.innerHTML = '';

  if (movements.length === 0) {
    setStatus('No stable movement clusters found (everything was classified as spurious).');
    return;
  }

  // Two sections, same movements (and matching per-movement color) in both:
  // duration distributions first, then median duration over time. Both use
  // Plotly — the trend section doesn't need real interactivity, but staying
  // on the same charting library/visual style keeps the two sections (and
  // the rest of the site) looking like one consistent system rather than a
  // static image bolted on.
  const pending = [];

  pending.push(...buildPanelSection(chart, 'Duration Distributions', movements, (panel, entries, color) => {
    const panelWidth = minPanelWidth(entries.length);
    panel.style.flexBasis = `${panelWidth}px`;
    panel.style.minWidth = `${panelWidth}px`; // hard floor — flexbox wraps rather than shrinking past this
    return { entries, color, render: renderMovementPlot };
  }));

  pending.push(...buildPanelSection(chart, 'Median Duration Over Time', movements, (panel, entries, color) => {
    const yearCount = computeYearlyMedians(entries).length;
    const panelWidth = minPanelWidth(yearCount);
    panel.style.flexBasis = `${panelWidth}px`;
    panel.style.minWidth = `${panelWidth}px`;
    return { entries, color, render: renderTrendPlot };
  }));

  // Plotting synchronously right after appending would race the browser's
  // layout of the flex grid — each panel's width isn't settled yet, so
  // Plotly can mis-measure a container and render charts oversized/
  // overlapping. Deferring to the next frame guarantees a layout pass has
  // happened first.
  requestAnimationFrame(() => {
    pending.forEach(({ plotEl, entries, color, render }) => render(plotEl, entries, color));
  });
}

// Appends a heading + a flex grid of panels (one per movement) to
// `container`, calling `configurePanel(panel, entries, color)` for each to
// size it and pick which plot function renders it. Returns
// [{ plotEl, entries, color, render }, ...] to actually plot later.
function buildPanelSection(container, heading, movements, configurePanel) {
  const headingEl = document.createElement('div');
  headingEl.className = 'section-heading';
  headingEl.textContent = heading;
  container.appendChild(headingEl);

  const grid = document.createElement('div');
  grid.className = 'movement-grid';
  container.appendChild(grid);

  return movements.map(({ title, entries }, i) => {
    const color = COLORS[i % COLORS.length];

    const panel = document.createElement('div');
    panel.className = 'movement-panel';

    const titleEl = document.createElement('div');
    titleEl.className = 'movement-title';
    titleEl.title = title;
    titleEl.textContent = title;
    panel.appendChild(titleEl);

    const plotEl = document.createElement('div');
    plotEl.className = 'movement-plot';
    panel.appendChild(plotEl);

    grid.appendChild(panel);

    const { render } = configurePanel(panel, entries, color);
    return { plotEl, entries, color, render };
  });
}

function renderMovementPlot(el, entries, color) {
  const durationsS = entries.map(e => e.ms / 1000);
  const sortedS = durationsS.slice().sort((a, b) => a - b);
  const median = sortedS[Math.floor(sortedS.length / 2)];
  // ~1 tick per 70px, so density adapts to however wide this panel actually
  // ended up (1-4+ columns depending on viewport) rather than assuming a
  // fixed step that only looked right at one specific panel width.
  const targetTicks = Math.max(3, Math.floor(el.clientWidth / 70));
  const { tickvals, ticktext } = buildAxisTicks(durationsS, targetTicks);

  // Y-axis: recording year, when we have any — jittered within each year's
  // band so same-year recordings don't stack into one indistinguishable
  // dot, with a reserved "Unknown" lane below the real data for the ~6% of
  // recordings with no known year (data/README.md) rather than dropping
  // them. Falls back to the old plain declutter-jitter (no axis meaning)
  // when a movement has no year data at all.
  const knownYears = entries.map(e => e.year).filter(y => typeof y === 'number' && y > 0);
  const hasYears = knownYears.length > 0;
  const minYear = hasYears ? Math.min(...knownYears) : null;
  const maxYear = hasYears ? Math.max(...knownYears) : null;
  const unknownY = hasYears ? minYear - 2 : 0;
  const hasUnknownYear = entries.some(e => !(typeof e.year === 'number' && e.year > 0));

  const yValues = entries.map(e => {
    const jitter = (_hashToUnitFloat(e.id) - 0.5) * (hasYears ? 0.7 : 0.9);
    const base = (typeof e.year === 'number' && e.year > 0) ? e.year : unknownY;
    return base + jitter;
  });

  const hoverText = entries.map(e => {
    const dur = fmtDuration(e.ms);
    return hasYears ? `${dur} · ${e.year || 'year unknown'}` : dur;
  });

  const yaxisLayout = hasYears
    ? (() => {
        const yearTicks = buildYearTicks(minYear, maxYear, 6);
        const tickvalsY = hasUnknownYear ? [unknownY, ...yearTicks] : yearTicks;
        const ticktextY = hasUnknownYear ? ['Unknown', ...yearTicks.map(String)] : yearTicks.map(String);
        return {
          gridcolor: '#eee', zerolinecolor: '#ddd', showgrid: true, automargin: true,
          tickvals: tickvalsY, ticktext: ticktextY,
          range: [unknownY - 1.5, maxYear + 1.5],
        };
      })()
    : { range: [-0.6, 0.6], showgrid: false, zeroline: false, showticklabels: false, ticks: '' };

  const traces = [
    // Faint, non-interactive histogram backdrop (own y-axis overlaid on jitter axis)
    {
      type: 'histogram',
      x: durationsS,
      yaxis: 'y2',
      nbinsx: 30,
      marker: { color, opacity: 0.25, line: { width: 0 } },
      hoverinfo: 'skip',
      showlegend: false,
    },
    {
      type: 'scattergl',
      mode: 'markers',
      x: durationsS,
      y: yValues,
      text: hoverText,
      customdata: entries.map(e => e.id),
      marker: { color, opacity: 0.7, size: 7, line: { color: '#fff', width: 0.5 } },
      hovertemplate: '%{text}<br>click to open recording<extra></extra>',
      showlegend: false,
    },
  ];

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#555', size: 11 },
    margin: { t: 30, b: 44, l: hasYears ? 40 : 16, r: 16 },
    height: 300,
    barmode: 'overlay',
    shapes: [{
      type: 'line',
      xref: 'x', yref: 'paper',
      x0: median, x1: median, y0: 0, y1: 1,
      line: { color, width: 2, dash: 'dash' },
    }],
    annotations: [{
      xref: 'x', yref: 'paper',
      x: median, y: 0.98,
      text: `median ${fmtDuration(median * 1000)}`,
      showarrow: false,
      font: { color, size: 11 },
      yanchor: 'top',
      bgcolor: 'rgba(255,255,255,0.85)',
      bordercolor: color,
      borderwidth: 1,
      borderpad: 3,
    }],
    xaxis: { title: 'Duration', gridcolor: '#eee', zerolinecolor: '#ddd', tickvals, ticktext },
    yaxis: yaxisLayout,
    yaxis2: {
      overlaying: 'y', side: 'right', rangemode: 'tozero',
      showgrid: false, zeroline: false, showticklabels: false, ticks: '',
    },
  };

  Plotly.newPlot(el, traces, layout, { responsive: true, displayModeBar: false });

  el.on('plotly_click', event => {
    const pt = event.points[0];
    if (!pt || pt.customdata === undefined) return;
    window.open(recordingUrl(pt.customdata), '_blank');
  });
}

// One point per year this movement has recordings for: median duration
// that year, plus how many recordings it's based on (used for the faint
// count backdrop in renderTrendPlot). Recordings with no known year are
// excluded — there's no x-position for them on a by-year chart.
function computeYearlyMedians(entries) {
  const byYear = new Map();
  for (const e of entries) {
    if (typeof e.year === 'number' && e.year > 0) {
      if (!byYear.has(e.year)) byYear.set(e.year, []);
      byYear.get(e.year).push(e.ms);
    }
  }
  return Array.from(byYear.keys())
    .sort((a, b) => a - b)
    .map(year => {
      const msSorted = byYear.get(year).slice().sort((a, b) => a - b);
      return { year, medianMs: msSorted[Math.floor(msSorted.length / 2)], count: msSorted.length };
    });
}

function renderTrendPlot(el, entries, color) {
  const yearly = computeYearlyMedians(entries);

  if (yearly.length === 0) {
    el.innerHTML = '<div class="no-data">No year data available for this movement.</div>';
    return;
  }

  const years = yearly.map(p => p.year);
  const medianS = yearly.map(p => p.medianMs / 1000);
  const counts = yearly.map(p => p.count);
  const hoverText = yearly.map(p =>
    `${fmtDuration(p.medianMs)} median · ${p.count} recording${p.count === 1 ? '' : 's'}`
  );

  const xTargetTicks = Math.max(3, Math.floor(el.clientWidth / 90));
  const xTicks = buildYearTicks(years[0], years[years.length - 1], xTargetTicks);
  const { tickvals: yTickvals, ticktext: yTicktext } = buildAxisTicks(medianS, 5);

  const traces = [
    // Faint, non-interactive backdrop: how many recordings that year's
    // median is based on — a lone outlier year (e.g. n=1-3) reads very
    // differently from a year backed by dozens of recordings.
    {
      type: 'bar',
      x: years,
      y: counts,
      yaxis: 'y2',
      marker: { color, opacity: 0.15, line: { width: 0 } },
      hoverinfo: 'skip',
      showlegend: false,
    },
    {
      type: 'scatter',
      mode: 'lines+markers',
      x: years,
      y: medianS,
      text: hoverText,
      marker: { color, size: 6, line: { color: '#fff', width: 0.5 } },
      line: { color, width: 2 },
      hovertemplate: '%{x}<br>%{text}<extra></extra>',
      showlegend: false,
    },
  ];

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#555', size: 11 },
    margin: { t: 14, b: 36, l: 44, r: 16 },
    height: 300,
    xaxis: { title: 'Year', gridcolor: '#eee', zerolinecolor: '#ddd', tickvals: xTicks, ticktext: xTicks.map(String) },
    yaxis: { title: 'Median duration', gridcolor: '#eee', zerolinecolor: '#ddd', tickvals: yTickvals, ticktext: yTicktext },
    yaxis2: {
      overlaying: 'y', side: 'right', rangemode: 'tozero',
      range: [0, Math.max(...counts) * 3], // keep bars short — a backdrop, not the focus
      showgrid: false, zeroline: false, showticklabels: false, ticks: '',
    },
  };

  Plotly.newPlot(el, traces, layout, { responsive: true, displayModeBar: false });
}

function recordingUrl(recordingId) {
  return SOURCE === 'musicbrainz'
    ? `https://musicbrainz.org/recording/${recordingId}`
    : `https://classical.music.apple.com/us/recording/${recordingId}`;
}

// --- Util ---

function setStatus(msg) { status.textContent = msg; }

function partitionMovements(movements) {
  if (!movements || movements.length === 0) {
    return { movements: [], unclassifiedMovements: [] };
  }

  const counts = movements.map(m => m.entries.length);
  const maxCount = Math.max(...counts);

  // Heuristic: treat tiny clusters as spurious.
  // Example: 150 vs 5–10 => those 5–10 should go to "Unclassified tracks".
  const minAbs = 12;
  const minFrac = 0.15; // 15% of max
  const threshold = Math.max(minAbs, Math.floor(maxCount * minFrac));

  const kept = [];
  const unclassifiedMovements = [];

  movements.forEach(m => {
    if (m.entries.length >= threshold) kept.push(m);
    else unclassifiedMovements.push(m);
  });

  return { movements: kept, unclassifiedMovements };
}

function renderUnclassified(movs) {
  if (!unclassified) return;

  if (!movs || movs.length === 0) {
    unclassified.style.display = 'none';
    unclassified.innerHTML = '';
    return;
  }

  const sorted = movs.slice().sort((a, b) => b.entries.length - a.entries.length);
  unclassified.style.display = 'block';
  unclassified.innerHTML = `
    <h2>Unclassified tracks</h2>
    <div class="note">Small, inconsistent clusters (often bonus tracks, cadenzas, split/merged movements) are listed here and excluded from the chart.</div>
    <ul>
      ${sorted.map(m => {
        const unique = [];
        const seen = new Set();
        for (const e of m.entries) {
          if (!seen.has(e.id)) {
            seen.add(e.id);
            unique.push(e);
          }
          if (unique.length >= 8) break;
        }

        const sub = unique.map(e => {
          const href = recordingUrl(e.id);
          return `<a href="${href}" target="_blank" rel="noopener noreferrer">${fmtDuration(e.ms)} — ${escHtml(e.id)}</a>`;
        }).join('');

        return `
          <li>
            <details>
              <summary title="${escHtml(m.title)}">${escHtml(m.title)} <span class="meta">(${m.entries.length})</span></summary>
              <div class="sublist">${sub || '<span class="meta">No recordings</span>'}</div>
            </details>
          </li>
        `;
      }).join('')}
    </ul>
  `;
}

function escHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmtDuration(ms) {
  const totalSec = Math.round(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${String(sec).padStart(2, '0')}`;
}

const NICE_STEPS_S = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

function buildAxisTicks(durationsS, targetTicks = 6) {
  const minS = Math.min(...durationsS);
  const maxS = Math.max(...durationsS);
  const range = Math.max(maxS - minS, 1);
  const rawStep = range / targetTicks;
  const stepS = NICE_STEPS_S.find(s => s >= rawStep) || NICE_STEPS_S[NICE_STEPS_S.length - 1];

  const start = Math.ceil(minS / stepS) * stepS;
  const tickvals = [];
  const ticktext = [];
  for (let v = start; v <= maxS + 1; v += stepS) {
    tickvals.push(v);
    ticktext.push(fmtDuration(v * 1000));
  }
  return { tickvals, ticktext };
}

const NICE_YEAR_STEPS = [1, 2, 5, 10, 20, 25, 50, 100];

function buildYearTicks(minYear, maxYear, targetTicks = 6) {
  const range = Math.max(maxYear - minYear, 1);
  const rawStep = range / targetTicks;
  const step = NICE_YEAR_STEPS.find(s => s >= rawStep) || NICE_YEAR_STEPS[NICE_YEAR_STEPS.length - 1];

  const start = Math.ceil(minYear / step) * step;
  const ticks = [];
  for (let y = start; y <= maxYear; y += step) ticks.push(y);
  // Always surface the most recent year even if it doesn't land on a "nice"
  // step boundary, so the newest recording's year is never hidden.
  if (ticks[ticks.length - 1] !== maxYear) ticks.push(maxYear);
  return ticks;
}
