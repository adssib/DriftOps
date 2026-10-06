"""The /predict contract (SPEC § 6) with the validation rules of OPERATIONS § 5.

Well-formed but unknown values (a new carrier code) are accepted and flagged; the data-quality
monitor counts them. Malformed values are rejected here. `source` is not a field: the caller's
identity decides it (ADR-0019), and `extra="forbid"` turns an attempt to send it into a 422.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Carrier = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{2}$")]
Airport = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
ClockTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

REPLAY_START = dt.date(2020, 1, 1)
REPLAY_END = dt.date(2020, 6, 30)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False, str_strip_whitespace=True)

    carrier: Carrier
    origin: Airport
    dest: Airport
    flight_date: dt.date
    dep_time: ClockTime
    flight_id: int | None = Field(default=None, ge=1)

    # Optional feature values: an upstream pipeline (the simulator) sends them; the web form
    # doesn't, and the server fills them from the schedule.
    distance: float | None = Field(default=None, ge=1, le=6000)
    sched_minutes: float | None = Field(default=None, ge=10, le=1000)
    origin_hour_load: float | None = Field(default=None, ge=0, le=500)
    dest_hour_load: float | None = Field(default=None, ge=0, le=500)

    @model_validator(mode="after")
    def _route(self) -> PredictRequest:
        if self.origin == self.dest:
            raise ValueError("origin and dest must differ")
        return self


def check_user_date(req: PredictRequest) -> None:
    """User requests must fall inside the replay range: the schedule lookup has no other days."""
    if not (REPLAY_START <= req.flight_date <= REPLAY_END):
        raise ValueError(f"flight_date must be between {REPLAY_START} and {REPLAY_END}")


class PredictResponse(BaseModel):
    request_id: str
    p_disrupted: float
    risk: str
    model_version: int
    top_reasons: list[tuple[str, float]]
    warnings: list[str] = []


def risk_band(p: float) -> str:
    """Bands around the 2018 base rate (20.6%), not a 0.5 cut: almost nothing is >50% likely."""
    if p < 0.2:
        return "low"
    if p < 0.35:
        return "elevated"
    return "high"
