import {
  api,
  toast
} from './utils.js';

import {
  state
} from './state.js';

import {
  documentView
} from './documents.js';

import {
  renderDocs
} from './documents.js';

import {
  deleteChat
} from './chat.js';

export function initGlobalHandlers() {

  document.addEventListener(
    'click',
    e => {

      const newChat =
        e.target.closest(
          '#new-chat'
        );

      if (newChat) {

        state.currentConversationId =
          null;

        location.hash =
          '#chat';

        return;
      }


      const deleteButton =
        e.target.closest(
          '[data-delete-chat]'
        );

      if (deleteButton) {

        e.stopPropagation();

        const chatId =
          deleteButton.dataset
            .deleteChat;

        if (
          confirm(
            'Delete this chat? This can’t be undone.'
          )
        ) {
          deleteChat(
            chatId
          );
        }

        return;
      }

      const deleteDocumentButton = e.target.closest('[data-delete-document]');
      if (deleteDocumentButton) {
        e.stopPropagation();
        if (!confirm('Delete this document and all associated medical data? This cannot be undone.')) return;
        api('/documents/' + encodeURIComponent(deleteDocumentButton.dataset.deleteDocument), { method: 'DELETE' })
          .then(() => renderDocs())
          .catch(error => toast(error.message));
        return;
      }


      const chatItem =
        e.target.closest(
          '[data-chat-id]'
        );

      if (chatItem) {

        location.hash =
          '#chat/' +
          encodeURIComponent(
            chatItem.dataset
              .chatId
          );

        return;
      }


      const go =
        e.target.closest(
          '[data-go]'
        );

      if (go) {

        location.hash =
          '#' +
          go.dataset.go;

        document
          .querySelector(
            '.sidebar'
          )
          ?.classList.remove(
            'open'
          );

        return;
      }


      const open =
        e.target.closest(
          '[data-open]'
        );

      if (open) {

        location.hash =
          '#documents/' +
          encodeURIComponent(
            open.dataset.open
          );

        return;
      }


      if (
        e.target.closest(
          '.menu'
        )
      ) {

        document
          .querySelector(
            '.sidebar'
          )
          ?.classList.toggle(
            'open'
          );

        return;
      }


      const retry =
        e.target.closest(
          '[data-retry]'
        );

      if (retry) {

        const question =
          document.querySelector(
            '#question'
          );

        const form =
          document.querySelector(
            '#chat-form'
          );

        if (
          question &&
          form
        ) {

          question.value =
            retry.dataset
              .retry;

          form.requestSubmit();
        }

        return;
      }


      const pdfRetry =
        e.target.closest(
          '[data-pdf-retry]'
        );

      if (pdfRetry) {

        documentView(
          pdfRetry
            .dataset
            .pdfRetry
        );

        return;
      }

    }
  );
}
