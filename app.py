import os
import re
import json
import random
import smtplib
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# ── 初始化 ──────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'qiusuo-forum-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///forum.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录'

# ── 时区过滤器 (UTC → 北京时间 UTC+8) ──────────────────
BEIJING_TZ = timezone(timedelta(hours=8))

@app.template_filter('bjtime')
def beijing_time(dt, fmt='%Y-%m-%d %H:%M'):
    """将 UTC naive datetime 转换为北京时间并格式化"""
    if dt is None:
        return ''
    utc_dt = dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(BEIJING_TZ).strftime(fmt)

# ── SiliconFlow API ─────────────────────────────────────
SILICONFLOW_API_KEY = os.environ.get('SILICONFLOW_API_KEY', '')
SILICONFLOW_URL = 'https://api.siliconflow.cn/v1/chat/completions'
SILICONFLOW_MODEL = 'Qwen/Qwen2.5-7B-Instruct'

# ── 邮件配置 ────────────────────────────────────────────
MAIL_SENDER = 'marmalade_service@outlook.com'
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
MAIL_SMTP_SERVER = 'smtp-mail.outlook.com'
MAIL_SMTP_PORT = 587
ALLOWED_EMAIL_SUFFIX = '@mails.tsinghua.edu.cn'

# 验证码内存缓存: {email: {'code': '123456', 'expires': datetime, 'type': 'register'|'login'}}
verification_codes = {}

# ── Models ──────────────────────────────────────────────
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    posts = db.relationship('Post', backref='author', lazy=True)
    comments = db.relationship('Comment', backref='author', lazy=True)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='approved')  # approved / pending / rejected
    reject_reason = db.Column(db.String(500), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    comments = db.relationship('Comment', backref='post', lazy=True, cascade='all, delete-orphan')

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='approved')
    reject_reason = db.Column(db.String(500), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── Admin decorator ─────────────────────────────────────
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ── AI Moderation ───────────────────────────────────────
def ai_moderate(text):
    """Call SiliconFlow API to moderate content.
    Returns (is_ok: bool, reason: str).
    If API unavailable, default to approved.
    """
    if not SILICONFLOW_API_KEY:
        return True, ''
    prompt = f"""你是一个论坛内容审核员。这个论坛是一个马克思主义理论学习与交流平台。
请判断以下内容是否合规。
合规标准：允许正常的学术讨论，包括马克思主义理论探讨。
不合规标准：人身攻击、脏话辱骂、色情内容、暴力内容、广告垃圾信息。
请用JSON格式回复：{{"ok": true/false, "reason": "如果不合规，简述原因"}}
只回复JSON，不要其他内容。

待审核内容：
{text[:1000]}"""
    try:
        payload = json.dumps({
            'model': SILICONFLOW_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 150,
            'temperature': 0.1,
        }).encode('utf-8')
        req = urllib.request.Request(SILICONFLOW_URL, data=payload, headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {SILICONFLOW_API_KEY}',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        reply = data['choices'][0]['message']['content'].strip()
        # Extract JSON from reply
        m = re.search(r'\{.*\}', reply, re.DOTALL)
        if m:
            result = json.loads(m.group())
            return result.get('ok', True), result.get('reason', '')
        return True, ''
    except Exception:
        return True, ''  # Default to approved if API fails

# ── 邮件验证码 ──────────────────────────────────────────
def generate_code():
    return ''.join([str(random.randint(0, 9)) for _ in range(6)])

def send_verification_email(to_email, code, purpose='注册'):
    """通过 Outlook SMTP 发送验证码邮件"""
    subject = f'求索论坛 — {purpose}验证码'
    html = f"""
    <div style="max-width:420px;margin:40px auto;font-family:sans-serif;">
        <h2 style="color:#c0392b;margin-bottom:4px;">求索论坛</h2>
        <p style="color:#888;font-size:14px;">SEEKING · 邮箱验证</p>
        <hr style="border:none;border-top:2px solid #e74c3c;margin:20px 0;">
        <p>你正在进行<strong>{purpose}</strong>操作，验证码为：</p>
        <div style="font-size:32px;font-weight:700;letter-spacing:8px;color:#c0392b;
                    background:#fdecea;padding:16px 24px;border-radius:6px;text-align:center;margin:20px 0;">
            {code}
        </div>
        <p style="color:#888;font-size:13px;">验证码 5 分钟内有效，请勿泄露给他人。</p>
    </div>"""
    msg = MIMEMultipart('alternative')
    msg['From'] = MAIL_SENDER
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        with smtplib.SMTP(MAIL_SMTP_SERVER, MAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(MAIL_SENDER, MAIL_PASSWORD)
            server.sendmail(MAIL_SENDER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f'[mail error] {e}')
        return False

def store_code(email, code, code_type):
    verification_codes[email] = {
        'code': code,
        'expires': datetime.utcnow() + timedelta(minutes=5),
        'type': code_type,
    }

def verify_code(email, code, code_type):
    entry = verification_codes.get(email)
    if not entry:
        return False
    if entry['type'] != code_type:
        return False
    if datetime.utcnow() > entry['expires']:
        verification_codes.pop(email, None)
        return False
    if entry['code'] != code:
        return False
    verification_codes.pop(email, None)
    return True

# ── Routes: Auth ────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        code = request.form.get('code', '').strip()
        if not email or not password or not code:
            flash('请填写所有字段', 'error')
            return redirect(url_for('register'))
        if not email.endswith(ALLOWED_EMAIL_SUFFIX):
            flash('仅支持 @mails.tsinghua.edu.cn 邮箱注册', 'error')
            return redirect(url_for('register'))
        if len(password) < 6:
            flash('密码至少6个字符', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('该邮箱已注册', 'error')
            return redirect(url_for('register'))
        if not verify_code(email, code, 'register'):
            flash('验证码错误或已过期', 'error')
            return redirect(url_for('register'))
        username = email.split('@')[0]
        # 确保用户名唯一（极端情况）
        if User.query.filter_by(username=username).first():
            username = username + str(random.randint(10, 99))
        user = User(username=username, email=email,
                     password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('注册成功，欢迎加入求索论坛！', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        code = request.form.get('code', '').strip()
        login_mode = request.form.get('login_mode', 'password')  # password or code

        if not email:
            flash('请输入邮箱', 'error')
            return redirect(url_for('login'))

        user = User.query.filter_by(email=email).first()

        if login_mode == 'code':
            # 验证码登录
            if not code:
                flash('请输入验证码', 'error')
                return redirect(url_for('login'))
            if not user:
                flash('该邮箱未注册', 'error')
                return redirect(url_for('login'))
            if not verify_code(email, code, 'login'):
                flash('验证码错误或已过期', 'error')
                return redirect(url_for('login'))
        else:
            # 密码登录
            if not password:
                flash('请输入密码', 'error')
                return redirect(url_for('login'))
            if not user or not check_password_hash(user.password_hash, password):
                flash('邮箱或密码错误', 'error')
                return redirect(url_for('login'))

        if user.is_banned:
            flash('该账号已被封禁', 'error')
            return redirect(url_for('login'))

        login_user(user)
        flash('登录成功', 'success')
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已退出登录', 'success')
    return redirect(url_for('index'))

@app.route('/send-code', methods=['POST'])
def send_code():
    """AJAX 端点：发送邮箱验证码"""
    data = request.get_json(silent=True) or {}
    email = data.get('email', '').strip().lower()
    code_type = data.get('type', 'register')  # register or login

    if not email:
        return jsonify({'ok': False, 'msg': '请输入邮箱'}), 400

    if not email.endswith(ALLOWED_EMAIL_SUFFIX):
        return jsonify({'ok': False, 'msg': '仅支持 @mails.tsinghua.edu.cn 邮箱'}), 400

    # 防止频繁发送
    existing = verification_codes.get(email)
    if existing and (existing['expires'] - timedelta(minutes=4)) > datetime.utcnow():
        return jsonify({'ok': False, 'msg': '验证码已发送，请稍后再试'}), 429

    if code_type == 'register' and User.query.filter_by(email=email).first():
        return jsonify({'ok': False, 'msg': '该邮箱已注册'}), 400

    if code_type == 'login' and not User.query.filter_by(email=email).first():
        return jsonify({'ok': False, 'msg': '该邮箱未注册'}), 400

    code = generate_code()
    purpose = '注册' if code_type == 'register' else '登录'
    if send_verification_email(email, code, purpose):
        store_code(email, code, code_type)
        return jsonify({'ok': True, 'msg': '验证码已发送'})
    else:
        return jsonify({'ok': False, 'msg': '邮件发送失败，请稍后重试'}), 500

# ── Routes: Forum ───────────────────────────────────────
@app.route('/')
def index():
    posts = Post.query.filter_by(status='approved').order_by(Post.created_at.desc()).all()
    return render_template('index.html', posts=posts)

@app.route('/post/<int:post_id>')
def view_post(post_id):
    post = Post.query.get_or_404(post_id)
    # Non-admin can only see approved posts, or their own posts
    if post.status != 'approved':
        if not current_user.is_authenticated:
            abort(404)
        if not current_user.is_admin and post.user_id != current_user.id:
            abort(404)
    comments = Comment.query.filter_by(post_id=post_id, status='approved').order_by(Comment.created_at.asc()).all()
    return render_template('post.html', post=post, comments=comments)

@app.route('/new', methods=['GET', 'POST'])
@login_required
def new_post():
    if current_user.is_banned:
        flash('你的账号已被封禁，无法发帖', 'error')
        return redirect(url_for('index'))
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        if not title or not content:
            flash('标题和内容不能为空', 'error')
            return redirect(url_for('new_post'))
        # AI moderation
        is_ok, reason = ai_moderate(title + '\n' + content)
        status = 'approved' if is_ok else 'pending'
        reject_reason = reason if not is_ok else ''
        post = Post(title=title, content=content, user_id=current_user.id,
                     status=status, reject_reason=reject_reason)
        db.session.add(post)
        db.session.commit()
        if status == 'approved':
            flash('帖子发布成功！', 'success')
            return redirect(url_for('view_post', post_id=post.id))
        else:
            flash('帖子已提交，AI审核认为需要人工复审，请等待管理员审核。', 'error')
            return redirect(url_for('index'))
    return render_template('new_post.html')

@app.route('/post/<int:post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    post = Post.query.get_or_404(post_id)
    if current_user.is_banned:
        flash('你的账号已被封禁，无法回复', 'error')
        return redirect(url_for('view_post', post_id=post_id))
    content = request.form.get('content', '').strip()
    if not content:
        flash('回复内容不能为空', 'error')
        return redirect(url_for('view_post', post_id=post_id))
    is_ok, reason = ai_moderate(content)
    status = 'approved' if is_ok else 'pending'
    reject_reason = reason if not is_ok else ''
    comment = Comment(content=content, user_id=current_user.id, post_id=post_id,
                       status=status, reject_reason=reject_reason)
    db.session.add(comment)
    db.session.commit()
    if status == 'approved':
        flash('回复成功', 'success')
    else:
        flash('回复已提交，AI审核认为需要人工复审，请等待管理员审核。', 'error')
    return redirect(url_for('view_post', post_id=post_id))

# ── Routes: Admin ───────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_dashboard():
    stats = {
        'users': User.query.count(),
        'posts': Post.query.count(),
        'comments': Comment.query.count(),
        'pending_posts': Post.query.filter_by(status='pending').count(),
        'pending_comments': Comment.query.filter_by(status='pending').count(),
        'rejected_posts': Post.query.filter_by(status='rejected').count(),
        'rejected_comments': Comment.query.filter_by(status='rejected').count(),
    }
    return render_template('admin/dashboard.html', stats=stats)

@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/<int:user_id>/ban', methods=['POST'])
@admin_required
def admin_ban_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('不能封禁自己', 'error')
        return redirect(url_for('admin_users'))
    user.is_banned = True
    db.session.commit()
    flash(f'已封禁用户 {user.username}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/unban', methods=['POST'])
@admin_required
def admin_unban_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_banned = False
    db.session.commit()
    flash(f'已解封用户 {user.username}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('不能删除自己', 'error')
        return redirect(url_for('admin_users'))
    db.session.delete(user)
    db.session.commit()
    flash(f'已删除用户 {user.username}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/posts')
@admin_required
def admin_posts():
    status_filter = request.args.get('status', 'all')
    if status_filter == 'all':
        posts = Post.query.order_by(Post.created_at.desc()).all()
    else:
        posts = Post.query.filter_by(status=status_filter).order_by(Post.created_at.desc()).all()
    return render_template('admin/posts.html', posts=posts, current_filter=status_filter)

@app.route('/admin/posts/<int:post_id>/approve', methods=['POST'])
@admin_required
def admin_approve_post(post_id):
    post = Post.query.get_or_404(post_id)
    post.status = 'approved'
    post.reject_reason = ''
    db.session.commit()
    flash('帖子已通过审核', 'success')
    referrer = request.form.get('referrer', '')
    if referrer:
        return redirect(referrer)
    return redirect(url_for('admin_posts'))

@app.route('/admin/posts/<int:post_id>/reject', methods=['POST'])
@admin_required
def admin_reject_post(post_id):
    post = Post.query.get_or_404(post_id)
    reason = request.form.get('reason', '管理员拒绝').strip()
    post.status = 'rejected'
    post.reject_reason = reason
    db.session.commit()
    flash('帖子已拒绝', 'success')
    referrer = request.form.get('referrer', '')
    if referrer:
        return redirect(referrer)
    return redirect(url_for('admin_posts'))

@app.route('/admin/posts/<int:post_id>/delete', methods=['POST'])
@admin_required
def admin_delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    db.session.delete(post)
    db.session.commit()
    flash('帖子已删除', 'success')
    referrer = request.form.get('referrer', '')
    if referrer:
        return redirect(referrer)
    return redirect(url_for('admin_posts'))

@app.route('/admin/comments')
@admin_required
def admin_comments():
    status_filter = request.args.get('status', 'all')
    if status_filter == 'all':
        comments = Comment.query.order_by(Comment.created_at.desc()).all()
    else:
        comments = Comment.query.filter_by(status=status_filter).order_by(Comment.created_at.desc()).all()
    return render_template('admin/comments.html', comments=comments, current_filter=status_filter)

@app.route('/admin/comments/<int:comment_id>/approve', methods=['POST'])
@admin_required
def admin_approve_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    comment.status = 'approved'
    comment.reject_reason = ''
    db.session.commit()
    flash('评论已通过审核', 'success')
    return redirect(url_for('admin_comments'))

@app.route('/admin/comments/<int:comment_id>/reject', methods=['POST'])
@admin_required
def admin_reject_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    reason = request.form.get('reason', '管理员拒绝').strip()
    comment.status = 'rejected'
    comment.reject_reason = reason
    db.session.commit()
    flash('评论已拒绝', 'success')
    return redirect(url_for('admin_comments'))

@app.route('/admin/comments/<int:comment_id>/delete', methods=['POST'])
@admin_required
def admin_delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    post_id = comment.post_id
    db.session.delete(comment)
    db.session.commit()
    flash('评论已删除', 'success')
    referrer = request.form.get('referrer', '')
    if referrer:
        return redirect(referrer)
    return redirect(url_for('admin_comments'))

# ── Init DB ─────────────────────────────────────────────
def init_db():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@mails.tsinghua.edu.cn',
            password_hash=generate_password_hash('admin123'),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        print('  [init] Created default admin: admin@mails.tsinghua.edu.cn / admin123')

if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, port=6321)
