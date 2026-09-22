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

export function health() {
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

      <button
        class="button"
        type="button"
        data-export-doctor>
        Export for doctor
      </button>

    </div>

    <div id="doctor-export-root"></div>

    <div class="card">

      <div
        id="health-list"
        class="loading">

        Loading available results…

      </div>

    </div>
  `;

  api('/lab-results')
    .then(labs => {

      const healthList =
        document.querySelector(
          '#health-list'
        );

      if (!healthList)
        return;

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