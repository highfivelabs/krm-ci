# krm-ci

One CI for the High Five Labs KRM extension repositories: reusable GitHub
Actions workflows for lint, hermetic tests, Claude review and gated live
runs. Every repository calls the same workflows at the same pinned commit;
adding one is a `[[repo]]` entry in `ci/krm/repos.toml`.

| Workflow | What it does |
| --- | --- |
| `krm-ci.yml` | actionlint, zizmor, gofmt, golangci-lint, gitleaks, govulncheck (only what a PR adds), dependency review, the repository's hermetic check, kind e2e, and one `ci` status to require. Safe for forks: no secrets. |
| `krm-review.yml` | Claude reads the pull request as data, never running it: a security verdict (`hfl/security-review`) and an advisory first pass. Members' PRs on push; anyone else's after a maintainer's `/review`. |
| `krm-live.yml` | Live tests against real accounts behind three locks: a maintainer's `/ok-to-test-live sha=<head>`, a passing review of that commit, and the `live` environment's approval. |
| `krm-security.yml` | Weekly full-history gitleaks, govulncheck and zizmor; CodeQL and Scorecard. |

`docs/krm-ci.md` has the survey of Azure Service Operator, ACK and Config
Connector it draws on, the design, and the rollout.

## Using it

A repository doesn't write its callers by hand. `ci/krm/sync.py` renders
them (`.github/workflows/hfl-*.yml`) from
`ci/krm/callers/` and the repository's entry in `ci/krm/repos.toml`,
pinned to a tag's commit:

```sh
task krm:sync -- --tag v1.0.0 --only cloudflare-krm-extensions   # into ../<repo>
task krm:sync -- --tag v1.0.0 --check                              # drift only
```

## Working on it

```sh
task krm:tools   # the pinned, checksummed lint tools
task check       # tests, a render of every repository, shellcheck, actionlint, zizmor
task krm:probe   # the review sandbox against a hostile checkout (calls a model)
```

Releases are tags, `vX.Y.Z`; callers pin the tag's full SHA. Security
reports: SECURITY.md.

Apache License 2.0.
