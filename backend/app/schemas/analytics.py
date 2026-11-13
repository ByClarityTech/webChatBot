from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

OutcomeKey = Literal["booking", "callback", "handover", "resolved", "abandoned"]


class PeriodOut(BaseModel):
    start: date
    end: date
    timezone: str
    days: int


class SummaryOut(BaseModel):
    started: int
    ended: int
    in_progress: int
    engaged: int
    bounced: int
    booking_rate: float | None
    callback_rate: float | None
    handover_rate: float | None
    resolution_rate: float | None
    abandonment_rate: float | None
    average_duration_seconds: float | None
    handovers_created: int
    handovers_accepted: int
    handovers_timed_out: int
    handover_accept_rate: float | None
    handover_timeout_rate: float | None


class FunnelStep(BaseModel):
    key: str
    label: str
    count: int
    percent_of_engaged: float | None


class OutcomeCountOut(BaseModel):
    outcome: OutcomeKey
    label: str
    count: int
    percent_of_engaged: float | None


class DayPointOut(BaseModel):
    day: date
    started: int
    engaged: int
    booking: int
    callback: int
    handover: int
    resolved: int
    abandoned: int


class TopGapOut(BaseModel):
    gap_id: UUID
    question: str
    reason: str
    asks: int
    last_asked_at: datetime


class AnalyticsResponse(BaseModel):
    client_id: UUID | None
    period: PeriodOut
    summary: SummaryOut
    funnel: list[FunnelStep]
    outcomes: list[OutcomeCountOut]
    daily: list[DayPointOut]
    top_gaps: list[TopGapOut]
    note: str


class ConversationItemOut(BaseModel):
    session_id: UUID
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    status: Literal["in_progress", "ended"]
    conversation_type: Literal["anonymous", "known"]
    outcome: OutcomeKey | None
    outcome_label: str | None
    reason: str | None
    engaged: bool | None
    visitor_messages: int | None
    bot_replies: int | None
    unanswered_questions: int | None


class ConversationListOut(BaseModel):
    items: list[ConversationItemOut]
    total: int
    limit: int
    offset: int


class PatientGroupOut(BaseModel):
    # An opaque key for the group on screen. It is not the phone number or its hash.
    patient_key: str
    masked_phone: str | None
    name: str | None
    conversations: int
    first_seen_at: datetime
    last_seen_at: datetime
    latest_session_id: UUID | None


class PatientGroupListOut(BaseModel):
    groups: list[PatientGroupOut]
    total: int
    limit: int
    offset: int


class TranscriptLineOut(BaseModel):
    position: int
    role: Literal["visitor", "assistant"]
    text: str


class StaffMessageOut(BaseModel):
    sender: Literal["visitor", "staff"]
    body: str
    created_at: datetime


class UnansweredOut(BaseModel):
    question: str
    asked_at: datetime


class ContactOut(BaseModel):
    name: str
    masked_phone: str | None


class ConversationDetailOut(BaseModel):
    session_id: UUID
    started_at: datetime
    ended_at: datetime | None
    status: Literal["in_progress", "ended"]
    conversation_type: Literal["anonymous", "known"]
    outcome: OutcomeKey | None
    outcome_label: str | None
    reason: str | None
    engaged: bool | None
    visitor_messages: int | None
    bot_replies: int | None
    duration_seconds: int | None
    unanswered: list[UnansweredOut]
    contact: ContactOut | None
    transcript: list[TranscriptLineOut]
    staff_messages: list[StaffMessageOut]
    notice: str
