"""FIFO serial lines with blocking after service, with time measured in minutes.

Snapshots contain observable work and service age, never latent completion times.
Truth simulation and conditional forecasts therefore use the same mechanics while
supplying their own service and residual-duration samplers.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import StrEnum
import math

import simpy


class Status(StrEnum):
    IDLE = "idle"
    PROCESSING = "processing"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class MachineState:
    status: Status
    job_id: int | None = None
    elapsed: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    time: float
    machines: tuple[MachineState, ...]
    queues: tuple[tuple[int, ...], ...]
    next_job_id: int


@dataclass(frozen=True)
class Operation:
    job_id: int
    station: int
    started_at: float
    finished_at: float


@dataclass
class Event:
    time: float
    station: int
    job_id: int
    kind: str


class Line:
    """An infinite-supply serial line with finite intermediate FIFO buffers.

    ``capacities[i]`` is the waiting-room capacity between stations i and i+1;
    it excludes both machines. Completed service is recorded before a machine
    waits for room downstream. The final station has an unrestricted sink.

    A processing snapshot requires ``remaining_duration(station, elapsed)``.
    New operations use ``service_duration(station, absolute_start_time)``.
    All times and durations are expressed in minutes. ``advance(t)`` excludes
    events at t, so an operation may have its full service age in a snapshot
    immediately before its completion event. Blocked and idle ages are zero.
    """

    def __init__(
        self,
        capacities: tuple[int, ...],
        service_duration: Callable[[int, float], float],
        *,
        snapshot: Snapshot | None = None,
        remaining_duration: Callable[[int, float], float] | None = None,
        trace: bool = False,
        record_operations: bool = True,
    ) -> None:
        if any(type(capacity) is not int or capacity < 1 for capacity in capacities):
            raise ValueError("Each buffer capacity must be a positive integer.")
        self.capacities = tuple(capacities)
        self._count = len(capacities) + 1
        if snapshot is None:
            snapshot = Snapshot(
                0.0,
                tuple(MachineState(Status.IDLE) for _ in range(self._count)),
                tuple(() for _ in capacities),
                1,
            )
        self._validate_snapshot(snapshot)
        if remaining_duration is None and any(
            machine.status == Status.PROCESSING for machine in snapshot.machines
        ):
            raise ValueError("Processing snapshots require remaining_duration.")

        self._env = simpy.Environment(initial_time=snapshot.time)
        self._service_duration = service_duration
        self._remaining_duration = remaining_duration
        self._trace_enabled = trace
        self._record_operations = record_operations
        self.operations: list[Operation] = []
        self.departures: list[float] = []
        self.trace: list[Event] = []
        self._machines = list(snapshot.machines)
        self._age_reference = [snapshot.time] * self._count
        self._next_job_id = snapshot.next_job_id
        self._buffers = [simpy.Store(self._env, capacity=cap) for cap in capacities]
        for buffer, jobs in zip(self._buffers, snapshot.queues):
            buffer.items.extend(jobs)
        for station in range(self._count):
            self._env.process(self._run_station(station))

    def advance(self, until: float) -> None:
        """Advance up to, but excluding, events at the absolute minute ``until``."""
        if not math.isfinite(until) or until < self._env.now:
            raise ValueError("Advance time must be finite and cannot move backward.")
        if until > self._env.now:
            self._env.run(until=until)

    def snapshot(self) -> Snapshot:
        """Return immutable observable state; no hidden completion times are exposed."""
        now = float(self._env.now)
        machines = tuple(
            MachineState(
                machine.status,
                machine.job_id,
                machine.elapsed + now - self._age_reference[station]
                if machine.status == Status.PROCESSING
                else 0.0,
            )
            for station, machine in enumerate(self._machines)
        )
        return Snapshot(
            now,
            machines,
            tuple(tuple(buffer.items) for buffer in self._buffers),
            self._next_job_id,
        )

    def _run_station(self, station: int) -> Generator:
        initial = self._machines[station]
        if initial.status == Status.BLOCKED:
            assert initial.job_id is not None
            yield from self._transfer(station, initial.job_id, resumed=True)
        elif initial.status == Status.PROCESSING:
            assert initial.job_id is not None
            assert self._remaining_duration is not None
            residual = self._duration(
                self._remaining_duration(station, initial.elapsed), allow_zero=True
            )
            yield from self._process(
                station, initial.job_id, residual, self._env.now - initial.elapsed
            )

        while True:
            if station == 0:
                job_id = self._next_job_id
                self._next_job_id += 1
            else:
                job_id = yield self._buffers[station - 1].get()
            now = float(self._env.now)
            duration = self._duration(self._service_duration(station, now))
            self._machines[station] = MachineState(Status.PROCESSING, job_id)
            self._age_reference[station] = now
            self._emit(station, job_id, "start")
            yield from self._process(station, job_id, duration, now)

    def _process(
        self, station: int, job_id: int, duration: float, started_at: float
    ) -> Generator:
        yield self._env.timeout(duration)
        if self._record_operations:
            self.operations.append(Operation(job_id, station, started_at, float(self._env.now)))
        self._emit(station, job_id, "finish")
        yield from self._transfer(station, job_id)

    def _transfer(self, station: int, job_id: int, *, resumed: bool = False) -> Generator:
        if station == self._count - 1:
            self.departures.append(float(self._env.now))
            self._emit(station, job_id, "departure")
        else:
            buffer = self._buffers[station]
            self._machines[station] = MachineState(Status.BLOCKED, job_id)
            if not resumed and len(buffer.items) == buffer.capacity:
                self._emit(station, job_id, "blocked")
            yield buffer.put(job_id)
            self._emit(station, job_id, "transfer")
        self._machines[station] = MachineState(Status.IDLE)

    def _emit(self, station: int, job_id: int, kind: str) -> None:
        if self._trace_enabled:
            self.trace.append(Event(float(self._env.now), station, job_id, kind))

    @staticmethod
    def _duration(value: float, *, allow_zero: bool = False) -> float:
        value = float(value)
        if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
            raise ValueError("Service duration must be finite and positive; residuals may be zero.")
        return value

    def _validate_snapshot(self, snapshot: Snapshot) -> None:
        if not math.isfinite(snapshot.time) or snapshot.time < 0:
            raise ValueError("Snapshot time must be finite and nonnegative.")
        if len(snapshot.machines) != self._count or len(snapshot.queues) != self._count - 1:
            raise ValueError("Snapshot shape must match the number of stations and buffers.")
        ids: list[int] = []
        for machine in snapshot.machines:
            if machine.status not in Status:
                raise ValueError("Snapshot contains an unknown machine status.")
            if not math.isfinite(machine.elapsed) or machine.elapsed < 0:
                raise ValueError("Snapshot service ages must be finite and nonnegative.")
            if machine.status == Status.IDLE:
                if machine.job_id is not None:
                    raise ValueError("Idle machines cannot hold jobs.")
            elif machine.job_id is None:
                raise ValueError("Working machines must hold a job.")
            else:
                ids.append(machine.job_id)
        if snapshot.machines[-1].status == Status.BLOCKED:
            raise ValueError("The final station cannot be blocked by the unrestricted sink.")
        for capacity, queue in zip(self.capacities, snapshot.queues):
            if len(queue) > capacity:
                raise ValueError("Snapshot queue exceeds buffer capacity.")
            ids.extend(queue)
        if any(type(job) is not int or job < 1 for job in ids) or len(set(ids)) != len(ids):
            raise ValueError("Snapshot job IDs must be unique positive integers.")
        if type(snapshot.next_job_id) is not int or snapshot.next_job_id <= max(ids, default=0):
            raise ValueError("Snapshot next_job_id must exceed every existing job ID.")
