# PFactory canonical commands.  Run `just --list` for the full menu.
#
# Categories:
#   - setup:  install / configure
#   - dev:    run locally
#   - docs:   the Docusaurus site at https://dataseeek.github.io/PFactory/
#   - demo:   the end-to-end Claude → portal → agent script
#   - test:   the test suites

# Default — list everything
default:
    @just --list

# ----- setup -----

# Install all deps (backend + frontend + docs)
install:
    npm run install:all
    cd docs && npm install

# Set up the Claude OAuth token
setup-token:
    claude setup-token

# ----- dev -----

# Start the FastAPI web-server (port 3102)
backend:
    cd apps/web-server && python -m server.main

# Start the Vite dev server (port 3100)
frontend:
    cd apps/frontend-web && npm run dev

# ----- docs -----

# Run the Docusaurus dev server (port 3000)
docs-dev:
    cd docs && npm start

# Build the static docs site
docs-build:
    cd docs && npm run build

# Serve the built docs locally
docs-serve:
    cd docs && npm run serve

# ----- demo -----

# Run the end-to-end demo
demo:
    ./scripts/demo.sh

# Run demo without pauses
demo-yolo:
    ./scripts/demo.sh --yolo

# Refresh portal screenshots (requires running portal + demo state)
screenshots:
    npm -w apps/frontend-web run capture-screenshots

# ----- test -----

# Backend unit tests (skip slow)
test-backend:
    apps/backend/.venv/bin/pytest tests/ -m "not slow"

# Frontend typecheck
test-frontend:
    cd apps/frontend-web && npm run typecheck

# Postgres acceptance tests (needs Docker)
test-postgres:
    apps/backend/.venv/bin/pytest tests/postgres/ -m postgres -v

# Everything CI runs (slow!)
test-all: test-backend test-frontend test-postgres
    @echo "All tests complete."
