"""Behavioral checks for the shared blocking-after-service line mechanics."""

import random

import pytest

from line_calibration.line import Line, MachineState, Snapshot, Status


def test_deterministic_departures_and_operation_times():
    line = Line((1,), lambda station, time: (1.0, 2.0)[station], trace=True)
    line.advance(7.1)

    assert line.departures == [3.0, 5.0, 7.0]
    first = next(op for op in line.operations if op.station == 1)
    assert (first.job_id, first.started_at, first.finished_at) == (1, 1.0, 3.0)


def test_completed_job_blocks_upstream_until_space_becomes_available():
    line = Line((1,), lambda station, time: (1.0, 5.0)[station], trace=True)
    line.advance(4.5)
    state = line.snapshot()

    assert state.machines[0].status == Status.BLOCKED
    assert state.machines[0].job_id == 3
    assert state.queues == ((2,),)
    assert any(op.job_id == 3 and op.finished_at == 3.0 for op in line.operations)
    line.advance(6.1)
    assert any(e.kind == "transfer" and e.job_id == 3 and e.time == 6.0 for e in line.trace)
    assert [e.time for e in line.trace if e.kind == "blocked"] == [3.0]


def test_snapshots_preserve_capacity_job_identity_and_conservation():
    line = Line((2, 1), lambda station, time: (0.7, 1.3, 2.1)[station])
    for moment in (0.0, 0.2, 2.0, 5.5, 17.2, 51.1):
        line.advance(moment)
        state = line.snapshot()
        ids = [m.job_id for m in state.machines if m.job_id is not None]
        ids.extend(job for queue in state.queues for job in queue)
        assert len(set(ids)) == len(ids)
        assert all(len(queue) <= cap for queue, cap in zip(state.queues, (2, 1)))
        assert len(ids) + len(line.departures) == state.next_job_id - 1


def test_advance_excludes_events_at_exact_target_and_can_continue():
    line = Line((1,), lambda station, time: (1.0, 2.0)[station])
    line.advance(1.0)
    state = line.snapshot()
    assert state.machines[0] == MachineState(Status.PROCESSING, 1, 1.0)
    assert state.machines[1].status == Status.IDLE
    assert line.operations == []
    line.advance(3.0)
    assert line.departures == []
    line.advance(3.01)
    assert line.departures == [3.0]


def test_processing_snapshot_resumes_residual_without_new_service_draw():
    observed = Snapshot(10.0, (MachineState(Status.PROCESSING, 4, 2.0),), (), 5)
    calls = []

    def service(station, time):
        calls.append((station, time))
        return 4.0

    resumed = Line((), service, snapshot=observed, remaining_duration=lambda station, elapsed: 3.0)
    resumed.advance(13.1)
    assert resumed.departures == [13.0]
    assert calls == [(0, 13.0)]
    assert resumed.operations[0].started_at == 8.0


def test_snapshot_resume_preserves_blocked_job_and_fifo_order():
    original = Line((1,), lambda station, time: (1.0, 5.0)[station])
    original.advance(4.5)
    observed = original.snapshot()
    resumed = Line(
        (1,),
        lambda station, time: (1.0, 5.0)[station],
        snapshot=observed,
        remaining_duration=lambda station, elapsed: (1.0, 5.0)[station] - elapsed,
        trace=True,
    )
    original.advance(18.1)
    resumed.advance(18.1)
    assert resumed.departures == original.departures
    assert resumed.snapshot() == original.snapshot()
    assert not any(op.job_id == 3 and op.station == 0 for op in resumed.operations)
    assert [e.job_id for e in resumed.trace if e.kind == "departure"] == [1, 2, 3]


@pytest.mark.parametrize("origin", [3.0, 5.0, 7.5])
def test_three_station_resume_preserves_departures_across_event_boundaries(origin):
    durations = (1.0, 1.0, 2.0)

    def duration(station, _):
        return durations[station]

    original = Line((1, 1), duration)
    original.advance(origin)
    resumed = Line(
        (1, 1),
        duration,
        snapshot=original.snapshot(),
        remaining_duration=lambda station, elapsed: durations[station] - elapsed,
    )
    original.advance(20.01)
    resumed.advance(20.01)
    assert resumed.departures == [time for time in range(4, 21, 2) if time >= origin]
    assert resumed.snapshot() == original.snapshot()


def test_exponential_two_station_rate_matches_known_finite_buffer_result():
    rng = random.Random(20261119)
    line = Line((1,), lambda station, time: rng.expovariate(1.0), record_operations=False)
    line.advance(30_000.0)
    observed_per_hour = sum(time >= 1000.0 for time in line.departures) / 29_000.0 * 60.0
    assert observed_per_hour == pytest.approx(45.0, rel=0.02)
    assert line.operations == []


def test_processing_snapshot_requires_explicit_residual_sampler():
    observed = Snapshot(2.0, (MachineState(Status.PROCESSING, 1, 1.0),), (), 2)
    with pytest.raises(ValueError, match="remaining_duration"):
        Line((), lambda station, time: 1.0, snapshot=observed)


@pytest.mark.parametrize("capacity", [0, -1, 1.5, True])
def test_rejects_invalid_buffer_capacity(capacity):
    with pytest.raises(ValueError, match="capacity"):
        Line((capacity,), lambda station, time: 1.0)


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan"), float("inf")])
def test_rejects_invalid_service_duration(duration):
    line = Line((), lambda station, time: duration)
    with pytest.raises(ValueError, match="duration"):
        line.advance(1.0)
