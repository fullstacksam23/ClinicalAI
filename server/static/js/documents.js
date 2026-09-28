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

let summaryPollTimer = null;

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
        placeholder="Search by document name or type"
        aria-label="Search documents"
      />

      <button
        class="button ghost"
        id="refresh">
        Refresh
      </button>

    </div>

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

  document.querySelector(
    '#search'
  ).oninput = e =>
    renderDocs(
      e.target.value
    );

  document.querySelector(
    '#refresh'
  ).onclick = () =>
    renderDocs();

  renderDocs();
}

export async function renderDocs(
  query = ''
) {
  const el =
    document.querySelector(
      '#documents-list'
    );

  if (!el) return;

  try {
    await loadDocs();

    const q =
      query.toLowerCase();

    const items =
      state.docs.filter(
        d =>
          `${d.filename} ${
            d.report_type || ''
          }`
            .toLowerCase()
            .includes(q)
      );

    el.innerHTML =
      docRows(items);

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
