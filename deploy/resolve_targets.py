#!/usr/bin/env python3
"""Resolve rollout targets for build-deploy.yml.

usage: resolve_targets.py <all|none|host1,host2> [targets.json]
Prints ``matrix=<json list>`` (GITHUB_OUTPUT format). Validates every entry so a
typo in deploy/targets.json fails the run before anything touches a Pi.
"""
import json
import pathlib
import re
import sys

NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
USER = re.compile(r"^[a-z_][a-z0-9_-]*$")


def resolve(requested, targets):
    for t in targets:
        if not (NAME.match(t.get("host", "")) and NAME.match(t.get("runner_label", ""))
                and USER.match(t.get("deploy_user", ""))):
            raise ValueError(f"invalid target entry: {t}")
    hosts = [t["host"] for t in targets]
    if len(set(hosts)) != len(hosts):
        raise ValueError("duplicate host in targets")
    req = requested.strip().lower()
    if req == "none":
        return []
    if req in ("", "all"):
        return [t for t in targets if t.get("enabled", True)]
    wanted = [w.strip() for w in req.split(",") if w.strip()]
    unknown = sorted(set(wanted) - set(hosts))
    if unknown:
        raise ValueError(f"unknown host(s) {unknown}; known: {hosts}")
    return [t for t in targets if t["host"] in wanted]


def main(argv):
    path = pathlib.Path(argv[2] if len(argv) > 2 else pathlib.Path(__file__).with_name("targets.json"))
    try:
        chosen = resolve(argv[1] if len(argv) > 1 else "all", json.loads(path.read_text())["targets"])
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    keys = ("host", "runner_label", "deploy_user")
    print("matrix=" + json.dumps([{k: t[k] for k in keys} for t in chosen], separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
