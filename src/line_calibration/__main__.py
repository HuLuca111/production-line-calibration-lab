"""Run the two-machine demonstration or the calibration experiment."""

import argparse
from pathlib import Path

from line_calibration.line import Line


def demo() -> None:
    """Show the events behind the first three departures of a deterministic line."""
    line = Line((1,), lambda station, _: (1.0, 2.0)[station], trace=True)
    line.advance(7.001)
    print("Time (min)  Station  Part  Event")
    for event in line.trace:
        print(f"{event.time:>10.2f}  M{event.station + 1:<7} {event.job_id:>4}  {event.kind}")
    departures = ", ".join(f"{time:g}" for time in line.departures)
    print(f"\nDepartures (min): {departures}")


def main() -> None:
    """Parse CLI arguments and run the requested workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="trace a deterministic two-machine line")
    study = commands.add_parser("study", help="compare calibration strategies and export results")
    study.add_argument("--profile", choices=("quick", "full"), default="full")
    study.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()

    if args.command == "demo":
        demo()
        return

    from line_calibration.study import run_study

    report = run_study(args.out, args.profile, show_progress=True)
    contrast = report["primary"]
    print(
        f"Threshold minus periodic MAE: {contrast['mean']:+.3f} parts per 30 min "
        f"(95% CI {contrast['lower']:+.3f} to {contrast['upper']:+.3f}; n={contrast['n']})"
    )
    print(f"Results written to {args.out.resolve()}")


if __name__ == "__main__":
    main()
