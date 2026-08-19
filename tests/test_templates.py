"""Test suite for the PFactory template engine and all 15 shipped templates.

Covers:
- Template engine unit tests (load_template, TemplateFile.instantiate)
- Per-framework loading (load_templates_for_framework)
- Per-template instantiation with realistic sample vars
- Cross-check: descriptor.yaml templates: field matches filesystem

Run with::

    PYTHONPATH=apps/backend pytest tests/test_templates.py -v
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml
from templates_pkg.engine import (
    TemplateError,
    TemplateFile,
    TemplateMetadata,
    load_template,
    load_templates_for_framework,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_template(tmp_path: Path, front_matter: str, body: str) -> Path:
    """Write a synthetic .tmpl file and return its path."""
    content = f"---\n{front_matter}\n---\n{body}"
    p = tmp_path / "test.tmpl"
    p.write_text(content)
    return p


def _no_unsubstituted(text: str) -> bool:
    """Return True if no ${...} placeholders remain in text."""
    return "$" not in text or not re.search(r"\$\{[^}]+\}", text)


def _ts_looks_valid(text: str) -> bool:
    """Cheap structural check: a TypeScript file has at least one import or test/describe call."""
    return bool(
        re.search(r"\bimport\b", text)
        or re.search(r"\b(test|describe|it)\s*\(", text)
    )


# ---------------------------------------------------------------------------
# Engine unit tests (synthetic templates)
# ---------------------------------------------------------------------------


class TestLoadTemplateParsing:
    def test_parses_front_matter_and_body(self, tmp_path: Path) -> None:
        p = _make_template(
            tmp_path,
            "description: hello\nvars:\n  - name\n",
            "hello ${name}\n",
        )
        tmpl = load_template(p)
        assert tmpl.metadata.description == "hello"
        assert tmpl.metadata.vars == ("name",)
        assert tmpl.body == "hello ${name}\n"

    def test_rejects_missing_front_matter(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.tmpl"
        p.write_text("no front matter here\n")
        with pytest.raises(TemplateError) as exc_info:
            load_template(p)
        assert "missing YAML front-matter" in str(exc_info.value)

    def test_rejects_unterminated_front_matter(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.tmpl"
        p.write_text("---\ndescription: hi\n")  # no closing ---
        with pytest.raises(TemplateError) as exc_info:
            load_template(p)
        assert "not terminated" in str(exc_info.value)

    def test_rejects_malformed_yaml_front_matter(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.tmpl"
        p.write_text("---\n: : bad yaml {[\n---\nbody\n")
        with pytest.raises(TemplateError) as exc_info:
            load_template(p)
        assert "YAML parse error" in str(exc_info.value)

    def test_rejects_missing_description(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.tmpl"
        p.write_text("---\nrequires_target: true\n---\nbody\n")
        with pytest.raises(TemplateError) as exc_info:
            load_template(p)
        assert "description" in str(exc_info.value)

    def test_handles_empty_vars(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: no vars\nvars: []\n", "static body\n")
        tmpl = load_template(p)
        assert tmpl.metadata.vars == ()

    def test_handles_missing_vars_key(self, tmp_path: Path) -> None:
        """vars key is optional — defaults to empty tuple."""
        p = _make_template(tmp_path, "description: no vars key\n", "static\n")
        tmpl = load_template(p)
        assert tmpl.metadata.vars == ()

    def test_requires_target_defaults_false(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: d\n", "b\n")
        assert load_template(p).metadata.requires_target is False

    def test_requires_auth_defaults_false(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: d\n", "b\n")
        assert load_template(p).metadata.requires_auth is False

    def test_parses_requires_target_true(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: d\nrequires_target: true\n", "b\n")
        assert load_template(p).metadata.requires_target is True

    def test_body_strips_leading_newline_after_delimiter(self, tmp_path: Path) -> None:
        """Body leading newline after the closing --- is stripped."""
        p = tmp_path / "t.tmpl"
        p.write_text("---\ndescription: d\n---\n\nhello\n")
        tmpl = load_template(p)
        assert tmpl.body.startswith("hello")


class TestTemplateFileInstantiate:
    def test_substitutes_vars(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: d\nvars:\n  - name\n", "hello ${name}\n")
        result = load_template(p).instantiate(name="world")
        assert result == "hello world\n"

    def test_missing_var_raises(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: d\nvars:\n  - name\n", "${name}\n")
        with pytest.raises(TemplateError) as exc_info:
            load_template(p).instantiate()
        err = str(exc_info.value)
        assert "missing required vars" in err
        assert "name" in err

    def test_unknown_var_raises(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: d\nvars:\n  - name\n", "${name}\n")
        with pytest.raises(TemplateError) as exc_info:
            load_template(p).instantiate(name="x", extra="y")
        assert "unknown vars passed" in str(exc_info.value)
        assert "extra" in str(exc_info.value)

    def test_instantiate_unsubstituted_placeholder_in_body(self, tmp_path: Path) -> None:
        """If the body has ${not_in_vars} but the var is not in metadata, it still errors."""
        # vars list is empty but body has a placeholder — string.Template will raise KeyError
        p = tmp_path / "t.tmpl"
        p.write_text("---\ndescription: d\nvars: []\n---\nhello ${ghost}\n")
        tmpl = load_template(p)
        # No vars declared — instantiate with no args is valid from the metadata perspective
        # but string.Template.substitute will raise because ${ghost} is unresolved.
        with pytest.raises(TemplateError) as exc_info:
            tmpl.instantiate()
        assert "unsubstituted placeholder" in str(exc_info.value)

    def test_multiple_vars_substituted(self, tmp_path: Path) -> None:
        p = _make_template(
            tmp_path,
            "description: d\nvars:\n  - a\n  - b\n",
            "${a} and ${b}\n",
        )
        result = load_template(p).instantiate(a="foo", b="bar")
        assert result == "foo and bar\n"

    def test_template_file_is_frozen(self, tmp_path: Path) -> None:
        p = _make_template(tmp_path, "description: d\n", "x\n")
        tmpl = load_template(p)
        with pytest.raises((AttributeError, TypeError)):
            tmpl.body = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_templates_for_framework
# ---------------------------------------------------------------------------


# Curated built-in template counts per framework. Playwright gained a 6th
# (visual-regression) with #109; the rest ship the original 5.
_CURATED_TEMPLATE_COUNTS = {"pytest": 5, "jest": 5, "playwright": 6}


class TestLoadTemplatesForFramework:
    @pytest.mark.parametrize("fw,expected", sorted(_CURATED_TEMPLATE_COUNTS.items()))
    def test_loads_all_templates(self, fw: str, expected: int) -> None:
        # The curated built-in set; the shipped platform `library/` is loaded
        # separately (include_library=False here).
        templates = load_templates_for_framework(
            fw, root=REPO_ROOT, include_library=False
        )
        assert len(templates) == expected, (
            f"Expected {expected} curated templates for {fw}, got "
            f"{len(templates)}: {list(templates)}"
        )

    @pytest.mark.parametrize("fw,expected", sorted(_CURATED_TEMPLATE_COUNTS.items()))
    def test_each_framework_has_expected_curated_templates(
        self, fw: str, expected: int
    ) -> None:
        templates = load_templates_for_framework(
            fw, root=REPO_ROOT, include_library=False
        )
        assert len(templates) == expected

    def test_returns_empty_for_unknown_framework(self) -> None:
        templates = load_templates_for_framework("nonexistent_fw_xyz", root=REPO_ROOT)
        assert templates == {}

    @pytest.mark.parametrize("fw", ["pytest", "jest", "playwright"])
    def test_all_templates_are_template_file_instances(self, fw: str) -> None:
        templates = load_templates_for_framework(fw, root=REPO_ROOT)
        for name, tmpl in templates.items():
            assert isinstance(tmpl, TemplateFile), f"{fw}/{name} is not a TemplateFile"

    @pytest.mark.parametrize("fw", ["pytest", "jest", "playwright"])
    def test_all_templates_have_non_empty_description(self, fw: str) -> None:
        templates = load_templates_for_framework(fw, root=REPO_ROOT)
        for name, tmpl in templates.items():
            assert tmpl.metadata.description, f"{fw}/{name} has empty description"


# ---------------------------------------------------------------------------
# Descriptor YAML cross-check
# ---------------------------------------------------------------------------


class TestDescriptorYamlTemplatesField:
    @pytest.mark.parametrize("fw", ["pytest", "jest", "playwright"])
    def test_descriptor_yaml_templates_field_matches_filesystem(self, fw: str) -> None:
        descriptor_path = REPO_ROOT / "frameworks" / fw / "descriptor.yaml"
        tmpl_dir = REPO_ROOT / "frameworks" / fw / "templates"

        raw = yaml.safe_load(descriptor_path.read_text())
        declared = set(raw.get("templates", []))
        on_disk = {p.name for p in tmpl_dir.glob("*.tmpl")}

        assert declared == on_disk, (
            f"{fw} descriptor.yaml templates: {sorted(declared)} "
            f"doesn't match disk: {sorted(on_disk)}"
        )


# ---------------------------------------------------------------------------
# Per-template instantiation with sample vars
# ---------------------------------------------------------------------------


class TestPlaywrightTemplateInstantiation:
    """Each Playwright template should substitute cleanly and produce valid TS structure."""

    def test_login_flow_instantiates(self) -> None:
        templates = load_templates_for_framework("playwright", root=REPO_ROOT)
        tmpl = templates["login-flow.spec.ts.tmpl"]
        result = tmpl.instantiate(
            target_base_url="https://example.com",
            test_name="logs in with valid credentials",
            login_path="/login",
            username_selector="#email",
            password_selector="#password",
            submit_selector='button[type="submit"]',
            success_url_pattern="dashboard",
            username_env="TEST_USERNAME",
            secret_env="TEST_SECRET",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        # The whole goto() call, not the base URL loose in the file: the loose
        # check passes even if base_url and login_path are concatenated in the
        # wrong order, or the path is dropped entirely.
        assert "page.goto('https://example.com/login')" in result
        assert "dashboard" in result
        # creds are read from injected env vars, not hard-coded (#107 task 5)
        assert "process.env['TEST_USERNAME']" in result

    def test_form_submit_validation_instantiates(self) -> None:
        templates = load_templates_for_framework("playwright", root=REPO_ROOT)
        result = templates["form-submit-validation.spec.ts.tmpl"].instantiate(
            target_base_url="https://example.com",
            test_name="shows error on empty email field",
            form_url="/register",
            field_selector='input[name="email"]',
            expected_error_text="Email is required",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "Email is required" in result

    def test_api_mocked_flow_instantiates(self) -> None:
        templates = load_templates_for_framework("playwright", root=REPO_ROOT)
        result = templates["api-mocked-flow.spec.ts.tmpl"].instantiate(
            target_base_url="https://example.com",
            test_name="renders page with mocked API response",
            mocked_endpoint="/api/users",
            mocked_response_json='{"users":[]}',
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "route.fulfill" in result

    def test_data_loaded_page_instantiates(self) -> None:
        templates = load_templates_for_framework("playwright", root=REPO_ROOT)
        result = templates["data-loaded-page.spec.ts.tmpl"].instantiate(
            target_base_url="https://example.com",
            test_name="dashboard shows user name",
            page_url="/dashboard",
            expected_data_selector=".user-name",
            expected_data_text="Alice",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "Alice" in result

    def test_error_state_instantiates(self) -> None:
        templates = load_templates_for_framework("playwright", root=REPO_ROOT)
        result = templates["error-state.spec.ts.tmpl"].instantiate(
            target_base_url="https://example.com",
            test_name="shows 404 page for unknown route",
            error_trigger_url="/no-such-page-xyz",
            expected_error_selector=".error-message",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert ".error-message" in result


class TestJestTemplateInstantiation:
    """Each Jest template should substitute cleanly and produce valid TS/TSX structure."""

    def test_function_pure_instantiates(self) -> None:
        templates = load_templates_for_framework("jest", root=REPO_ROOT)
        result = templates["function-pure.test.ts.tmpl"].instantiate(
            module_path="./src/math",
            function_name="add",
            input_args="2, 3",
            expected_output="5",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "add" in result
        assert "expect" in result

    def test_function_with_mock_instantiates(self) -> None:
        templates = load_templates_for_framework("jest", root=REPO_ROOT)
        result = templates["function-with-mock.test.ts.tmpl"].instantiate(
            module_path="./src/service",
            function_name="fetchUser",
            mocked_dependency_path="./src/http",
            mocked_return_value='{ id: 1, name: "Alice" }',
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "jest.mock" in result

    def test_react_component_instantiates(self) -> None:
        templates = load_templates_for_framework("jest", root=REPO_ROOT)
        result = templates["react-component.test.tsx.tmpl"].instantiate(
            component_path="./src/components/Button",
            component_name="Button",
            test_text_query="Click me",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "render" in result
        assert "screen.getByText" in result

    def test_async_function_instantiates(self) -> None:
        templates = load_templates_for_framework("jest", root=REPO_ROOT)
        result = templates["async-function.test.ts.tmpl"].instantiate(
            module_path="./src/api",
            function_name="getItems",
            async_input_args='"category"',
            expected_async_output="[]",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "async" in result
        assert "await" in result

    def test_error_boundary_instantiates(self) -> None:
        templates = load_templates_for_framework("jest", root=REPO_ROOT)
        result = templates["error-boundary.test.tsx.tmpl"].instantiate(
            component_path="./src/components/ErrorBoundary",
            boundary_component_name="ErrorBoundary",
            throwing_child_component="ThrowingWidget",
        )
        assert _no_unsubstituted(result)
        assert _ts_looks_valid(result)
        assert "ErrorBoundary" in result


class TestPytestTemplateInstantiation:
    """Each pytest template should substitute cleanly and produce valid Python (ast.parse)."""

    def test_function_pure_instantiates(self) -> None:
        templates = load_templates_for_framework("pytest", root=REPO_ROOT)
        result = templates["function-pure.py.tmpl"].instantiate(
            module_path="myapp.math",
            function_name="add",
            input_args="2, 3",
            expected_output="5",
        )
        assert _no_unsubstituted(result)
        ast.parse(result)  # raises SyntaxError if invalid Python
        assert "def test_" in result

    def test_function_with_mock_instantiates(self) -> None:
        templates = load_templates_for_framework("pytest", root=REPO_ROOT)
        result = templates["function-with-mock.py.tmpl"].instantiate(
            module_path="myapp.service",
            function_name="get_user",
            mocked_dependency_path="myapp.service.http_client",
            mocked_return_value='{"id": 1}',
        )
        assert _no_unsubstituted(result)
        ast.parse(result)
        assert "patch" in result

    def test_fixture_driven_instantiates(self) -> None:
        templates = load_templates_for_framework("pytest", root=REPO_ROOT)
        result = templates["fixture-driven.py.tmpl"].instantiate(
            module_path="myapp.db",
            function_name="query_users",
            fixture_name="db_conn",
            fixture_setup_code="return {'host': 'localhost'}",
            expected_output="[]",
        )
        assert _no_unsubstituted(result)
        ast.parse(result)
        assert "@pytest.fixture" in result
        assert "db_conn" in result

    def test_parametrize_instantiates(self) -> None:
        templates = load_templates_for_framework("pytest", root=REPO_ROOT)
        result = templates["parametrize.py.tmpl"].instantiate(
            module_path="myapp.math",
            function_name="double",
            parametrize_cases="    (1, 2),\n    (2, 4),\n    (0, 0),",
        )
        assert _no_unsubstituted(result)
        ast.parse(result)
        assert "parametrize" in result

    def test_async_function_instantiates(self) -> None:
        templates = load_templates_for_framework("pytest", root=REPO_ROOT)
        result = templates["async-function.py.tmpl"].instantiate(
            module_path="myapp.api",
            function_name="fetch_items",
            async_input_args='"electronics"',
            expected_async_output="[]",
        )
        assert _no_unsubstituted(result)
        ast.parse(result)
        assert "asyncio" in result
        assert "await" in result
