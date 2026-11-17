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

function cssColor(token) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(token)
    .trim();
}

function trendChartContainer(analyte) {
  return `
    <div class="trend-panel card" id="trend-${esc(analyte)}">
      <div class="trend-panel-head">
        <div>
          <span class="eyebrow">Lab history</span>
          <h3>${esc(analyte)}</h3>
        </div>
      </div>
      <div class="trend-panel-body">
        <div class="chart-wrapper">
          <canvas id="chart-${esc(analyte)}"></canvas>
        </div>
        <div class="trend-summary" id="summary-${esc(analyte)}"></div>
      </div>
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

function formatValue(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value))
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(Number(value))
    : '—';
}

function renderTrendSummary(data) {
  const s = data.summary;
  if (!s) return '';
  const first = data.series[0];
  const latest = data.series[data.series.length - 1];
  const comparable = !data.unit_mismatch;
  const change = s.delta >= 0 ? `+${formatValue(s.delta)}` : formatValue(s.delta);
  const pct = s.pct_change == null ? '' : ` (${s.pct_change > 0 ? '+' : ''}${formatValue(s.pct_change)}%)`;
  const notes = [
    data.unit_mismatch ? 'Units differ between reports. Compare each result in its reported unit.' : '',
    data.ref_range_mismatch ? 'Reference ranges differ between reports. Check each result against its own range.' : ''
  ].filter(Boolean);
  return `
    <div class="trend-values">
      <div class="trend-value trend-value-latest"><span>Latest result</span><strong>${formatValue(s.last_value)} <small>${esc(latest?.unit || '')}</small></strong><small>${esc(date(latest?.report_date))}</small></div>
      <div class="trend-value"><span>First result</span><strong>${formatValue(s.first_value)} <small>${esc(first?.unit || '')}</small></strong><small>${esc(date(first?.report_date))}</small></div>
    </div>
    <div class="trend-facts">
      <div><span>Change</span><strong>${comparable ? `${change} ${esc(first?.unit || '')}${pct}` : '—'}</strong></div>
      <div><span>Measurements</span><strong>${esc(String(s.count))}</strong></div>
      <div><span>Outside reference range</span><strong>${esc(String(s.out_of_range_count))}</strong></div>
    </div>
    ${notes.length ? `<p class="trend-note">${notes.map(esc).join(' ')}</p>` : ''}
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

  const primaryHover = cssColor('--color-primary-hover');
  const primaryChart = cssColor('--color-primary-chart');
  const primaryChartFill = cssColor('--color-primary-chart-fill');
  const normal = cssColor('--color-normal');
  const critical = cssColor('--color-critical');
  const surface = cssColor('--color-surface');
  const textPrimary = cssColor('--color-text-primary');
  const textSecondary = cssColor('--color-text-secondary');
  const border = cssColor('--color-border');

  const hasRefRange = data.series.some(p => p.ref_low !== null || p.ref_high !== null);

  const datasets = [
    {
      label: analyte,
      data: values,
      borderColor: primaryHover,
      backgroundColor: primaryChartFill,
      tension: 0,
      fill: !data.unit_mismatch,
      segment: data.unit_mismatch ? {
        borderColor: context => units[context.p0DataIndex] === units[context.p1DataIndex] ? primaryHover : 'transparent'
      } : undefined,
      pointRadius: 5,
      pointHoverRadius: 7,
      pointBackgroundColor: data.series.map((_, i) => inRange[i] === false ? critical : inRange[i] === true ? normal : primaryHover),
      pointBorderColor: surface,
      pointBorderWidth: 2,
    }
  ];

  if (hasRefRange && !data.ref_range_mismatch && !data.unit_mismatch) {
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
          borderColor: primaryChart,
          borderWidth: 1,
          borderDash: [5, 5],
          fill: '-1',
          backgroundColor: primaryChartFill,
          pointRadius: 0,
          type: 'line',
        },
        {
          label: '',
          data: labels.map(() => avgLow),
          borderColor: primaryChart,
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
          filter: context => context.datasetIndex === 0,
          backgroundColor: textPrimary,
          titleColor: surface,
          bodyColor: surface,
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
          ticks: { color: textSecondary, font: { size: 11 } },
        },
        y: {
          grid: { color: border },
          ticks: { color: textSecondary, font: { size: 11 } },
          title: {
            display: true,
            text: data.unit_mismatch ? 'Reported value' : data.series[0]?.unit || '',
            color: textSecondary,
            font: { size: 11, weight: 500 }
          }
        }
      }
    }
  });

  document.getElementById(`summary-${analyte}`).innerHTML = renderTrendSummary(data);
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
      <div id="trends-container" class="grid trends-grid"></div>
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

      container.innerHTML = analytes.map(trendChartContainer).join('');

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
