"""Rollout target resolution (multi-Pi matrix) and the shipped targets file."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))
from resolve_targets import resolve  # noqa: E402

T = [{"host": "pi-a", "runner_label": "deploy-e2proxy", "deploy_user": "pi"},
     {"host": "pi-b", "runner_label": "deploy-e2proxy-pi-b", "deploy_user": "pi", "enabled": False}]


def test_all_skips_disabled_but_explicit_selection_wins():
    assert [t["host"] for t in resolve("all", T)] == ["pi-a"]
    assert [t["host"] for t in resolve("pi-b", T)] == ["pi-b"]
    assert resolve("none", T) == []


@pytest.mark.parametrize("requested,targets", [
    ("pi-x", T),
    ("all", T + [dict(T[0])]),                                   # duplicate host
    ("all", [dict(T[0], deploy_user="pi; rm -rf /")]),           # injection guard
])
def test_invalid_input_rejected(requested, targets):
    with pytest.raises(ValueError):
        resolve(requested, targets)


def test_shipped_targets_file_is_valid():
    targets = json.loads((ROOT / "deploy" / "targets.json").read_text())["targets"]
    assert resolve("all", targets)
