"""Entry-point for ``python -m cli``.

Routes ``python -m cli <subcommand>`` to the click group in ``cli/__init__.py``.

Examples::

    python -m cli init
    python -m cli init --non-interactive --target-name api --target-type http \\
                       --base-url https://api.example.com
    python -m cli migrate v0_1_catalog
    python -m cli migrate v0_1_catalog --dry-run

Task 15 / #31 commit 4.
"""

from cli import pfactory_main

if __name__ == "__main__":
    pfactory_main()
