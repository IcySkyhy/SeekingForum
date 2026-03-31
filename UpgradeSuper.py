# 在项目目录下运行 python，然后执行：
from app import app, db, User
with app.app_context():
    user = User.query.filter_by(username='用户名').first()
    user.is_admin = True
    db.session.commit()