"""Compatibility launcher for the application CLI and benchmark entrypoints."""

from App.Main import Main, RunBenchmark


if __name__ == "__main__":
    try:
        raise SystemExit(Main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130) from None
