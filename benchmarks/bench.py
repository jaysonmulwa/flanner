"""What flanner costs, per operation.

"Fast" is not a number. This produces numbers, the same way every time, so a
change can be judged against the run before it rather than against a feeling.

Two kinds of thing are measured, and the second is the one people notice:

- **Work**: creating a project, writing a plan, listing them. Bounded by
  SQLite and by writing files.
- **Cold start**: how long `flanner --version` takes to say a word. A CLI is
  judged on this more than on anything else here, because it is paid on
  every single invocation. It is also the number this repository has been
  quoting in the README with nothing measuring it.

Run it:

    python benchmarks/bench.py
    python benchmarks/bench.py --json           # machine-readable
    python benchmarks/bench.py --check          # fail on a regression
    python benchmarks/bench.py --save-baseline  # after a deliberate change

## Why the regression tolerance is so wide

`--check` fails past `TOLERANCE`, which is 3x. That sounds uselessly loose
and is deliberate: CI runners are shared machines whose timings vary by a
factor of two between runs on identical code, and a gate that fails on noise
is switched off within a fortnight.

So it catches what it can honestly catch — an order-of-magnitude regression,
the kind an N+1 or an import moved to module scope produces. Anything
subtler needs a quiet machine and a person comparing the README's table.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flanner.database import get_session, init_database  # noqa: E402
from flanner.server import (  # noqa: E402
    create_plan_file_tool,
    create_project_tool,
    list_plan_files_tool,
)

N_PLANS = 100
BASELINE = Path(__file__).resolve().parent / "baseline.json"
TOLERANCE = 3.0

#: Cold start is measured in whole subprocesses, so it is slow and noisy.
#: Seven is enough for a median to mean something without the suite taking
#: a minute.
COLD_RUNS = 7


def _timed(fn: Any, *args: Any, **kwargs: Any) -> tuple[float, Any]:
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return (time.perf_counter() - start) * 1000, result


def _entry(operation: str, scale: str, samples: list[float]) -> dict[str, Any]:
    return {
        "operation": operation,
        "scale": scale,
        "median_ms": round(statistics.median(samples), 2),
        "p95_ms": round(max(samples), 2),
        "runs": len(samples),
    }


def bench_work() -> list[dict[str, Any]]:
    """Creating a project, writing plans, and listing them back."""
    results = []
    # ignore_cleanup_errors: on Windows the DB file stays locked by lingering
    # sessions, and failing to delete a temp directory is not a benchmark.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp) / "proj"
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=30)  # noqa: S607
        init_database(str(Path(tmp) / "bench.db"))
        get_session()

        ms, project = _timed(
            create_project_tool, name="bench", project_root=str(root), plan_directory=".plans"
        )
        assert not project.get("error"), project
        results.append(_entry("create_project", "one project", [ms]))

        writes = []
        for i in range(N_PLANS):
            ms, result = _timed(
                create_plan_file_tool,
                project_id=project["id"],
                name=f"plan-{i}",
                content=f"# Plan {i}\n\n" + ("lorem ipsum " * 200),
                created_by="bench",
            )
            assert not result.get("error"), result
            writes.append(ms)
        results.append(_entry("create_plan_file", f"n={N_PLANS}, ~2.4 KB each", writes))

        # `limit` is passed explicitly: the tool defaults to 50 because that
        # is a sane page for a model to read, and this measures listing every
        # plan. Left implicit it silently measured half the work and then
        # failed its own assertion — which is how it was broken for a while.
        reads = []
        for _ in range(20):
            ms, files = _timed(list_plan_files_tool, project["id"], limit=N_PLANS)
            reads.append(ms)
        assert len(files) == N_PLANS, f"listed {len(files)} of {N_PLANS}"
        results.append(_entry("list_plan_files", f"{N_PLANS} plans, n=20", reads))
    return results


def bench_cold_start() -> list[dict[str, Any]]:
    """How long the CLI takes to say anything at all.

    Paid on every invocation, so it is the number a user feels. SQLAlchemy
    alone used to cost 630ms of this, for commands that never open a store;
    the lazy wrappers in `cli.py` exist because of this measurement.

    Whole subprocesses, because an import is only cold once per process.

    The installed `flanner` script, not `python -m flanner.cli`. They are not
    the same number — the console script pays entry-point resolution that a
    module invocation does not, and it is the one a user actually types. The
    README quotes the console script, so this has to measure the same thing
    or the table drifts from the code that produced it.
    """
    import shutil

    launcher = shutil.which("flanner")
    prefix = [launcher] if launcher else [sys.executable, "-m", "flanner.cli"]

    results = []
    for command, scale in (
        (["--version"], "no store opened"),
        (["--help"], "no store opened"),
    ):
        samples = []
        for _ in range(COLD_RUNS):
            start = time.perf_counter()
            subprocess.run(  # noqa: S603 - a resolved launcher, a literal argv
                [*prefix, *command],
                capture_output=True,
                check=False,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            samples.append((time.perf_counter() - start) * 1000)
        how = scale if launcher else f"{scale} (no console script; ran as a module)"
        results.append(_entry(f"cold_start {command[0]}", how, samples))
    return results


BENCHMARKS = (bench_work, bench_cold_start)


def check(results: list[dict[str, Any]]) -> int:
    """Compare against the committed baseline. Returns an exit code.

    Never fails on an improvement. A gate that goes red because something got
    faster is a gate people learn to ignore; a real speed-up is recorded with
    --save-baseline, which is deliberate and shows in the diff.
    """
    if not BASELINE.exists():
        print(f"no baseline at {BASELINE.name}; run --save-baseline first", file=sys.stderr)
        return 1

    was = {e["operation"]: e for e in json.loads(BASELINE.read_text(encoding="utf-8"))}
    regressions = []
    for result in results:
        before = was.get(result["operation"])
        if before is None:
            print(f"  new: {result['operation']} ({result['median_ms']} ms)")
            continue
        if result["median_ms"] > before["median_ms"] * TOLERANCE:
            regressions.append(
                f"  {result['operation']}: {before['median_ms']} ms -> "
                f"{result['median_ms']} ms (over {TOLERANCE}x)"
            )

    if regressions:
        print(f"benchmark regressions against {BASELINE.name}:", file=sys.stderr)
        for line in regressions:
            print(line, file=sys.stderr)
        print("\nIf deliberate: python benchmarks/bench.py --save-baseline", file=sys.stderr)
        return 1

    print(f"no regression against {BASELINE.name} (tolerance {TOLERANCE}x)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--check", action="store_true", help="fail on a regression")
    parser.add_argument(
        "--save-baseline", action="store_true", help="record these as the baseline"
    )
    args = parser.parse_args()

    results = [entry for benchmark in BENCHMARKS for entry in benchmark()]

    if args.save_baseline:
        BASELINE.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE.name}")
        return 0
    if args.check:
        return check(results)
    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    width = max(len(r["operation"]) for r in results)
    print(f"{'Operation':<{width}}  {'Median':>10}  {'p95':>10}  Scale")
    print(f"{'-' * width}  {'-' * 10}  {'-' * 10}  {'-' * 28}")
    for r in results:
        print(
            f"{r['operation']:<{width}}  {r['median_ms']:>8.1f} ms  "
            f"{r['p95_ms']:>8.1f} ms  {r['scale']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
