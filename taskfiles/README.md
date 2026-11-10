# Shared Taskfiles

The [Task](https://taskfile.dev) tasks that repeat across the three
extension repositories (`config-connector-extensions`, `ack-extensions`,
`aso-extensions`). Each repository includes this file with Task's remote
includes, pinned to a tag of this repository and the file's checksum. Nothing
is copied or synced (the builder, 2026-11-10, decision F5).

| Task | What it does | Vars |
| --- | --- | --- |
| `go:install-tool` | `go install` a pinned tool into `BIN`, once (internal) | `BIN`, `NAME`, `PKG` |
| `go:fmt-check` | fail if a Go file under `DIRS` needs gofmt | `DIRS` |
| `go:committed` | fail if generated output under `PATHS` differs from the index or is untracked | `PATHS`, `WHAT`, `HINT` |
| `go:test-envtest` | run `go test` (or `CMD`) with envtest's assets found, the download retried; fail rather than skip | `BIN`, `ENVTEST_K8S_VERSION`, `PKGS`, `TIMEOUT` or `CMD` |

Only tasks all the repositories run belong here. A task that calls a
repository's own scripts (`upstream`, `coverage`, `upstream:yaml-check`)
stays in that repository.

## Including it

```yaml
includes:
  go:
    taskfile: https://github.com/highfivelabs/factory.git//taskfiles/go.yml?ref=taskfiles/v1.0.0
    checksum: <sha256sum of taskfiles/go.yml at that tag>
    vars:
      BIN: '{{.ROOT_DIR}}/bin'
```

and a `.taskrc.yml` beside the Taskfile:

```yaml
remote:
  cache-expiry: 8760h
```

- **Pin.** `ref` is a tag (`taskfiles/vX.Y.Z`); a tag is never moved.
  `checksum` is the SHA-256 of the file. Task refuses a file that does not
  match it, and a pinned checksum also makes the include trusted, so no
  prompt appears and `--yes` is not needed.
- **Task version.** Remote Taskfiles are released in Task 3.54 (`task:
  Experiment "REMOTE_TASKFILES" is released and no longer needs to be
  enabled`); `TASK_X_REMOTE_TASKFILES` is not needed. Older Task needs it
  set to `1`.
- **Fetching.** Task clones with the system `git`, so the clone uses the
  caller's own GitHub credentials (`gh auth setup-git`, an SSH key). This
  repository is private: a machine with no credentials cannot fetch it.
- **Cache.** The file is cached in `.task/remote/` of the including
  repository (git-ignored). `cache-expiry: 8760h` keeps Task from fetching
  again while the cached copy is fresh. A new tag is a new URL, so a pin move
  fetches once. `task --download` refetches; `task --offline` never fetches.
- **Offload.** Offload VMs have no GitHub credentials. `offload run` fills the
  cache on the laptop first (`task --list`) and packs `.task/remote/` with the
  checkout, so the VM reads the cached copy; the Ubuntu box gets it by rsync.

## Changing a task

1. Change `taskfiles/go.yml` here, in a pull request; `task check` parses it.
2. After the merge, tag the merge commit: `git tag taskfiles/vX.Y.Z && git
   push origin taskfiles/vX.Y.Z` (a new minor for a new task or variable, a
   new major for a change a caller must follow).
3. In each extension repository, move `ref` and `checksum`
   (`shasum -a 256 taskfiles/go.yml` at the tag) in one pull request, and run
   its `task check`.

| Tag | Change |
| --- | --- |
| `taskfiles/v1.0.0` | The four tasks above, from `ack-extensions` and `config-connector-extensions` (`taskfiles/go.yml`) and `aso-extensions` (`taskfiles/common.yml`). `test-envtest` takes both `CMD` (config-connector-extensions) and `TIMEOUT` (ack-extensions); `committed` takes `HINT` (aso-extensions' `diff-clean`). |
