"""Turn BTS rows into model features and the label.

Only what is known *before departure* becomes a feature: the published schedule and the
calendar. Departure delay, actual times and cancellations are outcomes, so they never appear
here (using DepDelay would leak most of the answer).

The label is "disrupted": arrived 15+ minutes late, was cancelled, or was diverted. A
passenger asking "will my flight be late?" cares about all three, and dropping cancelled
flights would hide the biggest part of the March 2020 shock (docs/decisions/0003).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

CATEGORICAL = ["carrier", "origin", "dest"]
NUMERIC = [
    "dep_hour",
    "arr_hour",
    "distance",
    "sched_minutes",
    "origin_hour_load",
    "dest_hour_load",
]
CALENDAR = ["month", "day_of_week", "days_to_holiday"]
FEATURES = CATEGORICAL + NUMERIC + CALENDAR
LABEL = "disrupted"

_HOLIDAYS = USFederalHolidayCalendar().holidays("2017-01-01", "2021-12-31").values


def load(paths: list[Path] | list[str]) -> pd.DataFrame:
    """Read monthly Parquet files into one frame (categories unified across months)."""
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    for c in ["Reporting_Airline", "Origin", "Dest", "OriginState", "DestState"]:
        df[c] = df[c].astype("string").astype("category")
    return df


def build(raw: pd.DataFrame) -> pd.DataFrame:
    """Features + label + the event time each row would be scored at."""
    out = pd.DataFrame(index=raw.index)
    out["flight_date"] = raw["FlightDate"]
    out["carrier"] = raw["Reporting_Airline"].astype("string")
    out["origin"] = raw["Origin"].astype("string")
    out["dest"] = raw["Dest"].astype("string")
    out["dep_hour"] = (raw["CRSDepTime"] // 100).clip(0, 23).astype("int8")
    out["arr_hour"] = (raw["CRSArrTime"] // 100).clip(0, 23).astype("int8")
    out["distance"] = raw["Distance"].astype("float32")
    out["sched_minutes"] = raw["CRSElapsedTime"].astype("float32")

    # Schedule congestion: how many flights are *scheduled* to leave the origin (and land at
    # the destination) in the same hour that day. Airlines publish schedules months ahead, so
    # this is known at prediction time; in serving it comes from the schedule table.
    out["origin_hour_load"] = (
        out.groupby(["flight_date", "origin", "dep_hour"])["carrier"]
        .transform("size")
        .astype("float32")
    )
    out["dest_hour_load"] = (
        out.groupby(["flight_date", "dest", "arr_hour"])["carrier"]
        .transform("size")
        .astype("float32")
    )

    out["month"] = raw["FlightDate"].dt.month.astype("int8")
    out["day_of_week"] = raw["FlightDate"].dt.dayofweek.astype("int8")
    out["days_to_holiday"] = _days_to_holiday(raw["FlightDate"]).astype("int16")

    # Event time: the scheduled departure. Requests are scored then; labels arrive later.
    out["event_time"] = raw["FlightDate"] + pd.to_timedelta(
        (raw["CRSDepTime"] // 100) * 60 + raw["CRSDepTime"] % 100, unit="m"
    )
    disrupted = (raw["ArrDel15"] == 1) | (raw["Cancelled"] == 1) | (raw["Diverted"] == 1)
    out[LABEL] = disrupted.astype("int8")
    return out


def to_model_frame(df: pd.DataFrame, categories: dict[str, list[str]]) -> pd.DataFrame:
    """Feature matrix with categoricals pinned to the training vocabulary.

    Values the model never saw (a new airline code, a new airport) become NaN, which LightGBM
    routes like a missing value. The data-quality check counts them separately.
    """
    X = df[FEATURES].copy()
    for c in CATEGORICAL:
        X[c] = pd.Categorical(X[c].astype("string"), categories=categories[c])
    return X


def vocabulary(df: pd.DataFrame) -> dict[str, list[str]]:
    return {c: sorted(df[c].dropna().unique().tolist()) for c in CATEGORICAL}


def _days_to_holiday(dates: pd.Series) -> np.ndarray:
    """Absolute distance in days to the nearest US federal holiday, capped at 30."""
    d = dates.values.astype("datetime64[D]")
    hol = _HOLIDAYS.astype("datetime64[D]")
    idx = np.searchsorted(hol, d)
    before = hol[np.clip(idx - 1, 0, len(hol) - 1)]
    after = hol[np.clip(idx, 0, len(hol) - 1)]
    dist = np.minimum(np.abs((d - before).astype(int)), np.abs((after - d).astype(int)))
    return np.minimum(dist, 30)


def days_to_holiday(dates: pd.Series) -> np.ndarray:
    """Public form of the calendar feature, for request-time feature building."""
    return _days_to_holiday(pd.to_datetime(pd.Series(dates)))
