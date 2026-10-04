"""Download BTS on-time performance months and keep only the columns DriftOps uses.

Source: the Bureau of Transportation Statistics "Reporting Carrier On-Time Performance"
prezipped monthly files (one row per scheduled flight, ~110 columns, ~250 MB of CSV a month).
We keep ~20 columns and write one Parquet file per month.

    python -m driftops.data 2018-01 2020-06
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
RAW = Path("data/raw")
PARQUET = Path("data/parquet")

# Known before departure: these can be model features.
SCHEDULE_COLUMNS = [
    "FlightDate",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "Dest",
    "OriginState",
    "DestState",
    "CRSDepTime",
    "CRSArrTime",
    "CRSElapsedTime",
    "Distance",
]
# Known only after the flight: outcomes. Never features (they leak the answer).
OUTCOME_COLUMNS = [
    "DepDelay",
    "ArrDelay",
    "ArrDel15",
    "Cancelled",
    "Diverted",
]
COLUMNS = SCHEDULE_COLUMNS + OUTCOME_COLUMNS

DTYPES = {
    "Reporting_Airline": "category",
    "Origin": "category",
    "Dest": "category",
    "OriginState": "category",
    "DestState": "category",
    "Flight_Number_Reporting_Airline": "int32",
    "CRSDepTime": "int16",
    "CRSArrTime": "int16",
    "CRSElapsedTime": "float32",
    "Distance": "float32",
    "DepDelay": "float32",
    "ArrDelay": "float32",
    "ArrDel15": "float32",
    "Cancelled": "float32",
    "Diverted": "float32",
}


def months(start: str, end: str) -> list[tuple[int, int]]:
    """Inclusive list of (year, month) between two YYYY-MM strings."""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch(year: int, month: int) -> Path:
    """Download one month and write data/parquet/YYYY-MM.parquet. Idempotent."""
    out = PARQUET / f"{year}-{month:02d}.parquet"
    if out.exists():
        return out
    raw = RAW / f"{year}_{month}.zip"
    if not raw.exists():
        tmp = raw.with_suffix(".part")
        urllib.request.urlretrieve(URL.format(year=year, month=month), tmp)
        tmp.rename(raw)
    with zipfile.ZipFile(raw) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(name) as f:
            df = pd.read_csv(io.TextIOWrapper(f), usecols=COLUMNS, dtype=DTYPES)
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    tmp = out.with_suffix(".part")
    df.to_parquet(tmp, index=False)
    tmp.rename(out)
    raw.unlink()  # the zip is 30 MB a month; the Parquet is all we need
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("start", help="first month, YYYY-MM")
    p.add_argument("end", help="last month, YYYY-MM (inclusive)")
    p.add_argument("-j", "--jobs", type=int, default=4)
    args = p.parse_args(argv)
    RAW.mkdir(parents=True, exist_ok=True)
    PARQUET.mkdir(parents=True, exist_ok=True)
    todo = months(args.start, args.end)
    with ThreadPoolExecutor(args.jobs) as pool:
        for path in pool.map(lambda ym: fetch(*ym), todo):
            print(f"ok  {path}  {path.stat().st_size / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    sys.exit(main())
