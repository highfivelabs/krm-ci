#!/usr/bin/env bash
# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
#
# Everything that must hold before a commit, and what check.yml runs on
# every pull request: the gate and sync tests, a render of every
# repository's callers, shellcheck, actionlint and zizmor. The lint tools
# come from ci/krm/tools.sh (task krm:tools); shellcheck from the package
# manager (it is on GitHub's runners).
set -euo pipefail
cd "$(dirname "$0")/.."

tools="$(ci/krm/tools.sh --dir)"
export PATH="${tools}:${PATH}"

python3 -m unittest discover -s ci/krm -p '*_test.py'

# Every repository's callers render at this commit.
out="$(mktemp -d)"
trap 'rm -rf "${out}"' EXIT
ci/krm/sync.py --ref "$(git rev-parse HEAD)" --out "${out}" >/dev/null

shellcheck hack/*.sh ci/krm/*.sh ci/krm/review/*.sh
actionlint
# The online audits (impostor commits, known-vulnerable actions) need
# GH_TOKEN; without it zizmor runs the offline ones.
zizmor --config ci/krm/zizmor.yml --min-severity medium .github
