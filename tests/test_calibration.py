from math import isclose

from line_calibration.calibration import Calibrator, Measurement, Policy, observed_before


def measurements(count: int, *, start: int = 0, duration: float = 1.4) -> tuple[Measurement, ...]:
    return tuple(
        Measurement(index, 1, float(index), index + duration, index + duration)
        for index in range(start, start + count)
    )


def test_only_available_middle_station_events_are_visible():
    events = (
        Measurement(1, 1, 5, 7, 12),
        Measurement(2, 1, 8, 9, 10),
        Measurement(3, 1, 9, 10, 11),
        Measurement(4, 0, 6, 8, 9),
    )
    assert [event.job_id for event in observed_before(events, 11)] == [2]


def test_periodic_update_requires_a_due_tick_and_a_full_window():
    calibrator = Calibrator(Policy.PERIODIC)
    assert calibrator.update(120, measurements(19)) == 1.0
    assert calibrator.update(150, measurements(20)) == 1.0
    assert isclose(calibrator.update(180, measurements(20)), 1.4)


def test_triggered_updates_only_when_drift_and_new_evidence_are_sufficient():
    calibrator = Calibrator(Policy.THRESHOLD)
    older = measurements(20, duration=1.05)
    later = older + measurements(20, start=20)
    assert calibrator.update(120, older) == 1.0
    assert isclose(calibrator.update(150, later), 1.4)
    assert calibrator.update(180, later + measurements(9, start=40, duration=3.0)) == calibrator.mean
    assert calibrator.update_count == 1
    assert isclose(calibrator.update(210, later + measurements(10, start=40, duration=3.0)), 2.2)
    assert calibrator.update_count == 2


def test_fixed_strategy_ignores_a_shift():
    calibrator = Calibrator(Policy.FIXED)
    assert calibrator.update(120, measurements(30, duration=2.0)) == 1.0
    assert calibrator.update_count == 0
