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

    // 表单提交防重复
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', function () {
            const btn = this.querySelector('button[type="submit"]');
            if (btn) {
                btn.disabled = true;
                btn.style.opacity = '0.6';
                setTimeout(() => {
                    btn.disabled = false;
                    btn.style.opacity = '1';
                }, 3000);
            }
        });
    });
});
