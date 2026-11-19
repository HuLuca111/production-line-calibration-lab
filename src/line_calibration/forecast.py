"""Simulate future output using only a snapshot and fitted service parameters."""

import numpy as np
from numpy.typing import NDArray
from scipy.stats import gamma

from line_calibration.line import Line, Snapshot, Status

SERVICE_CV = 0.30
GAMMA_SHAPE = 1 / SERVICE_CV**2


def remaining_service_times(
    mean: float,
    elapsed: float,
    survival_quantiles: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Sample the residual of a Gamma duration conditional on exceeding elapsed time."""
    scale = mean / GAMMA_SHAPE
    survival = gamma.sf(elapsed, GAMMA_SHAPE, scale=scale)
    remaining = np.asarray(
        gamma.isf(survival * survival_quantiles, GAMMA_SHAPE, scale=scale) - elapsed
    )
    if not np.all(np.isfinite(remaining) & (remaining > 0)):
        raise ValueError("Could not sample a positive conditional processing time")
    return remaining


def forecast_completions(
    snapshot: Snapshot,
    capacities: tuple[int, ...],
    means: tuple[float, ...],
    horizon: float,
    rollouts: int,
    seed: int,
) -> tuple[int, ...]:
    """Forecast counts in [snapshot.time, snapshot.time + horizon) with shared inputs."""
    residual_seed, *rollout_seeds = np.random.SeedSequence(seed).spawn(rollouts + 1)
    residual_rng = np.random.default_rng(residual_seed)
    residuals = {
        station: remaining_service_times(
            means[station], state.elapsed, 1 - residual_rng.random(rollouts)
        )
        for station, state in enumerate(snapshot.machines)
        if state.status == Status.PROCESSING
    }

    counts = []
    for index, rollout_seed in enumerate(rollout_seeds):
        station_rngs = [np.random.default_rng(stream) for stream in rollout_seed.spawn(len(means))]

        def duration(station: int, _: float) -> float:
            return float(station_rngs[station].gamma(GAMMA_SHAPE, means[station] / GAMMA_SHAPE))

        def remaining(station: int, _: float) -> float:
            return float(residuals[station][index])

        line = Line(
            capacities, duration, snapshot=snapshot, remaining_duration=remaining, record_operations=False
        )
        line.advance(snapshot.time + horizon)
        counts.append(len(line.departures))

    return tuple(counts)
