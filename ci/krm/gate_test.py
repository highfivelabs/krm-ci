# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import gate  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40


def f(name, add=1, dele=0, prev=None):
    d = {"filename": name, "additions": add, "deletions": dele, "status": "modified"}
    if prev:
        d["previous_filename"] = prev
    return d


def review_out(**over):
    out = {
        "verdict": "pass",
        "summary": "fine",
        "findings": [],
        "live_risks": {r: False for r in gate.LIVE_RISKS},
    }
    out.update(over)
    return {"subtype": "success", "is_error": False, "structured_output": out}


def event(body, action="created", user="maint", typ="User", pr=True):
    e = {"action": action, "comment": {"body": body, "user": {"login": user, "type": typ}}, "issue": {}}
    if pr:
        e["issue"]["pull_request"] = {"url": "x"}
    return e


def pr(head=SHA, state="open", draft=False):
    return {"state": state, "draft": draft, "head": {"sha": head}}


def status(state="success", creator="github-actions[bot]", desc="No blocking findings", context=gate.REVIEW_CONTEXT):
    return {"context": context, "state": state, "creator": {"login": creator}, "description": desc}


class Classify(unittest.TestCase):
    def test_docs_only(self):
        c = gate.classify([f("README.md"), f("docs/a.md")])
        self.assertTrue(c.docs_only)
        self.assertEqual(c.sensitive, [])

    def test_generated_code_is_still_reviewed(self):
        for files in ([f("pkg/apis/v1/zz_generated.deepcopy.go")], [f("config/crds/a.yaml")],
                      [f("hack/live/cassettes/x.json")], [f("docs/evil_test.go")], [f("pkg/x/testdata/a.go")],
                      [f("README.md"), f("zz_generated.go")], [f("docs/a.md", prev="pkg/a.go")]):
            self.assertFalse(gate.classify(files).docs_only, files)

    def test_sensitive_paths(self):
        for p in [".github/workflows/ci.yml", "Taskfile.yml", "go.mod", "hack/live/run.sh", "live_test.go",
                  "pkg/x/live_config_test.go", "hack/gen/generate.sh", ".claude/settings.json", "CLAUDE.md",
                  "services/s3/Dockerfile", "tests/operator/run.sh", ".review-generated", ".gitleaksignore"]:
            self.assertEqual(gate.classify([f(p)]).sensitive, [p], p)

    def test_rename_from_sensitive_counts(self):
        self.assertEqual(gate.classify([f("pkg/a.go", prev="hack/live/a.go")]).sensitive, ["pkg/a.go"])

    def test_generated_not_counted(self):
        c = gate.classify([f("zz_generated.deepcopy.go", 5000), f("config/crds/a.yaml", 900), f("pkg/a.go", 10, 2)])
        self.assertEqual(c.changed_lines, 12)
        self.assertFalse(c.docs_only)

    def test_extra_generated(self):
        c = gate.classify([f("apis/gen/x.go", 100)], ["apis/gen/**"])
        self.assertEqual(c.changed_lines, 0)

    def test_empty(self):
        c = gate.classify([])
        self.assertFalse(c.reviewable)
        self.assertFalse(c.docs_only)


class Verdict(unittest.TestCase):
    c = gate.Classification(files=1, changed_lines=10)

    def test_pass(self):
        v = gate.decide_verdict(review_out(), self.c)
        self.assertTrue(v.passed)
        self.assertEqual(v.state, "success")

    def test_pass_sensitive_says_so(self):
        v = gate.decide_verdict(review_out(), gate.Classification(sensitive=["go.mod"]))
        self.assertTrue(v.passed)
        self.assertIn("sensitive paths", v.description)

    def test_model_fail(self):
        self.assertFalse(gate.decide_verdict(review_out(verdict="fail"), self.c).passed)

    def test_high_finding_blocks_even_if_model_says_pass(self):
        out = review_out(findings=[{"severity": "high", "title": "t", "file": "a", "detail": "d"}])
        v = gate.decide_verdict(out, self.c)
        self.assertFalse(v.passed)
        self.assertEqual(v.state, "failure")

    def test_medium_finding_does_not_block(self):
        out = review_out(findings=[{"severity": "medium", "title": "t"}])
        self.assertTrue(gate.decide_verdict(out, self.c).passed)

    def test_live_risk_blocks(self):
        risks = {r: False for r in gate.LIVE_RISKS}
        risks["credential_exfiltration"] = True
        v = gate.decide_verdict(review_out(live_risks=risks), self.c)
        self.assertFalse(v.passed)
        self.assertIn("credential_exfiltration", v.description)

    def test_missing_risk_is_malformed(self):
        v = gate.decide_verdict(review_out(live_risks={"cost_risk": False}), self.c)
        self.assertEqual(v.state, "error")
        self.assertFalse(v.passed)

    def test_run_failed(self):
        for r in [{}, {"subtype": "error_max_turns"}, {"subtype": "success", "is_error": True}, None, []]:
            self.assertEqual(gate.decide_verdict(r, self.c).state, "error", r)

    def test_redacts(self):
        key = "sk-ant-api03-" + "x" * 40
        aws = "AKIA" + "ABCDEFGHIJKLMNOP"  # built here, so secret scanners don't flag the fixture
        out = review_out(summary=f"leaked {key} and {aws} and secretvalue123")
        v = gate.decide_verdict(out, self.c, ["secretvalue123"])
        self.assertNotIn(key, json.dumps(v.review))
        self.assertNotIn(aws, v.review["summary"])
        self.assertNotIn("secretvalue123", v.review["summary"])

    def test_description_fits_a_status(self):
        out = review_out(verdict="fail", findings=[{"severity": "critical", "title": "x"}] * 50,
                         live_risks={r: True for r in gate.LIVE_RISKS})
        self.assertLessEqual(len(gate.decide_verdict(out, self.c).description), 140)


def run(path=gate.REVIEW_WORKFLOW, ev="pull_request_target", sha=SHA, repo="highfivelabs/x"):
    return {"path": path, "event": ev, "head_sha": sha, "repository": {"full_name": repo}}


def record(sha=SHA, state="success", sensitive=False):
    return {"sha": sha, "state": state, "sensitive": sensitive}


class Live(unittest.TestCase):
    def decide(self, body=f"/ok-to-test-live sha={SHA}", ev=None, p=None, role="maintain", sts=None, rr=None,
               rec=None, **kw):
        return gate.decide_live(event=ev or event(body), pr=p or pr(), commenter_role=role,
                                statuses=[status()] if sts is None else sts,
                                review_run=run() if rr is None else rr,
                                review_record=record() if rec is None else rec, repository="highfivelabs/x", **kw)

    def test_forged_status_without_the_runs_record(self):
        # Any workflow can post a success status as github-actions[bot]; only
        # a run of main's hfl-review.yml has the verdict artifact.
        for bad in [{}, record(sha=OTHER), record(state="failure"), record(state="error")]:
            self.assertFalse(self.decide(rec=bad).ok, bad)

    def test_sensitive_from_the_record(self):
        self.assertFalse(self.decide(rec=record(sensitive=True)).ok)
        self.assertTrue(self.decide(f"/ok-to-test-live sha={SHA} sensitive=ok", rec=record(sensitive=True)).ok)
        # A record that doesn't say is treated as sensitive.
        self.assertFalse(self.decide(rec={"sha": SHA, "state": "success"}).ok)

    def test_review_provenance(self):
        for bad in [run(path=".github/workflows/evil.yml"), run(ev="pull_request"), run(sha=OTHER),
                    run(repo="mallory/x"), run(ev="workflow_dispatch"), {}]:
            self.assertFalse(self.decide(rr=bad).ok, bad)
        # A /review run (issue_comment) runs on the default branch's head.
        self.assertTrue(self.decide(rr=run(ev="issue_comment", sha=OTHER)).ok)

    def test_ok(self):
        d = self.decide()
        self.assertTrue(d.ok, d.reasons)
        self.assertEqual(d.sha, SHA)
        self.assertEqual(d.kinds, [])

    def test_kinds(self):
        d = self.decide(f"/ok-to-test-live sha={SHA} kinds=DNSRecord,Zone")
        self.assertTrue(d.ok, d.reasons)
        self.assertEqual(d.kinds, ["DNSRecord", "Zone"])

    def test_short_sha_refused(self):
        self.assertFalse(self.decide(f"/ok-to-test-live sha={SHA[:12]}").ok)

    def test_no_sha_refused(self):
        self.assertFalse(self.decide("/ok-to-test-live").ok)

    def test_junk_after_refused(self):
        self.assertFalse(self.decide(f"/ok-to-test-live sha={SHA} ; rm -rf /").ok)
        self.assertFalse(self.decide(f"/ok-to-test-live sha={SHA} kinds=$(id)").ok)

    def test_command_must_be_first_line(self):
        self.assertFalse(self.decide(f"looks good\n/ok-to-test-live sha={SHA}").ok)

    def test_head_moved(self):
        d = self.decide(p=pr(head=OTHER))
        self.assertFalse(d.ok)
        self.assertTrue(any("not the head" in r for r in d.reasons))

    def test_role(self):
        for role in ["write", "triage", "read", "", "none"]:
            self.assertFalse(self.decide(role=role).ok, role)
        self.assertTrue(self.decide(role="admin").ok)
        self.assertTrue(self.decide(role="write", allowed_roles=("write",)).ok)

    def test_bot_refused(self):
        self.assertFalse(self.decide(ev=event(f"/ok-to-test-live sha={SHA}", typ="Bot")).ok)

    def test_edit_refused(self):
        self.assertFalse(self.decide(ev=event(f"/ok-to-test-live sha={SHA}", action="edited")).ok)

    def test_issue_not_pr(self):
        self.assertFalse(self.decide(ev=event(f"/ok-to-test-live sha={SHA}", pr=False)).ok)

    def test_closed_or_draft(self):
        self.assertFalse(self.decide(p=pr(state="closed")).ok)
        self.assertFalse(self.decide(p=pr(draft=True)).ok)

    def test_review_missing(self):
        self.assertFalse(self.decide(sts=[]).ok)

    def test_review_failed(self):
        self.assertFalse(self.decide(sts=[status("failure")]).ok)
        self.assertFalse(self.decide(sts=[status("pending")]).ok)

    def test_newest_status_wins(self):
        self.assertFalse(self.decide(sts=[status("failure"), status("success")]).ok)
        self.assertTrue(self.decide(sts=[status("success"), status("failure")]).ok)

    def test_review_from_someone_else(self):
        self.assertFalse(self.decide(sts=[status(creator="mallory")]).ok)

    def test_other_contexts_ignored(self):
        self.assertTrue(self.decide(sts=[status(context="ci", state="failure"), status()]).ok)

    def test_sensitive_needs_ack(self):
        s = record(sensitive=True)
        self.assertFalse(self.decide(rec=s).ok)
        self.assertTrue(self.decide(f"/ok-to-test-live sha={SHA} sensitive=ok", rec=s).ok)
        self.assertTrue(self.decide(f"/ok-to-test-live sha={SHA} kinds=A sensitive=ok", rec=s).ok)

    def test_max_kinds(self):
        body = f"/ok-to-test-live sha={SHA} kinds=" + ",".join(f"K{i}" for i in range(11))
        self.assertFalse(self.decide(body).ok)


def vuln_stream(*called, uncalled=()):
    objs = []
    for v in called + tuple(uncalled):
        objs.append({"osv": {"id": v, "summary": "s " + v}})
    for v in called:
        objs.append({"finding": {"osv": v, "trace": [{"module": "m", "function": "F"}]}})
    for v in uncalled:
        objs.append({"finding": {"osv": v, "trace": [{"module": "m"}]}})
    return "\n".join(json.dumps(o, indent=2) for o in objs)


class Vulns(unittest.TestCase):
    def test_reachable_only(self):
        r = gate.reachable_vulns(vuln_stream("GO-1", uncalled=("GO-2",)))
        self.assertEqual(list(r), ["GO-1"])

    def test_new(self):
        base = gate.reachable_vulns(vuln_stream("GO-1"))
        head = gate.reachable_vulns(vuln_stream("GO-1", "GO-3"))
        self.assertEqual(list(gate.new_vulns(base, head)), ["GO-3"])
        self.assertEqual(gate.new_vulns(head, base), {})

    def test_empty(self):
        self.assertEqual(gate.reachable_vulns(""), {})


class Comment(unittest.TestCase):
    def test_neutralises(self):
        v = gate.decide_verdict(review_out(summary="@octocat <!-- hfl-krm-review --> <img src=x>",
                                           findings=[{"severity": "medium", "title": "@team ping", "file": "a.go",
                                                      "line": 3, "detail": "x\ny"}]),
                                gate.Classification())
        body = gate.render_comment(SHA, json.loads(json.dumps(gate.asdict(v))), None, {"sensitive": []}, "u")
        self.assertEqual(body.count(gate.COMMENT_MARKER), 1)
        self.assertNotIn("@octocat", body)
        self.assertNotIn("@team", body)
        self.assertNotIn("<img", body)
        self.assertIn(f"/ok-to-test-live sha={SHA}", body)

    def test_first_pass_and_sensitive(self):
        v = gate.asdict(gate.decide_verdict(review_out(), gate.Classification(sensitive=["go.mod"])))
        fp = {"summary": "ok", "findings": [{"severity": "nit", "title": "t", "file": "f", "line": 1, "detail": "d"}]}
        body = gate.render_comment(SHA, v, fp, {"sensitive": ["go.mod"]}, "u")
        self.assertIn("First-pass review", body)
        self.assertIn("`go.mod`", body)

    def test_truncates(self):
        v = gate.asdict(gate.decide_verdict(review_out(summary="x" * 1000), gate.Classification()))
        fp = {"summary": "y", "findings": [{"severity": "nit", "title": "t", "file": "f", "line": 1, "detail": "z" * 9000}] * 12}
        self.assertLessEqual(len(gate.render_comment(SHA, v, fp, {}, "u")), gate.MAX_COMMENT)


def fullpr(assoc="CONTRIBUTOR", state="open", draft=False):
    return {"number": 7, "state": state, "draft": draft, "author_association": assoc,
            "head": {"sha": SHA, "repo": {"full_name": "someone/fork"}}, "base": {"sha": OTHER}}


class Admit(unittest.TestCase):
    def pt(self, assoc, action="synchronize", **kw):
        return gate.decide_admit(event_name="pull_request_target", event={"action": action}, pr=fullpr(assoc, **kw))

    def cm(self, body="/review", role="maintain", typ="User", action="created", p=None):
        return gate.decide_admit(event_name="issue_comment", event=event(body, action=action, typ=typ),
                                 pr=p or fullpr("NONE"), commenter_role=role)

    def test_members_automatic(self):
        for a in ("OWNER", "MEMBER"):
            d = self.pt(a)
            self.assertTrue(d.run, a)
            self.assertEqual((d.number, d.sha, d.head_repo, d.base_sha), (7, SHA, "someone/fork", OTHER))

    def test_others_wait(self):
        for a in ("CONTRIBUTOR", "COLLABORATOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE", ""):
            d = self.pt(a)
            self.assertFalse(d.run, a)
            self.assertTrue(d.wait, a)

    def test_label_or_close_does_nothing(self):
        self.assertFalse(self.pt("MEMBER", action="labeled").run)
        self.assertFalse(self.pt("MEMBER", action="closed").wait)

    def test_draft_and_closed(self):
        self.assertFalse(self.pt("MEMBER", draft=True).run)
        self.assertFalse(self.pt("MEMBER", state="closed").run)
        self.assertFalse(self.cm(p=fullpr(draft=True)).run)

    def test_review_comment(self):
        self.assertTrue(self.cm().run)
        self.assertTrue(self.cm("/review  \nplease").run)
        self.assertTrue(self.cm(role="write").run)

    def test_review_comment_refused(self):
        for kw in [dict(role="triage"), dict(role="read"), dict(role=""), dict(typ="Bot"), dict(action="edited"),
                   dict(body="please /review"), dict(body="/reviewer"), dict(body="/review now")]:
            self.assertFalse(self.cm(**kw).run, kw)

    def test_comment_on_issue(self):
        e = event("/review", pr=False)
        self.assertFalse(gate.decide_admit(event_name="issue_comment", event=e, pr=fullpr(), commenter_role="admin").run)

    def test_other_event(self):
        self.assertFalse(gate.decide_admit(event_name="pull_request", event={"action": "opened"}, pr=fullpr("OWNER")).run)


class CLI(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(__file__), "gate.py"), *args],
                              capture_output=True, text=True)

    def write(self, d, name, obj):
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            json.dump(obj, fh)
        return p

    def test_verdict_bad_json_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            cl = self.write(d, "c.json", gate.Classification().__dict__)
            bad = os.path.join(d, "claude.json")
            with open(bad, "w") as fh:
                fh.write("not json")
            r = self.run_cli("verdict", "--claude", bad, "--classify", cl)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads(r.stdout)["state"], "error")

    def test_live_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli("live", "--event", self.write(d, "e", event(f"/ok-to-test-live sha={SHA}")),
                             "--pr", self.write(d, "p", pr()), "--role", "maintain",
                             "--statuses", self.write(d, "s", [status()]),
                             "--review-run", self.write(d, "r", run()),
                             "--review-record", self.write(d, "v", record()), "--repository", "highfivelabs/x")
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertTrue(json.loads(r.stdout)["ok"])

    def test_redact_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "x.json", {"result": "key sk-ant-api03-" + "y" * 30 + " and topsecretvalue"})
            env = dict(os.environ, MYKEY="topsecretvalue")
            subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(__file__), "gate.py"), "redact",
                            "--file", p, "--redact-env", "MYKEY"], check=True, env=env, capture_output=True)
            txt = open(p).read()
            self.assertNotIn("sk-ant-api03", txt)
            self.assertNotIn("topsecretvalue", txt)

    def test_classify_cli(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli("classify", "--files", self.write(d, "f", [f("go.mod")]))
            self.assertEqual(json.loads(r.stdout)["sensitive"], ["go.mod"])


if __name__ == "__main__":
    unittest.main()
