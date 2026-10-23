from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PaymentEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=255)
    transaction_id: str = Field(min_length=1, max_length=255)
    event_type: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    currency: str = Field(min_length=3, max_length=3)
    status: Literal["succeeded", "failed", "pending"]
    gateway: str = Field(min_length=1, max_length=64)
    browser: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=2)
    error_code: str | None = Field(default=None, max_length=100)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.upper()
        if not value.isalpha():
            raise ValueError("currency must contain three letters")
        return value
