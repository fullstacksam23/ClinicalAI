export const api = async (path, options = {}) => {
  const { query, ...fetchOptions } = options;
  const queryString = query
    ? new URLSearchParams(
        Object.entries(query).filter(([, value]) => value !== null && value !== undefined && value !== '')
      ).toString()
    : '';
  const requestPath = queryString
    ? `${path}${path.includes('?') ? '&' : '?'}${queryString}`
    : path;

  const r = await fetch('/api' + requestPath, fetchOptions);

  if (!r.ok) {
    let d = {};

    try {
      d = await r.json();
    } catch {}

    throw Error(
      d.detail || 'Something went wrong.'
    );
  }

  return r.status === 204
    ? null
    : r.json();
};

export const esc = s =>
  String(s ?? '').replace(
    /[&<>'"]/g,
    c => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[c])
  );

export const date = s =>
  s
    ? new Date(s).toLocaleDateString(
        undefined,
        {
          year: 'numeric',
          month: 'short',
          day: 'numeric'
        }
      )
    : 'Date unavailable';

export const toast = m => {
  const t =
    document.querySelector('#toast');

  t.textContent = m;
  t.classList.add('show');

  setTimeout(
    () => t.classList.remove('show'),
    3200
  );
};
