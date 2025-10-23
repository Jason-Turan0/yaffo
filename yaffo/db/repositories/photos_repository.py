import calendar

from sqlalchemy import extract
from sqlalchemy.orm import Session
from yaffo.db.models import Photo

def get_distinct_years(session: Session):
    return [year[0] for year in
            (session
                .query(extract('year', Photo.date_taken))
                .distinct()
                .order_by(extract('year', Photo.date_taken))
                .all())
            ]

def get_distinct_months():
    return [
        {'value': i, 'name': calendar.month_name[i]}
        for i in range(1, 13)
    ]