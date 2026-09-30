export function availableTags(documents) {
  const found = new Map();
  documents.forEach(document => {
    (document.tags || []).forEach(tag => {
      const key = tag.toLocaleLowerCase();
      if (!found.has(key)) found.set(key, tag);
    });
  });
  return [...found.values()].sort((a, b) => a.localeCompare(b));
}

export function filterDocuments(documents, { query = '', tag = '', from = '', to = '' } = {}) {
  const term = query.trim().toLocaleLowerCase();
  const selectedTag = tag.toLocaleLowerCase();
  return documents.filter(document => {
    const reportDate = String(document.report_date || document.uploaded_at || '').slice(0, 10);
    if (from && (!reportDate || reportDate < from)) return false;
    if (to && (!reportDate || reportDate > to)) return false;
    if (selectedTag && !(document.tags || []).some(value => value.toLocaleLowerCase() === selectedTag)) return false;
    if (!term) return true;
    const searchable = [
      document.filename, document.report_type, document.referring_doctor,
      reportDate, ...(document.tags || []),
    ].filter(Boolean).join(' ').toLocaleLowerCase();
    return searchable.includes(term);
  });
}
