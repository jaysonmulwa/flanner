"""The benchmark has to still run, and the gate has to still bite.

A benchmark nobody runs rots, and a rotted one is worse than none: it gets
quoted from the README long after it stopped measuring the thing it names.
This repository has the scar — the listing benchmark asserted 100 plans
against a tool that pages at 50, and was broken long enough that nobody
noticed.

No timings are asserted. Those are machine-dependent, and a test that fails
because a laptop was busy is a test people delete.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_benchmark_still_runs() -> None:
    finished = subprocess.run(  # noqa: S603 - this interpreter, a path from __file__
        [sys.executable, str(ROOT / "benchmarks" / "bench.py"), "--json"],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )
    assert finished.returncode == 0, f"benchmarks/bench.py failed:\n{finished.stderr}"

    results = json.loads(finished.stdout)
    for entry in results:
        # A zero median means the operation was optimised into nothing, which
        # in a benchmark means it stopped measuring rather than got fast.
        assert entry["median_ms"] > 0, f"{entry['operation']} measured no time at all"
        assert entry["scale"], f"{entry['operation']} does not say at what size"


def test_the_baseline_covers_every_benchmark() -> None:
    """A benchmark with no baseline entry is one the gate silently skips.

    `--check` reports a new operation and passes, which is right for the run
    that adds one and wrong as a standing state.
    """
    from benchmarks.bench import BASELINE

    recorded = {e["operation"] for e in json.loads(BASELINE.read_text(encoding="utf-8"))}
    finished = subprocess.run(  # noqa: S603 - this interpreter, a path from __file__
        [sys.executable, str(ROOT / "benchmarks" / "bench.py"), "--json"],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )
    produced = {e["operation"] for e in json.loads(finished.stdout)}
    missing = produced - recorded
    assert not missing, (
        f"{sorted(missing)} have no baseline entry, so the gate skips them. "
        f"Record one: python benchmarks/bench.py --save-baseline"
    )


def test_the_gate_fails_on_a_regression(tmp_path: Path) -> None:
    """A gate that cannot fail is worse than no gate.

    Checked by moving the baseline rather than by slowing the code down,
    which is the only way to exercise this deterministically.
    """
    from benchmarks import bench

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps([{"operation": "create_project", "median_ms": 0.0001}]))

    original = bench.BASELINE
    try:
        bench.BASELINE = baseline
        code = bench.check([{"operation": "create_project", "median_ms": 5.0, "scale": "x"}])
    finally:
        bench.BASELINE = original
    assert code == 1, "a 50,000x regression passed the gate"


def test_cold_start_measures_what_the_readme_quotes() -> None:
    """The console script, not `python -m`. They are not the same number.

    The README's table says `flanner --version`, so the benchmark has to
    measure the launcher a person types or the table drifts from the code
    that produced it.
    """
    import inspect

    from benchmarks import bench

    source = inspect.getsource(bench.bench_cold_start)
    assert 'shutil.which("flanner")' in source
