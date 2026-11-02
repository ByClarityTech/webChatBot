from datetime import datetime

from pydantic import BaseModel


class BusyPeriod(BaseModel):
    start: datetime
    end: datetime


class FreeBusyResult(BaseModel):
    calendar_id: str
    busy: list[BusyPeriod]
