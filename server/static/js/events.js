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
  retrySummary
} from './documents.js';

import {
  deleteChat,
  newChat
} from './chat.js';

import { setTimelineType } from './timeline.js';
import {
  openDoctorExport,
  closeDoctorExport,
  addDoctorQuestion,
  removeDoctorQuestion,
  submitDoctorExport
} from './exports.js';

export function initGlobalHandlers() {

  const closeNavigation = () => {
    document.querySelector('.sidebar')?.classList.remove('open');
    document.querySelector('.menu')?.setAttribute('aria-expanded', 'false');
  };

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeNavigation();
  });

  document.addEventListener(
    'click',
    e => {

      if (e.target.closest('.sidebar-backdrop') || e.target.closest('.sidebar nav a, .brand')) {
        closeNavigation();
      }

      const newChatButton =
        e.target.closest(
          '#new-chat'
        );

      if (newChatButton) {
        newChat();

        return;
      }

      const timelineType = e.target.closest('[data-timeline-type]');
      if (timelineType) {
        setTimelineType(timelineType.dataset.timelineType);
        return;
      }

      if (e.target.closest('[data-export-doctor]')) {
        openDoctorExport();
        return;
      }
      if (e.target.closest('[data-export-close]')) {
        closeDoctorExport();
        return;
      }
      if (e.target.closest('[data-export-add-question]')) {
        addDoctorQuestion();
        return;
      }
      const removeQuestion = e.target.closest('[data-export-remove-question]');
      if (removeQuestion) {
        removeDoctorQuestion(removeQuestion);
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

      const summaryRetry = e.target.closest('[data-summary-retry]');
      if (summaryRetry) {
        summaryRetry.disabled = true;
        retrySummary(summaryRetry.dataset.summaryRetry)
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
        const sidebar = document.querySelector('.sidebar');
        const isOpen = sidebar?.classList.toggle('open');
        document.querySelector('.menu')?.setAttribute('aria-expanded', String(!!isOpen));

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

  document.addEventListener('submit', e => {
    if (e.target.id !== 'doctor-export-form') return;
    e.preventDefault();
    submitDoctorExport(e.target);
  });
}
