"""Reproducible micro-benchmark for flanner's core operations.

Run: python benchmarks/bench.py
Uses a throwaway temp database and project directory; prints medians.
"""

import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from flanner.database import get_session, init_database
from flanner.server import create_plan_file_tool, create_project_tool, list_plan_files_tool

N_PLANS = 100


def timed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return (time.perf_counter() - start) * 1000, result


def main() -> None:
    # ignore_cleanup_errors: on Windows the DB file stays locked by lingering sessions
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp) / "proj"
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=30)
        init_database(str(Path(tmp) / "bench.db"))
        get_session()

        ms, project = timed(
            create_project_tool, name="bench", project_root=str(root), plan_directory=".plans"
        )
        assert not project.get("error"), project
        print(f"create_project:        {ms:8.1f} ms")

        create_times = []
        for i in range(N_PLANS):
            ms, result = timed(
                create_plan_file_tool,
                project_id=project["id"],
                name=f"plan-{i}",
                content=f"# Plan {i}\n\n" + ("lorem ipsum " * 200),
                created_by="bench",
            )
            assert not result.get("error"), result
            create_times.append(ms)
        print(
            f"create_plan_file:      {statistics.median(create_times):8.1f} ms median "
            f"(n={N_PLANS}, ~2.4 KB body each)"
        )

        list_times = []
        for _ in range(20):
            ms, files = timed(list_plan_files_tool, project["id"])
            list_times.append(ms)
        assert len(files) == N_PLANS
        print(
            f"list_plan_files:       {statistics.median(list_times):8.1f} ms median "
            f"({N_PLANS} plans, n=20)"
        )


if __name__ == "__main__":
    main()
