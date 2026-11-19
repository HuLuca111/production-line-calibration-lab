import csv
import json
from pathlib import Path

from line_calibration.calibration import Policy
from line_calibration.study import (
    HORIZON,
    SCENARIOS,
    run_study,
    run_trial,
    service_mean,
)


def test_service_drift_only_applies_to_new_middle_station_operations():
    assert service_mean(1, 359.99, change_at=360) == 1.0
    assert service_mean(1, 360, change_at=360) == 1.4
    assert service_mean(0, 360, change_at=360) == 0.95
    assert service_mean(2, 360, change_at=360) == 1.1
    assert service_mean(1, 400, change_at=None) == 1.0


def test_paired_strategies_share_observed_outcomes_and_ticks():
    rows, measurements = run_trial(SCENARIOS["drift_delayed"], seed=42, rollouts=2)
    assert len(rows) == 20 * len(Policy)
    assert {row.origin for row in rows} == set(range(120, 720, HORIZON))
    for origin in {row.origin for row in rows}:
        assert len({row.actual_count for row in rows if row.origin == origin}) == 1
    assert all(row.estimated_mean == 1.0 for row in rows if row.policy == Policy.FIXED)
    assert all(event.available_at == event.finished_at + 15 for event in measurements)


def test_quick_study_exports_reproducible_results(tmp_path: Path):
    report = run_study(tmp_path, profile="quick")
    assert report["primary"]["n"] == 2
    assert report["seeds"] == [1000, 1001]
    assert report["primary"]["lower"] <= report["primary"]["mean"] <= report["primary"]["upper"]
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == report
    with (tmp_path / "forecasts.csv").open(newline="", encoding="utf-8") as file:
        assert len(list(csv.DictReader(file))) == 360
    assert (tmp_path / "forecast-error.png").stat().st_size > 0
    assert (tmp_path / "calibration-tradeoff.png").stat().st_size > 0
