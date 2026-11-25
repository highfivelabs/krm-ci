#!/usr/bin/env bash
# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
#
# Everything that must hold before a commit, and what check.yml runs on
# every pull request: the gate and sync tests, a render of every
# repository's callers, the shared Taskfile parses, shellcheck, actionlint
# and zizmor. The lint tools come from ci/krm/tools.sh (task krm:tools);
# the package manager supplies shellcheck (GitHub's runners have it) and
# task (check.yml installs it).
set -euo pipefail
cd "$(dirname "$0")/.."

tools="$(ci/krm/tools.sh --dir)"
export PATH="${tools}:${PATH}"

python3 -m unittest discover -s ci/krm -p '*_test.py'

# The shared Taskfiles parse.
task --taskfile taskfiles/go.yml --list-all >/dev/null
task --taskfile taskfiles/licenses.yml --list-all >/dev/null
# krm-ci's own files carry no copyleft or source-available licence header.
task --taskfile taskfiles/licenses.yml headers

# Every repository's callers render at this commit.
out="$(mktemp -d)"
trap 'rm -rf "${out}"' EXIT
ci/krm/sync.py --ref "$(git rev-parse HEAD)" --out "${out}" >/dev/null

shellcheck hack/*.sh ci/krm/*.sh ci/krm/review/*.sh
actionlint
# The online audits (impostor commits, known-vulnerable actions) need
# GH_TOKEN; without it zizmor runs the offline ones.
zizmor --config ci/krm/zizmor.yml --min-severity medium .github
