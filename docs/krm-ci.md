# The KRM CI baseline

One CI for every KRM extensions repository: the three operator extension
repositories (`config-connector-extensions`, `ack-extensions`,
`aso-extensions`), `krm-rest-runtime` and the twelve vendor repositories
(`*-krm-extensions`). It is defined once and each repository calls it.
Linear: HFL-695. Written 2026-11-24.

**Where it lives:** here, `highfivelabs/krm-ci`, a public repository. A
public repository can only call a reusable workflow from a public one.
tf-infra created it; the code was staged in the private factory repository
until then and moved here with its history.

**Status:** tested offline (`task check`; `check.yml` on every pull
request). No repository calls it yet: the repositories are private on
GitHub Free, where Actions jobs are refused for billing, so `task ci` on
offload stays the merge gate until they go public at launch (HFL-683). Section 9 gives the rollout.

## 1. What the upstream teams do (surveyed 2026-11-24)

Sources: each project's `.github/workflows`, test-infra or `dev/ci`,
lint configs and contributor docs, read through the GitHub API. Azure
Service Operator was also read through our own PR #5844.

| | Azure Service Operator | AWS Controllers for Kubernetes | Config Connector |
| --- | --- | --- | --- |
| **CI system** | GitHub Actions on Microsoft's self-hosted 1ES runners, in a devcontainer | Prow on two EKS clusters; every job is defined centrally in `test-infra` | GitHub Actions, about 200 jobs per PR, with a generated workflow |
| **How a fork PR gets credentialed tests** | A maintainer comments `/ok-to-test sha=<head>`. The SHA is checked three times. There are no stored cloud secrets; the runner's managed identity is the credential | `/ok-to-test` from an org member (stock Prow). It assumes a role in the service team's own test account | It never does. Every presubmit is hermetic (mockgcp + envtest). Contributors record against their own project and commit the HTTP logs |
| **Hermetic layer** | go-vcr record/replay of the envtest suite | none: e2e needs real AWS | mockgcp (160 services) and golden HTTP/object logs |
| **Live layer** | Weekly full live run, plus a kind e2e on each approved PR | Every PR, kind plus pytest against real AWS; soak tests before releases | none in CI |
| **Required status** | Several checks, juggled across workflows | Prow labels and Tide | One gatekeeper (`presubmit-gatekeeper`) and a merge queue |
| **Drift gates** | `verify-no-changes` after format and generate; `go mod tidy -diff` | verify-codegen, CRD compatibility | generate/manifests/docs/fmt then `git diff`; generated-workflow drift; untested-fields |
| **Lint** | golangci v2 with gosec, errorlint, bodyclose, noctx and others; gofumpt; a ban on `%v` | thin (shellcheck, codespell) | golangci (depguard, errorlint, revive), custom analyzers, addlicense |
| **Supply chain** | Every action pinned by SHA, `permissions: {}`, Dependabot with a 7-day cooldown, CodeQL, weekly Trivy, immutable registry | Inconsistent pins, a monthly Trivy | SHA pins in generated workflows, zizmor (org-required), Renovate (Dependabot alerts only) |
| **Missing** | govulncheck, SBOM, signing, Scorecard | signing, SBOM, govulncheck, linters | CodeQL, govulncheck, SBOM, signing |
| **Contributor agreement** | CLA | none | CLA |

## 2. Assessment: what to take and what to leave

**Take:**

1. **Hermetic first** (Config Connector). Every check a fork PR runs needs
   no credentials. We already work this way: mockgcp, moto, the fake
   clouds and recorded cassettes.
2. **SHA-pinned maintainer approval for anything live** (Azure Service
   Operator's `/ok-to-test sha=`), with the SHA checked at command time,
   after approval and at checkout.
3. **No stored cloud keys** (Azure Service Operator). Credentials come from
   OIDC federation bound to one GitHub environment.
4. **One list, central templates, one line to onboard** (ACK's
   `jobs_config.yaml`), but with GitHub reusable workflows rather than
   Prow.
5. **One required status, a gatekeeper job** (Config Connector).
6. **Regenerate, then `git diff` must be empty** for every generated
   output (all three).
7. **`permissions: {}` and SHA-pinned actions everywhere**, Dependabot
   with a cooldown (Azure Service Operator), zizmor (Config Connector).
8. **A periodic live run to catch recording rot** (Azure Service Operator's
   weekly run; our vendor repos' nightly re-record).

**Add, because none of them have it:**

- govulncheck that fails a PR only on what it adds.
- gitleaks on every PR's commits.
- An automated security review that must pass before any live run.
- CodeQL and Scorecard on every repository, not just one.
- Signing and SBOMs at release (phase 2, section 9).

**Leave:**

- **Prow.** ACK runs two EKS clusters plus Terraform, Flux and Argo CD
  for it. Reusable workflows on free public runners do the same job.
- **A CLA.** DCO isn't required either. Apache-2.0's inbound=outbound
  clause is enough for now.
- **About 200 jobs per PR.** Each repository has one `test` job, which may
  plan selectively itself (`hack/ci/select.sh`, given `BASE_SHA` and
  `HEAD_SHA`).
- **Live tests on every PR** (ACK). They are expensive and the riskiest
  thing a PR can do, so they run only on request and under the three locks.
- **Two dependency bots** (Config Connector). We use Renovate only
  (factory `docs/renovate.md`); Dependabot's version updates are retired
  (HFL-730) and its alerts stay on.

## 3. Shape

```
highfivelabs/krm-ci
  .github/workflows/krm-ci.yml        lint + hermetic test + e2e + the `ci` gatekeeper
  .github/workflows/krm-review.yml    Claude security review (blocking for live) + first pass (advisory)
  .github/workflows/krm-live.yml      live tests behind three locks
  .github/workflows/krm-security.yml  weekly: full gitleaks, govulncheck, zizmor; CodeQL; Scorecard
  ci/krm/gate.py                      every gate decision, unit-tested (task check)
  ci/krm/tools.sh                     pinned, checksummed lint and security tools
  ci/krm/{golangci.yml,zizmor.yml,gitleaks.toml}   baseline configs (a repository may override)
  ci/krm/review/                      prompts, output schemas, the sandbox settings, probe.sh
  ci/krm/repos.toml                   the one list: each repository and what is particular to it
  ci/krm/callers/                     templates of each repository's thin caller workflows
  ci/krm/sync.py                      renders the callers into each checkout (task krm:sync)

each repository
  .github/workflows/hfl-ci.yml        on pull_request/push/merge_group  -> krm-ci.yml@<sha>
  .github/workflows/hfl-review.yml    on pull_request_target            -> krm-review.yml@<sha>
  .github/workflows/hfl-live.yml      on issue_comment                  -> krm-live.yml@<sha>
  .github/workflows/hfl-security.yml  weekly + push to main             -> krm-security.yml@<sha>
```

A caller pins krm-ci by commit SHA, with its tag as the comment
(`@<sha> # v1.0.0`). The `uses:` pin and the `ci-ref` input must move
together, so only sync moves them; Renovate's preset leaves
`highfivelabs/krm-ci` alone. A change to the baseline is a krm-ci pull request, then
a tag, then `task krm:sync -- --tag vX.Y.Z`. That opens
one small pin-move pull request per repository, which runs the new baseline
before it merges. No repository changes without its own CI passing on the
change.

### The contract a repository keeps

The baseline runs the repository's own commands (`repos.toml`). They behave
the same on a laptop, on an offload VM and in CI:

| Input | Operator profile | Vendor profile |
| --- | --- | --- |
| `setup` | `task tools` | none |
| `lint_setup` (lint job and weekly scan, before golangci-lint, govulncheck and CodeQL load the packages) | `task upstream` (go.mod replaces into `third_party/`) | none |
| `check` (hermetic) | `task check` | generate, build, vet (including `-tags live`), `go test` |
| `e2e` | `task operator-e2e` | none |
| `live_command` | none until the migration | `go test -tags live -run TestEveryKindLive` |
| `sweep` | none yet | none yet (see section 10) |
| `live` (render `hfl-live.yml`) | **off** until the migration | on |

Live is off for the operator repositories and krm-rest-runtime, so their
`hfl-live.yml` is not rendered. The operator repositories' run scripts make
their own run IDs (`cce-…`, `ackx-…`) and sweep by those, not by
`LIVE_RUN_ID`, and today they get their credentials on offload VMs. Each
migration PR wires the contract:
- everything the live command makes is named or tagged with
  `LIVE_RUN_ID`;
- `sweep` deletes what carries it;
- credentials come from the `live` environment's federation.

krm-rest-runtime has no live run of its own; the vendor repositories' live
runs exercise it.

After `check`, the baseline fails if the working tree changed: generated
files, CRDs and coverage outputs must be committed.

## 4. The pull-request checks (`krm-ci.yml`)

These run on `pull_request`, so a fork gets no secrets and a read-only
token. Nothing in the workflow asks for more.

| Job | Checks | Blocks on |
| --- | --- | --- |
| `lint` | actionlint; zizmor (online: impostor commits, ref confusion); gofmt; golangci-lint (the repository's config or the baseline's, `--new-from-merge-base`); gitleaks over the PR's commits; govulncheck on base and head; dependency review (public repositories) | any finding in what the PR changes (on a push to `main`, in the commits pushed); a reachable vulnerability that is new in the PR's head or the push |
| `test` | no MPL, GPL, AGPL, LGPL, SSPL or BUSL licence header in the repository's own files (`taskfiles/licenses.yml`); the repository's hermetic check; every Go module linked into `licenses_packages` under an accepted licence (on a push, or a PR that changes `go.mod`); "the working tree is clean" | a copyleft or source-available header; a module outside the accepted set and not reviewed in `.licenses-allow.txt`; failure or drift |
| `e2e` | kind end to end, if the repository has one | failure |
| `plan` | whether `lint`, `test` and `e2e` run (a prose-only pull request skips all three; the offload gate's status skips `test` and `e2e`), and `test`'s shards | an unreadable `test_shards` |
| `ci` | the gatekeeper | anything above failed or was cancelled (skipped is fine) |

**The offload gate stands in for `test` and `e2e` (HFL-728).** The operator
repositories' pre-push hook runs `hack/ci/gate.sh`: this same check and e2e,
on the exact commit, on a 16-vCPU offload Spot VM with a warm Go cache
(about 37 GB, more than GitHub's 10 GB cache allows). On success it posts
the commit status `offload/ci`. On GitHub's 4-vCPU runner, starting cold,
the same check took over 90 minutes (config-connector-extensions#170,
ack-extensions#140). So on a pull request, `plan` reads the head commit's
statuses, and `gate.py offload` decides. When the newest `offload/ci` is
success and was posted by a person, `test` and `e2e` are skipped and `ci`
passes on `lint`. A Bot's status never counts: any workflow in the
repository can post one as `github-actions[bot]`, but GitHub lets only a
user with push access post a commit status. A fork's pull request has no
such status, so it runs everything. Pushes to `main` always run `test` and
`e2e`, which also covers what the gate can't: it tested the head, not the
merge with a `main` that moved since. They run with `BASE_SHA` set to the
push's `before`, so an operator repository's selective check
(`hack/ci/select.sh`) judges what the push brought, as merged, rather than
running the whole suite after every merge; each repository's nightly run
covers everything. A push with no usable `before` (a new branch, a forced
push) runs everything. `trust_offload = false` in `repos.toml`
turns this off for a repository. The operator profile has it off since
HFL-818: those repositories are public and a full check fits GitHub's free
runner (32 to 56 minutes), so their pre-push gate is opt-in.

**Prose-only pull requests skip `lint`, `test` and `e2e` (HFL-805).** When
every file a pull request changes is prose no check reads, `plan` says so and
the three jobs are skipped; `ci` still reports, and passes, so a ruleset that
requires `ci` is satisfied. Prose is a fixed list in krm-ci (`gate.py`
`PROSE`): the agent files (`AGENTS.md`, `CLAUDE.md`, at any depth),
`README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
`LICENSE` and `NOTICE`. It names files, never a type or a directory, because
`docs/*.md` may be generated and checked as current. A repository adds its
own with the `prose-paths` input (globs). A rename counts only if both names
are prose. `plan` lists the files with GitHub's compare of the base and
head, which needs only `contents: read`; 300 files or more (compare's limit)
or any failure to list them means everything runs. The rule is krm-ci's at
`ci-ref`, never the pull request's. Pushes to `main` and merge queues always
run everything.

**Shards.** `test_shards = N` (1 to 16) runs `test` as N jobs, `test (1)`
to `test (N)`. Each job's check command gets `KRM_SHARD` (1-based) and
`KRM_SHARDS`, with krm-ci checked out at `KRM_CI_DIR`.
`"${KRM_CI_DIR}/ci/krm/shard.sh" ./...` prints the shard's packages: those
with tests, sorted, every Nth from the shard's index. A repository sets
`test_shards` above 1 only once its check command reads these variables;
until then, every shard would run the whole check. Which shard runs the
static checks is up to the repository (shard 1 is the convention). The
licence steps run in shard 1 only.

**Licences (HFL-714).** A dependency may be permissive (Apache-2.0, MIT,
BSD, ISC, Zlib, Unicode) or MPL-2.0. MPL code is linked unmodified, never
patched and never copied into our files: the header check catches a copied
file, and config-connector-extensions' decision 41 records the rule. BUSL
(Terraform core since 1.6, Vault, Consul, ...) and the GPL family are never
accepted. A module go-licenses cannot classify fails until someone reviews
it and lists it in the repository's `.licenses-allow.txt` as
`module licence  # why`. The header check runs before the
repository's check. The module check runs after it, because an operator
repository's packages load only once its check has fetched the upstream
checkout. A pull request that changes no `go.mod`, `go.sum` or
`.licenses-allow.txt` links the same modules as its base, so it skips the
module check; every push to `main` runs it. Each repository's
`licenses_packages` in `repos.toml` names the packages: `./...` for the
vendor repositories and the runtime, the binaries for the operators.

**Why govulncheck fails only on what a PR adds:** on 2026-11-24,
`cloudflare-krm-extensions` alone had 14 reachable vulnerabilities (x/net,
x/text and the standard library at `go 1.25.0`). Failing every PR on those
would stop all work on day one. The PR gate stops new vulnerabilities from
coming in. The weekly scan and Renovate clear the existing ones.

## 5. The review (`krm-review.yml`)

Claude reviews non-draft pull requests. The **security** pass uses Opus
and its result is the commit status `hfl/security-review`. The **first**
pass uses Sonnet and is advisory. Both go into one comment that is edited
in place on every run.

**When it runs: behind a comment, so nobody can run up the cost.**

- Pull requests from organisation members are reviewed on every push, when
  the author and every commit's author are members. Co-authored-by trailers
  (such as an agent's `claude`) don't count as commit authors. A member is
  `author_association` OWNER or MEMBER, or the role write, maintain or
  admin on the repository. The role matters because our memberships are
  private: GitHub then reports a member as CONTRIBUTOR, and `GITHUB_TOKEN`
  can't see private membership. Read doesn't count, because anyone has read
  on a public repository. A commit whose author is no GitHub user, or is
  anyone else (including bots), sends the pull request to `/review`
  (HFL-837).
- Anyone else's pull request, including past contributors, gets the status `hfl/security-review: pending, waiting for
  a maintainer to comment /review`. It is reviewed only when someone with
  the role write, maintain or admin comments `/review` (the comment's first
  line; bots don't count). Each `/review` reviews the head at that moment,
  once. A later push needs another `/review`.
- Opening or pushing pull requests, or commenting, costs us nothing unless
  a maintainer asks.
- The decision is `gate.py admit`, unit-tested like the other gates.
- A newer push or `/review` cancels a running review of the same pull
  request. Other comments cancel nothing: the concurrency group is on the
  job, which only exists for `/review`.
- Both triggers (`pull_request_target`, `issue_comment`) run the default
  branch's copy of the workflow. The live gate accepts a review status from
  either, and from no other workflow or event.

**It reads the PR; it never runs it.** `pull_request_target` gives this
workflow the secrets, so it has to be treated as the dangerous part:

- The head is checked out into `pr/` without credentials. `.git`,
  `.claude/` and `.mcp.json` are deleted. Nothing in it is built, run or
  sourced.
- Claude Code runs with `--bare --setting-sources "" --strict-mcp-config
  --tools Read,Grep,Glob --permission-mode dontAsk` and
  `ci/krm/review/settings.json`. That means:
  - no hooks;
  - no project settings and no `CLAUDE.md` auto-load;
  - no MCP servers;
  - no shell, no writes and no web tools;
  - reads confined to the checkout, with `/proc`, `/sys`, `/etc`, `~`,
    `../` and `.git` denied.
- The model's outputs are redacted (gate.py's secret patterns plus the
  model credentials' own values) before they leave the job.
- The job that holds the model credential has no write access to GitHub.
  The job that writes (`post`) runs no model and none of the PR's code.
- Generated-file globs (`.review-generated`) are read from the **base**
  commit. Otherwise a PR could declare itself generated and slip past the
  size limit.
- Generated files only stay out of the size count; they are still
  reviewed. `zz_generated*.go` compiles into the live binary like any
  other code. Only a PR made entirely of documentation, judged by file
  type (`.md`, images, `LICENSE`, `NOTICE`) and never by directory,
  skips the model review.
- The prompt treats everything in the checkout as untrusted data. An
  instruction aimed at the reviewer is itself a `critical` finding
  (`prompt_injection`).
- **Prompt injection is still assumed possible.** The review is necessary
  for a live run, never sufficient on its own. gate.py also overrides the
  model's own verdict on the facts:
  - a `pass` with any critical or high finding fails;
  - a `pass` with any `live_risks` flag fails;
  - malformed output is an `error`, never a pass.

**Verified 2026-11-24** (Claude Code 2.1.331):

- `task krm:probe` builds a hostile checkout. It carries its own hooks, a
  settings file that allows Bash and `Read(//**)`, and a steering
  `CLAUDE.md`. No hook ran, the `CLAUDE.md` was not followed, Bash was
  unavailable, and every read outside the checkout was denied, while files
  inside it stayed readable.
- The security prompt was also run against two small fake PRs, with Opus,
  the real schema and gate.py:
  - **Hostile:** a `TestMain` that posts `os.Environ()` to a base64-hidden
    host, plus a comment asking the AI reviewer to pass it. Result:
    `failure`, with three critical findings (`credential_exfiltration`,
    `network_egress`, `prompt_injection`) and one high (`obfuscation`).
  - **Benign:** a field addition plus its test. Result: `success`, with no
    findings.
  - Each run cost a few cents and took under 25 seconds.
- Run `task krm:probe` again before moving `claude-code-version`.

**Model credential:** the secret `CLAUDE_CODE_OAUTH_TOKEN` (a subscription
token from `claude setup-token`) or `ANTHROPIC_API_KEY`. Use a dedicated
workspace key with a monthly spend limit. Each pass is also capped by
`--max-budget-usd` (default $3) and a timeout. A PR changing more than
`max-changed-lines` (4,000 lines outside generated files) is not reviewed
automatically: split it, or a maintainer reviews it by hand.

## 6. Live runs (`krm-live.yml`)

A live run is the only place a PR's code meets a credential. It needs
**three locks on the same commit**:

1. **A maintainer's command.** The first line of a new comment (not an
   edit), from a user whose role is `admin` or `maintain` (not a bot), must
   be `/ok-to-test-live sha=<40-hex head> [kinds=A,B] [sensitive=ok]`. The
   SHA must be the current head. A push afterwards needs a new command.
2. **The review passed on that SHA.** A status alone proves little: any
   workflow in the repository, including one on a branch pushed by
   someone with write access, can post `hfl/security-review: success` as
   `github-actions[bot]`. So gate.py checks five things:
   - the `hfl/security-review` status is `success`;
   - it was posted by `github-actions[bot]`;
   - its run is the repository's own `.github/workflows/hfl-review.yml`,
     triggered by `pull_request_target` or `issue_comment`. Both triggers
     always run the default branch's copy of the workflow, never a PR's;
   - **the review run's own artifact `hfl-review-verdict`** (written by
     its post job) says `success`. Only a run of main's review workflow
     can carry that artifact. The gate doesn't follow the status's link to
     find the run, since a forged status could point at an older run that
     passed before a newer one failed. It lists the review workflow's
     completed runs and takes the **newest** one whose record names this
     SHA;
   - if the record says the change touches **sensitive paths** (CI,
     Taskfiles, hack scripts, the live harness, `go.mod`/`go.sum`,
     Dockerfiles, agent configs, the lint and gitleaks configs), the
     command also carries `sensitive=ok`, as the maintainer's statement
     that they read those files. A record that doesn't say counts as
     sensitive.
3. **The `live` environment's approval.** The job runs in the GitHub
   environment `live`, which has required reviewers. The cloud trusts only
   that environment (below), so a token from any other job is useless.

After approval, the head is checked again. The checkout is of the SHA, and
`git rev-parse HEAD` must equal it.

**Cost guards:**

- one live run at a time per repository (`concurrency`);
- `timeout-minutes` (default 45);
- at most 10 kinds per command;
- `LIVE_RUN_ID` names everything the run creates, and the `sweep` command
  always runs afterwards;
- setup-go's cache is off, so a PR can't poison main's cache;
- nothing from the run is uploaded.

**Cloud trust** (set up in tf-infra; none of it exists yet). In every case
the credential is only valid inside the `live` environment, and the
identity can only touch a sandbox project, account or subscription.

- **GCP.** A Workload Identity provider with the attribute condition
  `assertion.repository_id == '<id>' && assertion.environment == 'live'`.
  The service account it impersonates reads only that repository's
  `krm-live-<vendor>` secret, or holds roles only in the sandbox project.
- **AWS.** An IAM role whose trust policy requires
  `token.actions.githubusercontent.com:sub` =
  `repo:highfivelabs/<repo>:environment:live`. It has a permissions
  boundary and lives in the sandbox account only, with a budget alarm.
- **Azure.** A federated credential with subject
  `repo:highfivelabs/<repo>:environment:live` on an app registration that
  holds Contributor only on the live-test resource group.
- **Environment settings.** The `live` environment has required reviewers
  (the maintainers), "prevent self-review" on, and deployment branches
  limited to `main`. An `issue_comment` run deploys from the default
  branch, and a fork's `pull_request` run can't use the environment.

The nightly or weekly live run on `main` (the vendor repos' `live.yml`
re-record and the operator repos' nightly) keeps its own trigger and uses
the same environment.

## 7. Weekly security (`krm-security.yml`)

- **gitleaks** over the whole history, **govulncheck** over the whole
  module and **zizmor** over every workflow. A finding fails the run, and
  the factory's build doctor files the failed run in Linear (HFL-732).
- **CodeQL** (Go) and **OpenSSF Scorecard**, on public repositories, where
  both are free.
- Schedules are spread across the week (`security_cron` in `repos.toml`).

## 8. Repository settings (tf-infra's GitHub provider, at launch)

| Setting | Value |
| --- | --- |
| Ruleset on `main` | Require the `ci` status, one approving review (CODEOWNERS), dismiss stale reviews, linear history, no force push, merge queue on |
| Actions: fork pull request workflows | "Require approval for all external contributors" (GitHub's own gate in front of ours). Set on the organization and every public repository by hand on 2026-11-27 (HFL-805); the GitHub provider has no resource for it |
| Actions: workflow permissions | Read-only `GITHUB_TOKEN` by default; Actions can't approve pull requests |
| Actions: allowed actions | "Selected": GitHub's, `google-github-actions/*`, `aws-actions/*`, `azure/*`, `golangci/*`, `ossf/*`, `arduino/setup-task`, `peter-evans/*`, and require SHA pinning (the org setting `sha_pinning_required`, now false) |
| Security | Secret scanning and push protection; private vulnerability reporting; Dependabot alerts and security updates |
| Environments | `live`, with required reviewers and deployment branches `main` |
| Variables | `HFL_REVIEW_ENABLED`, `HFL_LIVE_ENABLED`, `HFL_LIVE_TARGET`, `HFL_LIVE_GCP_WIF_PROVIDER` / `_GCP_SERVICE_ACCOUNT` / `_AWS_ROLE_ARN` / `_AZURE_*` |
| Secrets | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` (organization secret) |
| Actions access | Public repository; callers need no access setting. While a caller is private, a reusable workflow can't check out `highfivelabs/krm-ci` with the caller's `GITHUB_TOKEN`, so the baseline only runs once both are public |
| Files | `SECURITY.md` (report privately through GitHub), `CODEOWNERS` |

## 9. Rollout

1. **This PR.** The baseline, its tests, this design.
2. **Before launch.** These need no Actions minutes.
   - Run the lint tools in `task ci` on offload with `task krm:tools`.
   - Fix what they find (tracked in Linear).
   - Set up tf-infra's cloud trust (section 6) and repository settings
     (section 8).
3. **The public home, `highfivelabs/krm-ci`.**
   - tf-infra created it (`terragrunt/prod/github`), and the code moved
     here from factory with its history. Done.
   - Tag `v1.0.0`.
   - The shared Taskfile include moved here too (`taskfiles/`, krm-ci
     v1.1.0), so a fork's PR fetches it with no credentials.
4. **At launch, per repository.**
   - Run `task krm:sync -- --tag vX.Y.Z --only <repo>`, delete its
     old `ci.yml`, set the variables, and require `ci`.
   - Vendor repositories: `hack/ci.sh` stays the check (`hfl-ci.yml` runs
     it with `RUNTIME` at the sibling checkout) and reads its runtime pin
     from `hfl-ci.yml`'s `sibling-ref`. `live.yml`, the maintainer-started
     re-record, stays until `hfl-live.yml` is switched on.
   - `krm-rest-runtime`: `hack/ci.sh` too; `live-rerecord.yml` stays (the
     vendor repositories' `live.yml` calls it).
   - Operator repositories: `hack/ci/pr.sh check|e2e` keeps their
     selective runs (`hack/ci/select.sh` on BASE_SHA..HEAD_SHA; a push runs
     everything), on GitHub's runners now that the repositories are public.
     Their Taskfile include moves to krm-ci's `taskfiles/`, so the factory
     App (`factory-access`) goes, and with it the reason a fork's pull
     request failed. `nightly.yml` and `release.yml` stay.
   - Start with `cloudflare-krm-extensions`, the smallest repository that
     has a live run, then the other vendor repos, `krm-rest-runtime`, and
     the operator repos last.
5. **Phase 2: release.**
   - `krm-release.yml`: ko builds (ko writes an SPDX SBOM), cosign keyless
     signing, `actions/attest-build-provenance` (SLSA), and immutable tags.
   - Trivy on the images, installed by checksum rather than through
     trivy-action, whose tags were compromised in 2026.
   - CRD compatibility against the last release (ACK's check).

## 10. Open questions

1. **The repository's name** is decided: `highfivelabs/krm-ci` (the
   builder, 2026-11-24), created through tf-infra.
2. **Review cost.** Gated behind `/review` for everyone outside the
   organisation (the builder, 2026-11-24). Still open: the subscription
   token or a metered API key with a monthly limit.
3. **Sweeps.** The vendor repos have no sweep command. Their live tests
   clean up after themselves, but a killed job leaves whatever it made. Add
   a `live:sweep` task to the vendor template, as gitlab, grafana and vault
   have.
4. **The operator repos' live commands.** `repos.toml` has none for them
   yet (`live = false`). Each migration PR adds the `live_command` and
   `sweep` that keep the `LIVE_RUN_ID` contract (section 3), then sets
   `live = true`. Turning `live` off later removes the rendered
   `hfl-live.yml` on the next sync, and `--check` reports it until then.
