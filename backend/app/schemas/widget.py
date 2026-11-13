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
    # Branding. Null means the widget uses its own default: the practice name, the env greeting,
    # and the default header color.
    bot_name: str | None = None
    greeting: str | None = None
    header_color: str = "#1D4ED8"
    header_text_color: str = "#FFFFFF"
    # Relative path of the logo on this API, or null when there is none.
    logo_url: str | None = None


class FrameAncestorsResponse(BaseModel):
    """Hosts that may frame the widget. The embed page turns these into its CSP
    frame-ancestors value. The browser sees the same list in that header anyway."""

    allowed_domains: list[str]
