#!/usr/bin/env bash
# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
#
# The lint and security tools of the KRM CI baseline (docs/krm-ci.md), at
# pinned versions, each release archive checked against its SHA-256 before
# it is unpacked. The same script runs in the reusable workflows, on an
# offload VM and on a laptop, so all three get the same binaries.
#
#   ci/krm/tools.sh [TOOL...]     install what is missing or at another version
#   ci/krm/tools.sh --dir         print the directory (KRM_TOOLS)
#
# Tools: golangci-lint, gitleaks, actionlint, zizmor (release binaries) and
# govulncheck (go install; the Go checksum database verifies the module).
# Linux and macOS, amd64 and arm64. A version bump changes the version
# and all four checksums together; take them from the release's own
# checksums file (zizmor publishes none: sha256sum the archives).
set -euo pipefail

DIR="${KRM_TOOLS:-$HOME/.local/hfl-krm-tools}"

GOLANGCI_LINT_VERSION=2.15.0
GITLEAKS_VERSION=8.30.1
ACTIONLINT_VERSION=1.7.12
ZIZMOR_VERSION=1.33.1
GOVULNCHECK_VERSION=v1.10.0

# want NAME_OS_ARCH: the archive's SHA-256 (bash 3.2 has no associative arrays).
want() {
  case "$1" in
    golangci-lint_linux_amd64) echo 9b7fe6a6bf6334b98decf8d86b11415ea7889afdabdd1959fdce23af2a9ebcea ;;
    golangci-lint_linux_arm64) echo b4d8f17068b35ee6060cc2f69a16fb48740d706fbba81661c318649c114a6998 ;;
    golangci-lint_darwin_amd64) echo 767b899a28285e88ded2823d5dcc4bc5ef681173eb2d80f029a7b17ee4ece3b8 ;;
    golangci-lint_darwin_arm64) echo d1e409481a8ca846c4bc97e781eb5cb1bd5c23353a0b3977a1cbda1b1b6447d5 ;;
    gitleaks_linux_amd64) echo 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
    gitleaks_linux_arm64) echo e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080 ;;
    gitleaks_darwin_amd64) echo dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709 ;;
    gitleaks_darwin_arm64) echo b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5 ;;
    actionlint_linux_amd64) echo 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 ;;
    actionlint_linux_arm64) echo 325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6 ;;
    actionlint_darwin_amd64) echo 5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644 ;;
    actionlint_darwin_arm64) echo aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f ;;
    zizmor_linux_amd64) echo 3e6a301c7a820a2501915f5a9de8c0486da82bdec7965f068477538a98743d43 ;;
    zizmor_linux_arm64) echo aa487c792a7cbbe86e4f8dccd3b66de33b3b51813c090aaa0842d7ea08a3fc35 ;;
    zizmor_darwin_amd64) echo 8b609d38243c6dd6ddd29bf93d02d886eb507383fe4735b1cde73e66e4de4f29 ;;
    zizmor_darwin_arm64) echo 68516d45723aac36e9c87c47d56e8418009fcb919c6ceb44514d85b9d0c1c011 ;;
    *) echo "tools: no checksum for $1" >&2; return 1 ;;
  esac
}

case "$(uname -s)" in Linux) OS=linux ;; Darwin) OS=darwin ;; *) echo "tools: unsupported OS" >&2; exit 2 ;; esac
case "$(uname -m)" in x86_64 | amd64) ARCH=amd64 ;; aarch64 | arm64) ARCH=arm64 ;; *) echo "tools: unsupported arch" >&2; exit 2 ;; esac

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# fetch NAME VERSION URL MEMBER: download URL, check it, put MEMBER (the
# path of the binary inside the archive) at $DIR/NAME, and record VERSION.
fetch() {
  local name="$1" version="$2" url="$3" member="$4"
  local stamp="$DIR/.$name.version"
  if [[ -x "$DIR/$name" && -f "$stamp" && "$(cat "$stamp")" == "$version" ]]; then return; fi
  local want
  want="$(want "${name}_${OS}_${ARCH}")"
  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN
  curl -fsSL --retry 3 -o "$tmp/a.tgz" "$url"
  local got
  got="$(sha256 "$tmp/a.tgz")"
  if [[ "$got" != "$want" ]]; then
    echo "tools: $name $version: checksum $got, want $want; refusing it" >&2
    exit 1
  fi
  tar -xzf "$tmp/a.tgz" -C "$tmp" "$member"
  install -m 0755 "$tmp/$member" "$DIR/$name"
  echo "$version" >"$stamp"
  echo "tools: $name $version"
}

golangci-lint() {
  local a="golangci-lint-${GOLANGCI_LINT_VERSION}-${OS}-${ARCH}"
  fetch golangci-lint "$GOLANGCI_LINT_VERSION" \
    "https://github.com/golangci/golangci-lint/releases/download/v${GOLANGCI_LINT_VERSION}/${a}.tar.gz" "${a}/golangci-lint"
}

gitleaks() {
  local arch="$ARCH"; [[ "$arch" == amd64 ]] && arch=x64
  fetch gitleaks "$GITLEAKS_VERSION" \
    "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${OS}_${arch}.tar.gz" gitleaks
}

actionlint() {
  fetch actionlint "$ACTIONLINT_VERSION" \
    "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_${OS}_${ARCH}.tar.gz" actionlint
}

zizmor() {
  local t
  case "${OS}_${ARCH}" in
    linux_amd64) t=x86_64-unknown-linux-gnu ;; linux_arm64) t=aarch64-unknown-linux-gnu ;;
    darwin_amd64) t=x86_64-apple-darwin ;; darwin_arm64) t=aarch64-apple-darwin ;;
  esac
  fetch zizmor "$ZIZMOR_VERSION" \
    "https://github.com/zizmorcore/zizmor/releases/download/v${ZIZMOR_VERSION}/zizmor-${t}.tar.gz" zizmor
}

# govulncheck loads packages with the go/packages of the Go it was built
# with, so a binary built with an older Go cannot read a module that needs a
# newer one ("application built with go1.25"). The stamp holds the Go
# version too: a different Go rebuilds it.
govulncheck() {
  local stamp="$DIR/.govulncheck.version" want
  want="${GOVULNCHECK_VERSION} $(go env GOVERSION)"
  if [[ -x "$DIR/govulncheck" && -f "$stamp" && "$(cat "$stamp")" == "$want" ]]; then return; fi
  GOBIN="$DIR" GOFLAGS=-mod=mod go install "golang.org/x/vuln/cmd/govulncheck@${GOVULNCHECK_VERSION}"
  echo "$want" >"$stamp"
  echo "tools: govulncheck $want"
}

ALL=(golangci-lint gitleaks actionlint zizmor govulncheck)

case "${1:-}" in
  --dir) echo "$DIR"; exit 0 ;;
  --pins)
    echo "golangci-lint-${GOLANGCI_LINT_VERSION}_gitleaks-${GITLEAKS_VERSION}_actionlint-${ACTIONLINT_VERSION}_zizmor-${ZIZMOR_VERSION}_govulncheck-${GOVULNCHECK_VERSION}"
    exit 0 ;;
esac

mkdir -p "$DIR"
tools=("$@")
[[ ${#tools[@]} -eq 0 ]] && tools=("${ALL[@]}")
for t in "${tools[@]}"; do
  case " ${ALL[*]} " in *" $t "*) "$t" ;; *) echo "tools: unknown tool $t (${ALL[*]})" >&2; exit 2 ;; esac
done
