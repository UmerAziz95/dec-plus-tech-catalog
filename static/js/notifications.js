(function () {
    const TOAST_ICONS = {
        success: '\u2713',
        error: '\u2717',
        info: '\u2139',
        warning: '\u26A0',
    };

    function scheduleToastDismiss(toast, delay) {
        if (!toast) return;
        setTimeout(() => {
            toast.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
            toast.style.opacity = '0';
            toast.style.transform = 'translateY(-10px)';
            setTimeout(() => toast.remove(), 400);
        }, delay || 5000);
    }

    function getToastStack() {
        let stack = document.getElementById('app-toast-stack');
        if (stack) return stack;

        const topbarRight = document.querySelector('.topbar-right');
        if (topbarRight) {
            stack = document.createElement('div');
            stack.id = 'app-toast-stack';
            stack.className = 'topbar-messages';
            stack.setAttribute('aria-live', 'polite');
            stack.setAttribute('aria-atomic', 'false');
            topbarRight.prepend(stack);
            return stack;
        }

        const authContainer = document.querySelector('.auth-container');
        if (authContainer) {
            stack = document.createElement('div');
            stack.id = 'app-toast-stack';
            stack.className = 'messages-wrapper';
            stack.setAttribute('aria-live', 'polite');
            stack.setAttribute('aria-atomic', 'false');
            authContainer.prepend(stack);
            return stack;
        }

        return null;
    }

    function createToastElement(message, type) {
        const kind = ['success', 'error', 'info', 'warning'].includes(type) ? type : 'error';
        const toast = document.createElement('div');
        toast.className = 'toast toast-' + kind;
        toast.setAttribute('role', 'alert');

        const icon = document.createElement('span');
        icon.className = 'toast-icon';
        icon.textContent = TOAST_ICONS[kind];

        const text = document.createElement('span');
        text.className = 'toast-text';
        text.textContent = message;

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'toast-close';
        close.setAttribute('aria-label', 'Close');
        close.textContent = '\u00D7';
        close.addEventListener('click', () => toast.remove());

        toast.append(icon, text, close);
        return toast;
    }

    function bindDismissibleNotice(notice, delay) {
        const close = notice.querySelector('.toast-close, .alert-close');
        if (close && !close.dataset.bound) {
            close.dataset.bound = '1';
            close.addEventListener('click', () => notice.remove());
        }
        if (!notice.classList.contains('form-feedback')) {
            scheduleToastDismiss(notice, delay);
        }
    }

    function showAppToast(message, type, options) {
        const stack = getToastStack();
        if (!stack || !message) return null;
        const opts = options || {};
        const toast = createToastElement(message, type || 'error');
        stack.appendChild(toast);
        if (opts.autoDismiss !== false) {
            scheduleToastDismiss(toast, opts.duration || 5000);
        }
        return toast;
    }

    function showAppFormFeedback(container, message, type) {
        if (!container || !message) return null;
        container.innerHTML = '';
        const toast = createToastElement(message, type || 'error');
        toast.classList.add('form-feedback');
        container.appendChild(toast);
        return toast;
    }

    function clearAppFormFeedback(container) {
        if (container) container.innerHTML = '';
    }

    function showAppConfirm(options) {
        const opts = typeof options === 'string' ? { message: options } : (options || {});
        const message = opts.message || 'Are you sure?';
        const title = opts.title || 'Please confirm';
        const confirmText = opts.confirmText || 'Confirm';
        const cancelText = opts.cancelText || 'Cancel';
        const tone = opts.tone || 'warning';

        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.className = 'app-confirm-overlay';
            overlay.setAttribute('role', 'presentation');

            const card = document.createElement('div');
            card.className = 'app-confirm-card';
            card.setAttribute('role', 'dialog');
            card.setAttribute('aria-modal', 'true');
            card.setAttribute('aria-labelledby', 'app-confirm-title');

            const header = document.createElement('div');
            header.className = 'app-confirm-header';

            const titleEl = document.createElement('h3');
            titleEl.id = 'app-confirm-title';
            titleEl.className = 'app-confirm-title';
            titleEl.textContent = title;

            const body = document.createElement('p');
            body.className = 'app-confirm-message';
            body.textContent = message;

            const footer = document.createElement('div');
            footer.className = 'app-confirm-footer';

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'btn btn-secondary';
            cancelBtn.textContent = cancelText;

            const confirmBtn = document.createElement('button');
            confirmBtn.type = 'button';
            confirmBtn.className = tone === 'danger' ? 'btn btn-danger' : 'btn btn-primary';
            confirmBtn.textContent = confirmText;

            let settled = false;
            function finish(result) {
                if (settled) return;
                settled = true;
                document.removeEventListener('keydown', onKeydown);
                overlay.remove();
                resolve(result);
            }

            function onKeydown(event) {
                if (event.key === 'Escape') finish(false);
            }

            cancelBtn.addEventListener('click', () => finish(false));
            confirmBtn.addEventListener('click', () => finish(true));
            overlay.addEventListener('click', (event) => {
                if (event.target === overlay) finish(false);
            });

            footer.append(cancelBtn, confirmBtn);
            header.appendChild(titleEl);
            card.append(header, body, footer);
            overlay.appendChild(card);
            document.body.appendChild(overlay);
            document.addEventListener('keydown', onKeydown);
            cancelBtn.focus();
        });
    }

    function bindConfirmForms() {
        document.addEventListener('submit', (event) => {
            const form = event.target;
            if (!(form instanceof HTMLFormElement)) return;
            const message = form.dataset.confirm;
            if (!message || form.dataset.confirmed === '1') return;

            event.preventDefault();
            showAppConfirm({
                title: form.dataset.confirmTitle || 'Please confirm',
                message: message,
                confirmText: form.dataset.confirmOk || 'Confirm',
                cancelText: form.dataset.confirmCancel || 'Cancel',
                tone: form.dataset.confirmTone || 'danger',
            }).then((confirmed) => {
                if (!confirmed) return;
                form.dataset.confirmed = '1';
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
            });
        }, true);
    }

    function initNotifications() {
        document.querySelectorAll('.toast:not(.form-feedback), .alert').forEach((notice) => {
            bindDismissibleNotice(notice);
        });

        document.addEventListener('click', (event) => {
            const close = event.target.closest('.toast-close, .alert-close');
            if (!close) return;
            const notice = close.closest('[role="alert"]');
            if (notice) notice.remove();
        });

        bindConfirmForms();

        if (!window.__appAlertReplaced) {
            window.__appAlertReplaced = true;
            window.__nativeAlert = window.alert;
            window.alert = function (message) {
                showAppToast(String(message), 'error', { duration: 8000 });
            };
        }
    }

    window.showAppToast = showAppToast;
    window.showAppFormFeedback = showAppFormFeedback;
    window.clearAppFormFeedback = clearAppFormFeedback;
    window.showAppConfirm = showAppConfirm;
    window.showAppAlert = function (message, type) {
        return showAppToast(message, type || 'error', { duration: 8000 });
    };
    window.scheduleAppToastDismiss = scheduleToastDismiss;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initNotifications);
    } else {
        initNotifications();
    }
})();
