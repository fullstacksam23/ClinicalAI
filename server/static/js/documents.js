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

import {
  cleanupPdfBlob,
  loadPdfIntoIframe
} from './pdf.js';
import { availableTags, filterDocuments } from './documentFilters.js';

let summaryPollTimer = null;

function tagChips(tags = []) {
  return tags.map(tag => `<span class="tag-chip">${esc(tag)}</span>`).join('');
}

function summaryCard(documentData) {
  const status = documentData.summary_status || 'pending';
  if (status === 'completed' && documentData.summary) {
    return `
      <section class="summary-card card" id="summary-card">
        <div class="summary-heading">
          <span class="eyebrow">At a glance</span>
          <span class="summary-status complete">Ready</span>
        </div>
        <p class="summary-text">${esc(documentData.summary)}</p>
      </section>
    `;
  }

  if (status === 'failed') {
    return `
      <section class="summary-card card" id="summary-card">
        <div class="summary-heading">
          <span class="eyebrow">At a glance</span>
          <span class="summary-status failed">Unavailable</span>
        </div>
        <p class="muted">A patient-friendly summary could not be generated yet.</p>
        <button class="button ghost summary-retry" data-summary-retry="${esc(documentData.id)}" type="button">
          Try again
        </button>
      </section>
    `;
  }

  return `
    <section class="summary-card card" id="summary-card">
      <div class="summary-heading">
        <span class="eyebrow">At a glance</span>
        <span class="summary-status pending">Preparing</span>
      </div>
      <p class="muted">We are preparing a plain-language summary of this report.</p>
    </section>
  `;
}

async function pollSummary(id) {
  try {
    const documentData = await api('/documents/' + encodeURIComponent(id));
    const card = document.querySelector('#summary-card');
    if (!card) return;
    card.outerHTML = summaryCard(documentData);
    if ((documentData.summary_status || 'pending') === 'pending') {
      summaryPollTimer = setTimeout(() => pollSummary(id), 2500);
    }
  } catch {
    summaryPollTimer = setTimeout(() => pollSummary(id), 5000);
  }
}

export async function retrySummary(id) {
  clearTimeout(summaryPollTimer);
  const result = await api('/documents/' + encodeURIComponent(id) + '/summary/retry', {
    method: 'POST'
  });
  await documentView(id, result);
}

export async function loadDocs() {
  state.docs =
    await api('/documents');

  return state.docs;
}

export function docRows(
  items,
  compact = false
) {
  if (!items.length) {
    return `
      <div class="empty">

        <strong>
          Your medical record is empty
        </strong>

        Upload your first report to start
        building your health record.

      </div>
    `;
  }

  return `
    <div class="doc-list">

      ${items
        .map(
          d => `
            <div class="doc-row">

              <span class="file-icon">
                PDF
              </span>

              <div>

                <div class="doc-name">
                  ${esc(d.filename)}
                </div>

                <div class="doc-meta">
                  ${esc(
                    d.report_type ||
                      'Medical document'
                  )}

                  ·

                  ${date(
                    d.report_date ||
                      d.uploaded_at
                  )}
                </div>
                ${d.tags?.length ? `<div class="tag-list" aria-label="Report tags">${tagChips(d.tags)}</div>` : ''}

              </div>

              <div>

                ${
                  d.status === 'failed'
                    ? `
                      <span
                        class="status failed">
                        Failed
                      </span>
                    `
                    : `
                      <button
                        class="link-button"
                        data-open="${esc(d.id)}">

                        ${
                          compact
                            ? 'Open'
                            : 'View document'
                        }

                      </button>
                    `
                }

              </div>

              <button
                class="link-button danger"
                type="button"
                data-delete-document="${esc(d.id)}">
                Delete
              </button>

            </div>
          `
        )
        .join('')}

    </div>
  `;
}

export async function dashboard() {
  cleanupPdfBlob();

  title.textContent =
    'Overview';

  view.innerHTML = `
    <div class="intro dashboard-hero">

      <div>

        <h2>
          Your health record,
          in one calm place.
        </h2>

        <p>
          Upload reports, keep them together,
          and ask questions grounded in your
          own records.
        </p>

      </div>

      <button
        class="button"
        data-go="documents">
        Upload a document
      </button>

    </div>

    <div class="dashboard-stats" aria-label="Your record at a glance">
      <button class="dashboard-stat stat-documents" type="button" data-go="documents">
        <span class="stat-icon" aria-hidden="true">D</span>
        <span class="stat-label">Documents</span>
        <strong class="stat-value" id="document-count">—</strong>
        <span class="stat-note">Reports in your record</span>
      </button>
      <button class="dashboard-stat stat-labs" type="button" data-go="health">
        <span class="stat-icon" aria-hidden="true">L</span>
        <span class="stat-label">Lab results</span>
        <strong class="stat-value" id="lab-count">—</strong>
        <span class="stat-note">Results from your reports</span>
      </button>
      <button class="dashboard-stat stat-trends" type="button" data-go="trends">
        <span class="stat-icon" aria-hidden="true">T</span>
        <span class="stat-label">Health trends</span>
        <strong class="stat-value" id="trend-count">—</strong>
        <span class="stat-note">Tests with recorded history</span>
      </button>
    </div>

    <div class="grid">

      <article class="card">

        <h3>
          Recent documents
        </h3>

        <div
          id="recent"
          class="loading">
          Loading your record…
        </div>

      </article>

      <aside class="card">

        <h3>
          Start here
        </h3>

        <div class="action-grid">

          <button
            class="quick-action"
            data-go="documents">

            Upload a medical report

            <small>
              PDF reports are processed securely.
            </small>

          </button>

          <button
            class="quick-action"
            data-go="chat">

            Ask ClinicalAI

            <small>
              Answers are based on your uploaded
              records.
            </small>

          </button>

        </div>

      </aside>

    </div>
  `;

  try {
    await loadDocs();
    if (!document.querySelector('#recent')) return;

    document.querySelector('#document-count').textContent =
      String(state.docs.filter(d => d.status !== 'failed').length);

    document.querySelector(
      '#recent'
    ).innerHTML =
      docRows(
        state.docs.slice(0, 4),
        true
      );

  } catch (e) {
    if (!document.querySelector('#recent')) return;
    document.querySelector(
      '#recent'
    ).innerHTML = `
      <div class="empty">

        <strong>
          Couldn’t load your record
        </strong>

        ${esc(e.message)}

      </div>
    `;
  }

  try {
    const labs = await api('/lab-results');
    if (!document.querySelector('#lab-count')) return;
    if (!Array.isArray(labs)) throw new Error('Lab results are unavailable.');
    document.querySelector('#lab-count').textContent = String(labs.length);
    const reportHistory = new Map();
    labs.forEach(lab => {
      if (!lab.investigation || !lab.report_date || !lab.report_id || lab.result_numeric == null) return;
      if (!reportHistory.has(lab.investigation)) reportHistory.set(lab.investigation, new Set());
      reportHistory.get(lab.investigation).add(lab.report_id);
    });
    document.querySelector('#trend-count').textContent =
      String([...reportHistory.values()].filter(reportIds => reportIds.size >= 2).length);
  } catch {
    if (document.querySelector('#lab-count')) {
      document.querySelector('#lab-count').textContent = '—';
      document.querySelector('#trend-count').textContent = '—';
    }
  }
}

export function documents() {
  cleanupPdfBlob();

  title.textContent =
    'Documents';

  view.innerHTML = `
    <div class="intro">

      <div>

        <h2>
          Your documents
        </h2>

        <p>
          Medical reports you’ve added to
          your private record.
        </p>

      </div>

    </div>

    <section
      class="upload-zone"
      id="drop">

      <strong>
        Add a scanned medical PDF
      </strong>

      <p>
        Drop a PDF here, or choose one from
        your computer. Maximum 25 MB.
      </p>

      <label
        class="button"
        for="file">
        Choose PDF
      </label>

      <input
        id="file"
        type="file"
        accept="application/pdf"
      />

      <div
        class="selected"
        id="selected">
      </div>

    </section>

    <div class="toolbar">

      <input
        class="search"
        id="search"
        placeholder="Search reports, doctors, dates, or tags"
        aria-label="Search documents"
      />

      <button
        class="button ghost"
        id="refresh">
        Refresh
      </button>

    </div>

    <div class="document-filters" aria-label="Document filters">
      <label>Tag
        <select class="search" id="tag-filter"><option value="">All tags</option></select>
      </label>
      <label>From
        <input class="search" type="date" id="document-date-from" />
      </label>
      <label>To
        <input class="search" type="date" id="document-date-to" />
      </label>
      <button class="button ghost" type="button" id="clear-document-filters">Clear filters</button>
    </div>
    <p class="filter-status" id="filter-status" role="status"></p>

    <div class="card">

      <div
        id="documents-list"
        class="loading">
        Loading documents…
      </div>

    </div>
  `;

  const input =
    document.querySelector(
      '#file'
    );

  const drop =
    document.querySelector(
      '#drop'
    );

  const select = f => {
    if (!f) return;

    document.querySelector(
      '#selected'
    ).innerHTML = `
      <b>
        ${esc(f.name)}
      </b>

      ·
      ${(f.size / 1024 / 1024).toFixed(1)}
      MB

      <button
        class="link-button"
        id="send"
        type="button">

        Upload now

      </button>
    `;

    document.querySelector(
      '#send'
    ).onclick = () =>
      upload(f);
  };

  input.onchange = () =>
    select(input.files[0]);

  [
    'dragover',
    'dragleave',
    'drop'
  ].forEach(type => {

    drop.addEventListener(
      type,
      e => {

        e.preventDefault();

        drop.classList.toggle(
          'drag',
          type ===
            'dragover'
        );

        if (
          type === 'drop'
        ) {
          select(
            e.dataTransfer
              .files[0]
          );
        }

      }
    );

  });

  document.querySelector('#search').oninput = paintDocList;
  document.querySelector('#tag-filter').onchange = paintDocList;
  document.querySelector('#document-date-from').onchange = paintDocList;
  document.querySelector('#document-date-to').onchange = paintDocList;
  document.querySelector('#clear-document-filters').onclick = () => {
    for (const id of ['search', 'tag-filter', 'document-date-from', 'document-date-to']) {
      document.getElementById(id).value = '';
    }
    paintDocList();
    document.getElementById('search').focus();
  };

  document.querySelector(
    '#refresh'
  ).onclick = () =>
    renderDocs();

  renderDocs();
}

function paintDocList() {
  const el = document.querySelector('#documents-list');
  if (!el) return;
  const query = document.getElementById('search').value;
  const tag = document.getElementById('tag-filter').value;
  const from = document.getElementById('document-date-from').value;
  const to = document.getElementById('document-date-to').value;
  const status = document.getElementById('filter-status');
  status.classList.toggle('invalid', !!(from && to && from > to));
  if (from && to && from > to) {
    status.textContent = 'The start date must be before the end date.';
    el.innerHTML = '';
    return;
  }
  const items = filterDocuments(state.docs, { query, tag, from, to });
  status.textContent = `${items.length} of ${state.docs.length} documents shown`;
  el.innerHTML = items.length || !state.docs.length
    ? docRows(items)
    : '<div class="empty"><strong>No matching documents</strong>Try another search or clear the filters.</div>';
}

export async function renderDocs() {
  const el =
    document.querySelector(
      '#documents-list'
    );

  if (!el) return;

  try {
    await loadDocs();
    if (!document.querySelector('#documents-list')) return;
    const selector = document.getElementById('tag-filter');
    const selected = selector.value;
    selector.innerHTML = '<option value="">All tags</option>' + availableTags(state.docs)
      .map(tag => `<option value="${esc(tag)}">${esc(tag)}</option>`).join('');
    selector.value = availableTags(state.docs).some(tag => tag.toLocaleLowerCase() === selected.toLocaleLowerCase())
      ? selected : '';
    paintDocList();

  } catch (e) {
    el.innerHTML = `
      <div class="empty">

        <strong>
          Couldn’t load documents
        </strong>

        ${esc(e.message)}

      </div>
    `;
  }
}

async function upload(file) {
  if (
    file.type &&
    file.type !== 'application/pdf'
  ) {
    toast(
      'Please choose a PDF.'
    );
    return;
  }

  if (
    file.size >
    25 * 1024 * 1024
  ) {
    toast(
      'PDF must be smaller than 25 MB.'
    );
    return;
  }

  const selected =
    document.querySelector(
      '#selected'
    );

  selected.textContent =
    'Uploading and processing your report… This can take a few minutes.';

  const fd =
    new FormData();

  fd.append(
    'file',
    file
  );

  try {
    const uploaded = await api(
      '/documents/upload',
      {
        method: 'POST',
        body: fd
      }
    );

    toast(
      'Document processed and added to your record.'
    );

    state.summaryOverrides[uploaded.id] = {
      summary: uploaded.summary,
      summary_status: uploaded.summary_status,
    };

    location.hash =
      '#documents/' +
      encodeURIComponent(uploaded.id);

  } catch (e) {
    selected.textContent =
      `Upload failed: ${e.message}`;
  }
}

export async function documentView(
  id,
  summaryOverride = null
) {
  cleanupPdfBlob();
  clearTimeout(summaryPollTimer);

  summaryOverride =
    summaryOverride ||
    state.summaryOverrides[id] ||
    null;
  delete state.summaryOverrides[id];

  title.textContent =
    'Document';

  view.innerHTML =
    '<div class="loading">Opening document…</div>';

  try {
    const loaded =
      await api(
        '/documents/' +
          encodeURIComponent(id)
      );
    const d = summaryOverride
      ? { ...loaded, ...summaryOverride }
      : loaded;

    const meta =
      Object.entries(
        d.metadata || {}
      )
        .filter(
          ([, v]) => v
        )
        .map(
          ([k, v]) => `
            <div>

              <span>
                ${esc(
                  k.replaceAll(
                    '_',
                    ' '
                  )
                )}
              </span>

              ${esc(v)}

            </div>
          `
        )
        .join('');

    const labs =
      (
        d.lab_results || []
      )
        .map(
          x => `
            <div class="lab">

              <span>
                ${esc(
                  x.investigation
                )}
              </span>

              <b>
                ${esc(
                  x.result_value
                )}

                ${esc(
                  x.result_unit || ''
                )}
              </b>

            </div>
          `
        )
        .join('');

    view.innerHTML = `
      <button
        class="link-button"
        data-go="documents">

        ← All documents

      </button>

      <div class="intro">

        <div>

          <h2>
            ${esc(d.filename)}
          </h2>

          <p>
            ${esc(
              d.report_type ||
                'Medical document'
            )}

            ·

            ${date(
              d.report_date ||
                d.uploaded_at
            )}
          </p>

        </div>

      </div>

  ${summaryCard(d)}

      <div class="viewer">

        <div>

          ${
            d.has_file
              ? `
                <iframe
                  class="pdf"
                  id="pdf-viewer"
                  title="${esc(
                    d.filename
                  )}"
                  src="about:blank">
                </iframe>
              `
              : `
                <div class="empty card">

                  <strong>
                    Original PDF unavailable
                  </strong>

                  <p>
                    This report was added before
                    document-file storage was enabled.
                  </p>

                </div>
              `
          }

        </div>

        <aside class="card">

          <section class="report-tags" aria-labelledby="report-tags-title">
            <h3 id="report-tags-title">Tags</h3>
            <div class="tag-list" id="report-tag-list">${tagChips(d.tags || []) || '<span class="muted">No tags yet</span>'}</div>
            <form id="report-tags-form">
              <label for="report-tags-input">Edit tags</label>
              <input class="search" id="report-tags-input" type="text" maxlength="400"
                value="${esc((d.tags || []).join(', '))}" placeholder="e.g. Cardiology, Follow-up" />
              <small>Separate tags with commas. Up to 10 tags, 32 characters each.</small>
              <p class="tag-error" id="report-tags-error" role="alert"></p>
              <button class="button ghost" type="submit">Save tags</button>
            </form>
          </section>

          <h3>
            Document information
          </h3>

          ${
            meta
              ? `
                <div class="detail-list">
                  ${meta}
                </div>
              `
              : `
                <p class="muted">
                  No structured information was
                  extracted from this document.
                </p>
              `
          }

          ${
            labs
              ? `
                <h3 style="margin-top:24px">
                  Extracted results
                </h3>

                ${labs}
              `
              : ''
          }

        </aside>

      </div>
    `;

    const tagsForm = document.querySelector('#report-tags-form');
    tagsForm.onsubmit = async event => {
      event.preventDefault();
      const input = document.querySelector('#report-tags-input');
      const error = document.querySelector('#report-tags-error');
      const button = tagsForm.querySelector('button[type="submit"]');
      const tags = input.value.split(',').map(tag => tag.trim()).filter(Boolean);
      const seen = new Set();
      const unique = tags.filter(tag => {
        const key = tag.toLocaleLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      if (tags.length > 10 || unique.some(tag => tag.length > 32)) {
        error.textContent = 'Use up to 10 tags with no more than 32 characters each.';
        return;
      }
      error.textContent = '';
      button.disabled = true;
      try {
        const updated = await api('/documents/' + encodeURIComponent(id) + '/tags', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tags: unique }),
        });
        d.tags = updated.tags;
        const stored = state.docs.find(item => item.id === id);
        if (stored) stored.tags = updated.tags;
        document.querySelector('#report-tag-list').innerHTML =
          tagChips(updated.tags) || '<span class="muted">No tags yet</span>';
        input.value = updated.tags.join(', ');
        toast('Tags saved.');
      } catch (saveError) {
        error.textContent = `Could not save tags: ${saveError.message}`;
      } finally {
        button.disabled = false;
      }
    };

    if (d.has_file) {
      loadPdfIntoIframe(
        document.querySelector(
          '#pdf-viewer'
        ),
        id
      );
    }

    if ((d.summary_status || 'pending') === 'pending') {
      summaryPollTimer = setTimeout(() => pollSummary(id), 2500);
    }

  } catch (e) {
    view.innerHTML = `
      <div class="empty">

        <strong>
          Couldn’t open document
        </strong>

        ${esc(e.message)}

      </div>
    `;
  }
}
