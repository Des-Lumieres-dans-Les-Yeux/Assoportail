/**
 * app.js — Assoportail global JavaScript.
 *
 * Rules:
 *  - No inline logic; all behavior lives here or in dedicated modules.
 *  - Alpine.js components are registered via Alpine.data() (CSP-compatible build).
 *  - HTMX CSRF header is injected globally below.
 */

// ---------------------------------------------------------------------------
// HTMX — inject CSRF token on every request
// ---------------------------------------------------------------------------
document.addEventListener("htmx:configRequest", function (evt) {
  const meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    evt.detail.headers["X-CSRFToken"] = meta.content;
  }
});

// ---------------------------------------------------------------------------
// HTMX — show a loading indicator on the active element
// ---------------------------------------------------------------------------
document.addEventListener("htmx:beforeRequest", function (evt) {
  const el = evt.detail.elt;
  if (el) {
    el.setAttribute("aria-busy", "true");
  }
});

document.addEventListener("htmx:afterRequest", function (evt) {
  const el = evt.detail.elt;
  if (el) {
    el.removeAttribute("aria-busy");
  }
});

// ---------------------------------------------------------------------------
// HTMX — re-open Bootstrap modals injected via HTMX swap
// ---------------------------------------------------------------------------
document.addEventListener("htmx:afterSwap", function (evt) {
  const modal = evt.detail.target.querySelector(".modal");
  if (modal && typeof bootstrap !== "undefined") {
    new bootstrap.Modal(modal).show();
  }
});

// ---------------------------------------------------------------------------
// Confirmation dialogs via data-confirm attribute (no inline JS)
// ---------------------------------------------------------------------------
document.addEventListener('submit', function (e) {
  const form = e.target;
  const message = form.getAttribute('data-confirm');
  if (message && !confirm(message)) {
    e.preventDefault();
  }
});

// ---------------------------------------------------------------------------
// Submit button loading state — disable + spinner while form posts
// Skipped for HTMX forms (they handle their own state via aria-busy).
// ---------------------------------------------------------------------------
document.addEventListener('submit', function (e) {
  if (e.defaultPrevented) return;
  const form = e.target;
  if (form.hasAttribute('hx-post') || form.hasAttribute('hx-get')) return;
  form.querySelectorAll('button[type="submit"]:not([data-no-loading])').forEach(function (btn) {
    btn.disabled = true;
    const label = btn.textContent.trim();
    btn.innerHTML =
      '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>' +
      label;
  });
});

// ---------------------------------------------------------------------------
// Auto-dismiss flash alerts — success/info fade after 5 s, warning after 8 s
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.alert').forEach(function (el) {
    const delay = el.classList.contains('alert-warning') ? 8000 : 5000;
    if (!el.classList.contains('alert-danger')) {
      setTimeout(function () {
        const bsAlert = bootstrap.Alert.getOrCreateInstance(el);
        if (bsAlert) bsAlert.close();
      }, delay);
    }
  });
});

// ---------------------------------------------------------------------------
// Bootstrap tooltips — initialize all [data-bs-toggle="tooltip"] elements
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
  var isTouch = window.matchMedia('(hover: none)').matches;
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
    new bootstrap.Tooltip(el, isTouch ? { trigger: 'click' } : {});
  });
});

// ---------------------------------------------------------------------------
// Copy-to-clipboard — [data-copy] copies text; [data-copy-target] copies
// text content of the element with that ID.
// ---------------------------------------------------------------------------
document.addEventListener('click', function (e) {
  const btn = e.target.closest('[data-copy], [data-copy-target]');
  if (!btn) return;
  let text = btn.dataset.copy || '';
  if (!text && btn.dataset.copyTarget) {
    const src = document.getElementById(btn.dataset.copyTarget);
    if (src) text = src.textContent.trim();
  }
  if (!text) return;
  navigator.clipboard.writeText(text).then(function () {
    const original = btn.innerHTML;
    const origClasses = btn.className;
    btn.innerHTML = '<i class="bi bi-check-lg" aria-hidden="true"></i>';
    btn.className = btn.className.replace('btn-outline-secondary', 'btn-success');
    setTimeout(function () {
      btn.innerHTML = original;
      btn.className = origClasses;
    }, 1500);
  });
});

// ---------------------------------------------------------------------------
// CSV import — [data-trigger-file] button clicks the hidden file input
// ---------------------------------------------------------------------------
document.addEventListener('click', function (e) {
  const btn = e.target.closest('[data-trigger-file]');
  if (!btn) return;
  const input = document.getElementById(btn.dataset.triggerFile);
  if (input) input.click();
});

// ---------------------------------------------------------------------------
// [data-auto-submit="formId"] — submits the named form on change.
// Works for: file inputs, <select>, checkboxes, radio buttons.
// Uses direct binding (not delegation) so label-triggered file inputs work.
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-auto-submit]').forEach(function (el) {
    el.addEventListener('change', function () {
      const form = document.getElementById(el.dataset.autoSubmit);
      if (form) form.submit();
    });
  });
});

// ---------------------------------------------------------------------------
// Client-side table sort — table[data-sortable] with th[data-sort] headers.
// Numeric-aware, locale-sensitive. Adds aria-sort and ↑/↓ icon via CSS class.
// NOT used for paginated tables (those use server-side sort links instead).
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('table[data-sortable]').forEach(function (table) {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;

    table.querySelectorAll('th[data-sort]').forEach(function (th) {
      th.style.cursor = 'pointer';
      th.setAttribute('tabindex', '0');
      th.setAttribute('role', 'columnheader');

      function doSort() {
        const colIndex = Array.from(th.parentElement.children).indexOf(th);
        const asc = th.getAttribute('aria-sort') !== 'ascending';

        // Reset all headers
        table.querySelectorAll('th[data-sort]').forEach(function (h) {
          h.removeAttribute('aria-sort');
          const sortIcon = h.querySelector('.sort-icon');
          if (sortIcon) sortIcon.remove();
        });

        th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
        const icon = document.createElement('i');
        icon.className = 'bi bi-caret-' + (asc ? 'up' : 'down') + '-fill small ms-1 sort-icon';
        icon.setAttribute('aria-hidden', 'true');
        th.appendChild(icon);

        const rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function (a, b) {
          const aText = (a.cells[colIndex] ? a.cells[colIndex].textContent.trim() : '');
          const bText = (b.cells[colIndex] ? b.cells[colIndex].textContent.trim() : '');
          const aVal = (a.cells[colIndex] && a.cells[colIndex].dataset.sortValue) || aText;
          const bVal = (b.cells[colIndex] && b.cells[colIndex].dataset.sortValue) || bText;
          const aNum = parseFloat(aVal.replace(/\s/g, '').replace(',', '.'));
          const bNum = parseFloat(bVal.replace(/\s/g, '').replace(',', '.'));
          const cmp = (!isNaN(aNum) && !isNaN(bNum))
            ? aNum - bNum
            : aVal.localeCompare(bVal, 'fr', { sensitivity: 'base' });
          return asc ? cmp : -cmp;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      }

      th.addEventListener('click', doSort);
      th.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doSort(); }
      });
    });
  });
});

// ---------------------------------------------------------------------------
// Client-side table filter — input[data-table-filter="tableId"] hides
// non-matching <tbody> rows.  Case-insensitive, matches any cell text.
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-table-filter]').forEach(function (input) {
    var table = document.getElementById(input.dataset.tableFilter);
    if (!table) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    input.addEventListener('input', function () {
      var q = input.value.toLowerCase().trim();
      tbody.querySelectorAll('tr').forEach(function (row) {
        row.style.display = (!q || row.textContent.toLowerCase().indexOf(q) !== -1) ? '' : 'none';
      });
    });
  });
});

// ---------------------------------------------------------------------------
// Extra dates widget — [data-extra-dates-widget]
// Manages a pick-list of non-consecutive event dates (YYYY-MM-DD).
// Initial values are read from data-existing-dates (JSON array).
// Each selected date generates a hidden extra_dates[] input on submit.
// ---------------------------------------------------------------------------
(function () {
  var widget = document.querySelector('[data-extra-dates-widget]');
  if (!widget) return;

  var dateInput = widget.querySelector('[data-date-input]');
  var addBtn = widget.querySelector('[data-add-date]');
  var tagList = widget.querySelector('[data-date-tags]');
  var hiddenContainer = widget.querySelector('[data-date-hidden]');
  if (!dateInput || !addBtn || !tagList || !hiddenContainer) return;

  var dates = [];
  try { dates = JSON.parse(widget.dataset.existingDates || '[]'); } catch (_) {}

  function fmtDate(iso) {
    var p = iso.split('-');
    return p[2] + '/' + p[1] + '/' + p[0];
  }

  function render() {
    dates = dates.slice().sort();
    tagList.innerHTML = '';
    hiddenContainer.innerHTML = '';
    dates.forEach(function (d) {
      var badge = document.createElement('span');
      badge.className = 'badge bg-secondary d-inline-flex align-items-center gap-1 me-1 mb-1';

      var label = document.createElement('span');
      label.textContent = fmtDate(d);

      var removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'btn-close btn-close-white';
      removeBtn.style.fontSize = '.5rem';
      removeBtn.setAttribute('aria-label', 'Retirer ' + fmtDate(d));
      removeBtn.addEventListener('click', function () {
        dates = dates.filter(function (x) { return x !== d; });
        render();
      });

      badge.appendChild(label);
      badge.appendChild(removeBtn);
      tagList.appendChild(badge);

      var hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = 'extra_dates[]';
      hidden.value = d;
      hiddenContainer.appendChild(hidden);
    });
  }

  function addDate() {
    var val = dateInput.value;
    if (!val || dates.indexOf(val) !== -1) return;
    dates.push(val);
    render();
    dateInput.value = '';
    dateInput.focus();
  }

  addBtn.addEventListener('click', addDate);
  dateInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); addDate(); }
  });

  render();
}());

// ---------------------------------------------------------------------------
// Member form — role select toggles the permissions section
// Triggered by [data-permission-toggle] on the <select>
// ---------------------------------------------------------------------------
(function () {
  var roleSelect = document.querySelector('[data-permission-toggle]');
  if (!roleSelect) return;
  var section = document.getElementById('permissions-section');
  if (!section) return;
  var checkboxes = section.querySelectorAll('input[type="checkbox"]');
  var memberExcluded = ['treasury', 'members', 'mailing'];

  roleSelect.addEventListener('change', function () {
    if (roleSelect.value === 'bureau') {
      section.classList.add('d-none');
      checkboxes.forEach(function (cb) { cb.checked = true; });
    } else {
      section.classList.remove('d-none');
      checkboxes.forEach(function (cb) {
        cb.checked = memberExcluded.indexOf(cb.value) === -1;
      });
    }
  });
}());

// ---------------------------------------------------------------------------
// Web Push — subscribe to push notifications
// Shows a bell icon in the navbar for logged-in users who haven't subscribed.
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
  // Only for authenticated users (logout link present in navbar)
  if (!document.querySelector('a[href*="logout"]')) return;
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

  var PUSH_BTN_ID = 'push-notify-btn';

  function urlB64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    var b64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(b64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function csrfToken() {
    var m = document.querySelector("meta[name='csrf-token']");
    return m ? m.content : '';
  }

  function injectBellButton(onClick) {
    if (document.getElementById(PUSH_BTN_ID)) return;
    var li = document.createElement('li');
    li.className = 'nav-item';
    li.innerHTML =
      '<button id="' + PUSH_BTN_ID + '" type="button"' +
      ' class="nav-link btn btn-link text-white border-0 px-2"' +
      ' title="Activer les notifications push" aria-label="Activer les notifications">' +
      '<i class="bi bi-bell" aria-hidden="true"></i></button>';
    var logoutAnchor = document.querySelector('a[href*="logout"]');
    if (logoutAnchor && logoutAnchor.closest('li')) {
      logoutAnchor.closest('li').before(li);
    }
    document.getElementById(PUSH_BTN_ID).addEventListener('click', onClick);
  }

  async function doSubscribe() {
    var btn = document.getElementById(PUSH_BTN_ID);
    if (btn) btn.disabled = true;
    try {
      var reg = await navigator.serviceWorker.ready;
      var resp = await fetch('/push/vapid-key');
      if (!resp.ok) throw new Error('vapid-key unavailable');
      var key = (await resp.text()).trim();
      if (!key) throw new Error('vapid-key empty');
      var sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(key)
      });
      await fetch('/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(sub.toJSON())
      });
      if (btn) {
        btn.innerHTML = '<i class="bi bi-bell-fill text-warning" aria-hidden="true"></i>';
        btn.title = 'Notifications activées';
        btn.disabled = true;
        btn.setAttribute('aria-label', 'Notifications activées');
      }
    } catch (err) {
      if (btn) btn.disabled = false;
      console.warn('[push] subscribe failed:', err);
    }
  }

  navigator.serviceWorker.ready.then(function (reg) {
    reg.pushManager.getSubscription().then(function (existing) {
      if (existing) return;
      if (typeof Notification !== 'undefined' && Notification.permission === 'denied') return;
      injectBellButton(doSubscribe);
    });
  });
});

// ---------------------------------------------------------------------------
// Email iframe — auto-resize to content height (no internal scrollbar)
// ---------------------------------------------------------------------------
document.querySelectorAll('iframe[data-autoresize]').forEach(function (frame) {
  frame.addEventListener('load', function () {
    try {
      var h = frame.contentDocument.body.scrollHeight;
      frame.style.height = (h + 32) + 'px';
    } catch (e) {
      // cross-origin fallback: keep the CSS default height
    }
  });
});

// ---------------------------------------------------------------------------
// Live table — [data-live-table] wraps a single table and coordinates
// live search, click-to-sort and client-side pagination on the same rows.
//   [data-live-search]      — text input (filters as you type)
//   th[data-sort]           — sortable header (uses cell [data-sort-value] or text)
//   [data-live-pagination]  — <ul.pagination> filled with page buttons
//   [data-live-info]        — element showing "x–y sur N"
//   data-page-size          — rows per page (default 50)
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-live-table]').forEach(function (root) {
    var table = root.querySelector('table');
    var tbody = table && table.querySelector('tbody');
    if (!tbody) return;

    var searchInput = root.querySelector('[data-live-search]');
    var pager = root.querySelector('[data-live-pagination]');
    var info = root.querySelector('[data-live-info]');
    var pageSize = parseInt(root.dataset.pageSize, 10) || 50;
    var allRows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
    var headers = Array.prototype.slice.call(table.querySelectorAll('th[data-sort]'));

    // Indices of columns that participate in search (those without data-no-search).
    var allTh = Array.prototype.slice.call(table.querySelectorAll('thead th'));
    var searchCols = allTh.reduce(function (acc, th, i) {
      if (!th.hasAttribute('data-no-search')) acc.push(i);
      return acc;
    }, []);

    var state = { q: '', sortIdx: null, asc: true, page: 1 };

    function cellVal(row, idx) {
      var c = row.cells[idx];
      if (!c) return '';
      return (c.dataset.sortValue || c.textContent).trim();
    }

    function getFiltered() {
      if (!state.q) return allRows;
      var q = state.q;
      return allRows.filter(function (r) {
        return searchCols.some(function (i) {
          var c = r.cells[i];
          return c && c.textContent.toLowerCase().indexOf(q) !== -1;
        });
      });
    }

    function getSorted(rows) {
      if (state.sortIdx === null) return rows;
      var idx = state.sortIdx, asc = state.asc;
      return rows.slice().sort(function (a, b) {
        var av = cellVal(a, idx), bv = cellVal(b, idx);
        var an = parseFloat(av.replace(/\s/g, '').replace(',', '.'));
        var bn = parseFloat(bv.replace(/\s/g, '').replace(',', '.'));
        var cmp = (!isNaN(an) && !isNaN(bn))
          ? an - bn
          : av.localeCompare(bv, 'fr', { sensitivity: 'base' });
        return asc ? cmp : -cmp;
      });
    }

    function renderPager(pages) {
      if (!pager) return;
      pager.innerHTML = '';
      if (pages <= 1) return;

      function addBtn(label, target, opts) {
        opts = opts || {};
        var li = document.createElement('li');
        li.className = 'page-item' + (opts.disabled ? ' disabled' : '') + (opts.active ? ' active' : '');
        var a = document.createElement(opts.disabled ? 'span' : 'a');
        a.className = 'page-link';
        a.innerHTML = label;
        if (!opts.disabled) {
          a.href = '#';
          a.addEventListener('click', function (e) {
            e.preventDefault();
            state.page = target;
            render();
          });
        }
        li.appendChild(a);
        pager.appendChild(li);
      }

      addBtn('&laquo;', state.page - 1, { disabled: state.page <= 1 });
      // Window of page numbers around the current page.
      var start = Math.max(1, state.page - 2);
      var end = Math.min(pages, state.page + 2);
      if (start > 1) addBtn('1', 1, {});
      if (start > 2) addBtn('&hellip;', 0, { disabled: true });
      for (var p = start; p <= end; p++) addBtn(String(p), p, { active: p === state.page });
      if (end < pages - 1) addBtn('&hellip;', 0, { disabled: true });
      if (end < pages) addBtn(String(pages), pages, {});
      addBtn('&raquo;', state.page + 1, { disabled: state.page >= pages });
    }

    function render() {
      var rows = getSorted(getFiltered());
      var total = rows.length;
      var pages = Math.max(1, Math.ceil(total / pageSize));
      if (state.page > pages) state.page = pages;
      if (state.page < 1) state.page = 1;
      var start = (state.page - 1) * pageSize;
      var end = start + pageSize;

      allRows.forEach(function (r) { r.style.display = 'none'; });
      // Re-append in sorted order so the DOM reflects the current sort.
      rows.forEach(function (r) { tbody.appendChild(r); });
      rows.slice(start, end).forEach(function (r) { r.style.display = ''; });

      if (info) {
        info.textContent = total === 0
          ? 'Aucun résultat'
          : (start + 1) + '–' + Math.min(end, total) + ' sur ' + total;
      }
      renderPager(pages);
    }

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        state.q = searchInput.value.toLowerCase().trim();
        state.page = 1;
        render();
      });
    }

    headers.forEach(function (th, idx) {
      function doSort() {
        state.asc = (state.sortIdx === idx) ? !state.asc : true;
        state.sortIdx = idx;
        state.page = 1;
        headers.forEach(function (h) {
          h.removeAttribute('aria-sort');
          var ic = h.querySelector('.sort-icon');
          if (ic) ic.remove();
        });
        th.setAttribute('aria-sort', state.asc ? 'ascending' : 'descending');
        var icon = document.createElement('i');
        icon.className = 'bi bi-caret-' + (state.asc ? 'up' : 'down') + '-fill small ms-1 sort-icon';
        icon.setAttribute('aria-hidden', 'true');
        th.appendChild(icon);
        render();
      }
      th.style.cursor = 'pointer';
      th.addEventListener('click', doSort);
      th.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doSort(); }
      });
    });

    render();
  });
});
