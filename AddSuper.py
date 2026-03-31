from app import app, db, User
from werkzeug.security import generate_password_hash
with app.app_context():
    admin = User(
        username='新管理员用户名',
        email='邮箱@example.com',
        password_hash=generate_password_hash('密码'),
        is_admin=True
    )
    db.session.add(admin)
    db.session.commit()