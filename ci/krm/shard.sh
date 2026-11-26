#!/usr/bin/env bash
# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
#
# This test shard's Go packages, for a repository's check command when
# krm-ci.yml runs its test job as KRM_SHARDS shards (the `test-shards`
# input; docs/krm-ci.md, "Shards"):
#
#   ci/krm/shard.sh [go list patterns, default ./...]
#
# prints the import paths of the packages that have tests, sorted, and keeps
# every KRM_SHARDS-th one starting at KRM_SHARD (1-based). Round robin over
# the sorted list spreads neighbours (pkg/controller/direct/a, .../b, ...),
# which tend to cost alike, across the shards. With KRM_SHARDS unset or 1
# it prints them all. In the job, krm-ci is checked out at
# ${KRM_CI_DIR} (.hfl-ci), so: "${KRM_CI_DIR}/ci/krm/shard.sh" ./...
set -euo pipefail

n="${KRM_SHARDS:-1}"
i="${KRM_SHARD:-1}"
if ! [[ "${n}" =~ ^[1-9][0-9]*$ && "${i}" =~ ^[1-9][0-9]*$ ]] || (( i > n )); then
  echo "shard.sh: KRM_SHARD=${i} of KRM_SHARDS=${n} is not a shard" >&2
  exit 2
fi
(( $# )) || set -- ./...

pkgs="$(go list -f '{{if or .TestGoFiles .XTestGoFiles}}{{.ImportPath}}{{end}}' "$@")"
sed '/^$/d' <<<"${pkgs}" | LC_ALL=C sort | awk -v n="${n}" -v i="${i}" '(NR - 1) % n == i - 1'
