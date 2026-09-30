import assert from 'node:assert/strict';
import test from 'node:test';
import { availableTags, filterDocuments } from './static/js/documentFilters.js';
import { api } from './static/js/utils.js';

const documents = [
  { filename: 'hematology.pdf', report_type: 'Lab', referring_doctor: 'Dr Rao',
    report_date: '2026-01-14', tags: ['Cardiology', 'Follow-up'] },
  { filename: 'scan.pdf', report_type: 'Imaging', referring_doctor: 'Dr Shah',
    report_date: '2025-04-20', tags: ['Imaging'] },
  { filename: 'visit.pdf', report_type: 'Consultation', referring_doctor: '',
    uploaded_at: '2026-03-02T10:00:00Z', tags: ['cardiology'] },
];

test('search combines doctor, date, and tag filters', () => {
  assert.deepEqual(filterDocuments(documents, { query: 'rao', tag: 'CARDIOLOGY',
    from: '2026-01-01', to: '2026-02-01' }), [documents[0]]);
  assert.deepEqual(filterDocuments(documents, { query: '2026-03' }), [documents[2]]);
  assert.deepEqual(filterDocuments(documents, { query: 'missing' }), []);
});

test('tags are unique across reports and dates stay inclusive', () => {
  assert.deepEqual(availableTags(documents), ['Cardiology', 'Follow-up', 'Imaging']);
  assert.deepEqual(filterDocuments(documents, { from: '2026-01-14', to: '2026-01-14' }), [documents[0]]);
});

test('validation failures keep a readable save error', async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    json: async () => ({ detail: [{ msg: 'Tags must be 1–32 characters.' }] }),
  });
  try {
    await assert.rejects(() => api('/documents/r1/tags', { method: 'PATCH' }),
      /Tags must be 1–32 characters/);
  } finally {
    globalThis.fetch = previousFetch;
  }
});
