import os

from sqlalchemy.orm import Session

from ..models import Setting


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        return row.value
    return os.environ.get(key.upper(), default)


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def delete_setting(db: Session, key: str) -> None:
    db.query(Setting).filter(Setting.key == key).delete()
    db.commit()
