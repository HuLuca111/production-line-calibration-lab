"""Calibration policies based on completed, available operation records."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import fmean

MIDDLE_STATION = 1
WINDOW_SIZE = 20
MIN_NEW_RECORDS = 10
PERIOD_MINUTES = 60
SHIFT_THRESHOLD = 0.15


class Policy(StrEnum):
    """How often to replace the middle station's processing-time mean."""

    FIXED = "fixed"
    PERIODIC = "periodic"
    THRESHOLD = "threshold"


@dataclass(frozen=True)
class Measurement:
    """One completed operation and the time its record becomes observable."""

    job_id: int
    station: int
    started_at: float
    finished_at: float
    available_at: float

    @property
    def duration(self) -> float:
        """Processing time up to completion, before any downstream blocking."""
        return self.finished_at - self.started_at


def observed_before(records: Iterable[Measurement], origin: int) -> tuple[Measurement, ...]:
    """Return observable M2 measurements in completion order, strictly before origin."""
    return tuple(
        sorted(
            (
                record
                for record in records
                if record.station == MIDDLE_STATION and record.available_at < origin
            ),
            key=lambda record: (record.finished_at, record.job_id),
        )
    )


@dataclass
class Calibrator:
    """Keep an online estimate using the growing prefix of visible measurements."""

    policy: Policy
    mean: float = 1.0
    update_count: int = field(default=0, init=False)
    _observations_at_update: int = field(default=0, init=False, repr=False)

    def update(self, origin: int, visible: tuple[Measurement, ...]) -> float:
        """Update at a forecast origin if the chosen rule and sample minimum allow it."""
        if self.policy == Policy.FIXED:
            return self.mean
        if len(visible) < WINDOW_SIZE or len(visible) - self._observations_at_update < MIN_NEW_RECORDS:
            return self.mean
        if self.policy == Policy.PERIODIC and origin % PERIOD_MINUTES:
            return self.mean

        candidate = fmean(record.duration for record in visible[-WINDOW_SIZE:])
        if self.policy == Policy.THRESHOLD and abs(candidate - self.mean) / self.mean < SHIFT_THRESHOLD:
            return self.mean

        self.mean = candidate
        self.update_count += 1
        self._observations_at_update = len(visible)
        return self.mean
