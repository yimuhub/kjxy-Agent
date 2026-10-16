"""认证模块数据访问层。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.auth.models import User


def get_by_username(db: Session, username: str) -> User | None:
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def get_by_id(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    email: str | None = None,
    display_name: str = "",
    status: str = "active",
) -> User:
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        status=status,
    )
    db.add(user)
    db.flush()
    return user
