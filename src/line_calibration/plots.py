"""Render the study's two figures without requiring an interactive display."""

from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from line_calibration.calibration import Policy

if TYPE_CHECKING:
    from line_calibration.study import ForecastRow

STYLE = {
    Policy.FIXED: ("Fixed", "#53616d", "--", "o"),
    Policy.PERIODIC: ("Periodic", "#215b9a", "-", "s"),
    Policy.THRESHOLD: ("Threshold", "#a84624", "-.", "D"),
}
SCENARIO_TITLE = {
    "stable": "Stable",
    "drift": "Slower M2",
    "drift_delayed": "Slower M2 + delayed logs",
}


def plot_results(output: Path, rows: list["ForecastRow"], summaries: list[dict]) -> None:
    """Create forecast-error and calibration trade-off charts."""
    error_figure = Figure(figsize=(10.5, 3.75), layout="constrained")
    FigureCanvasAgg(error_figure)
    error_axes = error_figure.subplots(1, 3, sharey=True)
    origins = sorted({row.origin for row in rows})
    for axis, (scenario, title) in zip(error_axes, SCENARIO_TITLE.items(), strict=True):
        for policy, (label, color, linestyle, _) in STYLE.items():
            errors = [
                fmean(row.absolute_error for row in rows if (row.scenario, row.policy, row.origin) == (scenario, policy, origin))
                for origin in origins
            ]
            axis.plot(origins, errors, label=label, color=color, linestyle=linestyle, linewidth=2)
        if scenario != "stable":
            axis.axvline(360, color="#9ba4ad", linewidth=1)
        axis.set(title=title, xlabel="Forecast origin (min)", xticks=[120, 360, 600])
        axis.grid(axis="y", alpha=0.25)
    error_axes[0].set_ylabel("Mean absolute error (parts per 30 min)")
    error_axes[0].legend(loc="upper left", frameon=False)
    error_figure.suptitle("How forecast errors change during a shift", fontweight="bold")
    error_figure.savefig(output / "forecast-error.png", dpi=180)

    tradeoff = Figure(figsize=(9, 3.75), layout="constrained")
    FigureCanvasAgg(tradeoff)
    tracking_axis, error_axis = tradeoff.subplots(1, 2)
    scenario = "drift_delayed"
    tracking_axis.step([120, 360, 690], [1, 1.4, 1.4], where="post", color="#1e272e", alpha=0.5,
                       linewidth=1.5, label="Simulated M2 mean")
    for policy, (label, color, linestyle, marker) in STYLE.items():
        estimates = [
            fmean(row.estimated_mean for row in rows if (row.scenario, row.policy, row.origin) == (scenario, policy, origin))
            for origin in origins
        ]
        tracking_axis.step(origins, estimates, where="post", label=label, color=color, linestyle=linestyle, linewidth=2)

        summary = next(item for item in summaries if item["scenario"] == scenario and item["policy"] == policy)
        mae = summary["mae"]
        error_axis.errorbar(summary["mean_updates"], mae["mean"],
                            yerr=[[mae["mean"] - mae["lower"]], [mae["upper"] - mae["mean"]]],
                            color=color, marker=marker, markersize=6, capsize=4, label=label)

    tracking_axis.set(title="Tracking the new processing time", xlabel="Forecast origin (min)",
                      ylabel="M2 mean processing time (min)", xticks=[120, 360, 600])
    error_axis.set(title="Forecast error and update frequency", xlabel="Mean updates per shift",
                   ylabel="Post-change MAE (parts per 30 min)")
    error_axis.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    for axis in (tracking_axis, error_axis):
        axis.grid(axis="y", alpha=0.25)
        axis.legend(frameon=False, fontsize=8)
    tradeoff.suptitle("Calibration with a 15-minute operation-log delay", fontweight="bold")
    tradeoff.savefig(output / "calibration-tradeoff.png", dpi=180)
