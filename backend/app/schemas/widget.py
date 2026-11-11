from pydantic import BaseModel


class WidgetConfigResponse(BaseModel):
    """What a client's chat widget needs before the first message. Public: nothing private."""

    client: str
    # Shown in the widget header and its question buttons.
    name: str
    # The client's default questions, in display order (up to 4; may be empty).
    default_questions: list[str]
    # Whether the widget may offer booking (calendar connected and healthy).
    booking_available: bool = False


class FrameAncestorsResponse(BaseModel):
    """Hosts that may frame the widget. The embed page turns these into its CSP
    frame-ancestors value. The browser sees the same list in that header anyway."""

    allowed_domains: list[str]
