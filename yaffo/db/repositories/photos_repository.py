import calendar

from sqlalchemy.orm import Session
from yaffo.db.models import Photo


def get_distinct_years(session: Session) -> list[int]:
    return [row[0] for row in
            (session
                .query(Photo.year)
                .filter(Photo.year.isnot(None))
                .distinct()
                .order_by(Photo.year)
                .all())
            ]

def get_distinct_months():
    return [
        {'value': i, 'name': calendar.month_name[i]}
        for i in range(1, 13)
    ]