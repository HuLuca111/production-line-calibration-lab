"""Replay a synthetic production line and evaluate three calibration policies."""

import csv
import json
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from math import log, log1p, sqrt
from pathlib import Path
from statistics import fmean, stdev

import numpy as np
from scipy.stats import t as student_t

from line_calibration.calibration import (
    MIDDLE_STATION,
    MIN_NEW_RECORDS,
    PERIOD_MINUTES,
    SHIFT_THRESHOLD,
    WINDOW_SIZE,
    Calibrator,
    Measurement,
    Policy,
    observed_before,
)
from line_calibration.forecast import SERVICE_CV, forecast_completions
from line_calibration.line import Line

CAPACITIES = (3, 3)
NOMINAL_MEANS = (0.95, 1.0, 1.10)
SHIFTED_MEAN = 1.4
FIRST_ORIGIN = 120
CHANGE_AT = 360
END = 720
HORIZON = 30
RECOVERY_TOLERANCE = 0.10


@dataclass(frozen=True)
class Scenario:
    """Production change and measurement delivery delay, in minutes."""

    name: str
    change_at: int | None
    delay: int


SCENARIOS = {
    "stable": Scenario("stable", None, 0),
    "drift": Scenario("drift", CHANGE_AT, 0),
    "drift_delayed": Scenario("drift_delayed", CHANGE_AT, 15),
}
PROFILES = {"quick": (2, 4), "full": (24, 16)}


@dataclass(frozen=True)
class ForecastRow:
    """One origin and policy, scored after the forecast window closes."""

    scenario: str
    seed: int
    policy: Policy
    origin: int
    forecast_count: float
    actual_count: int
    estimated_mean: float
    updated: bool

    @property
    def absolute_error(self) -> float:
        """Absolute forecast error in completed items per 30-minute window."""
        return abs(self.forecast_count - self.actual_count)


@dataclass(frozen=True)
class RunMetrics:
    """Treat an independent virtual shift as the statistical unit."""

    scenario: str
    seed: int
    policy: Policy
    post_change_mae: float
    updates: int
    pre_change_updates: int
    recovery_minutes: int | None


def service_mean(station: int, started_at: float, change_at: int | None) -> float:
    """The scenario changes only new operations at M2."""
    if station == MIDDLE_STATION and change_at is not None and started_at >= change_at:
        return SHIFTED_MEAN
    return NOMINAL_MEANS[station]


def run_trial(scenario: Scenario, seed: int, rollouts: int) -> tuple[list[ForecastRow], list[Measurement]]:
    """Replay one shift, forecast from available data, then score the stored forecasts."""
    streams = np.random.SeedSequence(seed).spawn(len(NOMINAL_MEANS) + 1)
    forecast_rng = np.random.default_rng(streams[0])
    truth_rngs = [np.random.default_rng(stream) for stream in streams[1:]]
    log_variance = log1p(SERVICE_CV**2)

    def truth_duration(station: int, started_at: float) -> float:
        mean = service_mean(station, started_at, scenario.change_at)
        return float(truth_rngs[station].lognormal(log(mean) - log_variance / 2, sqrt(log_variance)))

    truth = Line(CAPACITIES, truth_duration)
    policies = [Calibrator(policy) for policy in Policy]
    measurements: list[Measurement] = []
    predictions = []
    for origin in range(FIRST_ORIGIN, END, HORIZON):
        truth.advance(origin)
        _append_measurements(truth, measurements, scenario.delay)
        visible = observed_before(measurements, origin)
        snapshot = truth.snapshot()
        paired_seed = int(forecast_rng.integers(0, 2**63))
        for policy in policies:
            previous_updates = policy.update_count
            mean = policy.update(origin, visible)
            means = (NOMINAL_MEANS[0], mean, NOMINAL_MEANS[2])
            counts = forecast_completions(snapshot, CAPACITIES, means, HORIZON, rollouts, paired_seed)
            predictions.append((policy.policy, origin, fmean(counts), mean, policy.update_count > previous_updates))

    truth.advance(END)
    _append_measurements(truth, measurements, scenario.delay)
    rows = []
    for policy, origin, estimate, mean, updated in predictions:
        actual = bisect_left(truth.departures, origin + HORIZON) - bisect_left(truth.departures, origin)
        rows.append(ForecastRow(scenario.name, seed, policy, origin, estimate, actual, mean, updated))
    return rows, measurements


def _append_measurements(line: Line, records: list[Measurement], delay: int) -> None:
    records.extend(
        Measurement(item.job_id, item.station, item.started_at, item.finished_at, item.finished_at + delay)
        for item in line.operations[len(records) :]
    )


def _summarize_run(rows: list[ForecastRow], scenario: Scenario, seed: int) -> list[RunMetrics]:
    runs = []
    for policy in Policy:
        policy_rows = [row for row in rows if row.policy == policy]
        after = [row for row in policy_rows if row.origin >= CHANGE_AT]
        recovery = None
        if scenario.change_at is not None:
            close = [abs(row.estimated_mean / SHIFTED_MEAN - 1) <= RECOVERY_TOLERANCE for row in after]
            for index, (first, second) in enumerate(zip(close, close[1:])):
                if first and second:
                    recovery = after[index].origin - scenario.change_at
                    break
        runs.append(
            RunMetrics(
                scenario.name,
                seed,
                policy,
                fmean(row.absolute_error for row in after),
                sum(row.updated for row in policy_rows),
                sum(row.updated for row in policy_rows if row.origin < CHANGE_AT),
                recovery,
            )
        )
    return runs


def _mean_interval(values: list[float]) -> dict[str, float | int]:
    mean = fmean(values)
    half_width = float(student_t.ppf(0.975, len(values) - 1)) * stdev(values) / sqrt(len(values))
    return {"mean": mean, "lower": mean - half_width, "upper": mean + half_width, "n": len(values)}


def _summarize_study(runs: list[RunMetrics]) -> tuple[dict[str, float | int], list[dict]]:
    by_scenario_policy = {
        (name, policy): {run.seed: run for run in runs if run.scenario == name and run.policy == policy}
        for name in SCENARIOS
        for policy in Policy
    }
    delayed = "drift_delayed"
    differences = [
        run.post_change_mae - by_scenario_policy[(delayed, Policy.PERIODIC)][seed].post_change_mae
        for seed, run in by_scenario_policy[(delayed, Policy.THRESHOLD)].items()
    ]

    summaries = []
    for (scenario, policy), group in by_scenario_policy.items():
        recovered = [run.recovery_minutes for run in group.values() if run.recovery_minutes is not None]
        summaries.append(
            {
                "scenario": scenario,
                "policy": str(policy),
                "mae": _mean_interval([run.post_change_mae for run in group.values()]),
                "mean_updates": fmean(run.updates for run in group.values()),
                "mean_pre_change_updates": fmean(run.pre_change_updates for run in group.values()),
                "n_recovered": len(recovered),
                "mean_recovery_minutes_if_recovered": fmean(recovered) if recovered else None,
            }
        )
    return _mean_interval(differences), summaries


def _write_csv(path: Path, rows: Iterable[dict]) -> None:
    iterator = iter(rows)
    first = next(iterator)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(first))
        writer.writeheader()
        writer.writerow(first)
        writer.writerows(iterator)


def run_study(output: Path, profile: str = "full", *, show_progress: bool = False) -> dict:
    """Run the fixed study protocol and export evidence for every paired comparison."""
    from line_calibration.plots import plot_results

    replications, rollouts = PROFILES[profile]
    seeds = list(range(1000, 1000 + replications))
    output.mkdir(parents=True, exist_ok=True)
    forecasts: list[ForecastRow] = []
    runs: list[RunMetrics] = []
    for scenario in SCENARIOS.values():
        if show_progress:
            print(f"{scenario.name}: {replications} shifts, {rollouts} rollouts per forecast", flush=True)
        for seed in seeds:
            rows, measurements = run_trial(scenario, seed, rollouts)
            forecasts.extend(rows)
            runs.extend(_summarize_run(rows, scenario, seed))
            if scenario.name == "drift_delayed" and seed == seeds[0]:
                _write_csv(output / "example-operation-log.csv", (asdict(event) for event in measurements))

    primary, summaries = _summarize_study(runs)
    report = {
        "profile": profile,
        "replications_per_scenario": replications,
        "rollouts_per_forecast": rollouts,
        "seeds": seeds,
        "protocol": {
            "scenarios": [asdict(scenario) for scenario in SCENARIOS.values()],
            "buffer_capacities": list(CAPACITIES),
            "nominal_means": list(NOMINAL_MEANS),
            "shifted_mean": SHIFTED_MEAN,
            "service_cv": SERVICE_CV,
            "first_origin": FIRST_ORIGIN,
            "end": END,
            "horizon": HORIZON,
            "period_minutes": PERIOD_MINUTES,
            "observation_window": WINDOW_SIZE,
            "minimum_new_records": MIN_NEW_RECORDS,
            "shift_threshold": SHIFT_THRESHOLD,
            "recovery_tolerance": RECOVERY_TOLERANCE,
        },
        "primary_comparison": "drift_delayed, threshold MAE minus periodic MAE over origins 360–690",
        "primary": primary,
        "by_policy": summaries,
    }
    _write_csv(output / "forecasts.csv", ({**asdict(row), "absolute_error": row.absolute_error} for row in forecasts))
    _write_csv(output / "run-metrics.csv", (asdict(run) for run in runs))
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    plot_results(output, forecasts, summaries)
    return report
