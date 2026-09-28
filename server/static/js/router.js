import { cleanupPdfBlob } from './pdf.js';

import {
  dashboard,
  documents,
  documentView
} from './documents.js';

import { health } from './health.js';
import { chat } from './chat.js';
import { trends, cleanupTrends } from './trends.js';
import { timeline } from './timeline.js';

import {
  renderSidebarChats
} from './sidebar.js';

export function route() {
  cleanupPdfBlob();
  cleanupTrends();

  const hash = location.hash.slice(1);
  const routePath = hash.split('?')[0];
  const [page, ...rest] = routePath.split('/');
  const rawId = rest.join('/');

  const id =
    rawId
      ? decodeURIComponent(rawId)
      : null;

  document
    .querySelectorAll(
      '[data-route]'
    )
    .forEach(a => {
      const active = a.dataset.route === (page || 'dashboard');
      a.classList.toggle(
        'active',
        active
      );
      if (active) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });

  document.querySelector('.sidebar')?.classList.remove('open');
  document.querySelector('.menu')?.setAttribute('aria-expanded', 'false');

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

  if (page === 'trends') {
    return trends();
  }

  if (page === 'timeline') {
    return timeline();
  }

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
