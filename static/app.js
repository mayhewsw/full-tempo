const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const resultsList = document.getElementById('results-list');
const status = document.getElementById('status');
const chart = document.getElementById('chart');
const unclassified = document.getElementById('unclassified');

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
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const { results } = await resp.json();

    if (results.length === 0) {
      setStatus('No results. Try a different query, or paste a work ID directly below.');
      showDirectEntry();
    } else {
      setStatus(`${results.length} result(s) — click one to load its duration data.`);
      results.forEach(w => {
        const el = document.createElement('div');
        el.className = 'result-item';
        el.innerHTML = `<div class="title">${escHtml(w.title)}</div><div class="id">${escHtml(w.id)}</div>`;
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
    <input id="direct-id" style="flex:1;padding:10px 14px;border-radius:8px;border:1px solid #333;background:#1a1a1a;color:#f0f0f0;font-size:0.9rem;" placeholder="e.g. ludwig-van-beethoven-1770-pp252" />
    <button id="direct-btn" style="padding:10px 16px;border-radius:8px;border:none;background:#444;color:#fff;cursor:pointer;">Load</button>
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

  resultsList.innerHTML = '';
  chart.innerHTML = '';
  if (unclassified) unclassified.style.display = 'none';
  setStatus(`Fetching recordings for "${workId}"… this may take a minute.`);

  try {
    const resp = await fetch(`/api/work/${encodeURIComponent(workId)}`);
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    const data = await resp.json();
    setStatus('');
    renderChart(data, data._title || workId);
  } catch (err) {
    setStatus(`Failed to load: ${err.message}`);
  }
}

// Deep-linking + back/forward support
window.addEventListener('popstate', () => {
  const workId = getWorkFromUrl();
  if (workId && workId !== _currentWorkId) {
    loadWork(workId, { updateUrl: false });
  }
});

// Auto-load if ?work=... is present
const initialWork = getWorkFromUrl();
if (initialWork) {
  loadWork(initialWork, { updateUrl: true, replaceUrl: true });
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

const COLORS = ['#fa243c', '#3b82f6', '#22c55e', '#f59e0b', '#a855f7', '#06b6d4'];

function _axisId(prefix, n) {
  if (prefix === 'x') return n === 1 ? 'x' : `x${n}`;
  if (prefix === 'y') return n === 1 ? 'y' : `y${n}`;
  if (prefix === 'xaxis') return n === 1 ? 'xaxis' : `xaxis${n}`;
  if (prefix === 'yaxis') return n === 1 ? 'yaxis' : `yaxis${n}`;
  throw new Error(`Unknown axis prefix: ${prefix}`);
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
  const movementsAll = loadMovements(data);
  const { movements, unclassifiedMovements } = partitionMovements(movementsAll);

  renderUnclassified(unclassifiedMovements);
  const n = movements.length;

  if (n === 0) {
    chart.innerHTML = '';
    setStatus('No stable movement clusters found (everything was classified as spurious).');
    return;
  }

  const traces = [];
  const annotations = [];

  movements.forEach(({ title, entries }, i) => {
    const primaryAxisN = i + 1;
    const secondaryAxisN = n + i + 1; // histogram y-axis for this subplot
    const xaxis = _axisId('x', primaryAxisN);
    const yaxis = _axisId('y', primaryAxisN);
    const yaxisHist = _axisId('y', secondaryAxisN);
    const color = COLORS[i % COLORS.length];

    const durationsS = entries.map(e => e.ms / 1000);
    const ids = entries.map(e => e.id);
    const hoverText = entries.map(e => fmtDuration(e.ms));
    const median = durationsS.slice().sort((a, b) => a - b)[Math.floor(durationsS.length / 2)];

    // Faint, non-interactive histogram backdrop (own y-axis overlaid on jitter axis)
    traces.push({
      type: 'histogram',
      x: durationsS,
      xaxis,
      yaxis: yaxisHist,
      nbinsx: 30,
      marker: { color, opacity: 0.22, line: { width: 0 } },
      hoverinfo: 'skip',
      showlegend: false,
    });

    traces.push({
      type: 'scattergl',
      mode: 'markers',
      x: durationsS,
      y: ids.map(id => {
        const u = _hashToUnitFloat(id);
        // Jitter in [-0.45, 0.45]
        return (u - 0.5) * 0.9;
      }),
      text: hoverText,
      customdata: ids,
      name: title,
      marker: {
        color,
        opacity: 0.55,
        size: 7,
        line: { color: '#000', width: 0.5 },
      },
      xaxis,
      yaxis,
      hovertemplate: '%{text}<br>click to open recording<extra></extra>',
    });

    // Median line as a shape — added via layout.shapes
    annotations.push({ median, xaxis, yaxis, color });
  });

  const layout = {
    title: {
      text: pieceTitle,
      font: { color: '#f0f0f0', size: 16 },
      y: 0.995,
      yanchor: 'top',
    },
    paper_bgcolor: '#0a0a0a',
    plot_bgcolor: '#111',
    font: { color: '#aaa' },
    showlegend: false,
    barmode: 'overlay',
    grid: { rows: 1, columns: n, pattern: 'independent' },
    shapes: [],
    annotations: [],
    margin: { t: 92, b: 50, l: 50, r: 20 },
    height: 400,
  };

  movements.forEach(({ title, entries }, i) => {
    const primaryAxisN = i + 1;
    const secondaryAxisN = n + i + 1;
    const xaxis = _axisId('xaxis', primaryAxisN);
    const yaxis = _axisId('yaxis', primaryAxisN);
    const yaxisHist = _axisId('yaxis', secondaryAxisN);
    const xref = _axisId('x', primaryAxisN);
    const yref = _axisId('y', primaryAxisN);

    const { median, color } = annotations[i];
    const durationsS = entries.map(e => e.ms / 1000);
    const { tickvals, ticktext } = buildAxisTicks(durationsS);
    layout[xaxis] = { title: 'Duration', gridcolor: '#222', zerolinecolor: '#333', tickvals, ticktext };
    layout[yaxis] = {
      range: [-0.6, 0.6],
      showgrid: false,
      zeroline: false,
      showticklabels: false,
      ticks: '',
    };
    layout[yaxisHist] = {
      overlaying: yref,
      side: 'right',
      rangemode: 'tozero',
      showgrid: false,
      zeroline: false,
      showticklabels: false,
      ticks: '',
    };
    const medianVal = durationsS.slice().sort((a, b) => a - b)[Math.floor(durationsS.length / 2)];

    layout.shapes.push({
      type: 'line',
      xref,
      yref: 'paper',
      x0: medianVal, x1: medianVal,
      y0: 0, y1: 1,
      line: { color, width: 2, dash: 'dash' },
    });

    layout.annotations.push({
      xref,
      yref: 'paper',
      x: medianVal,
      y: 0.98,
      text: `median ${fmtDuration(medianVal * 1000)}`,
      showarrow: false,
      font: { color, size: 11 },
      yanchor: 'top',
      bgcolor: 'rgba(0,0,0,0.55)',
      bordercolor: 'rgba(255,255,255,0.12)',
      borderwidth: 1,
      borderpad: 3,
    });

    // Subplot title
    layout.annotations.push({
      xref: 'paper',
      yref: 'paper',
      x: (i + 0.5) / n,
      y: 1.07,
      text: title,
      showarrow: false,
      font: { color: '#f0f0f0', size: 13 },
      xanchor: 'center',
    });
  });

  Plotly.newPlot(chart, traces, layout, { responsive: true });

  chart.on('plotly_click', event => {
    const pt = event.points[0];
    if (!pt || !pt.customdata) return;
    const recordingId = pt.customdata;
    window.open(`https://classical.music.apple.com/us/recording/${recordingId}`, '_blank');
  });
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
          const href = `https://classical.music.apple.com/us/recording/${encodeURIComponent(e.id)}`;
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

function buildAxisTicks(durationsS, stepS = 30) {
  const minS = Math.min(...durationsS);
  const maxS = Math.max(...durationsS);
  const start = Math.ceil(minS / stepS) * stepS;
  const tickvals = [];
  const ticktext = [];
  for (let v = start; v <= maxS + 1; v += stepS) {
    tickvals.push(v);
    ticktext.push(fmtDuration(v * 1000));
  }
  return { tickvals, ticktext };
}
