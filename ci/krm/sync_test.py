# Copyright 2026 High Five Labs
# SPDX-License-Identifier: Apache-2.0
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import sync  # noqa: E402

REF = "c" * 40


class Sync(unittest.TestCase):
    def setUp(self):
        self.repos = sync.load(os.path.join(os.path.dirname(__file__), "repos.toml"))

    def test_every_repo_renders(self):
        self.assertGreaterEqual(len(self.repos), 16)
        for r in self.repos:
            for dest, text in sync.outputs(r, REF).items():
                self.assertNotIn("@@{", text, f"{r['name']} {dest}")
                if dest.endswith("hfl-ci.yml") or dest.endswith("hfl-review.yml"):
                    self.assertIn(f"highfivelabs/krm-ci/.github/workflows/", text)
                    self.assertIn(f"@{REF} # untagged", text)
                self.assertNotIn("highfivelabs/factory", text)

    def test_pin_is_the_ref_everywhere(self):
        r = self.repos[0]
        for dest, text in sync.outputs(r, REF).items():
            if "workflows" in dest:
                self.assertEqual(text.count(REF), 2, dest)  # uses: and ci-ref:

    def test_quoting(self):
        r = dict(self.repos[0], check='echo "a" && echo \'b\' # not a comment')
        text = sync.outputs(r, REF)[".github/workflows/hfl-ci.yml"]
        self.assertIn('check-command: "echo \\"a\\" && echo \'b\' # not a comment"', text)

    def test_vendor_profile(self):
        cf = next(r for r in self.repos if r["name"] == "cloudflare-krm-extensions")
        self.assertEqual(cf["sibling"], "krm-rest-runtime")
        self.assertEqual(cf["live_cloud"], "gcp")
        gl = next(r for r in self.repos if r["name"] == "gitlab-krm-extensions")
        self.assertEqual(gl["live_cloud"], "none")

    def test_live_flag(self):
        names = {r["name"]: r for r in self.repos}
        for n in ("config-connector-extensions", "ack-extensions", "aso-extensions", "krm-rest-runtime"):
            self.assertNotIn(".github/workflows/hfl-live.yml", sync.outputs(names[n], REF), n)
        self.assertIn(".github/workflows/hfl-live.yml", sync.outputs(names["cloudflare-krm-extensions"], REF))

    def test_sibling_pinned_by_commit(self):
        # krm-rest-runtime has no tags: a sibling is pinned by its full SHA.
        for r in self.repos:
            if r["sibling"]:
                self.assertRegex(r["sibling_ref"], r"^[0-9a-f]{40}$", r["name"])

    def test_dependabot_retired(self):
        # Renovate updates dependencies (HFL-730): sync deletes the
        # dependabot.yml it rendered, and leaves a hand-written one alone.
        for r in self.repos:
            self.assertNotIn(".github/dependabot.yml", sync.outputs(r, REF), r["name"])
        with tempfile.TemporaryDirectory() as d:
            gh = os.path.join(d, "ack-extensions", ".github")
            os.makedirs(gh)
            dep = os.path.join(gh, "dependabot.yml")
            with open(dep, "w") as fh:
                fh.write(f"# {sync.RENDERED_MARK}; do not edit here.\nversion: 2\n")
            argv = ["--ref", REF, "--repos-dir", d, "--only", "ack-extensions"]
            self.assertEqual(sync.main(argv + ["--check"]), 1)
            self.assertEqual(sync.main(argv), 0)
            self.assertFalse(os.path.exists(dep))
            self.assertEqual(sync.main(argv + ["--check"]), 0)
            with open(dep, "w") as fh:
                fh.write("version: 2\n")
            self.assertEqual(sync.main(argv), 0)
            self.assertTrue(os.path.exists(dep))

    def test_timeout(self):
        names = {r["name"]: r for r in self.repos}
        self.assertIn("timeout-minutes: 180", sync.outputs(names["ack-extensions"], REF)[".github/workflows/hfl-ci.yml"])
        for bad in ("0", "1000", '"30"', "true"):
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
                with open(os.path.join(os.path.dirname(__file__), "repos.toml")) as orig:
                    fh.write(orig.read().replace("timeout = 20\n", f"timeout = {bad}\n", 1))
            try:
                with self.assertRaises(ValueError, msg=bad):
                    sync.load(fh.name)
            finally:
                os.unlink(fh.name)

    def test_shards_and_offload(self):
        for r in sync.load(sync.os.path.join(sync.HERE, "repos.toml")):
            out = sync.outputs(r, "a" * 40)[sync.OUTPUTS["hfl-ci.yml"]]
            self.assertIn(f"test-shards: {r['test_shards']}\n", out)
            self.assertIn(f"trust-offload-status: {str(r['trust_offload']).lower()}\n", out)
            self.assertIn("statuses: read", out)

    def test_bad_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write('[profile.p]\nsetup=""\n[[repo]]\nname="x"\nprofile="p"\n')
        try:
            with self.assertRaises(ValueError):
                sync.load(fh.name)
        finally:
            os.unlink(fh.name)

    def test_check_and_write(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cloudflare-krm-extensions"))
            argv = ["--ref", REF, "--repos-dir", d, "--only", "cloudflare-krm-extensions"]
            self.assertEqual(sync.main(argv + ["--check"]), 1)
            self.assertEqual(sync.main(argv), 0)
            self.assertEqual(sync.main(argv + ["--check"]), 0)

    def test_live_turned_off_removes_the_caller(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cloudflare-krm-extensions"))
            cfg = os.path.join(d, "repos.toml")
            with open(os.path.join(os.path.dirname(__file__), "repos.toml")) as fh:
                src = fh.read()
            with open(cfg, "w") as fh:
                fh.write(src)
            argv = ["--ref", REF, "--repos-dir", d, "--only", "cloudflare-krm-extensions", "--config", cfg]
            self.assertEqual(sync.main(argv), 0)
            live = os.path.join(d, "cloudflare-krm-extensions", ".github/workflows/hfl-live.yml")
            self.assertTrue(os.path.exists(live))
            with open(cfg, "w") as fh:
                fh.write(src.replace('name = "cloudflare-krm-extensions"\nprofile = "vendor"',
                                     'name = "cloudflare-krm-extensions"\nprofile = "vendor"\nlive = false'))
            self.assertEqual(sync.main(argv + ["--check"]), 1)
            self.assertEqual(sync.main(argv), 0)
            self.assertFalse(os.path.exists(live))
            self.assertEqual(sync.main(argv + ["--check"]), 0)

    def test_unrendered_file_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            wf = os.path.join(d, "ack-extensions", ".github/workflows")
            os.makedirs(wf)
            with open(os.path.join(wf, "hfl-live.yml"), "w") as fh:
                fh.write("name: hand-written\n")
            self.assertEqual(sync.main(["--ref", REF, "--repos-dir", d, "--only", "ack-extensions"]), 0)
            self.assertTrue(os.path.exists(os.path.join(wf, "hfl-live.yml")))

    def test_ref_must_be_sha(self):
        self.assertEqual(sync.main(["--ref", "main", "--out", "/nonexistent"]), 2)
        self.assertEqual(sync.main(["--out", "/nonexistent"]), 2)

    def test_tag_shape(self):
        self.assertEqual(sync.main(["--tag", "v1", "--out", "/nonexistent"]), 2)
        self.assertEqual(sync.main(["--tag", "krm-ci/v1.0.0", "--out", "/nonexistent"]), 2)
        self.assertEqual(sync.main(["--tag", "v9.9.9", "--out", "/nonexistent"]), 2)  # no such tag

    def test_tag_comment(self):
        text = sync.outputs(self.repos[0], REF, "v1.2.3")[".github/workflows/hfl-ci.yml"]
        self.assertIn(f"@{REF} # v1.2.3", text)


if __name__ == "__main__":
    unittest.main()
