"""Tiny template engine for PFactory test-file templates.

Templates have a YAML front-matter metadata block + a body using
``${var}`` placeholders (Python's string.Template syntax). The engine
substitutes vars + validates the var set against the metadata.

Usage::

    from templates_pkg.engine import load_template, load_templates_for_framework

    tmpl = load_template(Path("frameworks/pytest/templates/function-pure.py.tmpl"))
    result = tmpl.instantiate(
        module_path="myapp.math",
        function_name="add",
        input_args="2, 3",
        expected_output="5",
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

import yaml


class TemplateError(Exception):
    """Raised when template parsing or substitution fails."""

    def __init__(self, template_path: Path, reason: str):
        self.template_path = template_path
        self.reason = reason
        super().__init__(f"{template_path}: {reason}")


@dataclass(frozen=True)
class TemplateMetadata:
    """Parsed YAML front-matter for a PFactory template file.

    Attributes:
        description: One-line description of what the template generates.
        requires_target: Whether the template needs a running application target
            (e.g. a base URL for Playwright browser tests).
        requires_auth: Whether the template requires an authenticated user
            session (e.g. login-flow tests against a protected route).
        vars: Ordered tuple of placeholder names expected in the body.
            These must exactly match the keys passed to ``instantiate()``.
    """

    description: str
    requires_target: bool = False
    requires_auth: bool = False
    vars: tuple[str, ...] = ()  # ordered for stable rendering


@dataclass(frozen=True)
class TemplateFile:
    """A parsed PFactory template: metadata + substitutable body.

    Attributes:
        path: Filesystem path of the ``.tmpl`` file (for error messages).
        metadata: Parsed YAML front-matter as a :class:`TemplateMetadata`.
        body: The post-front-matter text, ready for ``string.Template``
            substitution with ``${var}`` placeholders.
    """

    path: Path
    metadata: TemplateMetadata
    body: str  # the post-front-matter body, ready for Template substitution

    def instantiate(self, **values: str) -> str:
        """Substitute vars in the body; raises TemplateError on missing or unknown vars.

        Args:
            **values: Keyword arguments whose names must exactly match
                ``self.metadata.vars``.

        Returns:
            The fully-substituted file text.

        Raises:
            TemplateError: If any required var is missing, any unknown var is
                passed, or the body contains an unresolved ``${placeholder}``.
        """
        missing = set(self.metadata.vars) - set(values.keys())
        unknown = set(values.keys()) - set(self.metadata.vars)
        if missing:
            raise TemplateError(self.path, f"missing required vars: {sorted(missing)}")
        if unknown:
            raise TemplateError(self.path, f"unknown vars passed: {sorted(unknown)}")
        try:
            return Template(self.body).substitute(values)
        except KeyError as exc:
            raise TemplateError(
                self.path, f"unsubstituted placeholder in body: {exc}"
            ) from exc


def load_template(path: Path) -> TemplateFile:
    """Parse a template file: front-matter metadata + body.

    The file must begin with ``---\\n``, followed by YAML content, followed by
    ``\\n---\\n`` on its own line, followed by the body text.

    Args:
        path: Path to the ``.tmpl`` file.

    Returns:
        A :class:`TemplateFile` with parsed metadata and the raw body string.

    Raises:
        TemplateError: If the file is missing front-matter delimiters, has
            invalid YAML, or is missing the required ``description`` field.
    """
    text = path.read_text()
    if not text.startswith("---\n"):
        raise TemplateError(path, "missing YAML front-matter (must start with ---)")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise TemplateError(
            path, "front-matter not terminated with --- on its own line"
        )
    front_raw = parts[0][4:]  # strip leading "---\n"
    body = parts[1].lstrip("\n")
    try:
        raw_meta = yaml.safe_load(front_raw)
    except yaml.YAMLError as exc:
        raise TemplateError(path, f"YAML parse error in front-matter: {exc}") from exc
    if not isinstance(raw_meta, dict):
        raise TemplateError(path, "front-matter must be a YAML mapping")
    for required in ("description",):
        if required not in raw_meta:
            raise TemplateError(
                path, f"front-matter missing required field: {required}"
            )
    metadata = TemplateMetadata(
        description=str(raw_meta["description"]),
        requires_target=bool(raw_meta.get("requires_target", False)),
        requires_auth=bool(raw_meta.get("requires_auth", False)),
        vars=tuple(raw_meta.get("vars", [])),
    )
    return TemplateFile(path=path, metadata=metadata, body=body)


def load_templates_for_framework(
    framework_name: str,
    root: Path | None = None,
    project_dir: Path | None = None,
    include_harvested: bool = True,
    include_library: bool = True,
) -> dict[str, TemplateFile]:
    """Load all templates for a framework, across the built-in set, the shipped
    platform library, and the harvested libraries (most reusable last so they
    can shadow by name).

    Search order (later wins on filename collision):
      1. built-in  — ``<repo>/frameworks/{name}/templates/`` (the curated set)
      2. library   — ``<repo>/frameworks/{name}/library/`` (shipped platform /
                     infra patterns, e.g. ServiceNow / Salesforce / k8s / nginx)
      3. global    — ``~/.pfactory/templates/{name}/`` (harvested, cross-project)
      4. project   — ``{project_dir}/.pfactory/templates/{name}/`` (harvested,
                     committed with the repo)

    Args:
        framework_name: ``"pytest"``, ``"jest"``, ``"playwright"``, …
        root: Repository root override (defaults to the inferred repo root).
        project_dir: The AIFactory project checkout, to pick up its harvested
            ``.pfactory/templates/`` library. Omit to skip the project library.
        include_harvested: Set False to load only the shipped sets.
        include_library: Set False to load only the curated built-in set
            (used by the "exactly 5 curated templates" invariant tests).

    Returns:
        ``{filename: TemplateFile}`` for every ``*.tmpl`` found.
    """
    if root is None:
        root = Path(__file__).resolve().parents[3]

    search_dirs: list[Path] = [root / "frameworks" / framework_name / "templates"]
    if include_library:
        search_dirs.append(root / "frameworks" / framework_name / "library")
    if include_harvested:
        search_dirs.append(Path.home() / ".pfactory" / "templates" / framework_name)
        if project_dir is not None:
            search_dirs.append(
                Path(project_dir) / ".pfactory" / "templates" / framework_name
            )

    out: dict[str, TemplateFile] = {}
    for tdir in search_dirs:
        if not tdir.is_dir():
            continue
        for p in sorted(tdir.glob("*.tmpl")):
            out[p.name] = load_template(p)
    return out
