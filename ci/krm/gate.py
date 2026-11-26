#!/usr/bin/env python3
# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
"""The decisions behind the KRM extension repos' CI gates (docs/krm-ci.md).

Pure functions, plus a small CLI that the reusable workflows in
.github/workflows/krm-*.yml call. Standard library only. It is always run
from highfivelabs/krm-ci's checkout at the pinned ref, never from the pull request's
tree, so a pull request cannot change the rules it is judged by.

  gate.py classify  --files FILE [--generated FILE]        -> JSON
  gate.py verdict   --claude FILE --classify FILE [--redact-env NAME...]
  gate.py live      --event FILE --pr FILE --role ROLE --statuses FILE
                    [--allowed-roles admin,maintain] [--max-kinds N]
  gate.py admit     --event-name NAME --event FILE --pr FILE [--role ROLE] [--auto OWNER,MEMBER]
  gate.py redact    --file FILE [--redact-env NAME...]   (in place)
  gate.py vulns     --base FILE --head FILE    (govulncheck -format json)
  gate.py comment   --sha SHA --verdict FILE --classify FILE
                    [--first-pass FILE] [--run-url URL]   -> Markdown
  gate.py offload   --statuses FILE --sha SHA [--context offload/ci]

Each prints one JSON object on stdout. `verdict`, `live`, `vulns` and `offload` exit 0
when the gate passes, 1 when it does not, and 2 on bad input. Bad input never passes.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field

# --- What a change touches ---------------------------------------------------

# Paths whose change makes a pull request "sensitive". The live job runs the
# pull request's code with cloud credentials. A change to these paths changes
# how that code is built, which tools run, or what the harness does with the
# credentials, so a maintainer must acknowledge it explicitly
# (`/ok-to-test-live ... sensitive=ok`). The other paths are covered by the
# reviews alone.
SENSITIVE = [
    ".github/**",
    ".claude/**",
    ".mcp.json",
    "CLAUDE.md",
    "AGENTS.md",
    "Taskfile.yml",
    "Taskfile.yaml",
    ".taskrc.yml",
    "taskfiles/**",
    "Makefile",
    "**/Dockerfile",
    "go.mod",
    "go.sum",
    "**/requirements*.txt",
    "hack/ci/**",
    "hack/live/**",
    "hack/hooks/**",
    "hack/**/*.sh",
    "**/live_*.go",
    "**/*_live_test.go",
    "**/live/**",
    "tests/operator/**",
    ".ko.yaml",
    ".gitattributes",
    ".gitmodules",
    ".review-generated",
    ".golangci.yml",
    ".golangci.yaml",
    ".gitleaks.toml",
    ".gitleaksignore",
]

# Generated or recorded files: they don't count toward the size limit, but
# they are still reviewed (zz_generated*.go is code like any other). Each
# repository can add globs in .review-generated (the same file the local
# review loop reads), read from the base branch.
GENERATED = [
    "**/zz_generated*",
    "zz_generated*",
    "config/crds/**",
    "config/crd/**",
    "**/testdata/**",
    "hack/live/cassettes/**",
    "**/recordings/**",
    "docs/coverage/**",
    "docs/third-party-licenses.csv",
    "go.sum",
]

# Documentation by file type only, never by directory: docs/x_test.go is
# code. A pull request made only of these skips the model review.
DOCS = ["**/*.md", "*.md", "LICENSE", "NOTICE", "**/*.png", "**/*.jpg", "**/*.svg"]


def _match(path: str, globs: list[str]) -> bool:
    # fnmatch's * crosses "/", which is what we want for "**/x"; also try the
    # pattern without its leading "**/" so "**/live/**" matches "live/a.go".
    for g in globs:
        if fnmatch.fnmatchcase(path, g):
            return True
        if g.startswith("**/") and fnmatch.fnmatchcase(path, g[3:]):
            return True
    return False


@dataclass
class Classification:
    files: int = 0
    changed_lines: int = 0  # additions + deletions outside generated files
    docs_only: bool = False
    sensitive: list[str] = field(default_factory=list)
    reviewable: bool = True  # anything for a model to read


def classify(files: list[dict], extra_generated: list[str] | None = None) -> Classification:
    """files: GitHub's "list pull request files" entries
    ({filename, additions, deletions, status, previous_filename?})."""
    generated = GENERATED + (extra_generated or [])
    c = Classification(files=len(files))
    non_doc = 0
    for f in files:
        names = [f["filename"]] + ([f["previous_filename"]] if f.get("previous_filename") else [])
        if any(_match(n, SENSITIVE) for n in names):
            c.sensitive.append(f["filename"])
        # Generated files only stay out of the size count: they are still
        # code (zz_generated*.go compiles into the live binary), so they are
        # still reviewed. Only documentation, by file type, skips review.
        if not _match(f["filename"], generated):
            c.changed_lines += int(f.get("additions", 0)) + int(f.get("deletions", 0))
        if not all(_match(n, DOCS) for n in names):
            non_doc += 1
    c.docs_only = len(files) > 0 and non_doc == 0 and not c.sensitive
    c.reviewable = len(files) > 0
    return c


def read_generated_globs(path: str | None) -> list[str]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]


# --- The security review's verdict -------------------------------------------

SEVERITIES = ("critical", "high", "medium", "low")
LIVE_RISKS = (
    "credential_exfiltration",
    "unexpected_network_egress",
    "destructive_cloud_operations",
    "cost_risk",
    "ci_or_harness_tampering",
)

# Values that must never reach a comment or a status, whatever the model
# wrote. The model has no way to read these (no shell, reads confined to the
# checkout), but a review comment is public, so it is checked anyway.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWTs (OIDC tokens)
]


def redact(value, literals: list[str]):
    if isinstance(value, str):
        for lit in literals:
            if lit and len(lit) >= 8:
                value = value.replace(lit, "[redacted]")
        for p in SECRET_PATTERNS:
            value = p.sub("[redacted]", value)
        return value
    if isinstance(value, list):
        return [redact(v, literals) for v in value]
    if isinstance(value, dict):
        return {k: redact(v, literals) for k, v in value.items()}
    return value


@dataclass
class Verdict:
    passed: bool
    state: str  # commit status state: success | failure | error
    description: str  # <= 140 chars, for the commit status
    reasons: list[str]
    review: dict  # the redacted review, for the comment


def decide_verdict(claude_result: dict, c: Classification, secrets: list[str] | None = None) -> Verdict:
    """claude_result: the JSON `claude -p --output-format json --json-schema`
    prints. Passing needs every one of: the run succeeded, the structured
    output is well formed, the model's verdict is pass, no critical or high
    finding, and no live risk flagged. Anything else fails; a malformed run is
    an error, not a pass."""
    secrets = secrets or []
    if not isinstance(claude_result, dict) or claude_result.get("is_error") or claude_result.get("subtype") != "success":
        return Verdict(False, "error", "Security review did not complete", ["the review run failed or was cut off"], {})
    out = claude_result.get("structured_output")
    problems = _schema_problems(out)
    if problems:
        return Verdict(False, "error", "Security review returned malformed output", problems, {})
    out = redact(out, secrets)
    reasons = []
    if out["verdict"] != "pass":
        reasons.append("the reviewer's verdict is fail")
    blocking = [f for f in out["findings"] if f["severity"] in ("critical", "high")]
    if blocking:
        reasons.append(f"{len(blocking)} critical or high finding(s)")
    risks = [r for r in LIVE_RISKS if out["live_risks"].get(r)]
    if risks:
        reasons.append("live risk: " + ", ".join(risks))
    if reasons:
        desc = "Blocked: " + "; ".join(reasons)
        return Verdict(False, "failure", desc[:140], reasons, out)
    desc = "No blocking findings" + (f"; sensitive paths: {len(c.sensitive)} (needs sensitive=ok)" if c.sensitive else "")
    return Verdict(True, "success", desc[:140], [], out)


def _schema_problems(out) -> list[str]:
    if not isinstance(out, dict):
        return ["no structured output"]
    p = []
    if out.get("verdict") not in ("pass", "fail"):
        p.append("verdict must be pass or fail")
    if not isinstance(out.get("summary"), str):
        p.append("summary must be a string")
    findings = out.get("findings")
    if not isinstance(findings, list):
        p.append("findings must be a list")
    else:
        for i, f in enumerate(findings):
            if not isinstance(f, dict) or f.get("severity") not in SEVERITIES or not isinstance(f.get("title"), str):
                p.append(f"finding {i} is malformed")
    risks = out.get("live_risks")
    if not isinstance(risks, dict) or any(not isinstance(risks.get(r), bool) for r in LIVE_RISKS):
        p.append("live_risks must give a boolean for each of " + ", ".join(LIVE_RISKS))
    return p


# --- The live-test command ---------------------------------------------------

# One line, nothing else on it. The full 40-character SHA, so the approval is
# of one exact commit (Azure Service Operator's /ok-to-test sha=, checked the
# same three ways).
COMMAND = re.compile(
    r"^/ok-to-test-live"
    r"[ \t]+sha=(?P<sha>[0-9a-f]{40})"
    r"(?:[ \t]+kinds=(?P<kinds>[A-Za-z0-9_.\-]+(?:,[A-Za-z0-9_.\-]+)*))?"
    r"(?:[ \t]+(?P<sensitive>sensitive=ok))?"
    r"[ \t]*$"
)

REVIEW_CONTEXT = "hfl/security-review"
# Statuses posted with a workflow's GITHUB_TOKEN carry this creator. A fork
# cannot post a status on the base repository at all; this also rules out a
# status from any other integration or person with the same context name.
REVIEW_CREATOR = "github-actions[bot]"
REVIEW_WORKFLOW = ".github/workflows/hfl-review.yml"


@dataclass
class LiveDecision:
    ok: bool
    sha: str = ""
    kinds: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def parse_command(body: str):
    lines = [ln.strip() for ln in (body or "").strip().splitlines()]
    if not lines:
        return None
    m = COMMAND.match(lines[0])
    return m


def decide_live(
    *,
    event: dict,
    pr: dict,
    commenter_role: str,
    statuses: list[dict],
    allowed_roles=("admin", "maintain"),
    max_kinds: int = 10,
    review_creator: str = REVIEW_CREATOR,
    review_run: dict | None = None,
    review_record: dict | None = None,
    repository: str = "",
    review_workflow: str = REVIEW_WORKFLOW,
) -> LiveDecision:
    """event: the issue_comment event payload. pr: GET /pulls/{n} fetched
    after the event (so it is the head *now*). commenter_role: role_name from
    GET /collaborators/{user}/permission. statuses: GET
    /commits/{sha}/statuses, newest first. review_run: GET
    /actions/runs/{id} for the run the review status links to; it must be
    the base repository's review workflow, run by pull_request_target or
    issue_comment (both always run the default branch's copy, never the
    pull request's); a pull_request_target run must also be for this SHA.
    review_record: the `hfl-review-verdict` artifact (verdict.json, written
    by the post job) of the newest completed run of the review workflow
    whose record names this head; review_run is that run. The workflow
    finds it by listing the review workflow's runs, not by following the
    status's link, so an older passing record can't be replayed after a
    newer review failed. A status alone can be posted by
    any workflow in the repository as github-actions[bot]; an artifact of a
    run of the default branch's hfl-review.yml can't. The record must name
    this SHA and say success, and its `sensitive` decides sensitive=ok."""
    reasons = []
    if event.get("action") != "created":
        return LiveDecision(False, reasons=["only a new comment counts, not an edit"])
    if not event.get("issue", {}).get("pull_request"):
        return LiveDecision(False, reasons=["not a pull request"])
    m = parse_command(event.get("comment", {}).get("body", ""))
    if not m:
        return LiveDecision(False, reasons=["the comment's first line is not /ok-to-test-live sha=<40-hex> [kinds=a,b] [sensitive=ok]"])
    sha = m.group("sha")
    kinds = m.group("kinds").split(",") if m.group("kinds") else []
    commenter = event.get("comment", {}).get("user", {}).get("login", "")
    if event.get("comment", {}).get("user", {}).get("type") == "Bot":
        reasons.append("bots cannot approve live runs")
    if commenter_role not in allowed_roles:
        reasons.append(f"{commenter} has role {commenter_role or 'none'}; live runs need one of {', '.join(allowed_roles)}")
    if pr.get("state") != "open":
        reasons.append("the pull request is not open")
    if pr.get("draft"):
        reasons.append("the pull request is a draft")
    head = pr.get("head", {}).get("sha", "")
    if sha != head:
        reasons.append(f"sha={sha[:12]} is not the head ({head[:12]}): approve the current head")
    if len(kinds) > max_kinds:
        reasons.append(f"{len(kinds)} kinds asked for; at most {max_kinds} per run")
    review = next((s for s in statuses if s.get("context") == REVIEW_CONTEXT), None)
    if review is None:
        reasons.append(f"no {REVIEW_CONTEXT} status on {sha[:12]} yet")
    else:
        if review.get("creator", {}).get("login") != review_creator:
            reasons.append(f"{REVIEW_CONTEXT} was posted by {review.get('creator', {}).get('login')}, not {review_creator}")
        run = review_run or {}
        # Both events run the default branch's copy of the workflow, never
        # the pull request's. A pull_request_target run also names the
        # commit; an issue_comment run's head_sha is the default branch.
        ev = run.get("event")
        if run.get("path") != review_workflow or ev not in ("pull_request_target", "issue_comment") \
                or (ev == "pull_request_target" and run.get("head_sha") != sha) \
                or (repository and run.get("repository", {}).get("full_name") != repository):
            reasons.append(f"{REVIEW_CONTEXT} on {sha[:12]} does not come from this repository's {review_workflow}")
        if review.get("state") != "success":
            reasons.append(f"{REVIEW_CONTEXT} is {review.get('state')} on {sha[:12]}")
        rec = review_record or {}
        if rec.get("sha") != sha or rec.get("state") != "success":
            reasons.append(f"the review run's own record (artifact hfl-review-verdict) does not say success for {sha[:12]}")
        elif rec.get("sensitive") is not False and not m.group("sensitive"):
            reasons.append("the change touches sensitive paths (build, harness, CI, dependencies): add sensitive=ok once you have read them")
    return LiveDecision(not reasons, sha, kinds, reasons)


# --- Whether the offload gate already ran the check -------------------------

OFFLOAD_CONTEXT = "offload/ci"


@dataclass
class OffloadDecision:
    passed: bool
    sha: str = ""
    creator: str = ""
    reason: str = ""


def decide_offload(statuses: list[dict], sha: str, context: str = OFFLOAD_CONTEXT) -> OffloadDecision:
    """Whether a repository's pre-push gate (its hack/ci/gate.sh, on an
    offload VM) already ran krm-ci.yml's test and e2e on this exact commit,
    so they can be skipped. statuses: GET /commits/{sha}/statuses, newest
    first. Only the newest status of the context counts, and it must be
    success, for this SHA, posted by a person: GitHub lets only users with
    push access create a commit status, while any workflow in the
    repository can post one as github-actions[bot], so a Bot's status
    never counts (decide_live's rule)."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        return OffloadDecision(False, reason="no head commit")
    st = next((s for s in statuses if s.get("context") == context), None)
    if st is None:
        return OffloadDecision(False, sha, reason=f"no {context} status on {sha[:12]}")
    creator = st.get("creator") or {}
    login = creator.get("login", "")
    if st.get("state") != "success":
        return OffloadDecision(False, sha, login, f"{context} is {st.get('state')} on {sha[:12]}")
    if creator.get("type") != "User":
        return OffloadDecision(False, sha, login, f"{context} on {sha[:12]} was posted by {login or 'unknown'} ({creator.get('type') or 'no type'}), not a person")
    # GET /commits/{sha}/statuses names no SHA per status; the URL does.
    url = st.get("url", "")
    if url and not url.endswith("/" + sha):
        return OffloadDecision(False, sha, login, f"{context} is for another commit")
    return OffloadDecision(True, sha, login, f"{context} passed on {sha[:12]} (posted by {login})")


# --- Whether a pull request is reviewed now ---------------------------------

REVIEW_COMMAND = re.compile(r"^/review[ \t]*$")
REVIEW_ACTIONS = ("opened", "synchronize", "reopened", "ready_for_review")


@dataclass
class Admission:
    run: bool
    wait: bool = False  # post "waiting for /review" on the head
    reason: str = ""
    number: int = 0
    sha: str = ""
    head_repo: str = ""
    base_sha: str = ""


def decide_admit(
    *,
    event_name: str,
    event: dict,
    pr: dict,
    commenter_role: str = "",
    auto_associations=("OWNER", "MEMBER"),
    allowed_roles=("admin", "maintain", "write"),
) -> Admission:
    """Whether to run the review now, and on what. A model review costs
    money, so only organisation members are reviewed on their own pushes;
    anyone else's pull request waits for a maintainer's `/review` comment.

    event_name: pull_request_target or issue_comment. pr: GET /pulls/{n},
    fetched by the workflow (for pull_request_target, the event's own
    pull_request is the same object). commenter_role: role_name of the
    comment's author (issue_comment only)."""
    def target(reason, run=True, wait=False):
        return Admission(run, wait, reason, int(pr.get("number") or 0), pr.get("head", {}).get("sha", ""),
                         (pr.get("head", {}).get("repo") or {}).get("full_name", ""), pr.get("base", {}).get("sha", ""))

    if pr.get("state") != "open":
        return Admission(False, reason="the pull request is not open")
    if pr.get("draft"):
        return Admission(False, reason="a draft is not reviewed")
    if event_name == "pull_request_target":
        if event.get("action") not in REVIEW_ACTIONS:
            return Admission(False, reason=f"action {event.get('action')} does not start a review")
        assoc = pr.get("author_association", "")
        if assoc in auto_associations:
            return target(f"{assoc}: reviewed automatically")
        return target(f"{assoc or 'NONE'}: waits for a maintainer's /review", run=False, wait=True)
    if event_name == "issue_comment":
        c = event.get("comment", {})
        if event.get("action") != "created" or not event.get("issue", {}).get("pull_request"):
            return Admission(False, reason="not a new comment on a pull request")
        first = (c.get("body") or "").strip().splitlines()[:1]
        if not first or not REVIEW_COMMAND.match(first[0].strip()):
            return Admission(False, reason="the comment is not /review")
        if c.get("user", {}).get("type") == "Bot":
            return Admission(False, reason="bots cannot start a review")
        if commenter_role not in allowed_roles:
            return Admission(False, reason=f"{c.get('user', {}).get('login')} has role {commenter_role or 'none'}; "
                                           f"/review needs one of {', '.join(allowed_roles)}")
        return target(f"/review from {c.get('user', {}).get('login')}")
    return Admission(False, reason=f"event {event_name} does not start a review")


# --- govulncheck: what a pull request adds ------------------------------------

def reachable_vulns(stream: str) -> dict[str, str]:
    """govulncheck -format json output (a stream of JSON objects) -> the OSV
    IDs whose vulnerable symbol the code calls, with each one's summary."""
    dec = json.JSONDecoder()
    i, n = 0, len(stream)
    summaries, called = {}, set()
    while i < n:
        while i < n and stream[i].isspace():
            i += 1
        if i >= n:
            break
        obj, i = dec.raw_decode(stream, i)
        if "osv" in obj:
            summaries[obj["osv"]["id"]] = obj["osv"].get("summary", "")
        f = obj.get("finding")
        if f and f.get("trace") and f["trace"][0].get("function"):
            called.add(f["osv"])
    return {v: summaries.get(v, "") for v in sorted(called)}


def new_vulns(base: dict[str, str], head: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in head.items() if k not in base}


# --- The pull request comment -------------------------------------------------

COMMENT_MARKER = "<!-- hfl-krm-review -->"
MAX_COMMENT = 60000


def _safe(text) -> str:
    # The model's words go into a public comment: no @mentions (they would
    # notify), no HTML comments (they could forge our marker), no raw HTML.
    t = str(text or "")
    t = t.replace("<!--", "&lt;!--").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"@(?=[A-Za-z0-9_-])", "@\u200b", t)
    return t


def render_comment(sha: str, verdict: dict, first_pass: dict | None, classification: dict, run_url: str) -> str:
    v = verdict
    icon = {"success": "✅", "failure": "⛔", "error": "⚠️"}.get(v.get("state"), "⚠️")
    lines = [COMMENT_MARKER, f"### {icon} Automated review of `{sha[:12]}`", ""]
    lines.append(f"**Security review** (`{REVIEW_CONTEXT}`): {_safe(v.get('description'))}")
    for r in v.get("reasons") or []:
        lines.append(f"- {_safe(r)}")
    review = v.get("review") or {}
    if review.get("summary"):
        lines += ["", _safe(review["summary"])]
    findings = review.get("findings") or []
    if findings:
        lines += ["", "| Severity | Where | Finding |", "| --- | --- | --- |"]
        for f in findings:
            where = f"`{_safe(f.get('file'))}:{f.get('line')}`" if f.get("file") else ""
            lines.append(f"| {_safe(f.get('severity'))} | {where} | **{_safe(f.get('title'))}**: {_safe(f.get('detail'))} |".replace("\n", " "))
    if classification.get("sensitive"):
        lines += ["", f"Sensitive paths changed ({len(classification['sensitive'])}): "
                  + ", ".join(f"`{_safe(p)}`" for p in classification["sensitive"][:20])
                  + ". A live run needs `sensitive=ok` on the command once a maintainer has read them."]
    if first_pass:
        fp = redact(first_pass, [])
        lines += ["", "<details><summary><b>First-pass review</b> (advisory)</summary>", ""]
        if fp.get("summary"):
            lines += [_safe(fp["summary"]), ""]
        for f in fp.get("findings") or []:
            lines.append(f"- **{_safe(f.get('severity'))}** `{_safe(f.get('file'))}:{f.get('line')}`: "
                         f"**{_safe(f.get('title'))}**: {_safe(f.get('detail'))}".replace("\n", " "))
        lines += ["", "</details>"]
    lines += ["", f"A maintainer can run the live tests on this exact commit with `/ok-to-test-live sha={sha}` "
              "(add `kinds=A,B` to limit them). It needs this review to pass and the live environment's approval. "
              f"[Run]({run_url})"]
    body = "\n".join(lines)
    return body if len(body) <= MAX_COMMENT else body[: MAX_COMMENT - 40] + "\n\n…(truncated)"


# --- CLI ----------------------------------------------------------------------

def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gate.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("classify")
    a.add_argument("--files", required=True)
    a.add_argument("--generated")
    a = sub.add_parser("verdict")
    a.add_argument("--claude", required=True)
    a.add_argument("--classify", required=True)
    a.add_argument("--redact-env", nargs="*", default=[])
    a = sub.add_parser("live")
    a.add_argument("--event", required=True)
    a.add_argument("--pr", required=True)
    a.add_argument("--role", required=True)
    a.add_argument("--statuses", required=True)
    a.add_argument("--allowed-roles", default="admin,maintain")
    a.add_argument("--max-kinds", type=int, default=10)
    a.add_argument("--review-run", help="GET /actions/runs/{id} of the run the review status links to")
    a.add_argument("--review-record", help="that run's hfl-review-verdict artifact (verdict.json)")
    a.add_argument("--repository", default="")
    a = sub.add_parser("admit")
    a.add_argument("--event-name", required=True)
    a.add_argument("--event", required=True)
    a.add_argument("--pr", required=True)
    a.add_argument("--role", default="")
    a.add_argument("--auto", default="OWNER,MEMBER", help="author associations reviewed without /review")
    a = sub.add_parser("redact")
    a.add_argument("--file", required=True)
    a.add_argument("--redact-env", nargs="*", default=[])
    a = sub.add_parser("vulns")
    a.add_argument("--base", required=True)
    a.add_argument("--head", required=True)
    a = sub.add_parser("comment")
    a.add_argument("--sha", required=True)
    a.add_argument("--verdict", required=True)
    a.add_argument("--first-pass")
    a.add_argument("--classify", required=True)
    a.add_argument("--run-url", default="")
    a = sub.add_parser("offload")
    a.add_argument("--statuses", required=True)
    a.add_argument("--sha", required=True)
    a.add_argument("--context", default=OFFLOAD_CONTEXT)
    args = ap.parse_args(argv)

    try:
        if args.cmd == "classify":
            c = classify(_load(args.files), read_generated_globs(args.generated))
            print(json.dumps(asdict(c)))
            return 0
        if args.cmd == "verdict":
            cd = _load(args.classify)
            c = Classification(**cd)
            try:
                result = _load(args.claude)
            except (OSError, ValueError):
                result = {}
            v = decide_verdict(result, c, [os.environ.get(n, "") for n in args.redact_env])
            print(json.dumps(asdict(v)))
            return 0 if v.passed else 1
        if args.cmd == "live":
            d = decide_live(
                event=_load(args.event),
                pr=_load(args.pr),
                commenter_role=args.role,
                statuses=_load(args.statuses),
                allowed_roles=tuple(r for r in args.allowed_roles.split(",") if r),
                max_kinds=args.max_kinds,
                review_run=_load(args.review_run) if args.review_run else None,
                review_record=_load(args.review_record) if args.review_record and os.path.exists(args.review_record) else None,
                repository=args.repository,
            )
            print(json.dumps(asdict(d)))
            return 0 if d.ok else 1
        if args.cmd == "offload":
            try:
                statuses = _load(args.statuses)
            except (OSError, ValueError):
                statuses = None
            if not isinstance(statuses, list):
                print(json.dumps(asdict(OffloadDecision(False, args.sha, reason="unreadable statuses"))))
                return 1
            d = decide_offload(statuses, args.sha, args.context)
            print(json.dumps(asdict(d)))
            return 0 if d.passed else 1
        if args.cmd == "admit":
            d = decide_admit(event_name=args.event_name, event=_load(args.event), pr=_load(args.pr),
                             commenter_role=args.role,
                             auto_associations=tuple(a for a in args.auto.split(",") if a))
            print(json.dumps(asdict(d)))
            return 0
        if args.cmd == "redact":
            try:
                data = _load(args.file)
            except (OSError, ValueError):
                data = {"subtype": "error_unreadable"}
            data = redact(data, [os.environ.get(n, "") for n in args.redact_env])
            with open(args.file, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            print(json.dumps({"redacted": args.file}))
            return 0
        if args.cmd == "vulns":
            with open(args.base, encoding="utf-8") as fh:
                base = reachable_vulns(fh.read())
            with open(args.head, encoding="utf-8") as fh:
                head = reachable_vulns(fh.read())
            added = new_vulns(base, head)
            print(json.dumps({"base": len(base), "head": len(head), "new": added}))
            return 1 if added else 0
        if args.cmd == "comment":
            fp = None
            if args.first_pass:
                try:
                    r = _load(args.first_pass)
                    fp = r.get("structured_output") if isinstance(r, dict) and r.get("subtype") == "success" else None
                except (OSError, ValueError):
                    fp = None
            print(render_comment(args.sha, _load(args.verdict), fp if isinstance(fp, dict) else None,
                                 _load(args.classify), args.run_url))
            return 0
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(json.dumps({"ok": False, "passed": False, "reasons": [f"bad input: {e}"]}))
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
