// MCP Plan File Manager - JavaScript

// Auto-hide alerts after 5 seconds
document.addEventListener('DOMContentLoaded', function() {
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.3s ease-out';
            alert.style.opacity = '0';
            setTimeout(() => {
                alert.remove();
            }, 300);
        }, 5000);
    });

    // Add close button to alerts
    alerts.forEach(alert => {
        const closeBtn = document.createElement('button');
        closeBtn.innerHTML = '×';
        closeBtn.style.cssText = `
            margin-left: auto;
            background: none;
            border: none;
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0;
            width: 1.5rem;
            height: 1.5rem;
            display: flex;
            align-items: center;
            justify-content: center;
        `;
        closeBtn.onclick = () => {
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        };
        alert.appendChild(closeBtn);
    });
});

// Textarea auto-resize
document.addEventListener('DOMContentLoaded', function() {
    const textareas = document.querySelectorAll('textarea.form-control');
    textareas.forEach(textarea => {
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = (this.scrollHeight) + 'px';
        });
    });
});

// Markdown editor tab support
document.addEventListener('DOMContentLoaded', function() {
    const editors = document.querySelectorAll('.markdown-editor');
    editors.forEach(editor => {
        editor.addEventListener('keydown', function(e) {
            if (e.key === 'Tab') {
                e.preventDefault();
                const start = this.selectionStart;
                const end = this.selectionEnd;
                this.value = this.value.substring(0, start) + '    ' + this.value.substring(end);
                this.selectionStart = this.selectionEnd = start + 4;
            }
        });
    });
});

// Reading settings popover (plan viewer). Presentation only: sets data-* on
// <html>, which drives CSS variables, and persists to localStorage.
document.addEventListener('DOMContentLoaded', function () {
    const toggle = document.getElementById('reading-toggle');
    const panel = document.getElementById('reading-panel');
    if (!toggle || !panel) return;

    const KEY = 'flanner.reading';
    const DEFAULTS = { preset: 'default', font: 'serif', size: 'm', measure: 'comfortable' };

    function load() {
        try {
            return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(KEY) || '{}'));
        } catch (e) {
            return Object.assign({}, DEFAULTS);
        }
    }

    function apply(state) {
        const d = document.documentElement;
        d.dataset.readingPreset = state.preset;
        d.dataset.readingFont = state.font;
        d.dataset.readingSize = state.size;
        d.dataset.readingMeasure = state.measure;
        panel.querySelectorAll('.reading-seg').forEach(function (seg) {
            const key = seg.getAttribute('data-reading');
            seg.querySelectorAll('button').forEach(function (b) {
                b.setAttribute('aria-pressed', String(b.getAttribute('data-value') === state[key]));
            });
        });
    }

    let state = load();
    apply(state);

    toggle.addEventListener('click', function () {
        const willOpen = panel.hasAttribute('hidden');
        if (willOpen) { panel.removeAttribute('hidden'); } else { panel.setAttribute('hidden', ''); }
        toggle.setAttribute('aria-expanded', String(willOpen));
    });

    panel.querySelectorAll('.reading-seg button').forEach(function (b) {
        b.addEventListener('click', function () {
            const key = b.parentElement.getAttribute('data-reading');
            state[key] = b.getAttribute('data-value');
            try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
            apply(state);
        });
    });

    document.addEventListener('click', function (e) {
        if (!panel.hasAttribute('hidden') && !e.target.closest('.reading-settings')) {
            panel.setAttribute('hidden', '');
            toggle.setAttribute('aria-expanded', 'false');
        }
    });
});

// Theme toggle: cycles system -> light -> dark, persisted. The no-flash script
// in base.html applies the saved theme before first paint.
document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    const GLYPH = { system: '◐', light: '○', dark: '●' };
    const d = document.documentElement;

    function current() {
        try {
            const t = localStorage.getItem('flanner.theme');
            return (t === 'light' || t === 'dark') ? t : 'system';
        } catch (e) { return 'system'; }
    }
    function render(mode) {
        btn.firstElementChild.textContent = GLYPH[mode];
        btn.setAttribute('aria-label', 'Theme: ' + mode);
        btn.title = 'Theme: ' + mode + ' (click to change)';
    }
    function apply(mode) {
        if (mode === 'system') { delete d.dataset.theme; } else { d.dataset.theme = mode; }
        try {
            if (mode === 'system') localStorage.removeItem('flanner.theme');
            else localStorage.setItem('flanner.theme', mode);
        } catch (e) {}
        render(mode);
    }
    render(current());
    btn.addEventListener('click', function () {
        const order = ['system', 'light', 'dark'];
        apply(order[(order.indexOf(current()) + 1) % 3]);
    });
});

// Command palette (Cmd/Ctrl+K): jump to any project or plan.
document.addEventListener('DOMContentLoaded', function () {
    const dlg = document.getElementById('cmdk');
    const input = document.getElementById('cmdk-input');
    const list = document.getElementById('cmdk-list');
    if (!dlg || !input || !list || typeof dlg.showModal !== 'function') return;

    let index = null;   // cached search index
    let items = [];      // current filtered results
    let active = 0;

    async function loadIndex() {
        if (index) return;
        try { index = await (await fetch('/api/search')).json(); } catch (e) { index = []; }
    }

    function render(query) {
        const q = query.trim().toLowerCase();
        const all = index || [];
        items = (q
            ? all.filter(function (it) {
                return (it.name + ' ' + (it.context || '')).toLowerCase().indexOf(q) !== -1;
            })
            : all
        ).slice(0, 20);
        active = 0;
        list.innerHTML = '';
        items.forEach(function (it, i) {
            const li = document.createElement('li');
            li.className = 'cmdk-item';
            li.id = 'cmdk-opt-' + i;
            li.setAttribute('role', 'option');
            const kind = document.createElement('span');
            kind.className = 'cmdk-kind';
            kind.textContent = it.type;
            const name = document.createElement('span');
            name.className = 'cmdk-name';
            name.textContent = it.name;
            li.append(kind, name);
            if (it.context) {
                const ctx = document.createElement('span');
                ctx.className = 'cmdk-ctx';
                ctx.textContent = it.context;
                li.appendChild(ctx);
            }
            li.addEventListener('click', function () { go(i); });
            list.appendChild(li);
        });
        if (!items.length) {
            const empty = document.createElement('li');
            empty.className = 'cmdk-empty';
            empty.textContent = 'No matches';
            list.appendChild(empty);
        }
        updateActive();
    }

    function updateActive() {
        const els = list.querySelectorAll('.cmdk-item');
        els.forEach(function (li, i) { li.setAttribute('aria-selected', String(i === active)); });
        if (els[active] && els[active].scrollIntoView) els[active].scrollIntoView({ block: 'nearest' });
        input.setAttribute('aria-activedescendant', items[active] ? 'cmdk-opt-' + active : '');
    }

    function go(i) {
        const it = items[i];
        if (it) window.location.href = it.url;
    }

    async function open() {
        await loadIndex();
        input.value = '';
        render('');
        if (!dlg.open) dlg.showModal();
        input.focus();
    }

    document.addEventListener('keydown', function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            open();
        }
    });
    const trigger = document.getElementById('cmdk-open');
    if (trigger) trigger.addEventListener('click', open);
    input.addEventListener('input', function () { render(input.value); });
    input.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, items.length - 1); updateActive(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); updateActive(); }
        else if (e.key === 'Enter') { e.preventDefault(); go(active); }
    });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
});

// Confirmation dialogs
function confirmDelete(message) {
    return confirm(message || 'Are you sure you want to delete this item?');
}

// Copy to clipboard
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Copied to clipboard!', 'success');
    });
}

// Toasts: client-side notifications in a bottom-right, aria-live region.
// Styling and motion live in styles.css (.toast*); this only builds the nodes.
function showNotification(message, type = 'info') {
    let region = document.querySelector('.toast-region');
    if (!region) {
        region = document.createElement('div');
        region.className = 'toast-region';
        region.setAttribute('role', 'status');
        region.setAttribute('aria-live', 'polite');
        document.body.appendChild(region);
    }

    const toast = document.createElement('div');
    toast.className = 'toast toast--' + type;

    const body = document.createElement('div');
    body.className = 'toast__body';
    body.textContent = message;

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast__close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';

    let removed = false;
    function dismiss() {
        if (removed) return;
        removed = true;
        toast.classList.add('is-leaving');
        toast.addEventListener('animationend', () => toast.remove(), { once: true });
        setTimeout(() => toast.remove(), 400);  // fallback if animation is disabled
    }

    close.addEventListener('click', dismiss);
    toast.append(body, close);
    region.appendChild(toast);
    setTimeout(dismiss, 4000);
}

// Handle URL query parameters
document.addEventListener('DOMContentLoaded', function() {
    const params = new URLSearchParams(window.location.search);
    if (params.has('message')) {
        const messageType = params.get('message');
        if (messageType === 'no_changes') {
            showNotification('No changes detected. Content is identical to the current version.', 'info');
        }
    }
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl+S or Cmd+S to save (prevent default and trigger form submit)
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        const form = document.querySelector('form');
        if (form) {
            form.submit();
        }
    }
});

console.log('🚀 MCP Plan File Manager loaded successfully!');
