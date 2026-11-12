from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class BusinessHoursResponse(BaseModel):
    business_days: list[int] = Field(
        ...,
        description="ISO weekdays (1=Monday … 7=Sunday)",
    )
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    slot_duration_minutes: int = Field(..., ge=5, le=480)
    slot_buffer_minutes: int = Field(..., ge=0, le=120)
    timezone: str = Field(..., min_length=1, max_length=100)
    min_notice_minutes: int = Field(..., ge=0, le=10080)
    source: str = Field(..., description="database or env")
    updated_at: datetime | None = None


class AllowedDomainsResponse(BaseModel):
    domains: list[str] = Field(..., description="Saved host patterns, e.g. clinic.com")


class AllowedDomainsUpdateRequest(BaseModel):
    """Replaces the whole list. An empty list clears it, and production then refuses the widget."""

    domains: list[str] = Field(default_factory=list, max_length=50)


class BusinessHoursUpdateRequest(BaseModel):
    business_days: list[int] = Field(..., min_length=1)
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    slot_duration_minutes: int = Field(..., ge=5, le=480)
    slot_buffer_minutes: int = Field(..., ge=0, le=120)
    timezone: str = Field(..., min_length=1, max_length=100)
    # How soon a slot may start, from now (0 to 7 days).
    min_notice_minutes: int = Field(default=60, ge=0, le=10080)

    @field_validator("business_days")
    @classmethod
    def validate_business_days(cls, value: list[int]) -> list[int]:
        unique = sorted(set(value))
        if not all(1 <= day <= 7 for day in unique):
            raise ValueError("business_days must be ISO weekdays between 1 and 7")
        return unique

    @model_validator(mode="after")
    def end_after_start(self) -> "BusinessHoursUpdateRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class PracticeContactResponse(BaseModel):
    phone: str | None = Field(None, description="E.164 US number, e.g. +13125550187")


class PracticeContactUpdateRequest(BaseModel):
    phone: str | None = Field(None, max_length=30)
