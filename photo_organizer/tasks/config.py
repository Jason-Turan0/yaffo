from huey import SqliteHuey
huey = SqliteHuey(
    filename=str('huey.db'),
    immediate=False,
    utc=True,
)
