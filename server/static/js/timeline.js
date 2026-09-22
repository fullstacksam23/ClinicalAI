import {
  api,
  esc,
  date,
  toast
} from './utils.js';

import {
  view,
  title
} from './state.js';

function timelineParams() {
  return new URLSearchParams(location.hash.split('?')[1] || '');
}

function currentFilters() {
  const params = timelineParams();
  return {
    types: params.get('types') || '',
    dateFrom: params.get('date_from') || '',
    dateTo: params.get('date_to') || ''
  };
}

function timelineHash(filters) {
  const params = new URLSearchParams();
  if (filters.types) params.set('types', filters.types);
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
  const query = params.toString();
  return query ? `#timeline?${query}` : '#timeline';
}

export function setTimelineType(type) {
  const filters = currentFilters();
  filters.types = type === 'all' ? '' : type;
  location.hash = timelineHash(filters);
}

function filterControls(filters) {
  const activeType = filters.types || 'all';
  return `
    <div class="timeline-filters" aria-label="Timeline filters">
      <div class="timeline-type-filters">
        ${[
          ['all', 'All'],
          ['report', 'Reports'],
          ['abnormal_lab', 'Abnormal results']
        ].map(([value, label]) => `
          <button
            class="timeline-filter ${activeType === value ? 'active' : ''}"
            type="button"
            data-timeline-type="${value}"
            aria-pressed="${activeType === value}">
            ${label}
          </button>
        `).join('')}
      </div>
      <form class="timeline-date-filters" id="timeline-date-form">
        <label>
          <span>From</span>
          <input class="search" type="date" id="timeline-date-from" value="${esc(filters.dateFrom)}">
        </label>
        <label>
          <span>To</span>
          <input class="search" type="date" id="timeline-date-to" value="${esc(filters.dateTo)}">
        </label>
        <button class="button ghost" type="submit">Apply dates</button>
      </form>
    </div>
  `;
}

function eventDetails(event) {
  if (event.type === 'report') {
    const doctor = event.referring_doctor
      ? `<span>${esc(event.referring_doctor)}</span>`
      : '<span>No referring doctor recorded</span>';
    const status = event.summary_status
      ? `<span class="timeline-status">Summary: ${esc(event.summary_status)}</span>`
      : '';
    return `
      <div class="timeline-event-type">Report</div>
      <h3>${esc(event.title || 'Medical report')}</h3>
      <p class="timeline-subtitle">${doctor}${status}</p>
      <a class="link-button" href="#documents/${esc(encodeURIComponent(event.report_id))}">View report</a>
    `;
  }

  const value = `${event.value || ''}${event.unit ? ` ${event.unit}` : ''}`.trim();
  const reference = event.ref_interval_raw || 'Reference range unavailable';
  return `
    <div class="timeline-event-type">Abnormal result</div>
    <h3>${esc(event.title || event.investigation || 'Laboratory result')}</h3>
    <p class="timeline-subtitle">
      <span class="timeline-direction ${event.direction === 'high' ? 'high' : 'low'}">${esc(event.direction || 'abnormal')}</span>
      <strong>${esc(value)}</strong>
      <span>Reference: ${esc(reference)}</span>
    </p>
    <a class="link-button" href="#trends?analytes=${esc(encodeURIComponent(event.investigation || event.title || ''))}">View trend</a>
  `;
}

function groupedEvents(events) {
  const groups = new Map();
  events.forEach(event => {
    const parsed = event.date ? new Date(`${event.date}T00:00:00`) : null;
    const key = parsed && !Number.isNaN(parsed.valueOf())
      ? `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}`
      : 'unknown';
    if (!groups.has(key)) {
      groups.set(key, {
        label: parsed && !Number.isNaN(parsed.valueOf())
          ? parsed.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
          : 'Date unavailable',
        events: []
      });
    }
    groups.get(key).events.push(event);
  });
  return Array.from(groups.values());
}

function renderEvents(events) {
  if (!events.length) {
    return `
      <div class="empty timeline-empty">
        <strong>No records yet</strong>
        No reports or abnormal laboratory results match these filters.
      </div>
    `;
  }

  return groupedEvents(events).map(group => `
    <section class="timeline-month">
      <h2>${esc(group.label)}</h2>
      <div class="timeline-list">
        ${group.events.map(event => `
          <article class="timeline-event">
            <div class="timeline-marker ${event.type === 'abnormal_lab' ? 'abnormal' : ''}" aria-hidden="true"></div>
            <div class="timeline-event-date">${esc(date(event.date))}</div>
            <div class="timeline-event-card card">
              ${eventDetails(event)}
            </div>
          </article>
        `).join('')}
      </div>
    </section>
  `).join('');
}

export async function timeline() {
  title.textContent = 'Health timeline';
  const filters = currentFilters();

  view.innerHTML = `
    <div class="intro">
      <div>
        <h2>Your health timeline</h2>
        <p>Reports and abnormal laboratory results, organized by date.</p>
      </div>
    </div>
    ${filterControls(filters)}
    <div id="timeline-content" class="timeline-content loading">Loading timeline...</div>
  `;

  document.querySelector('#timeline-date-form').addEventListener('submit', event => {
    event.preventDefault();
    location.hash = timelineHash({
      types: filters.types,
      dateFrom: document.querySelector('#timeline-date-from').value,
      dateTo: document.querySelector('#timeline-date-to').value
    });
  });

  const query = {};
  if (filters.types) query.types = filters.types;
  if (filters.dateFrom) query.date_from = filters.dateFrom;
  if (filters.dateTo) query.date_to = filters.dateTo;

  try {
    const result = await api('/timeline', { query });
    const content = document.querySelector('#timeline-content');
    if (content) {
      content.classList.remove('loading');
      content.innerHTML = renderEvents(result.events || []);
    }
  } catch (error) {
    const content = document.querySelector('#timeline-content');
    if (content) {
      content.classList.remove('loading');
      content.innerHTML = `
        <div class="empty timeline-empty">
          <strong>Could not load timeline</strong>
          ${esc(error.message)}
        </div>
      `;
    }
    toast(`Could not load timeline: ${error.message}`);
  }
}
