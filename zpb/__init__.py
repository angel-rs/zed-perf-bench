"""zpb — evidence-grade memory/CPU benchmark harness for the Zed editor."""

__version__ = "0.2.2"

# Schema version embedded in every result JSON (report.build_scenario_result)
# and checked by `zpb compare` to warn when comparing results produced by
# different harness versions, whose metric shapes or methodology may not
# line up. Bump this whenever a result JSON's fields change shape or
# meaning; it tracks __version__ today but is kept as a separate name since
# a CLI-only or doc-only release need not be a schema break.
#
# 0.2.1: added top-level fixture_git_sha / fixture_dirty result fields
# (zpb/scenario.py:git_fixture_provenance) — new keys, so results from
# 0.2.0 and earlier won't have them.
#
# 0.2.2: added host.ci (bool, from ZPB_CI env var) so a shared-runner CI
# result is never silently compared against a lab/laptop result as if they
# were equivalent — see zpb/report.py render_compare_markdown's ci-vs-non-ci
# warning, same pattern as the harness_version mismatch warning.
harness_version = __version__
