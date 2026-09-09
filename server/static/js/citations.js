import { esc } from './utils.js';

/*
 * Supported citation formats:
 *
 *   [report:UUID]
 *   [lab:UUID]
 *
 * We also recover citations when the model accidentally returns
 * the HTML that the frontend normally generates, for example:
 *
 * class="citation-chip"
 * type="button"
 * data-open="UUID"
 * title="View source document">
 * 1
 *
 * This MUST be cleaned before escaping the answer.
 */

const CITATION_RE =
  /\[\s*(report|lab)(?:\s*id)?\s*[:\-]\s*([\w-]+)\s*\]/gi;


/*
 * Recover leaked citation HTML/text.
 */
function recoverLeakedCitations(text) {
  if (!text) return '';

  let result = String(text);

  /*
   * Case 1:
   *
   * <button class="citation-chip"
   *   type="button"
   *   data-open="UUID"
   *   title="View source document">
   *   1
   * </button>
   */
  result = result.replace(
    /<button\b[^>]*class\s*=\s*["'][^"']*citation-chip[^"']*["'][^>]*data-open\s*=\s*["']([^"']+)["'][^>]*>[\s\S]*?<\/button>/gi,
    (_match, id) =>
      `[report:${id}]`
  );

  /*
   * Case 2:
   * The <button> tag has already disappeared and only
   * the attributes/text remain.
   */
  result = result.replace(
    /class\s*=\s*["']citation-chip["']\s*[\r\n\s]*type\s*=\s*["']button["']\s*[\r\n\s]*data-open\s*=\s*["']([^"']+)["']\s*[\r\n\s]*title\s*=\s*["']View source document["']\s*>\s*(\d+)\s*(?:<\/button>)?/gi,
    (_match, id) =>
      `[report:${id}]`
  );

  /*
   * Same thing, but attributes can arrive in a different order.
   * We locate data-open and then remove the surrounding citation
   * markup.
   */
  result = result.replace(
    /(?:class\s*=\s*["']citation-chip["'][\s\S]{0,300}?data-open\s*=\s*["']([^"']+)["'][\s\S]{0,300}?title\s*=\s*["']View source document["'][\s\S]{0,50}?)(?:<\/button>)?/gi,
    (_match, id) =>
      `[report:${id}]`
  );

  /* Recover the current numbered aria-label/title form when the opening
   * button tag has leaked and only its attributes remain. */
  result = result.replace(
    /class\s*=\s*["'][^"']*citation-chip[^"']*["'][\s\S]{0,500}?data-open\s*=\s*["']([^"']+)["'][\s\S]{0,500}?(?:aria-label\s*=\s*["']Open source document\s+\d+["'][\s\S]{0,200}?)?(?:title\s*=\s*["']Open source document\s+\d+["'][\s\S]{0,100}?)?>\s*\d+\s*(?:<\/button>)?/gi,
    (_match, id) =>
      `[report:${id}]`
  );

  /* Last-resort form: recover any citation-chip attribute block whose
   * opening tag was stripped, independent of attribute order. */
  result = result.replace(
    /class\s*=\s*["'][^"']*citation-chip[^"']*["'][\s\S]*?data-open\s*=\s*["']([^"']+)["'][\s\S]*?>\s*\d+\s*(?:<\/button>)?/gi,
    (_match, id) =>
      `[report:${id}]`
  );

  /*
   * If the model returned an actual <button> with data-open
   * but omitted the citation-chip class, recover that too.
   */
  result = result.replace(
    /<button\b[^>]*data-open\s*=\s*["']([^"']+)["'][^>]*>[\s\S]*?<\/button>/gi,
    (_match, id) =>
      `[report:${id}]`
  );

  /*
   * Remove leftover citation-only attribute lines.
   * These should never be visible to the user.
   */
  result = result
    .replace(
      /^\s*class\s*=\s*["']citation-chip["']\s*$/gim,
      ''
    )
    .replace(
      /^\s*type\s*=\s*["']button["']\s*$/gim,
      ''
    )
    .replace(
      /^\s*data-open\s*=\s*["'][^"']+["']\s*$/gim,
      ''
    )
    .replace(
      /^\s*title\s*=\s*["']View source document["']\s*>\s*$/gim,
      ''
    )
    .replace(
      /^\s*aria-label\s*=\s*["']Open source document\s+\d+["']\s*$/gim,
      ''
    )
    .replace(
      /^\s*title\s*=\s*["']Open source document\s+\d+["']\s*>?\s*$/gim,
      ''
    );

  /*
   * Remove standalone "View source document" text only when
   * it is clearly part of leaked citation markup.
   */
  result = result.replace(
    /^\s*(?:View source document|Open source document\s+\d+)\s*$/gim,
    ''
  );

  /*
   * Clean stray HTML closing tags that can appear after
   * the model's malformed citation.
   */
  result = result
    .replace(
      /^\s*<\/button>\s*$/gim,
      ''
    )
    .replace(
      /^\s*<\/span>\s*$/gim,
      ''
    );

  return result;
}


/*
 * Removes accidental HTML while preserving useful text.
 */
function cleanModelAnswer(answer) {
  let text =
    recoverLeakedCitations(answer);

  /*
   * Remove any remaining citation-specific markup.
   */
  text = text
    .replace(
      /<button\b[^>]*>/gi,
      ''
    )
    .replace(
      /<\/button>/gi,
      ''
    )
    .replace(
      /class\s*=\s*["'][^"']*citation[^"']*["']/gi,
      ''
    )
    .replace(
      /data-open\s*=\s*["'][^"']+["']/gi,
      ''
    )
    .replace(
      /title\s*=\s*["']View source document["']/gi,
      ''
    );

  /*
   * Remove excessive blank lines created by cleanup.
   */
  text = text.replace(
    /\n{3,}/g,
    '\n\n'
  );

  return text.trim();
}


export function renderAnswerWithCitations(
  answer,
  sources = []
) {
  const labToReport = new Map();

  /*
   * Some retrieval pipelines return source objects where
   * the lab result points back to its parent report.
   */
  sources.forEach(source => {
    if (
      source?.lab_result_id &&
      source?.report_id
    ) {
      labToReport.set(
        String(source.lab_result_id),
        String(source.report_id)
      );
    }
  });

  /*
   * Reports are numbered in first-seen order.
   */
  const citationOrder = [];

  function numberFor(reportId) {
    reportId = String(reportId);

    let index =
      citationOrder.indexOf(reportId);

    if (index === -1) {
      citationOrder.push(
        reportId
      );

      index =
        citationOrder.length - 1;
    }

    return index + 1;
  }


  /*
   * IMPORTANT:
   * Clean/recover citations BEFORE escaping HTML.
   */
  const cleaned =
    cleanModelAnswer(answer);

  let text =
    esc(cleaned);


  /*
   * Convert citation markers to actual buttons.
   */
  text = text.replace(
    CITATION_RE,
    (_match, kind, rawId) => {

      const id =
        String(rawId);

      const reportId =
        kind.toLowerCase() === 'report'
          ? id
          : labToReport.get(id);

      /*
       * Unknown citation:
       * remove it instead of displaying broken text.
       */
      if (!reportId) {
        return '';
      }

      const number =
        numberFor(reportId);

      /* Keep the generated control on one logical line. The paragraph
       * renderer below treats newlines as text boundaries; multiline HTML
       * here would expose the button's attributes as visible paragraphs. */
      return `<button class="citation-chip" type="button" data-open="${esc(reportId)}" aria-label="Open source document ${number}" title="Open source document ${number}">${number}</button>`;
    }
  );


  /*
   * Basic markdown.
   */
  text = text.replace(
    /\*\*(.+?)\*\*/g,
    '<strong>$1</strong>'
  );


  /*
   * Render lines while keeping citation buttons inline.
   */
  const lines =
    text.split('\n');

  const parts = [];

  let inList = false;

  for (
    const rawLine of lines
  ) {
    const line =
      rawLine.trim();

    if (!line) {
      continue;
    }

    const isBullet =
      /^[*-]\s+/.test(line);

    if (isBullet) {

      if (!inList) {
        parts.push('<ul>');
        inList = true;
      }

      parts.push(
        `<li>${line.replace(
          /^[*-]\s+/,
          ''
        )}</li>`
      );

      continue;
    }

    if (inList) {
      parts.push('</ul>');
      inList = false;
    }

    parts.push(
      `<p>${line}</p>`
    );
  }

  if (inList) {
    parts.push('</ul>');
  }


  /*
   * Source list at the bottom of the answer.
   */
  const legend =
    citationOrder.length
      ? `
        <div class="citation-legend">

          <div class="citation-legend-title">
            Sources
          </div>

          ${citationOrder
            .map(
              (reportId, index) => `
                <button
                  class="citation-legend-item"
                  type="button"
                  data-open="${esc(
                    reportId
                  )}">

                  <span
                    class="citation-chip citation-chip-small"
                    aria-hidden="true">

                    ${index + 1}

                  </span>

                  <span class="citation-source-text">
                    View source document
                  </span>

                </button>
              `
            )
            .join('')}

        </div>
      `
      : '';


  return (
    parts.join('') +
    legend
  );
}


export function messageHtml(
  role,
  content,
  sources = []
) {
  if (role === 'user') {

    return `
      <div class="message-row user-row">

        <div class="message user">
          ${esc(content)}
        </div>

      </div>
    `;
  }

  return `
    <div class="message-row assistant-row">

      <div class="message assistant">
        ${renderAnswerWithCitations(
          content,
          sources
        )}
      </div>

      ${
        sources?.length
          ? `
            <div class="source-note">
              Grounded in records from
              this workspace.
            </div>
          `
          : ''
      }

    </div>
  `;
}
