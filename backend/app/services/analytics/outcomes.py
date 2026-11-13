"""Deterministic outcome labels for an ended conversation.

Priority, first match wins: Booking > Callback > Handover > Resolved > Abandoned.

- Booking: a confirmed booking was made in this conversation.
- Callback: a callback was requested in this conversation (open or handled).
- Handover: a team member accepted a live chat in this conversation.
- Resolved: the assistant's last reply was an answer, and then the conversation ended.
- Abandoned: anything else. The reason says which case it was.

A conversation with no visitor message is a bounce. It is labeled Abandoned and is left out of
the rate denominators (it is not "engaged").

Idle endings follow the same rule as closed ones: the last assistant reply decides.
"""

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage

LABELER_VERSION = 1
END_GRACE_MINUTES = 5

BOOKING = "booking"
CALLBACK = "callback"
HANDOVER = "handover"
RESOLVED = "resolved"
ABANDONED = "abandoned"
OUTCOMES = (BOOKING, CALLBACK, HANDOVER, RESOLVED, ABANDONED)

OUTCOME_LABELS = {
    BOOKING: "Booking",
    CALLBACK: "Callback",
    HANDOVER: "Handover",
    RESOLVED: "Resolved",
    ABANDONED: "Abandoned",
}

# The assistant's last turn, read from the saved conversation state.
TURN_ANSWER = "answer"
TURN_FALLBACK = "fallback"
TURN_SAFETY = "safety"
TURN_BOOKING = "booking"
TURN_NONE = "none"

REASON_BOOKING = "Booking confirmed"
REASON_CALLBACK = "Callback requested"
REASON_HANDOVER = "Handed to a team member"
REASON_RESOLVED = "Answered, then the visitor left"
REASON_FALLBACK = "Ended after a question the knowledge base could not answer"
REASON_SAFETY = "Ended after a safety message"
REASON_BOOKING_OPEN = "Ended while booking questions were open"
REASON_NO_ANSWER = "Ended with no answer from the assistant"
REASON_BOUNCE = "Left before any message"


@dataclass(frozen=True)
class SessionFacts:
    visitor_messages: int
    bot_replies: int
    last_turn: str
    has_booking: bool
    has_callback: bool
    has_handover: bool
    unanswered_questions: int
    duration_seconds: int


@dataclass(frozen=True)
class Classification:
    outcome: str
    reason: str
    engaged: bool


def classify(facts: SessionFacts) -> Classification:
    engaged = facts.visitor_messages >= 1
    if facts.has_booking:
        return Classification(BOOKING, REASON_BOOKING, engaged)
    if facts.has_callback:
        return Classification(CALLBACK, REASON_CALLBACK, engaged)
    if facts.has_handover:
        return Classification(HANDOVER, REASON_HANDOVER, engaged)
    if not engaged:
        return Classification(ABANDONED, REASON_BOUNCE, False)
    if facts.last_turn == TURN_ANSWER:
        return Classification(RESOLVED, REASON_RESOLVED, True)
    if facts.last_turn == TURN_FALLBACK:
        return Classification(ABANDONED, REASON_FALLBACK, True)
    if facts.last_turn == TURN_SAFETY:
        return Classification(ABANDONED, REASON_SAFETY, True)
    if facts.last_turn == TURN_BOOKING:
        return Classification(ABANDONED, REASON_BOOKING_OPEN, True)
    return Classification(ABANDONED, REASON_NO_ANSWER, True)


def count_turns(messages: list) -> tuple[int, int]:
    """(visitor messages, assistant replies) in a saved conversation."""
    visitor = sum(1 for message in messages if isinstance(message, HumanMessage))
    bot = sum(1 for message in messages if isinstance(message, AIMessage))
    return visitor, bot


def last_turn(values: dict) -> str:
    """What the assistant's last reply was, from the saved conversation state.

    The safety and booking checks come first. The answer flag is only set by the answer nodes,
    so it can be stale after those turns. The graph sets intent on every turn.
    """
    messages = list(values.get("messages") or [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return TURN_NONE
    if values.get("safety"):
        return TURN_SAFETY
    if values.get("intent") == "booking":
        return TURN_BOOKING
    if values.get("answer_declined"):
        return TURN_FALLBACK
    return TURN_ANSWER
