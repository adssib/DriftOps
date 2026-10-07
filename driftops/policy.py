"""The retrain-controller's decision and the eval gate.

Pure functions over the monitors' outputs, so the same code runs in the backtest and in the
retrain-controller CronJob (docs/decisions/0005).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Limits:
    psi_feature: float = 0.10  # ADR-0004: max(0.1, 1.5 x worst 2019 day)
    calibration_gap: float = 0.158
    brier: float = 0.326
    k_consecutive: int = 3
    cooldown_days: int = 7
    window_days: int = 7  # a breach taints every window that contains the breached day
    min_labelled_rows: int = 50_000
    # data quality: any one breached means "the pipeline broke"
    null_increase: float = 0.05
    out_of_range: float = 0.02
    unseen_category: float = 0.02
    route_distance_mismatch: float = 0.02


@dataclass
class Signals:
    """What the three monitors wrote for one window."""

    psi_max_feature: float
    worst_feature: str
    calibration_gap: float | None  # None until labels exist for the window
    brier: float | None
    data_quality: dict[str, float]
    labelled_rows: int


@dataclass
class Decision:
    action: str  # "none" | "retrain" | "block" | "wait"
    reason: str
    alarms: list[str] = field(default_factory=list)


class Controller:
    """Keeps the run of consecutive alarm windows and the last retrain day."""

    def __init__(self, limits: Limits | None = None, guard: bool = True):
        self.limits = limits or Limits()
        self.guard = guard
        self.run = 0
        self.last_retrain: pd.Timestamp | None = None
        self.last_breach: pd.Timestamp | None = None
        # (start, end) day ranges the retrain Job must leave out of its training data
        self.quarantine: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    def alarms(self, s: Signals) -> list[str]:
        return feature_alarms(s.psi_max_feature, s.worst_feature, self.limits) + label_alarms(
            s.calibration_gap, s.brier, self.limits
        )

    def quality_breaches(self, s: Signals) -> list[str]:
        return quality_breaches(s.data_quality, self.limits)

    def decide(self, day: pd.Timestamp, s: Signals) -> Decision:
        alarms = self.alarms(s)
        self.run = self.run + 1 if alarms else 0
        breaches = self.quality_breaches(s)
        if breaches and self.guard:
            # Drift seen while the data is broken is the pipeline's fault until proven
            # otherwise. Never train on it: quarantine the window, restart the alarm count.
            start = day - pd.Timedelta(days=self.limits.window_days - 1)
            self.quarantine.append((start, day))
            self.last_breach = day
            self.run = 0
            return Decision("block", "data quality: " + ", ".join(breaches), alarms)
        if (
            self.guard
            and self.last_breach is not None
            and (day - self.last_breach).days < self.limits.window_days
        ):
            # The window still holds breached days, so its alarms are still suspect.
            self.run = 0
            return Decision("wait", "window still holds breached days", alarms)
        if not alarms:
            return Decision("none", "quiet")
        if self.run < self.limits.k_consecutive:
            return Decision("wait", f"alarm {self.run}/{self.limits.k_consecutive}", alarms)
        if (
            self.last_retrain is not None
            and (day - self.last_retrain).days < self.limits.cooldown_days
        ):
            return Decision("wait", "cooldown", alarms)
        if s.labelled_rows < self.limits.min_labelled_rows:
            return Decision("wait", f"only {s.labelled_rows} labelled rows", alarms)
        self.last_retrain = day
        self.run = 0
        return Decision("retrain", "sustained drift, data clean", alarms)


# The alarm rules, shared by the controller and the monitors (one definition, ADR-0004) -------


def feature_alarms(psi_max_feature: float, worst_feature: str, limits: Limits) -> list[str]:
    return [f"feature:{worst_feature}"] if psi_max_feature > limits.psi_feature else []


def label_alarms(calibration_gap: float | None, brier: float | None, limits: Limits) -> list[str]:
    out = []
    if calibration_gap is not None and abs(calibration_gap) > limits.calibration_gap:
        out.append("label:calibration_gap")
    if brier is not None and brier > limits.brier:
        out.append("label:brier")
    return out


def quality_breaches(data_quality: dict[str, float], limits: Limits) -> list[str]:
    return [k for k, v in data_quality.items() if v > getattr(limits, k)]


@dataclass
class GateResult:
    passed: bool
    reason: str
    champion: dict[str, float]
    challenger: dict[str, float]


def gate(
    champion: dict[str, float], challenger: dict[str, float], auc_margin: float = 0.005
) -> GateResult:
    """Challenger must beat the champion on Brier (calibration + sharpness) and not lose more
    than `auc_margin` of AUC, on the newest out-of-time labelled days neither model trained on.
    """
    if challenger["brier"] > champion["brier"]:
        reason = f"brier {challenger['brier']:.4f} > champion {champion['brier']:.4f}"
        return GateResult(False, reason, champion, challenger)
    if challenger["auc"] < champion["auc"] - auc_margin:
        reason = f"auc {challenger['auc']:.4f} < champion {champion['auc']:.4f} - {auc_margin}"
        return GateResult(False, reason, champion, challenger)
    return GateResult(True, "better brier, auc held", champion, challenger)
