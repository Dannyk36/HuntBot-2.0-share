from pydantic import BaseModel


class JobRecord(BaseModel):
    source: str
    title: str
    company: str
    location: str
    url: str
    posted_at: str | None = None
    raw_snapshot: str | None = None
