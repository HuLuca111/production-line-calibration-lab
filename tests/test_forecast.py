import numpy as np

from line_calibration.forecast import forecast_completions, remaining_service_times
from line_calibration.line import MachineState, Snapshot, Status


def test_remaining_time_is_conditioned_on_surviving_to_elapsed_time():
    quantiles = np.array([0.1, 0.5, 0.9])
    remaining = remaining_service_times(mean=1.0, elapsed=2.0, survival_quantiles=quantiles)
    assert np.all(remaining > 0)
    assert np.all(np.diff(remaining) < 0)
    assert remaining[1] < 0.2


def test_forecast_is_reproducible_and_leaves_snapshot_unchanged():
    snapshot = Snapshot(
        120.0,
        (MachineState(Status.BLOCKED, 3), MachineState(Status.PROCESSING, 1, 0.2)),
        ((2,),),
        4,
    )
    first = forecast_completions(snapshot, (1,), (1.0, 2.0), 10.0, 8, seed=42)
    second = forecast_completions(snapshot, (1,), (1.0, 2.0), 10.0, 8, seed=42)
    assert first == second
    assert len(first) == 8
    assert all(count > 0 for count in first)
    assert snapshot.queues == ((2,),)
