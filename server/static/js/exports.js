import { esc, toast } from './utils.js';

const today = () => new Date().toISOString().slice(0, 10);
const yearAgo = () => {
  const value = new Date();
  value.setFullYear(value.getFullYear() - 1);
  return value.toISOString().slice(0, 10);
};

function questionRow(value = '') {
  return `
    <div class="export-question-row">
      <input class="search" type="text" maxlength="300" data-export-question value="${esc(value)}" placeholder="Question for your doctor" />
      <button class="link-button danger" type="button" data-export-remove-question aria-label="Remove question">Remove</button>
    </div>
  `;
}

export function openDoctorExport() {
  const root = document.querySelector('#doctor-export-root');
  if (!root) return;
  root.innerHTML = `
    <div class="export-panel card" role="dialog" aria-labelledby="doctor-export-title">
      <div class="summary-heading">
        <h3 id="doctor-export-title">Export for doctor</h3>
        <button class="link-button" type="button" data-export-close>Close</button>
      </div>
      <form id="doctor-export-form">
        <div class="export-date-grid">
          <label>From <input class="search" type="date" name="date_from" value="${yearAgo()}" required /></label>
          <label>To <input class="search" type="date" name="date_to" value="${today()}" required /></label>
        </div>
        <label class="export-label">Questions for the doctor</label>
        <div id="export-questions">${questionRow()}</div>
        <button class="link-button" type="button" data-export-add-question>Add question</button>
        <div class="export-actions">
          <button class="button" type="submit" data-export-submit>Download PDF</button>
        </div>
      </form>
    </div>
  `;
  root.querySelector('[data-export-submit]')?.focus();
}

export function closeDoctorExport() {
  const root = document.querySelector('#doctor-export-root');
  if (root) root.textContent = '';
}

export function addDoctorQuestion() {
  const list = document.querySelector('#export-questions');
  if (!list || list.querySelectorAll('[data-export-question]').length >= 10) return;
  list.insertAdjacentHTML('beforeend', questionRow());
}

export function removeDoctorQuestion(button) {
  const row = button.closest('.export-question-row');
  const list = document.querySelector('#export-questions');
  if (!row || !list) return;
  if (list.children.length === 1) {
    row.querySelector('input').value = '';
    return;
  }
  row.remove();
}

export async function submitDoctorExport(form) {
  const button = form.querySelector('[data-export-submit]');
  const dateFrom = form.elements.date_from.value;
  const dateTo = form.elements.date_to.value;
  const questions = [...form.querySelectorAll('[data-export-question]')]
    .map(input => input.value)
    .filter(value => value.length > 0);
  if (dateFrom > dateTo) {
    toast('The start date must be before the end date.');
    return;
  }
  button.disabled = true;
  button.textContent = 'Preparing PDF...';
  try {
    const response = await fetch('/api/exports/doctor-summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date_from: dateFrom, date_to: dateTo, questions }),
    });
    if (!response.ok) {
      let message = 'Could not create the doctor summary.';
      try { message = (await response.json()).detail || message; } catch {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `doctor-visit-summary-${today()}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    closeDoctorExport();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Download PDF';
  }
}