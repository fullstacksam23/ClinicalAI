import {
  api,
  esc,
  date,
  toast
} from './utils.js';

import {
  view,
  title,
  state
} from './state.js';

let trendCharts = {};

function trendWarning(analyte, data) {
  if (!data || !data.warning) return '';
  return `
    <div class="trend-warning card" style="border-color: #f5d0c0; background: #fef5f4; margin-bottom: 16px;">
      <span class="eyebrow" style="color: var(--red);">Data Quality Notice</span>
      <p class="muted" style="margin: 8px 0 0;">${esc(data.warning)}</p>
    </div>
  `;
}

function trendChartContainer(analyte) {
  return `
    <div class="trend-panel card" id="trend-${esc(analyte)}">
      <h3>${esc(analyte)}</h3>
      <div class="chart-wrapper">
        <canvas id="chart-${esc(analyte)}"></canvas>
      </div>
      <div class="trend-summary" id="summary-${esc(analyte)}"></div>
    </div>
  `;
}

function analyteSelectOptions(analytes) {
  const allAnalytes = Array.from(new Set(analytes)).sort();
  return allAnalytes.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join('');
}

function analytePills(analytes) {
  return Array.from(new Set(analytes)).sort().map(analyte => `
    <button type="button" class="button ghost analyte-pill" data-analyte="${esc(analyte)}" aria-pressed="false">
      ${esc(analyte)}
    </button>
  `).join('');
}

function renderTrendSummary(analyte, data) {
  const s = data.summary;
  if (!s) return '';
  return `
    <div class="detail-list" style="margin-top: 12px;">
      <div><span>First value</span>${s.first_value !== undefined ? `${esc(String(s.first_value))} ${esc(data.series[0]?.unit || '')}` : '—'}</div>
      <div><span>Latest value</span>${s.last_value !== undefined ? `${esc(String(s.last_value))} ${esc(data.series[data.series.length - 1]?.unit || '')}` : '—'}</div>
      <div><span>Change</span>${s.delta !== undefined ? `${esc(String(s.delta >= 0 ? '+' : ''))}${esc(String(s.delta))} ${esc(data.series[0]?.unit || '')} (${s.pct_change !== null && s.pct_change !== undefined ? esc(String(s.pct_change)) + '%' : '—'})` : '—'}</div>
      <div><span>Measurements</span>${esc(String(s.count))}</div>
      <div><span>Out of range</span>${esc(String(s.out_of_range_count))}</div>
    </div>
  `;
}

function createChart(analyte, data) {
  const ctx = document.getElementById(`chart-${analyte}`);
  if (!ctx) return;

  const labels = data.series.map(p => date(p.report_date));
  const values = data.series.map(p => p.value);
  const units = data.series.map(p => p.unit);
  const refLow = data.series.map(p => p.ref_low);
  const refHigh = data.series.map(p => p.ref_high);
  const inRange = data.series.map(p => p.in_range);

  const hasRefRange = data.series.some(p => p.ref_low !== null || p.ref_high !== null);

  const datasets = [
    {
      label: analyte,
      data: values,
      borderColor: 'var(--deep)',
      backgroundColor: 'rgba(15, 69, 58, 0.1)',
      tension: 0.3,
      fill: true,
      pointRadius: 5,
      pointHoverRadius: 7,
      pointBackgroundColor: data.series.map((_, i) => inRange[i] === false ? 'var(--red)' : inRange[i] === true ? 'var(--green)' : 'var(--deep)'),
      pointBorderColor: '#fff',
      pointBorderWidth: 2,
    }
  ];

  if (hasRefRange) {
    const lowValues = data.series.filter(p => Number.isFinite(p.ref_low)).map(p => p.ref_low);
    const highValues = data.series.filter(p => Number.isFinite(p.ref_high)).map(p => p.ref_high);
    const lowAverage = lowValues.length ? lowValues.reduce((sum, value) => sum + value, 0) / lowValues.length : null;
    const highAverage = highValues.length ? highValues.reduce((sum, value) => sum + value, 0) / highValues.length : null;
    const avgLow = Number.isFinite(lowAverage) ? lowAverage : null;
    const avgHigh = Number.isFinite(highAverage) ? highAverage : null;
    if (avgLow !== null || avgHigh !== null) {
      datasets.push(
        {
          label: 'Reference Range',
          data: labels.map(() => avgHigh),
          borderColor: 'rgba(23, 105, 90, 0.3)',
          borderWidth: 1,
          borderDash: [5, 5],
          fill: '-1',
          backgroundColor: 'rgba(23, 105, 90, 0.05)',
          pointRadius: 0,
          type: 'line',
        },
        {
          label: '',
          data: labels.map(() => avgLow),
          borderColor: 'rgba(23, 105, 90, 0.3)',
          borderWidth: 1,
          borderDash: [5, 5],
          fill: false,
          pointRadius: 0,
          type: 'line',
        }
      );
    }
  }

  if (trendCharts[analyte]) {
    trendCharts[analyte].destroy();
  }

  trendCharts[analyte] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'var(--ink)',
          titleColor: '#fff',
          bodyColor: '#fff',
          padding: 12,
          cornerRadius: 8,
          callbacks: {
            label: (context) => {
              const idx = context.dataIndex;
              const unit = units[idx] || '';
              const range = (refLow[idx] !== null || refHigh[idx] !== null)
                ? ` (ref: ${refLow[idx] ?? '—'}–${refHigh[idx] ?? '—'} ${unit})`
                : '';
              const rangeStatus = inRange[idx] === false ? ' ⚠ Out of range' : inRange[idx] === true ? ' ✓ In range' : '';
              return `${context.dataset.label}: ${context.raw} ${unit}${range}${rangeStatus}`;
            }
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: 'var(--muted)', font: { size: 11 } },
        },
        y: {
          grid: { color: 'var(--line)' },
          ticks: { color: 'var(--muted)', font: { size: 11 } },
          title: {
            display: true,
            text: data.series[0]?.unit || '',
            color: 'var(--muted)',
            font: { size: 11, weight: 500 }
          }
        }
      }
    }
  });

  document.getElementById(`summary-${analyte}`).innerHTML = renderTrendSummary(analyte, data);
}

export async function trends() {
  const analyteParam = new URLSearchParams(location.hash.split('?')[1] || '').get('analytes');
  const dateFromParam = new URLSearchParams(location.hash.split('?')[1] || '').get('date_from');
  const dateToParam = new URLSearchParams(location.hash.split('?')[1] || '').get('date_to');

  const hasExplicitParams = !!analyteParam;

  title.textContent = 'Lab Trends';

  view.innerHTML = `
    <div class="intro">
      <div>
        <h2>Lab trends over time</h2>
        <p>Select analytes to visualize their historical values with reference ranges.</p>
      </div>
    </div>

    <div class="card" style="margin-bottom: 24px;">
      <div class="toolbar" id="trends-toolbar" style="flex-wrap: wrap; gap: 12px; margin-bottom: 16px; display: ${hasExplicitParams ? 'flex' : 'none'};">
        <select id="analyte-select" multiple class="search" style="flex: 1; min-width: 200px; height: auto; padding: 8px;">
        </select>
        <div id="analyte-pills" style="display: flex; flex-wrap: wrap; gap: 8px; width: 100%;"></div>
        <input type="date" id="date-from" class="search" style="width: 160px;" placeholder="From">
        <input type="date" id="date-to" class="search" style="width: 160px;" placeholder="To">
        <button class="button" id="load-trends" type="button">Load trends</button>
      </div>
      <div id="trends-toolbar-toggle" style="display: ${hasExplicitParams ? 'none' : 'block'}; margin-bottom: 16px;">
        <button class="button ghost" id="customize-trends" type="button">Customize analytes & date range</button>
      </div>
      <div id="trends-container" class="grid" style="grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));"></div>
      <div id="trends-empty" class="empty" style="display: none;">
        <strong>Not enough data for trends</strong>
        <p>Trends require at least 2 measurements for the same lab test over time. Upload more lab reports to see trends.</p>
      </div>
    </div>
  `;

  const selectEl = document.getElementById('analyte-select');
  const pillsEl = document.getElementById('analyte-pills');
  const dateFromEl = document.getElementById('date-from');
  const dateToEl = document.getElementById('date-to');
  const loadBtn = document.getElementById('load-trends');
  const container = document.getElementById('trends-container');
  const emptyEl = document.getElementById('trends-empty');
  const toolbar = document.getElementById('trends-toolbar');
  const toolbarToggle = document.getElementById('trends-toolbar-toggle');
  const customizeBtn = document.getElementById('customize-trends');

  if (dateFromParam) dateFromEl.value = dateFromParam;
  if (dateToParam) dateToEl.value = dateToParam;

  try {
    const allTrends = await api('/lab-trends');
    const allAnalytes = Object.keys(allTrends);
    selectEl.innerHTML = analyteSelectOptions(allAnalytes);
    pillsEl.innerHTML = analytePills(allAnalytes);

    const syncPills = () => {
      const selected = new Set(Array.from(selectEl.selectedOptions).map(option => option.value));
      pillsEl.querySelectorAll('[data-analyte]').forEach(pill => {
        const isSelected = selected.has(pill.dataset.analyte);
        pill.classList.toggle('active', isSelected);
        pill.setAttribute('aria-pressed', String(isSelected));
      });
    };

    pillsEl.onclick = event => {
      const pill = event.target.closest('[data-analyte]');
      if (!pill) return;
      const option = Array.from(selectEl.options).find(item => item.value === pill.dataset.analyte);
      if (!option) return;
      option.selected = !option.selected;
      syncPills();
    };
    selectEl.onchange = syncPills;

    if (analyteParam) {
      const selected = analyteParam.split(',');
      Array.from(selectEl.options).forEach(opt => {
        opt.selected = selected.includes(opt.value);
      });
      syncPills();
      await loadAndRender(analyteParam, dateFromParam, dateToParam);
    } else {
      // Show default trends: top 5 analytes with most data points
      const analytesByCount = Object.entries(allTrends)
        .map(([name, data]) => ({ name, count: data.series?.length || 0 }))
        .filter(a => a.count > 1)
        .sort((a, b) => b.count - a.count)
        .slice(0, 5)
        .map(a => a.name);
      
      if (analytesByCount.length) {
        Array.from(selectEl.options).forEach(opt => {
          opt.selected = analytesByCount.includes(opt.value);
        });
        syncPills();
        await loadAndRender(analytesByCount.join(','), null, null);
      } else {
        emptyEl.style.display = 'block';
      }
    }
  } catch (e) {
    emptyEl.style.display = 'block';
    emptyEl.querySelector('strong').textContent = 'Could not load lab trends';
    emptyEl.querySelector('p').textContent = e.message;
    toast('Could not load available analytes: ' + e.message);
  }

  customizeBtn.onclick = () => {
    toolbar.style.display = 'flex';
    toolbarToggle.style.display = 'none';
  };

  loadBtn.onclick = async () => {
    const selected = Array.from(selectEl.selectedOptions).map(o => o.value);
    if (!selected.length) {
      toast('Select at least one analyte');
      return;
    }
    const analyteStr = selected.join(',');
    const dateFrom = dateFromEl.value || null;
    const dateTo = dateToEl.value || null;
    const params = new URLSearchParams();
    params.set('analytes', analyteStr);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    location.hash = '#trends?' + params.toString();
    await loadAndRender(analyteStr, dateFrom, dateTo);
  };

  async function loadAndRender(analyteStr, dateFrom, dateTo) {
    container.innerHTML = '<div class="loading">Loading trends…</div>';
    emptyEl.style.display = 'none';
    try {
      const data = await api('/lab-trends', {
        query: { analytes: analyteStr, date_from: dateFrom, date_to: dateTo }
      });

      const analytes = analyteStr.split(',');
      if (!Object.keys(data).length) {
        container.innerHTML = '';
        emptyEl.style.display = 'block';
        return;
      }

      container.innerHTML = analytes.map(a => trendWarning(a, data[a]) + trendChartContainer(a)).join('');

      for (const analyte of analytes) {
        if (data[analyte] && data[analyte].series?.length) {
          createChart(analyte, data[analyte]);
        } else {
          const panel = document.getElementById(`trend-${analyte}`);
          if (panel) panel.innerHTML = '<p class="muted">No data for this analyte in the selected range.</p>';
        }
      }
    } catch (e) {
      container.innerHTML = '';
      emptyEl.style.display = 'block';
      toast('Failed to load trends: ' + e.message);
    }
  }
}

export function cleanupTrends() {
  Object.values(trendCharts).forEach(c => c.destroy());
  trendCharts = {};
}
