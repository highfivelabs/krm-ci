#!/usr/bin/env python3
# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
"""Render each KRM repository's caller workflows from ci/krm/callers/ and
ci/krm/repos.toml (docs/krm-ci.md, "Adding a repository").

  ci/krm/sync.py --tag vX.Y.Z [--repos-dir ..] [--only NAME...]   write them
  ci/krm/sync.py --tag vX.Y.Z --check                            fail on drift
  ci/krm/sync.py --tag vX.Y.Z --out DIR                          render into DIR/<name>/

The callers pin a highfivelabs/krm-ci commit by SHA, with its tag as the
comment (`uses: ...@<sha> # vX.Y.Z`), which is what zizmor checks. The tag
is resolved to its commit in this checkout (git rev-parse TAG^{commit});
--ref SHA overrides it (tests, a render before the tag exists). Tags are
never moved; a pin move is a sync with a new tag, reviewed like any
change.

Writes .github/workflows/hfl-{ci,review,live,security}.yml and
.github/dependabot.yml. It does not delete a repository's older workflows;
the migration pull request does (docs/krm-ci.md, "Migration").
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
CALLERS = os.path.join(HERE, "callers")
OUTPUTS = {
    "hfl-ci.yml": ".github/workflows/hfl-ci.yml",
    "hfl-review.yml": ".github/workflows/hfl-review.yml",
    "hfl-live.yml": ".github/workflows/hfl-live.yml",
    "hfl-security.yml": ".github/workflows/hfl-security.yml",
    "dependabot.yml": ".github/dependabot.yml",
}
# Values inserted as YAML scalars: quoted with JSON's escaping, which YAML's
# double-quoted style reads the same way.
QUOTED = {"setup", "lint_setup", "check", "e2e", "sibling", "sibling_ref", "licenses_packages", "live_command", "sweep", "live_cloud"}
RAW = {"ci_ref", "ci_tag", "free_disk", "timeout", "live_timeout", "security_cron"}
FLAGS = {"live"}
# The header every rendered file starts with (callers/*.yml); sync only
# deletes a file that carries it.
RENDERED_MARK = "Rendered by highfivelabs/krm-ci ci/krm/sync.py"
PLACEHOLDER = re.compile(r"@@\{(\w+)\}")
SHA = re.compile(r"^[0-9a-f]{40}$")
CRON = re.compile(r"^[0-9*/,\- ]+$")


def load(path: str) -> list[dict]:
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    profiles = cfg.get("profile", {})
    repos, seen = [], set()
    for r in cfg.get("repo", []):
        name = r.get("name", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or name in seen:
            raise ValueError(f"bad or duplicate repository name {name!r}")
        seen.add(name)
        prof = profiles.get(r.get("profile", ""))
        if prof is None:
            raise ValueError(f"{name}: unknown profile {r.get('profile')!r}")
        merged = {**prof, **{k: v for k, v in r.items() if k != "profile"}}
        for k in QUOTED | FLAGS | (RAW - {"ci_ref", "ci_tag"}):
            if k not in merged:
                raise ValueError(f"{name}: {k} is not set (in the repository or its profile)")
        if not isinstance(merged["live"], bool):
            raise ValueError(f"{name}: live must be true or false")
        if merged["live"] and not merged["live_command"]:
            raise ValueError(f"{name}: live = true needs a live_command")
        if merged["live_cloud"] not in ("gcp", "aws", "azure", "none"):
            raise ValueError(f"{name}: live_cloud must be gcp, aws, azure or none")
        for k in ("timeout", "live_timeout"):
            if not isinstance(merged[k], int) or isinstance(merged[k], bool) or not 5 <= merged[k] <= 360:
                raise ValueError(f"{name}: {k} must be minutes, 5 to 360")
        if not CRON.fullmatch(str(merged["security_cron"])):
            raise ValueError(f"{name}: security_cron {merged['security_cron']!r} is not a cron expression")
        repos.append(merged)
    return repos


def render(template: str, values: dict) -> str:
    def sub(m):
        k = m.group(1)
        if k not in values:
            raise KeyError(f"template placeholder {k} has no value")
        v = values[k]
        if k in QUOTED:
            return json.dumps(str(v))
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    return PLACEHOLDER.sub(sub, template)


def removed(repo: dict) -> list[str]:
    """Rendered files this repository must no longer have."""
    return [] if repo["live"] else [OUTPUTS["hfl-live.yml"]]


def outputs(repo: dict, ref: str, tag: str = "untagged") -> dict[str, str]:
    values = {**repo, "ci_ref": ref, "ci_tag": tag}
    out = {}
    for tmpl, dest in OUTPUTS.items():
        if tmpl == "hfl-live.yml" and not repo["live"]:
            continue
        with open(os.path.join(CALLERS, tmpl), encoding="utf-8") as fh:
            out[dest] = render(fh.read(), values)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sync.py")
    ap.add_argument("--tag", help="the krm-ci tag the callers pin (vX.Y.Z)")
    ap.add_argument("--ref", help="the commit to pin, instead of the tag's")
    ap.add_argument("--repos-dir", default=os.path.join(HERE, "..", "..", ".."))
    ap.add_argument("--config", default=os.path.join(HERE, "repos.toml"))
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.tag and not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag):
        print("sync: --tag must be vX.Y.Z", file=sys.stderr)
        return 2
    if args.tag and not args.ref:
        import subprocess
        r = subprocess.run(["git", "-C", HERE, "rev-parse", "--verify", "--quiet", args.tag + "^{commit}"],
                           capture_output=True, text=True)
        args.ref = r.stdout.strip()
        if r.returncode != 0:
            print(f"sync: no tag {args.tag} in this checkout (git fetch --tags)", file=sys.stderr)
            return 2
    if not args.ref or not SHA.fullmatch(args.ref):
        print("sync: give --tag vX.Y.Z, or --ref as a full 40-character commit SHA", file=sys.stderr)
        return 2
    repos = load(args.config)
    unknown = set(args.only) - {r["name"] for r in repos}
    if unknown:
        print(f"sync: not in repos.toml: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    drift = 0
    for r in repos:
        if args.only and r["name"] not in args.only:
            continue
        root = os.path.join(args.out, r["name"]) if args.out else os.path.join(args.repos_dir, r["name"])
        if not args.out and not os.path.isdir(root):
            print(f"sync: {r['name']}: no checkout at {root}; skipped")
            continue
        for dest, text in outputs(r, args.ref, args.tag or "untagged").items():
            path = os.path.join(root, dest)
            current = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    current = fh.read()
            if current == text:
                continue
            if args.check:
                print(f"sync: {r['name']}: {dest} differs from a render")
                drift += 1
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"sync: {r['name']}: wrote {dest}")
        for dest in removed(r):
            path = os.path.join(root, dest)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                ours = RENDERED_MARK in fh.read()
            if not ours:
                print(f"sync: {r['name']}: {dest} exists but was not rendered by sync; left alone")
                continue
            if args.check:
                print(f"sync: {r['name']}: {dest} should not exist (live = false)")
                drift += 1
                continue
            os.remove(path)
            print(f"sync: {r['name']}: removed {dest} (live = false)")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
