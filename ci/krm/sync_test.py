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
                self.assertEqual(text.count(REF), 2, dest)  # uses: and factory-ref:

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
