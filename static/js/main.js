/* ═══════════════════════════════════════════════════════════════
   HR Audit — South African HR Compliance Suite
   Main JavaScript
═══════════════════════════════════════════════════════════════ */

'use strict';

document.addEventListener('DOMContentLoaded', function () {

  // ── Bootstrap tooltips init ──────────────────────────────────
  const tooltipEls = document.querySelectorAll('[data-bs-toggle="tooltip"]');
  tooltipEls.forEach(el => new bootstrap.Tooltip(el, { trigger: 'hover' }));

  // ── Bootstrap popovers init ──────────────────────────────────
  const popoverEls = document.querySelectorAll('[data-bs-toggle="popover"]');
  popoverEls.forEach(el => new bootstrap.Popover(el));

  // ── Confirm before delete ────────────────────────────────────
  document.querySelectorAll('.delete-form').forEach(form => {
    form.addEventListener('submit', function (e) {
      if (!confirm('Are you sure you want to delete this record? This action cannot be undone.')) {
        e.preventDefault();
      }
    });
  });

  // ── Employee table search filter ─────────────────────────────
  const empSearch = document.getElementById('emp-search');
  const empTable  = document.getElementById('emp-table');
  if (empSearch && empTable) {
    empSearch.addEventListener('input', function () {
      const query = this.value.toLowerCase().trim();
      empTable.querySelectorAll('tbody tr').forEach(row => {
        const text = row.textContent.toLowerCase();
        row.style.display = (!query || text.includes(query)) ? '' : 'none';
      });
    });
  }

  // ── Upload dropzone drag-and-drop highlight ──────────────────
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('bulk-file');
  if (dropzone && fileInput) {
    ['dragenter', 'dragover'].forEach(evt =>
      dropzone.addEventListener(evt, e => {
        e.preventDefault();
        dropzone.classList.add('drag-over');
      })
    );
    ['dragleave', 'drop'].forEach(evt =>
      dropzone.addEventListener(evt, e => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');
      })
    );
    dropzone.addEventListener('drop', function (e) {
      const files = e.dataTransfer.files;
      if (files.length) {
        fileInput.files = files;
        const nameEl = document.getElementById('file-name');
        if (nameEl) nameEl.textContent = files[0].name;
      }
    });
  }

  // ── Auto-dismiss flash alerts after 5 seconds ────────────────
  document.querySelectorAll('.alert.alert-success, .alert.alert-info').forEach(alert => {
    setTimeout(() => {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      if (bsAlert) bsAlert.close();
    }, 5000);
  });

  // ── COIDA: live levy calculator ──────────────────────────────
  // (inline script in coida.html handles per-form live preview;
  //  this block handles any additional COIDA summary updates)
  const coidaEarnings = document.getElementById('coida-earnings');
  if (coidaEarnings) {
    coidaEarnings.addEventListener('input', function () {
      // Trigger the inline updateLevyPreview if defined
      if (typeof updateLevyPreview === 'function') updateLevyPreview();
    });
  }

  // ── Sidebar: mark active from current URL ────────────────────
  const currentPath = window.location.pathname.split('/')[1];
  document.querySelectorAll('.sidebar-link').forEach(link => {
    const href = link.getAttribute('href') || '';
    const linkPath = href.split('/')[1];
    if (currentPath && linkPath === currentPath) {
      link.classList.add('active');
    }
  });

  // ── Number formatting helper for display ────────────────────
  window.formatRand = function (value) {
    return 'R ' + parseFloat(value || 0).toLocaleString('en-ZA', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };

  // ── BBBEE: score input clamp (prevent over-max entry) ────────
  document.querySelectorAll('.score-input').forEach(input => {
    input.addEventListener('blur', function () {
      const max = parseFloat(this.dataset.max || 999);
      if (parseFloat(this.value) > max) {
        this.value = max;
        this.dispatchEvent(new Event('input'));
      }
      if (parseFloat(this.value) < 0) {
        this.value = 0;
        this.dispatchEvent(new Event('input'));
      }
    });
  });

  // ── WSP: training status filter ─────────────────────────────
  // (handled inline in wsp.html; exposed here for completeness)

  // ── Print button helper ──────────────────────────────────────
  document.querySelectorAll('.btn-print').forEach(btn => {
    btn.addEventListener('click', () => window.print());
  });

  // ── Keyboard shortcut: Ctrl+/ focuses search ────────────────
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === '/') {
      e.preventDefault();
      const search = document.getElementById('emp-search') ||
                     document.querySelector('input[type="search"], input[placeholder*="Search"]');
      if (search) { search.focus(); search.select(); }
    }
  });

  // ── Submission checklist: persist + live progress ────────────
  initChecklist();

});

/* ═══════════════════════════════════════════════════════════════
   SUBMISSION CHECKLIST  (event-delegated so dynamic tasks work)
═══════════════════════════════════════════════════════════════ */
function initChecklist() {
  const wrap = document.querySelector('.checklist-wrap');
  if (!wrap) return;

  const module = wrap.dataset.module;

  // ── Expand / collapse all steps ───────────────────────────────
  wrap.querySelectorAll('.checklist-expand-all').forEach(btn => {
    btn.addEventListener('click', function () {
      wrap.querySelectorAll('.accordion-collapse').forEach(panel => {
        panel.classList.add('show');
        const hdr = wrap.querySelector('[data-bs-target="#' + panel.id + '"]');
        if (hdr) hdr.classList.remove('collapsed');
      });
    });
  });
  wrap.querySelectorAll('.checklist-collapse-all').forEach(btn => {
    btn.addEventListener('click', function () {
      wrap.querySelectorAll('.accordion-collapse').forEach(panel => {
        panel.classList.remove('show');
        const hdr = wrap.querySelector('[data-bs-target="#' + panel.id + '"]');
        if (hdr) hdr.classList.add('collapsed');
      });
    });
  });

  // ── Toggle a checkbox (predefined or custom) ──────────────────
  wrap.addEventListener('change', function (e) {
    const cb = e.target.closest('.checklist-checkbox');
    if (!cb) return;
    const itemKey = cb.dataset.item;
    const checked = cb.checked;

    const label = cb.parentElement.querySelector('.form-check-label');
    if (label) {
      label.classList.toggle('text-decoration-line-through', checked);
      label.classList.toggle('text-muted', checked);
    }
    updateStepBadge(cb.dataset.step);

    fetch('/checklist/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module: module, item_key: itemKey, checked: checked }),
    })
      .then(r => r.json())
      .then(data => { if (data && data.ok) updateOverallProgress(data.done, data.total, data.percent); })
      .catch(() => {
        cb.checked = !checked;
        if (label) {
          label.classList.toggle('text-decoration-line-through', cb.checked);
          label.classList.toggle('text-muted', cb.checked);
        }
        updateStepBadge(cb.dataset.step);
      });
  });

  // ── Add task (button click) ───────────────────────────────────
  wrap.addEventListener('click', function (e) {
    const addBtn = e.target.closest('.add-task-btn');
    if (addBtn) {
      const stepKey = addBtn.dataset.step;
      const input = wrap.querySelector('.add-task-input[data-step="' + stepKey + '"]');
      addTask(stepKey, input);
      return;
    }
    const delBtn = e.target.closest('.delete-task-btn');
    if (delBtn) {
      deleteTask(delBtn.dataset.taskId, delBtn.dataset.step, delBtn);
      return;
    }
    const hideBtn = e.target.closest('.hide-task-btn');
    if (hideBtn) {
      hideTask(hideBtn.dataset.itemKey, hideBtn.dataset.step, hideBtn);
      return;
    }
  });

  // ── Add task (Enter key in input) ─────────────────────────────
  wrap.addEventListener('keydown', function (e) {
    const input = e.target.closest('.add-task-input');
    if (input && e.key === 'Enter') {
      e.preventDefault();
      addTask(input.dataset.step, input);
    }
  });

  function addTask(stepKey, input) {
    if (!input) return;
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    input.disabled = true;

    fetch('/checklist/task/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module: module, step_key: stepKey, text: text }),
    })
      .then(r => r.json())
      .then(data => {
        input.disabled = false;
        if (!data || !data.ok) return;
        insertTaskRow(stepKey, data.task);
        input.value = '';
        input.focus();
        // bump step count then refresh badge + overall
        const step = stepEl(stepKey);
        if (step) step.dataset.count = (parseInt(step.dataset.count, 10) || 0) + 1;
        updateStepBadge(stepKey);
        updateOverallProgress(data.done, data.total, data.percent);
      })
      .catch(() => { input.disabled = false; });
  }

  function deleteTask(taskId, stepKey, btn) {
    if (!confirm('Delete this custom task?')) return;
    fetch('/checklist/task/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module: module, task_id: taskId }),
    })
      .then(r => r.json())
      .then(data => {
        if (!data || !data.ok) return;
        const row = btn.closest('.custom-task-row');
        if (row) row.remove();
        const step = stepEl(stepKey);
        if (step) step.dataset.count = Math.max(0, (parseInt(step.dataset.count, 10) || 1) - 1);
        updateStepBadge(stepKey);
        updateOverallProgress(data.done, data.total, data.percent);
      });
  }

  function hideTask(itemKey, stepKey, btn) {
    if (!confirm('Remove this default task from this project?\n\nYou can restore it later from the checklist header.')) return;
    fetch('/checklist/task/hide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module: module, item_key: itemKey }),
    })
      .then(r => r.json())
      .then(data => {
        if (!data || !data.ok) return;
        const row = btn.closest('.default-task-row');
        if (row) row.remove();
        const step = stepEl(stepKey);
        if (step) step.dataset.count = Math.max(0, (parseInt(step.dataset.count, 10) || 1) - 1);
        updateStepBadge(stepKey);
        updateOverallProgress(data.done, data.total, data.percent);
        // The restore link only appears on full page reload, but progress is correct.
      });
  }

  function insertTaskRow(stepKey, task) {
    const container = wrap.querySelector('.checklist-items[data-step="' + stepKey + '"]');
    if (!container) return;
    const id = 'cl-' + module + '-' + task.key;
    const row = document.createElement('div');
    row.className = 'form-check checklist-item-row custom-task-row d-flex align-items-start gap-2 py-1';
    row.dataset.taskId = task.id;
    row.innerHTML =
      '<input class="form-check-input checklist-checkbox mt-1" type="checkbox" id="' + id + '"' +
        ' data-module="' + module + '" data-item="' + task.key + '" data-step="' + stepKey + '">' +
      '<label class="form-check-label small flex-grow-1" for="' + id + '">' +
        escapeHtml(task.text) +
        ' <span class="badge bg-light text-secondary border ms-1" style="font-size:.6rem;">custom</span>' +
      '</label>' +
      '<button type="button" class="btn btn-link btn-sm text-danger p-0 delete-task-btn"' +
        ' data-task-id="' + task.id + '" data-module="' + module + '" data-step="' + stepKey + '"' +
        ' title="Delete this task"><i class="bi bi-trash"></i></button>';
    container.appendChild(row);
  }

  function stepEl(stepKey) {
    return wrap.querySelector('.checklist-step[data-step="' + stepKey + '"]');
  }

  function updateStepBadge(stepKey) {
    const step = stepEl(stepKey);
    if (!step) return;
    const boxes = step.querySelectorAll('.checklist-checkbox');
    const count = boxes.length;
    const done  = Array.from(boxes).filter(b => b.checked).length;
    const complete = done === count && count > 0;

    const badge = step.querySelector('.step-count-badge');
    if (badge) {
      badge.querySelector('.step-done-count').textContent = done;
      const totalEl = badge.querySelector('.step-total-count');
      if (totalEl) totalEl.textContent = count;
      badge.classList.toggle('bg-success', complete);
      badge.classList.toggle('bg-secondary', !complete);
    }
    const icon = step.querySelector('.step-check-icon i');
    if (icon) {
      icon.className = complete
        ? 'bi bi-check-circle-fill text-success'
        : 'bi bi-circle text-muted';
    }
  }

  function updateOverallProgress(done, totalCount, pct) {
    const bar = wrap.querySelector('.checklist-overall-bar');
    if (bar) {
      bar.style.width = pct + '%';
      bar.classList.toggle('bg-success', pct === 100);
    }
    const doneEl  = wrap.querySelector('.checklist-overall-label .cl-done');
    const totalEl = wrap.querySelector('.checklist-overall-label .cl-total');
    const pctEl   = wrap.querySelector('.checklist-overall-label .cl-pct');
    if (doneEl)  doneEl.textContent = done;
    if (totalEl) totalEl.textContent = totalCount;
    if (pctEl)   pctEl.textContent = pct;

    const banner = wrap.querySelector('.checklist-complete-banner');
    if (banner) banner.classList.toggle('d-none', pct !== 100);

    const tabBadge = document.querySelector('#tab-checklist-btn .badge');
    if (tabBadge) tabBadge.textContent = done + '/' + totalCount;
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }
}
