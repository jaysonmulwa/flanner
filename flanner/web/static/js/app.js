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
