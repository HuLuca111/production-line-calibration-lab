# Event-Driven Calibration of a Manufacturing Line Simulator

Run the deterministic two-machine demonstration or reproduce the calibration experiment
with Python 3.12 and uv.

~~~sh
uv sync --locked
uv run --locked pytest -q
uv run --locked python -m line_calibration demo
uv run --locked python -m line_calibration study --profile quick --out results/quick
~~~

The demo's first three departures occur at 3, 5 and 7 minutes.
