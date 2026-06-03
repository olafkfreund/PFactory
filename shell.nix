{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    python312
    nodejs_24
    just
    git
    gh
    stdenv.cc.cc
    zlib
    libffi
    openssl
  ];

  shellHook = ''
    # Create the virtualenv if it doesn't exist
    if [ ! -d .venv ]; then
      echo "Creating Python virtualenv..."
      python3 -m venv .venv
    fi
    source .venv/bin/activate

    # Set up the LD_LIBRARY_PATH for compiled Python C-extensions (NixOS dynamic linker fix)
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [
      pkgs.stdenv.cc.cc.lib
      pkgs.zlib
      pkgs.libffi
      pkgs.openssl
    ]}:$LD_LIBRARY_PATH"

    # Install python dependencies inside the virtualenv
    if [ ! -f .venv/packages-installed ]; then
      echo "========================================================="
      echo "  PFactory Native NixOS Development Environment Setup   "
      echo "========================================================="
      echo "  1. Installing Python packages into virtualenv..."
      echo "     (Compiling tree-sitter & LadybugDB C-extensions)"
      echo "---------------------------------------------------------"
      pip install -r apps/backend/requirements.txt -r apps/web-server/requirements.txt

      echo "---------------------------------------------------------"
      echo "  2. Installing Node.js frontend workspace dependencies..."
      echo "---------------------------------------------------------"
      npm ci --workspace=apps/frontend-web

      touch .venv/packages-installed
      echo "---------------------------------------------------------"
      echo "  Setup Complete! Your environment is ready."
      echo "========================================================="
    fi

    echo "========================================================="
    echo "  PFactory Native Nix Development Shell Active!         "
    echo "========================================================="
    echo "  Python: $(python --version)"
    echo "  Node:   $(node --version)"
    echo "  NPM:    $(npm --version)"
    echo "========================================================="
    echo "  Available Quick Recipes (via 'just'):"
    echo "    - just start   : Start background web-server & frontend"
    echo "    - just stop    : Stop all active processes"
    echo "    - just reload  : Restart the stack"
    echo "    - just logs    : Print and follow logs"
    echo "========================================================="
  '';
}
