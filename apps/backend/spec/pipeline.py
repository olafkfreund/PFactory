"""Where a project's specs live.

``cli/utils.py`` has imported ``get_specs_dir`` from here since the fork, and
this module has never existed in PFactory. That single missing import made
``cli.utils`` unimportable, which made ``cli.main`` unimportable, which made
``run.py`` unimportable -- and ``run.py --spec <id> --project-dir <path>`` is
the command ``agent_service`` spawns to execute a build, in two places. So the
planner could create a spec and then never build it, in the repo and in the
deployed image alike (PFactory#621).

The directory is ``<project>/.pfactory/specs``, which is not a guess: the
service writes spec directories to exactly that path in six places
(``agent_service.py``: ``project_path / ".pfactory" / "specs" / spec_id``). A
resolver that disagreed with the writer would leave ``run.py`` unable to find
the spec that had just been created -- a subtler failure than the crash it
replaces, so it is asserted by a test rather than left to inspection.

Note ``cli/pfactory_migrate.py`` uses a bare ``specs/`` directory. That is the
pre-migration layout it exists to convert FROM, not a second live convention.
"""

from __future__ import annotations

from pathlib import Path

#: Directory under a project root holding PFactory's own state.
STATE_DIRNAME = ".pfactory"

#: Directory under :data:`STATE_DIRNAME` holding one directory per spec.
SPECS_DIRNAME = "specs"


def get_specs_dir(project_dir: Path | str) -> Path:
    """Return ``<project_dir>/.pfactory/specs``.

    Does not create it and does not require it to exist -- callers list it,
    and a project with no specs yet is a normal state, not an error.
    """
    return Path(project_dir) / STATE_DIRNAME / SPECS_DIRNAME
