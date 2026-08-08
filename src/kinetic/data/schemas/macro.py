"""Canonical macroeconomic records.

``MacroObservation`` carries both ``observation_date`` (the period the number
describes) and ``release_datetime`` (when it became public), because a macro
series is only usable point-in-time if those are kept apart. No macro provider
is implemented yet; see ``docs/getting-started/adding-a-provider.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from kinetic.data.schemas._validation import (
    LogicalKey,
    _date,
    _finite,
    _identity_text,
    _utc,
)


@dataclass(frozen=True, slots=True)
class MacroObservation:
    series_id: str
    observation_date: date
    value: float | None
    realtime_start: date
    realtime_end: date
    vintage_date: date | None
    provider: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _identity_text(self.series_id, "series_id"))
        for field_name in ("observation_date", "realtime_start", "realtime_end"):
            object.__setattr__(self, field_name, _date(getattr(self, field_name), field_name))
        if self.vintage_date is not None:
            object.__setattr__(self, "vintage_date", _date(self.vintage_date, "vintage_date"))
        if self.value is not None:
            object.__setattr__(self, "value", _finite(self.value, "value"))
        object.__setattr__(self, "provider", _identity_text(self.provider, "provider"))
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at, "retrieved_at"))
        if self.realtime_start > self.realtime_end:
            raise ValueError("realtime_start must not be after realtime_end")

    @property
    def logical_key(self) -> LogicalKey:
        return (
            self.series_id,
            self.observation_date,
            self.realtime_start,
            self.realtime_end,
            self.vintage_date,
            self.provider,
        )
