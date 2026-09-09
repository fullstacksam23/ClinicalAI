import {
  api,
  esc
} from './utils.js';

import {
  view,
  title
} from './state.js';

import {
  cleanupPdfBlob
} from './pdf.js';

import {
  loadDocs,
  docRows
} from './documents.js';

export function health() {
  cleanupPdfBlob();

  title.textContent =
    'Health record';

  view.innerHTML = `
    <div class="intro">

      <div>

        <h2>
          Your health information
        </h2>

        <p>
          Structured information appears
          here only when it has been extracted
          from your reports.
        </p>

      </div>

    </div>

    <div class="card">

      <div
        id="health-list"
        class="loading">

        Loading available results…

      </div>

    </div>
  `;

  loadDocs()
    .then(async docs => {

      const healthList =
        document.querySelector(
          '#health-list'
        );

      if (!healthList)
        return;

      if (!docs.length) {
        healthList.innerHTML =
          docRows([]);

        return;
      }

      const details =
        await Promise.all(
          docs
            .filter(
              x =>
                x.status ===
                'processed'
            )
            .slice(0, 12)
            .map(
              x =>
                api(
                  '/documents/' +
                    encodeURIComponent(
                      x.id
                    )
                ).catch(
                  () => null
                )
            )
        );

      const labs =
        details.flatMap(
          x =>
            x?.lab_results ||
            []
        );

      healthList.innerHTML =
        labs.length
          ? labs
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
                        x.result_unit ||
                          ''
                      )}
                    </b>

                    <small>
                      ${esc(
                        x.ref_interval_raw ||
                          ''
                      )}
                    </small>

                  </div>
                `
              )
              .join('')
          : `
            <div class="empty">

              <strong>
                No structured results yet
              </strong>

              Upload a report with
              extractable medical data
              to see it here.

            </div>
          `;

    })
    .catch(e => {

      const healthList =
        document.querySelector(
          '#health-list'
        );

      if (healthList) {
        healthList.textContent =
          e.message;
      }

    });
}