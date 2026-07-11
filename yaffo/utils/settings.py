from pathlib import Path

from sqlalchemy.orm import Session

from yaffo.db.models import ApplicationSettings

SHARED_DOWNLOAD_DIR_SETTING = "shared_download_dir"


def get_thumbnail_dir(session: Session) -> Path | None:
    setting = session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()
    if setting and setting.value:
        return Path(setting.value)
    return None


def get_shared_download_dir(session: Session) -> Path | None:
    setting = session.query(ApplicationSettings).filter_by(name=SHARED_DOWNLOAD_DIR_SETTING).first()
    if setting and setting.value:
        return Path(setting.value)
    return None


def set_shared_download_dir(session: Session, directory: Path) -> None:
    setting = session.query(ApplicationSettings).filter_by(name=SHARED_DOWNLOAD_DIR_SETTING).first()
    value = str(directory)
    if setting is None:
        session.add(ApplicationSettings(name=SHARED_DOWNLOAD_DIR_SETTING, type="string", value=value))
    else:
        setting.value = value
    session.commit()
