"""Portable subprocess support for the synthetic Django consumer."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_consumer(
    settings_module: str,
    source: str,
    *,
    database: Path | None = None,
    template_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Python against one isolated consumer capability profile.

    Args:
        settings_module: Import path for the consumer settings variant.
        source: Python source executed after environment isolation.
        database: Optional temporary SQLite path.
        template_dir: Optional temporary filesystem-template root.

    Returns:
        Completed child process with captured text output.
    """
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": settings_module,
        "PYTHONPATH": os.pathsep.join((str(PROJECT_ROOT / "src"), str(PROJECT_ROOT))),
    }
    if database is not None:
        environment["DJ_HYPERVIEW_CONSUMER_DB"] = str(database)
    if template_dir is not None:
        environment["DJ_HYPERVIEW_CONSUMER_TEMPLATES"] = str(template_dir)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
