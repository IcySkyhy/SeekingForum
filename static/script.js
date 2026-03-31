// Forum – 轻量交互脚本

document.addEventListener('DOMContentLoaded', () => {
    // 自动隐藏 flash 消息
    document.querySelectorAll('.flash').forEach(el => {
        setTimeout(() => {
            el.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
            el.style.opacity = '0';
            el.style.transform = 'translateY(-8px)';
            setTimeout(() => el.remove(), 400);
        }, 4000);
    });

    // 文本框自动增高
    document.querySelectorAll('textarea').forEach(textarea => {
        textarea.addEventListener('input', function () {
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        });
    });

    // 表单提交防重复（排除含验证码按钮的表单，由验证码逻辑处理）
    document.querySelectorAll('form').forEach(form => {
        if (form.querySelector('.btn-send-code')) return;
        form.addEventListener('submit', function () {
            const btn = this.querySelector('button[type="submit"]');
            if (btn) {
                btn.disabled = true;
                btn.style.opacity = '0.6';
                setTimeout(() => { btn.disabled = false; btn.style.opacity = '1'; }, 3000);
            }
        });
    });

    // ── 登录页 Tab 切换 ──────────────────────────────
    document.querySelectorAll('.auth-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.target;
            // 切换 tab 激活
            document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            // 切换面板
            document.querySelectorAll('.auth-panel').forEach(p => p.classList.remove('active'));
            const panel = document.getElementById(target);
            if (panel) panel.classList.add('active');
        });
    });

    // ── 发送验证码 ───────────────────────────────────
    document.querySelectorAll('.btn-send-code').forEach(btn => {
        btn.addEventListener('click', () => handleSendCode(btn));
    });
});

function handleSendCode(btn) {
    const codeType = btn.dataset.type; // 'register' or 'login'
    // 找到同一 form 或页面中的 email 输入框
    const form = btn.closest('form') || btn.closest('.auth-card');
    const emailInput = form.querySelector('input[type="email"]');
    const email = emailInput ? emailInput.value.trim().toLowerCase() : '';

    if (!email) {
        showToast('请先输入邮箱');
        if (emailInput) emailInput.focus();
        return;
    }
    if (!email.endsWith('@mails.tsinghua.edu.cn')) {
        showToast('仅支持 @mails.tsinghua.edu.cn 邮箱');
        return;
    }

    btn.disabled = true;
    btn.textContent = '发送中...';

    fetch('/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, type: codeType }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.ok) {
            showToast('验证码已发送至邮箱');
            startCountdown(btn, 60);
        } else {
            showToast(data.msg || '发送失败');
            btn.disabled = false;
            btn.textContent = '发送验证码';
        }
    })
    .catch(() => {
        showToast('网络错误，请重试');
        btn.disabled = false;
        btn.textContent = '发送验证码';
    });
}

function startCountdown(btn, seconds) {
    let remaining = seconds;
    btn.textContent = `${remaining}s`;
    btn.disabled = true;
    const timer = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
            clearInterval(timer);
            btn.textContent = '发送验证码';
            btn.disabled = false;
        } else {
            btn.textContent = `${remaining}s`;
        }
    }, 1000);
}

function showToast(msg) {
    // 复用 flash 样式
    let existing = document.querySelector('.js-toast');
    if (existing) existing.remove();
    const div = document.createElement('div');
    div.className = 'flash flash-error js-toast';
    div.innerHTML = `<span>${msg}</span><button class="flash-close" onclick="this.parentElement.remove()">&times;</button>`;
    const container = document.querySelector('.container') || document.body;
    container.prepend(div);
    setTimeout(() => {
        div.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
        div.style.opacity = '0';
        div.style.transform = 'translateY(-8px)';
        setTimeout(() => div.remove(), 400);
    }, 3000);
}
