"""The boot decision table, pinned.

The case that earns this file: a judge follows the README quickstart --
`just ingest-demo` then `just dev` -- and the boot probe must recognise the
fixture graph as deliberately loaded rather than treating it as the debris of
an interrupted slice load and silently rebuilding 52k packages on top of it.
"""

from __future__ import annotations

import pytest

from api.main import load_decision


@pytest.mark.parametrize(
    ("packages", "probe_rows", "fixture_rows", "expected"),
    [
        # An empty graph loads, no matter what the probes claim to have seen.
        (0, 0, 0, "load"),
        # A loaded real slice serves, even if the fixture hub also matches --
        # the real probe outranks it.
        (52_161, 1_034, 0, "serve"),
        (52_161, 1_034, 3, "serve"),
        # The quickstart case: fixture nodes and fixture edges, no real slice.
        (30, 0, 3, "serve-fixture"),
        # Nodes without edges anywhere is an interrupted load; MERGE resumes.
        (52_161, 0, 0, "load"),
    ],
)
def test_the_boot_decision_table(packages, probe_rows, fixture_rows, expected) -> None:
    assert load_decision(packages, probe_rows, fixture_rows) == expected
