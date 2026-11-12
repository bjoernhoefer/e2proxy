"""Package structure guards (regressions from the monolith split)."""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = sorted(
    ".".join(p.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
    for p in (ROOT / "e2proxy").rglob("*.py") if p.name != "__main__.py"
)


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_standalone(module, data_dir):
    """Every module must import on its own — catches import-order-dependent cycles."""
    r = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=ROOT,
                       env={**os.environ, "E2PROXY_DATA_DIR": data_dir},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]


def test_package_import_has_no_side_effects():
    r = subprocess.run([sys.executable, "-c", "import e2proxy; print(e2proxy.VERSION)"],
                       cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip()
    assert "Config" not in r.stderr  # no config load / logging at import time


def test_build_id_from_environment():
    env = {**os.environ, "E2PROXY_BUILD_SEQ": "abc1234", "E2PROXY_BUILD_BRANCH": "feature/x"}
    r = subprocess.run([sys.executable, "-c", "import e2proxy; print(e2proxy.INTERNAL_VERSION)"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert r.stdout.strip().endswith("+x.abc1234")
