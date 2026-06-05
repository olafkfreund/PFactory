{
  description = "PFactory — autonomous test generation + execution platform";

  inputs = {
    # nixpkgs-unstable matches python313 + recent nodejs_22
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    systems.url = "github:nix-systems/default";
  };

  outputs =
    { self
    , nixpkgs
    , systems
    , ...
    }:
    let
      forEachSystem = nixpkgs.lib.genAttrs (import systems);
      mkDevShell = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          # Python 3.13 with pyyaml on the base interpreter so `catalog-validate`
          # works straight from the devShell — no venv needed. The backend's own
          # deps still live in the isolated apps/backend/.venv (uv).
          pythonWithDocs = pkgs.python313.withPackages (ps: with ps; [ pyyaml ]);
        in
        pkgs.mkShell {
          name = "pfactory-dev";

          # ────────────────────────────────────────────────────────────
          # Build inputs (packages on PATH inside the shell)
          # ────────────────────────────────────────────────────────────
          packages = with pkgs; [
            # Languages
            pythonWithDocs # python3.13 + pyyaml (catalog-validate, scripts)
            nodejs_22

            # Python tooling — uv backs `bootstrap-venv` + `techdocs-build`
            uv

            # Core dev tools
            git
            gh # GitHub CLI
            just # Justfile runner
            ripgrep # used by scripts/verify-fork.sh
            jq # JSON in shell scripts
            yamllint # lint catalog-info.yaml / openapi.yaml / mkdocs.yml
            direnv # auto-loading via .envrc

            # Container runtime — DockerRunner (Task 4) shells out to `docker`.
            # The daemon must be running on the host; this is the CLI shim.
            docker-client

            # Native deps for Python C-extensions
            stdenv.cc.cc
            zlib
            libffi
            openssl
            pkg-config
          ];

          # ────────────────────────────────────────────────────────────
          # Environment
          # ────────────────────────────────────────────────────────────
          # NOTE: env literals here don't undergo bash expansion. For values
          # that need $HOME / $PWD interpolation, set them in shellHook.
          env = {
            PFACTORY_PORTAL_PORT = "3114";
            # Off by default for deterministic tests; production sets to "1".
            PFACTORY_AUTO_PLAN = "0";
          };

          # ────────────────────────────────────────────────────────────
          # Shell hook — env vars + bash functions for project scripts
          # ────────────────────────────────────────────────────────────
          shellHook = ''
            export PFACTORY_ROOT="$PWD"
            # Workspace dir defaults to ~/.pfactory; user-overridable via .env.
            export PFACTORY_WORKSPACE_ROOT="''${PFACTORY_WORKSPACE_ROOT:-$HOME/.pfactory}"
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
              pkgs.zlib
              pkgs.libffi
              pkgs.openssl
            ]}:''${LD_LIBRARY_PATH:-}"

            # bootstrap-venv — create apps/backend/.venv + install backend deps.
            bootstrap-venv() {
              set -e
              cd "$PFACTORY_ROOT"
              if [ -d apps/backend/.venv ]; then
                echo "venv already exists at apps/backend/.venv — leaving it alone."
                echo "  (rm -rf apps/backend/.venv if you want a fresh install)"
                return 0
              fi
              echo "Creating apps/backend/.venv with Python 3.13 via uv …"
              uv venv apps/backend/.venv --python python3.13
              echo "Installing backend dependencies …"
              uv pip install --python apps/backend/.venv/bin/python \
                -r apps/backend/requirements.txt
              if [ -f tests/requirements-test.txt ]; then
                uv pip install --python apps/backend/.venv/bin/python \
                  -r tests/requirements-test.txt
              fi
              echo "Done. Run \`pfactory-test\` to exercise the suite."
            }

            # pfactory-minimal-venv — only pytest+pytest-asyncio (no SDK).
            # Sufficient to run the 120-case non-SDK suite that exists today.
            pfactory-minimal-venv() {
              set -e
              cd "$PFACTORY_ROOT"
              uv venv apps/backend/.venv --python python3.13
              uv pip install --python apps/backend/.venv/bin/python pytest pytest-asyncio
              echo "Minimal venv ready. Run \`pfactory-test\` for the non-SDK suite."
            }

            # pfactory-test — run the 8 non-SDK pytest files.
            pfactory-test() {
              if [ ! -x apps/backend/.venv/bin/pytest ]; then
                echo "venv missing — run \`bootstrap-venv\` or \`pfactory-minimal-venv\`."
                return 1
              fi
              cd "$PFACTORY_ROOT"
              PYTHONPATH=apps/backend apps/backend/.venv/bin/pytest -v "$@" \
                tests/test_test_plan_lane.py \
                tests/test_test_plan_subtask_fields.py \
                tests/test_snapshotter.py \
                tests/test_docker_runner.py \
                tests/test_lang_registry.py \
                tests/test_lane_dispatch.py \
                tests/test_planner_stub.py \
                tests/test_planner_prompts.py
            }

            # verify-fork — run scripts/verify-fork.sh against the working tree.
            verify-fork() {
              cd "$PFACTORY_ROOT"
              bash scripts/verify-fork.sh "$@"
            }

            # techdocs-venv — create .techdocs-venv with the pinned Backstage
            # TechDocs toolchain (mkdocs-techdocs-core). Not in nixpkgs, so it
            # lives in a uv venv like the backend. Idempotent.
            techdocs-venv() {
              set -e
              cd "$PFACTORY_ROOT"
              if [ ! -x .techdocs-venv/bin/mkdocs ]; then
                echo "Creating .techdocs-venv (Backstage TechDocs toolchain) …"
                uv venv .techdocs-venv --python python3.13
                uv pip install --python .techdocs-venv/bin/python \
                  -r techdocs/requirements.txt
              fi
            }

            # techdocs-build — build the TechDocs site exactly as Backstage does
            # (techdocs-core plugin, --strict). Output: ./site/.
            techdocs-build() {
              techdocs-venv
              cd "$PFACTORY_ROOT"
              .techdocs-venv/bin/mkdocs build --strict "$@"
              echo "TechDocs built → $PFACTORY_ROOT/site/"
            }

            # techdocs-serve — live preview at http://localhost:8000.
            techdocs-serve() {
              techdocs-venv
              cd "$PFACTORY_ROOT"
              .techdocs-venv/bin/mkdocs serve "$@"
            }

            # catalog-validate — parse + sanity-check the Backstage catalog,
            # OpenAPI spec and mkdocs nav (pure Nix python, no venv needed).
            catalog-validate() {
              cd "$PFACTORY_ROOT"
              yamllint -d relaxed catalog-info.yaml openapi.yaml mkdocs.yml || true
              python scripts/validate-catalog.py
            }

            export -f bootstrap-venv pfactory-minimal-venv pfactory-test \
              verify-fork techdocs-venv techdocs-build techdocs-serve \
              catalog-validate

            echo ""
            echo "  PFactory devshell  ──────────────────────────────"
            echo "    python  : $(python --version 2>&1)"
            echo "    node    : $(node --version 2>&1)"
            echo "    uv      : $(uv --version 2>&1 | head -1)"
            echo "    docker  : $(docker --version 2>/dev/null || echo 'daemon not running')"
            echo "  ───────────────────────────────────────────────────"
            echo "  shell fns:  bootstrap-venv  pfactory-minimal-venv"
            echo "              pfactory-test   verify-fork"
            echo "  techdocs :  techdocs-build  techdocs-serve"
            echo "              catalog-validate"
            echo "  ───────────────────────────────────────────────────"
            echo ""
          '';
        };
    in
    {
      devShells = forEachSystem (system: {
        default = mkDevShell system;
      });

      # `nix fmt` runs nixpkgs-fmt across the repo.
      formatter = forEachSystem (system:
        nixpkgs.legacyPackages.${system}.nixpkgs-fmt
      );
    };
}
