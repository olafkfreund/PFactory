"""Tests for the Gen-Functional pre-flight static check — Task 6 (#7) commit 2.

Covered:
  - extract_imports: every import form (import, from-import with single
    + multi-name, aliased, star, relative), with line numbers
  - extract_imports: syntax error in source returns (empty, error_str)
  - check_import: stdlib happy path, hallucinated module, hallucinated
    attribute, relative import → skipped
  - preflight_check end-to-end:
      • valid stdlib-only test → ok=True
      • hallucinated module → ok=False with clear reason
      • hallucinated attribute → ok=False with "has no attribute"
      • mixed valid + invalid → ok=False, only the bad ones in .failures
      • syntax error → ok=False, no imports checked
      • against the planner_smoke fixture project_tree (happy)
      • skip_stdlib_check shortcut
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents.preflight_static import (
    PreflightImport,
    PreflightResult,
    check_import,
    extract_imports,
    preflight_check,
)

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "planner_smoke" / "project_tree"


# ── extract_imports ─────────────────────────────────────────────────────


def test_extract_plain_import() -> None:
    imports, err = extract_imports("import json\n")
    assert err is None
    assert len(imports) == 1
    assert imports[0].module == "json"
    assert imports[0].name is None
    assert imports[0].lineno == 1


def test_extract_dotted_import() -> None:
    imports, err = extract_imports("import a.b.c\n")
    assert err is None
    assert imports[0].module == "a.b.c"


def test_extract_from_import_single_name() -> None:
    imports, err = extract_imports("from pathlib import Path\n")
    assert err is None
    assert len(imports) == 1
    assert imports[0].module == "pathlib"
    assert imports[0].name == "Path"


def test_extract_from_import_multiple_names_expanded() -> None:
    imports, err = extract_imports("from typing import List, Dict, Tuple\n")
    assert err is None
    assert len(imports) == 3
    assert {i.name for i in imports} == {"List", "Dict", "Tuple"}
    assert all(i.module == "typing" for i in imports)


def test_extract_aliased_import_records_alias_and_original() -> None:
    imports, err = extract_imports("import asyncio as a\n")
    assert err is None
    assert imports[0].module == "asyncio"
    assert imports[0].alias == "a"


def test_extract_from_import_aliased() -> None:
    imports, err = extract_imports("from datetime import datetime as dt\n")
    assert err is None
    assert imports[0].module == "datetime"
    assert imports[0].name == "datetime"
    assert imports[0].alias == "dt"


def test_extract_star_import() -> None:
    imports, err = extract_imports("from json import *\n")
    assert err is None
    assert imports[0].name == "*"


def test_extract_relative_imports_marked() -> None:
    imports, err = extract_imports("from . import sibling\nfrom ..pkg import thing\n")
    assert err is None
    assert all(i.is_relative for i in imports)
    assert imports[0].module == "."
    assert imports[1].module == "..pkg"


def test_extract_records_line_numbers() -> None:
    src = "\nimport os\n\n\nimport sys\n"  # lines 2 and 5
    imports, _ = extract_imports(src)
    linenos = sorted(i.lineno for i in imports)
    assert linenos == [2, 5]


def test_extract_returns_syntax_error_for_bad_source() -> None:
    imports, err = extract_imports("def \n")  # syntax error
    assert imports == []
    assert err is not None
    assert "SyntaxError" in err


def test_extract_handles_empty_source() -> None:
    imports, err = extract_imports("")
    assert err is None
    assert imports == []


def test_extract_walks_into_function_bodies() -> None:
    """Imports inside functions / classes are still imports — collect them."""
    src = (
        "def f():\n"
        "    import json\n"
        "class C:\n"
        "    from pathlib import Path\n"
    )
    imports, _ = extract_imports(src)
    assert {(i.module, i.name) for i in imports} == {
        ("json", None), ("pathlib", "Path"),
    }


# ── check_import (per-import subprocess) ────────────────────────────────


def test_check_import_passes_for_stdlib() -> None:
    imp = PreflightImport(module="json")
    check_import(imp)
    assert imp.failed is False
    assert imp.skipped is False


def test_check_import_fails_for_hallucinated_module() -> None:
    imp = PreflightImport(module="this_module_definitely_does_not_exist_xyz")
    check_import(imp)
    assert imp.failed is True
    assert "ModuleNotFoundError" in (imp.reason or "")


def test_check_from_import_passes_for_real_attribute() -> None:
    imp = PreflightImport(module="json", name="loads")
    check_import(imp)
    assert imp.failed is False


def test_check_from_import_fails_for_missing_attribute() -> None:
    imp = PreflightImport(module="json", name="ghost_func_does_not_exist")
    check_import(imp)
    assert imp.failed is True
    assert "has no attribute" in (imp.reason or "")


def test_check_relative_import_is_skipped() -> None:
    imp = PreflightImport(module=".sibling", is_relative=True)
    check_import(imp)
    assert imp.failed is False
    assert imp.skipped is True
    assert "relative import" in (imp.reason or "")


def test_check_star_import_only_verifies_module() -> None:
    imp = PreflightImport(module="json", name="*")
    check_import(imp)
    assert imp.failed is False


# ── preflight_check end-to-end ──────────────────────────────────────────


def test_preflight_valid_stdlib_test_passes() -> None:
    src = (
        "import json\n"
        "from pathlib import Path\n"
        "from typing import List\n"
        "import asyncio\n"
    )
    res = preflight_check(src)
    assert res.ok is True
    assert len(res.imports_checked) == 4
    assert res.failures == []


def test_preflight_hallucinated_module_fails() -> None:
    src = "from totally_fake_module import nothing\n"
    res = preflight_check(src)
    assert res.ok is False
    assert len(res.failures) == 1
    assert "totally_fake_module" in res.failures[0].module


def test_preflight_hallucinated_attribute_fails() -> None:
    src = "from json import ghost_func_xyz\n"
    res = preflight_check(src)
    assert res.ok is False
    assert len(res.failures) == 1
    assert "has no attribute" in res.failures[0].reason


def test_preflight_mixed_valid_and_invalid_isolates_failures() -> None:
    src = (
        "import json\n"
        "from json import loads, ghost_func\n"
        "from pathlib import Path\n"
        "import not_a_real_module_xyz\n"
    )
    res = preflight_check(src)
    assert res.ok is False
    fail_names = {f.describe() for f in res.failures}
    assert any("ghost_func" in n for n in fail_names)
    assert any("not_a_real_module_xyz" in n for n in fail_names)
    # The valid ones aren't in failures
    assert not any("import json\n" in f.describe() for f in res.failures)
    assert not any("Path" in f.describe() for f in res.failures)


def test_preflight_syntax_error_returns_early() -> None:
    src = "def \n"  # malformed
    res = preflight_check(src)
    assert res.ok is False
    assert res.syntax_error is not None
    assert "SyntaxError" in res.syntax_error
    assert res.imports_checked == []


def test_preflight_empty_source_is_ok() -> None:
    res = preflight_check("")
    assert res.ok is True
    assert res.imports_checked == []


def test_preflight_relative_imports_dont_block_success() -> None:
    """Relative imports are skipped but shouldn't fail the whole check
    when nothing else is wrong."""
    src = "import json\nfrom . import sibling\n"
    res = preflight_check(src)
    assert res.ok is True
    assert len(res.skipped) == 1
    assert res.skipped[0].is_relative


def test_preflight_against_real_fixture_project_happy() -> None:
    """Targets a real fixture project — the planner_smoke tree."""
    src = (
        "from app.auth import login_user, get_session\n"
        "from app.auth.session import GRACE_WINDOW_MIN\n"
    )
    res = preflight_check(src, project_dir=FIXTURE_PROJECT)
    assert res.ok is True, f"unexpected failures: {[f.reason for f in res.failures]}"


def test_preflight_against_real_fixture_project_hallucinated_attr() -> None:
    src = "from app.auth import this_method_does_not_exist\n"
    res = preflight_check(src, project_dir=FIXTURE_PROJECT)
    assert res.ok is False
    assert "has no attribute" in res.failures[0].reason


def test_skip_stdlib_check_bypasses_subprocess_for_stdlib() -> None:
    src = "import json\nfrom pathlib import Path\nfrom typing import List\n"
    res = preflight_check(src, skip_stdlib_check=True)
    assert res.ok is True
    # All three were skipped (with the "stdlib" reason)
    assert len(res.skipped) == 3
    assert all("stdlib" in s.reason for s in res.skipped)


# ── Summary helper ──────────────────────────────────────────────────────


def test_summary_reports_ok_count() -> None:
    src = "import json\nfrom pathlib import Path\n"
    res = preflight_check(src)
    assert "OK" in res.summary()
    assert "2" in res.summary()


def test_summary_reports_failure_count() -> None:
    src = "from totally_fake import thing\nimport another_fake_xyz\n"
    res = preflight_check(src)
    assert "2 failed" in res.summary()
