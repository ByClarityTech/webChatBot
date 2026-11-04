from pydantic import BaseModel


class WidgetConfigResponse(BaseModel):
    """What a client's chat widget needs before the first message. Public: nothing private."""

    client: str
    # The client's default questions, in display order (up to 4; may be empty).
    default_questions: list[str]
