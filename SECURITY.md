# Security

krm-ci runs with the secrets of every High Five Labs KRM repository that
calls it, so a weakness here is a weakness in all of them.

Report a vulnerability privately: use **Report a vulnerability** on this
repository's Security tab, or email hello@highfivelabs.dev. Please don't
open a public issue or pull request for it.

What counts: anything that lets a pull request, a comment or a fork run
code with a repository's secrets or cloud credentials, start a live run or
a review it shouldn't, forge a review verdict, or get past the review
sandbox (`ci/krm/review/settings.json`). docs/krm-ci.md describes the
gates.
