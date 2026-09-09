import { esc } from './utils.js';

export async function loadPdfIntoIframe(
  iframe,
  documentId
) {
  if (!iframe) return;

  try {
    iframe.style.display =
      'none';

    const response =
      await fetch(
        `/api/documents/${encodeURIComponent(
          documentId
        )}/file`,
        {
          method: 'GET',
          credentials:
            'same-origin'
        }
      );

    if (!response.ok) {

      let message =
        `Unable to load PDF (${response.status})`;

      try {
        const data =
          await response.json();

        if (data.detail) {
          message =
            data.detail;
        }

      } catch {}

      throw new Error(
        message
      );
    }

    const blob =
      await response.blob();

    if (
      blob.type &&
      blob.type !==
        'application/pdf'
    ) {
      throw new Error(
        `Server returned "${blob.type}" instead of application/pdf.`
      );
    }

    const blobUrl =
      URL.createObjectURL(
        new Blob(
          [blob],
          {
            type:
              'application/pdf'
          }
        )
      );

    iframe.dataset.blobUrl =
      blobUrl;

    iframe.src =
      `${blobUrl}#toolbar=1&view=FitH`;

    iframe.onload = () => {
      iframe.style.display =
        'block';
    };

    setTimeout(
      () => {
        iframe.style.display =
          'block';
      },
      500
    );

  } catch (error) {

    console.error(
      'PDF viewer error:',
      error
    );

    const wrapper =
      iframe.parentElement;

    if (wrapper) {
      wrapper.innerHTML = `
        <div class="empty card">

          <strong>
            Couldn’t display the PDF
          </strong>

          <p>
            ${esc(
              error.message
            )}
          </p>

          <button
            class="button"
            type="button"
            data-pdf-retry="${esc(
              documentId
            )}">

            Try again

          </button>

        </div>
      `;
    }
  }
}

export function cleanupPdfBlob() {
  const iframe =
    document.querySelector(
      '#pdf-viewer'
    );

  if (!iframe) return;

  const blobUrl =
    iframe.dataset.blobUrl;

  if (blobUrl) {
    URL.revokeObjectURL(
      blobUrl
    );

    delete iframe.dataset
      .blobUrl;
  }
}