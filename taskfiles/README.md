# Shared Taskfiles

The [Task](https://taskfile.dev) tasks that repeat across the three
extension repositories (`config-connector-extensions`, `ack-extensions`,
`aso-extensions`). Each repository includes this file with Task's remote
includes, pinned to a tag of this repository and the file's checksum. Nothing
is copied or synced (the builder, 2026-11-10, decision F5). It moved here
from the private factory repository (krm-ci v1.1.0), so a pull request from
a fork can fetch it with no credentials.

| Task | What it does | Vars |
| --- | --- | --- |
| `go:install-tool` | `go install` a pinned tool into `BIN`, once (internal) | `BIN`, `NAME`, `PKG` |
| `go:fmt-check` | fail if a Go file under `DIRS` needs gofmt | `DIRS` |
| `go:committed` | fail if generated output under `PATHS` differs from the index or is untracked | `PATHS`, `WHAT`, `HINT` |
| `go:test-envtest` | run `go test` (or `CMD`) with envtest's assets found, the download retried; fail rather than skip | `BIN`, `ENVTEST_K8S_VERSION`, `PKGS`, `TIMEOUT` or `CMD` |
| `licenses:check` | `licenses:headers`, then `licenses:deps` (`taskfiles/licenses.yml`) | as below |
| `licenses:headers` | fail if a file outside `third_party/` and `vendor/` carries an MPL, GPL, AGPL, LGPL, SSPL or BUSL licence header | `EXCLUDE` |
| `licenses:deps` | fail if a Go module linked into `PKGS` is under a licence outside the accepted set (permissive, or MPL-2.0 linked unmodified) and not reviewed in `ALLOW_FILE` | `PKGS` (default `./...`), `ALLOW_FILE` (default `.licenses-allow.txt`) |

`licenses.yml` is included like `go.yml`, under the name `licenses`, and
`krm-ci.yml`'s test job runs it too, so the vendor repositories and the
runtime (which have no Taskfile) get it from CI alone.

Only tasks all the repositories run belong here. A task that calls a
repository's own scripts (`upstream`, `coverage`, `upstream:yaml-check`)
stays in that repository.

## Including it

```yaml
includes:
  go:
    taskfile: https://github.com/highfivelabs/krm-ci.git//taskfiles/go.yml?ref=v1.1.0
    checksum: <sha256sum of taskfiles/go.yml at that tag>
    vars:
      BIN: '{{.ROOT_DIR}}/bin'
```

and a `.taskrc.yml` beside the Taskfile:

```yaml
remote:
  cache-expiry: 8760h
```

- **Pin.** `ref` is a krm-ci release tag (`vX.Y.Z`, the same tags the
  caller workflows pin); a tag is never moved.
  `checksum` is the SHA-256 of the file. Task refuses a file that does not
  match it, and a pinned checksum also makes the include trusted, so no
  prompt appears and `--yes` is not needed.
- **Task version.** Remote Taskfiles are released in Task 3.54 (`task:
  Experiment "REMOTE_TASKFILES" is released and no longer needs to be
  enabled`); `TASK_X_REMOTE_TASKFILES` is not needed. Older Task needs it
  set to `1`.
- **Fetching.** Task clones with the system `git`. This repository is
  public, so no credentials are needed: a fork's pull request, an offload
  VM and a fresh laptop all fetch it.
- **Cache.** The file is cached in `.task/remote/` of the including
  repository (git-ignored). `cache-expiry: 8760h` keeps Task from fetching
  again while the cached copy is fresh. A new tag is a new URL, so a pin move
  fetches once. `task --download` refetches; `task --offline` never fetches.
- **Offload.** `offload run` still packs `.task/remote/` with the checkout,
  so a VM reads the cached copy without fetching.

## Changing a task

1. Change `taskfiles/go.yml` here, in a pull request; `task check` parses it.
2. After the merge, tag a krm-ci release (`vX.Y.Z`: a new minor for a new
   task or variable, a new major for a change a caller must follow).
3. In each extension repository, move `ref` and `checksum`
   (`shasum -a 256 taskfiles/go.yml` at the tag) in one pull request, and run
   its `task check`.

| Tag | Change |
| --- | --- |
| factory `taskfiles/v1.0.0` | The four tasks above, from `ack-extensions` and `config-connector-extensions` (`taskfiles/go.yml`) and `aso-extensions` (`taskfiles/common.yml`). `test-envtest` takes both `CMD` (config-connector-extensions) and `TIMEOUT` (ack-extensions); `committed` takes `HINT` (aso-extensions' `diff-clean`). |
| krm-ci `v1.1.0` | Moved here from factory, unchanged but for the include URL in this file's header. |
| krm-ci `v1.2.0` | `licenses.yml`: `licenses:check`, `licenses:headers`, `licenses:deps` (HFL-714). |
| krm-ci `v1.2.1` | Unchanged (krm-ci.yml judges a push on the commits it pushed). |
| krm-ci `v1.2.2` | Unchanged (krm-ci.yml's lint job and krm-security.yml run `lint-setup-command`). |
| krm-ci `v1.2.3` | Unchanged (govulncheck looks at `govulncheck-packages`, the binaries). |
