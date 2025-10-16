from photo_organizer.common import HUEY_DB_PATH
from huey import SqliteHuey
huey = SqliteHuey(
    filename=str(HUEY_DB_PATH),
    immediate=False,
    utc=True,
)
