from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    thread_id: str | None = None


class SourceCitation(BaseModel):
    filename: str
    excerpt: str
    score: float | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str
    sources: list[SourceCitation]
    action: str | None = None
