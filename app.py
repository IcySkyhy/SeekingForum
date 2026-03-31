import os
import re
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort
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

# ── Routes: Auth ────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not username or not email or not password:
            flash('请填写所有字段', 'error')
            return redirect(url_for('register'))
        if len(username) < 2 or len(username) > 20:
            flash('用户名长度应为2-20个字符', 'error')
            return redirect(url_for('register'))
        if len(password) < 6:
            flash('密码至少6个字符', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('用户名已被使用', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('邮箱已被使用', 'error')
            return redirect(url_for('register'))
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
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            if user.is_banned:
                flash('该账号已被封禁', 'error')
                return redirect(url_for('login'))
            login_user(user)
            flash('登录成功', 'success')
            return redirect(url_for('index'))
        flash('用户名或密码错误', 'error')
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已退出登录', 'success')
    return redirect(url_for('index'))

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
            email='admin@tms.org',
            password_hash=generate_password_hash('admin123'),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        print('  [init] Created default admin: admin / admin123')

if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, port=5000)
