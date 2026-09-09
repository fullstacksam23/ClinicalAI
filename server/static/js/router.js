import { cleanupPdfBlob } from './pdf.js';

import {
  dashboard,
  documents,
  documentView
} from './documents.js';

import { health } from './health.js';
import { chat } from './chat.js';

import {
  renderSidebarChats
} from './sidebar.js';

export function route() {
  const [
    page,
    rawId
  ] =
    location.hash
      .slice(1)
      .split('/');

  const id =
    rawId
      ? decodeURIComponent(rawId)
      : null;

  document
    .querySelectorAll(
      '[data-route]'
    )
    .forEach(a =>
      a.classList.toggle(
        'active',
        a.dataset.route ===
          (page ||
            'dashboard')
      )
    );

  renderSidebarChats(
    page === 'chat'
      ? id
      : null
  );

  if (
    page === 'documents' &&
    id
  ) {
    return documentView(id);
  }

  if (page === 'chat') {
    return chat(id);
  }

  cleanupPdfBlob();

  return (
    {
      documents,
      health
    }[page] ||
    dashboard
  )();
}

export function go(page) {
  location.hash =
    '#' + page;
}