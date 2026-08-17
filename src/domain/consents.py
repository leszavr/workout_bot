"""Модель согласия на обработку данных.

Согласие фиксируется явно (timestamp, версия документа, scope, источник),
а не выставляется автоматически по факту нажатия кнопки.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import CONSENT_DOCUMENT_VERSION, ConsentScope


class ConsentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ConsentScope
    granted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0)
    )
    document_version: str = CONSENT_DOCUMENT_VERSION
    source: str = Field(default="telegram_review_confirm", max_length=100)
