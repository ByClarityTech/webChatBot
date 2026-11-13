from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.widget_branding import HEX_COLOR


class WidgetStatusRequest(BaseModel):
    status: Literal["live", "paused"]


class WidgetBrandingRequest(BaseModel):
    """Empty values clear a setting, so the widget falls back to its default."""

    bot_name: str | None = Field(None, max_length=200)
    greeting: str | None = Field(None, max_length=400)
    header_color: str | None = Field(None, max_length=7)

    @field_validator("bot_name", "greeting", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("header_color", mode="before")
    @classmethod
    def _color(cls, value):
        if value is None or str(value).strip() == "":
            return None
        text = str(value).strip()
        if not HEX_COLOR.match(text):
            raise ValueError("Use a color like #1D4ED8")
        return text.upper()

    @field_validator("bot_name")
    @classmethod
    def _bot_name_length(cls, value):
        if value is not None and len(value) > 60:
            raise ValueError("The bot name can be up to 60 characters")
        return value

    @field_validator("greeting")
    @classmethod
    def _greeting_length(cls, value):
        if value is not None and len(value) > 200:
            raise ValueError("The greeting can be up to 200 characters")
        return value


class WidgetVerifyRequest(BaseModel):
    url: str = Field(..., max_length=2048)


class WidgetAdminResponse(BaseModel):
    client_id: str
    client_slug: str
    status: Literal["live", "paused"]
    bot_name: str | None
    greeting: str | None
    # The colors the widget uses now: the saved ones, or the defaults.
    header_color: str
    header_text_color: str
    contrast_ratio: float
    logo_url: str | None
    has_logo: bool
    public_url: str
    snippet: str
    updated_at: datetime | None


class WidgetVerifyResponse(BaseModel):
    code: str
    message: str
    checked_url: str
    note: str
