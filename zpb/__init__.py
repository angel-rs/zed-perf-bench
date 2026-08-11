"""zpb — evidence-grade memory/CPU benchmark harness for the Zed editor."""

__version__ = "0.2.1"

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
harness_version = __version__
