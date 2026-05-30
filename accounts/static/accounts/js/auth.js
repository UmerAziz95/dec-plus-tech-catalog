document.addEventListener('DOMContentLoaded', () => {
    // ---- Password Visibility Toggle ----
    document.querySelectorAll('.toggle-password').forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.dataset.target;
            const input = document.getElementById(targetId);
            if (!input) return;

            const isPassword = input.type === 'password';
            input.type = isPassword ? 'text' : 'password';

            const eyeOpen = btn.querySelector('.eye-open');
            const eyeClosed = btn.querySelector('.eye-closed');
            if (eyeOpen && eyeClosed) {
                eyeOpen.style.display = isPassword ? 'none' : 'block';
                eyeClosed.style.display = isPassword ? 'block' : 'none';
            }
        });
    });

    // ---- Password Match Indicator (Signup & Reset) ----
    const p1 = document.getElementById('signup-password1') || document.getElementById('reset-password1');
    const p2 = document.getElementById('signup-password2') || document.getElementById('reset-password2');
    const indicator = document.getElementById('password-match');

    if (p1 && p2 && indicator) {
        const check = () => {
            const v1 = p1.value;
            const v2 = p2.value;

            if (!v2) {
                indicator.style.display = 'none';
                return;
            }

            indicator.style.display = 'flex';
            const icon = indicator.querySelector('.match-icon');
            const text = indicator.querySelector('.match-text');

            if (v1 === v2) {
                indicator.className = 'password-match-indicator match';
                icon.textContent = '✓';
                text.textContent = 'Passwords match';
            } else {
                indicator.className = 'password-match-indicator no-match';
                icon.textContent = '✕';
                text.textContent = 'Passwords do not match';
            }
        };

        p1.addEventListener('input', check);
        p2.addEventListener('input', check);
    }

    // ---- Form Submit Loading State ----
    document.querySelectorAll('.auth-form').forEach(form => {
        form.addEventListener('submit', () => {
            const btn = form.querySelector('.btn-primary');
            if (!btn) return;

            const btnText = btn.querySelector('.btn-text');
            const btnLoader = btn.querySelector('.btn-loader');

            if (btnText) btnText.style.display = 'none';
            if (btnLoader) btnLoader.style.display = 'inline-flex';
            btn.disabled = true;
            btn.style.opacity = '0.7';
        });
    });
});
