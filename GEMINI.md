# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PFactory is a web-based AI task management and agent orchestration platform that builds software through coordinated AI agent sessions. It uses the Claude Agent SDK to run agents in isolated workspaces with security controls.

**Project:** PFactory
**Repository:** https://github.com/olafkfreund/PFactory
**Author:** DataSeek Team
**License:** MIT OR GPL-3.0

**CRITICAL: All AI interactions use the Claude Agent SDK (`claude-agent-sdk` package), NOT the Anthropic API directly.**

## Project Structure

```
PFactory/
├── apps/
│   ├── backend/           # Python backend/CLI - ALL agent logic lives here
│   │   ├── core/          # Client, auth, security
│   │   ├── agents/        # Agent implementations
│   │   ├── spec_agents/   # Spec creation agents
│   │   ├── integrations/  # Graphiti, Linear, GitHub
│   │   └── prompts/       # Agent system prompts
│   ├── web-server/        # FastAPI backend for web UI (REST/WebSocket)
│   └── frontend-web/      # React web frontend (browser-based)
├── guides/                # Documentation
├── tests/                 # Test suite
└── scripts/               # Build and utility scripts
```

**When working with AI/LLM code:**
- Look in `apps/backend/core/client.py` for the Claude SDK client setup
- Reference `apps/backend/agents/` for working agent implementations
- Check `apps/backend/spec_agents/` for spec creation agent examples
- NEVER use `anthropic.Anthropic()` directly - always use `create_client()` from `core.client`

**Frontend (Web):**
- Built with React 19, TypeScript, Vite
- Browser-based UI accessible from any device
- Real-time updates via WebSocket

## Commands

### Setup

**Requirements:**
- Python 3.12+ (required for backend)
- Node.js (for frontend)

```bash
# Install all dependencies from root
npm run install:all

# Or install separately:
# Backend (from apps/backend/)
cd apps/backend && uv venv && uv pip install -r requirements.txt

# Frontend (from apps/frontend/)
cd apps/frontend && npm install

# Set up OAuth token
claude setup-token
# Add to apps/backend/.env: CLAUDE_CODE_OAUTH_TOKEN=your-token
```

### Creating and Running Specs
```bash
cd apps/backend

# Create a spec interactively
python spec_runner.py --interactive

# Create spec from task description
python spec_runner.py --task "Add user authentication"

# Force complexity level (simple/standard/complex)
python spec_runner.py --task "Fix button" --complexity simple

# Run autonomous build
python run.py --spec 001

# List all specs
python run.py --list
```

### Workspace Management
```bash
cd apps/backend

# Review changes in isolated worktree
python run.py --spec 001 --review

# Merge completed build into project
python run.py --spec 001 --merge

# Discard build
python run.py --spec 001 --discard
```

### QA Validation
```bash
cd apps/backend

# Run QA manually
python run.py --spec 001 --qa

# Check QA status
python run.py --spec 001 --qa-status
```

### Testing
```bash
# Install test dependencies (required first time)
cd apps/backend && uv pip install -r ../../tests/requirements-test.txt

# Run all tests (use virtual environment pytest)
apps/backend/.venv/bin/pytest tests/ -v

# Run single test file
apps/backend/.venv/bin/pytest tests/test_security.py -v

# Run specific test
apps/backend/.venv/bin/pytest tests/test_security.py::test_bash_command_validation -v

# Skip slow tests
apps/backend/.venv/bin/pytest tests/ -m "not slow"

# Or from root
npm run test:backend
```

### Spec Validation
```bash
python apps/backend/validate_spec.py --spec-dir apps/backend/specs/001-feature --checkpoint all
```

### Releases
```bash
# 1. Bump version on your branch (creates commit, no tag)
node scripts/bump-version.js patch   # 2.8.0 -> 2.8.1
node scripts/bump-version.js minor   # 2.8.0 -> 2.9.0
node scripts/bump-version.js major   # 2.8.0 -> 3.0.0

# 2. Push and create PR to main
git push origin your-branch
gh pr create --base main

# 3. Merge PR → GitHub Actions automatically:
#    - Creates tag
#    - Builds all platforms
#    - Creates release with changelog
#    - Updates README
```

See [RELEASE.md](RELEASE.md) for detailed release process documentation.

## Architecture

### Core Pipeline

**Spec Creation (spec_runner.py)** - Dynamic 3-8 phase pipeline based on task complexity:
- SIMPLE (3 phases): Discovery → Quick Spec → Validate
- STANDARD (6-7 phases): Discovery → Requirements → [Research] → Context → Spec → Plan → Validate
- COMPLEX (8 phases): Full pipeline with Research and Self-Critique phases

**Implementation (run.py → agent.py)** - Multi-session build:
1. Planner Agent creates subtask-based implementation plan
2. Coder Agent implements subtasks (can spawn subagents for parallel work)
3. QA Reviewer validates acceptance criteria
4. QA Fixer resolves issues in a loop

### Key Components (apps/backend/)

**Core Infrastructure:**
- **core/client.py** - Claude Agent SDK client factory with security hooks and tool permissions
- **core/security.py** - Dynamic command allowlisting based on detected project stack
- **core/auth.py** - OAuth token management for Claude SDK authentication
- **agents/** - Agent implementations (planner, coder, qa_reviewer, qa_fixer)
- **spec_agents/** - Spec creation agents (gatherer, researcher, writer, critic)

**Memory & Context:**
- **integrations/graphiti/** - Graphiti memory system (mandatory)
  - `queries_pkg/graphiti.py` - Main GraphitiMemory class
  - `queries_pkg/client.py` - LadybugDB client wrapper
  - `queries_pkg/queries.py` - Graph query operations
  - `queries_pkg/search.py` - Semantic search logic
  - `queries_pkg/schema.py` - Graph schema definitions
- **graphiti_config.py** - Configuration and validation for Graphiti integration
- **graphiti_providers.py** - Multi-provider factory (OpenAI, Anthropic, Azure, Ollama, Google AI)
- **agents/memory_manager.py** - Session memory orchestration

**Workspace & Security:**
- **cli/worktree.py** - Git worktree isolation for safe feature development
- **context/project_analyzer.py** - Project stack detection for dynamic tooling
- **pfactory_ai_tools.py** - Custom MCP tools integration

**Integrations:**
- **linear_updater.py** - Optional Linear integration for progress tracking
- **runners/github/** - GitHub Issues & PRs automation

### Agent Prompts (apps/backend/prompts/)

| Prompt | Purpose |
|--------|---------|
| planner.md | Creates implementation plan with subtasks |
| coder.md | Implements individual subtasks |
| coder_recovery.md | Recovers from stuck/failed subtasks |
| qa_reviewer.md | Validates acceptance criteria |
| qa_fixer.md | Fixes QA-reported issues |
| spec_gatherer.md | Collects user requirements |
| spec_researcher.md | Validates external integrations |
| spec_writer.md | Creates spec.md document |
| spec_critic.md | Self-critique using ultrathink |
| complexity_assessor.md | AI-based complexity assessment |

### Spec Directory Structure

Each spec in `.pfactory/specs/XXX-name/` contains:
- `spec.md` - Feature specification
- `requirements.json` - Structured user requirements
- `context.json` - Discovered codebase context
- `test_plan.json` - Subtask-based plan with status tracking
- `qa_report.md` - QA validation results
- `QA_FIX_REQUEST.md` - Issues to fix (when rejected)

### Branching & Worktree Strategy

PFactory uses git worktrees for isolated builds. All branches stay LOCAL until user explicitly pushes:

```
main (user's branch)
└── pfactory/{spec-name}  ← spec branch (isolated worktree)
```

**Key principles:**
- ONE branch per spec (`pfactory/{spec-name}`)
- Parallel work uses subagents (agent decides when to spawn)
- NO automatic pushes to GitHub - user controls when to push
- User reviews in spec worktree (`.worktrees/{spec-name}/`)
- Final merge: spec branch → main (after user approval)

**Workflow:**
1. Build runs in isolated worktree on spec branch
2. Agent implements subtasks (can spawn subagents for parallel work)
3. User tests feature in `.worktrees/{spec-name}/`
4. User runs `--merge` to add to their project
5. User pushes to remote when ready

### Contributing

**Workflow for contributions:**
1. Create feature branch from develop: `git checkout -b fix/my-fix develop`
2. Make changes and commit with sign-off: `git commit -s -m "fix: description"`
3. Push to your branch: `git push origin fix/my-fix`
4. Create PR targeting `develop`: `gh pr create --base develop`

**Verify before PR:**
```bash
# Ensure only your commits are included
git log --oneline origin/develop..HEAD
```

### Security Model

Three-layer defense:
1. **OS Sandbox** - Bash command isolation
2. **Filesystem Permissions** - Operations restricted to project directory
3. **Command Allowlist** - Dynamic allowlist from project analysis (security.py + project_analyzer.py)

Security profile cached in `.pfactory-security.json`.

### Claude Agent SDK Integration

**CRITICAL: PFactory uses the Claude Agent SDK for ALL AI interactions. Never use the Anthropic API directly.**

**Client Location:** `apps/backend/core/client.py`

The `create_client()` function creates a configured `ClaudeSDKClient` instance with:
- Multi-layered security (sandbox, permissions, security hooks)
- Agent-specific tool permissions (planner, coder, qa_reviewer, qa_fixer)
- Dynamic MCP server integration based on project capabilities
- Extended thinking token budget control

**Example usage in agents:**
```python
from core.client import create_client

# Create SDK client (NOT raw Anthropic API client)
client = create_client(
    project_dir=project_dir,
    spec_dir=spec_dir,
    model="claude-sonnet-4-5-20250929",
    agent_type="coder",
    max_thinking_tokens=None  # or 5000/10000/16000
)

# Run agent session
response = client.create_agent_session(
    name="coder-agent-session",
    starting_message="Implement the authentication feature"
)
```

**Why use the SDK:**
- Pre-configured security (sandbox, allowlists, hooks)
- Automatic MCP server integration (Context7, Linear, Graphiti, Puppeteer)
- Tool permissions based on agent role
- Session management and recovery
- Unified API across all agent types

**Where to find working examples:**
- `apps/backend/agents/planner.py` - Planner agent
- `apps/backend/agents/coder.py` - Coder agent
- `apps/backend/agents/qa_reviewer.py` - QA reviewer
- `apps/backend/agents/qa_fixer.py` - QA fixer
- `apps/backend/spec_agents/` - Spec creation agents

### Memory System

**Graphiti Memory (Mandatory)** - `integrations/graphiti/`

PFactory uses Graphiti as its primary memory system with embedded LadybugDB (no Docker required):

- **Graph database with semantic search** - Knowledge graph for cross-session context
- **Session insights** - Patterns, gotchas, discoveries automatically extracted
- **Multi-provider support:**
  - LLM: OpenAI, Anthropic, Azure OpenAI, Ollama, Google AI (Gemini)
  - Embedders: OpenAI, Voyage AI, Azure OpenAI, Ollama, Google AI
- **Modular architecture:** (`integrations/graphiti/queries_pkg/`)
  - `graphiti.py` - Main GraphitiMemory class
  - `client.py` - LadybugDB client wrapper
  - `queries.py` - Graph query operations
  - `search.py` - Semantic search logic
  - `schema.py` - Graph schema definitions

**Configuration:**
- Set provider credentials in `apps/backend/.env` (see `.env.example`)
- Required env vars: `GRAPHITI_ENABLED=true`, `ANTHROPIC_API_KEY` or other provider keys
- Memory data stored in `.pfactory/specs/XXX/graphiti/`

**Usage in agents:**
```python
from integrations.graphiti.memory import get_graphiti_memory

memory = get_graphiti_memory(spec_dir, project_dir)
context = memory.get_context_for_session("Implementing feature X")
memory.add_session_insight("Pattern: use React hooks for state")
```

## Development Guidelines

### Frontend Internationalization (i18n)

**CRITICAL: Always use i18n translation keys for all user-facing text in the frontend.**

The frontend uses `react-i18next` for internationalization. All labels, buttons, messages, and user-facing text MUST use translation keys.

**Translation file locations:**
- `apps/frontend/src/shared/i18n/locales/en/*.json` - English translations
- `apps/frontend/src/shared/i18n/locales/fr/*.json` - French translations

**Translation namespaces:**
- `common.json` - Shared labels, buttons, common terms
- `navigation.json` - Sidebar navigation items, sections
- `settings.json` - Settings page content
- `dialogs.json` - Dialog boxes and modals
- `tasks.json` - Task/spec related content
- `onboarding.json` - Onboarding wizard content
- `welcome.json` - Welcome screen content

**Usage pattern:**
```tsx
import { useTranslation } from 'react-i18next';

// In component
const { t } = useTranslation(['navigation', 'common']);

// Use translation keys, NOT hardcoded strings
<span>{t('navigation:items.githubPRs')}</span>  // ✅ CORRECT
<span>GitHub PRs</span>                          // ❌ WRONG
```

**When adding new UI text:**
1. Add the translation key to ALL language files (at minimum: `en/*.json` and `fr/*.json`)
2. Use `namespace:section.key` format (e.g., `navigation:items.githubPRs`)
3. Never use hardcoded strings in JSX/TSX files

## Web Interface

PFactory is a browser-based web interface. This enables:
- Remote access from any device with a browser
- Server-based deployments
- Headless operation with web UI control

### Web Interface Architecture

```
apps/
├── web-server/     # FastAPI backend (Python) - REST API + WebSocket
└── frontend-web/   # React frontend (TypeScript/Vite) - Browser UI
```

### Quick Start (Web Interface)

```bash
# Terminal 1: Start the backend server
cd apps/web-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m server.main
# Note: Token is printed to console and saved to ~/.pfactory/.token

# Terminal 2: Start the frontend dev server
cd apps/frontend-web
npm install
npm run dev
```

Access the web UI at `http://localhost:3115` (or your server IP for remote access).

### Configuration

**Backend (`apps/web-server/.env`):**
```bash
APP_HOST=0.0.0.0      # Listen on all interfaces
APP_PORT=3114          # API server port
APP_DEBUG=true         # Enable Swagger docs at /docs
# APP_API_TOKEN=xxx    # Optional: Set fixed token (auto-generated if not set)
```

**Frontend (`apps/frontend-web/.env`):**
```bash
VITE_API_BASE_URL=/api                    # API base (proxied to backend)
VITE_WS_BASE_URL=ws://your-server:3114    # WebSocket URL for remote deployments
VITE_API_URL=http://localhost:3114        # Backend URL for Vite proxy
```

### Remote Access

For remote deployments:
1. Ensure ports 3114 (backend) and 3115 (frontend dev) are accessible
2. Set `VITE_WS_BASE_URL` to your server's WebSocket URL
3. Access via `http://YOUR_SERVER_IP:3115`

For production, build the frontend (`npm run build`) and serve from `apps/web-server/static/`.

### API Documentation

When `APP_DEBUG=true`:
- Swagger UI: `http://localhost:3114/docs`
- ReDoc: `http://localhost:3114/redoc`

See `apps/web-server/README.md` and `apps/frontend-web/README.md` for detailed documentation.

### Web Interface Troubleshooting

| Issue | Solution |
|-------|----------|
| "Claude Code not installed" | Hard refresh browser (`Ctrl+Shift+R`) |
| UI blocked/frozen | Check browser console for errors, restart servers |
| Can't add projects | Use project discovery dropdown or enter custom path |
| API errors | Verify token in `~/.pfactory/.token` |
| Git Repository Required keeps appearing | Click "Skip for now" or initialize git; state persists in localStorage |
| Usage shows NaN | Backend reads stats from `~/.claude/stats-cache.json` |
| New Task button not working | Ensure TaskCreationWizard is imported in App.tsx |
| Terminal 500 error | Check PTYManager creates sessions with UUID (don't pass `id=None`) |
| File editor 404/TypeError | Backend file routes return raw data, frontend extracts `.entries` or `.content` |
| Themes not changing | Ensure CSS theme variables exist in `index.css` with `@theme` block |
| Folder tree not expanding | FileTree needs `onLoadChildren` prop for lazy loading subdirectories |
| Task creation fails | Add POST endpoint at `/api/projects/{id}/tasks` in projects.py |
| Task start 404 | Mount execution.router at `/api/tasks` prefix (not `/api/execution`) in main.py |
| Task start 422 | Frontend must send `{}` body even when options are undefined (Pydantic needs JSON body for defaults) |
| Terminal resize 422 | Backend endpoint must use Pydantic model for `{ cols, rows }` body, not query params |
| Task stuck with "Stream closed" | Ensure `permission_mode="bypassPermissions"` is set in all `ClaudeAgentOptions` AND pfactory MCP tools are in permissions allow list (see `APP_TOOLS` in `models.py`) |
| Frontend shows task "stuck" but agent is working | File sync issue - worktree files not syncing to main spec dir. Fixed in agent_service.py with periodic sync every 3 seconds |
| Roadmap/Changelog 500 error | FastAPI `Path` shadowing `pathlib.Path` - use `from pathlib import Path as FilePath` at top of route files |
| Roadmap progress stuck at 0% | Roadmap generation was not implemented - see `roadmap_service.py` for the service pattern |
| Task options not applied (model/thinking) | Frontend saves to `requirements.json["metadata"]` but `phase_config.py` reads `task_metadata.json`. Fixed: `projects.py` now writes `task_metadata.json` on task creation |
| "Failed to parse insights JSON" in logs | Empty SDK response in `insight_extractor.py`. Fixed with empty response validation and brace-matching fallback |

### Web Interface Development Patterns

**API Response Wrapping:**
- Frontend `api-client.ts` wraps all responses in `{ success: true, data: <response> }`
- Backend endpoints should return raw data objects, NOT wrapped in `{success, data}`
- Exception: Error responses should return `{ success: false, error: "message" }`

**React useEffect Dependencies:**
- Avoid using object references (like `selectedProject`) as dependencies - they change on every render
- Use primitive values (like `selectedProjectId`) instead
- Use refs (`useRef`) to access state in effects without triggering re-runs

**localStorage for Persistent UI State:**
- Use localStorage for UI state that should survive page refresh (e.g., skipped dialogs)
- Initialize state with lazy initializer: `useState(() => loadFromStorage())`
- Update localStorage in state setter callback

**File Routes:**
- `/api/files/list?path=...` - List directory by absolute path (returns `{path, entries, parent}`)
- `/api/files/read?path=...` - Read file by absolute path (returns `{path, content, size, modified, language}`)
- `/api/files/discover?base_path=...` - Discover projects in directory

**Theme System (Tailwind v4):**
- CSS theme variables defined in `apps/frontend-web/src/index.css`
- Light mode: `:root { --background: ...; --foreground: ...; }`
- Dark mode: `.dark { --background: ...; }`
- Color themes: `[data-theme="ocean"]`, `[data-theme="forest"]`, etc.
- Tailwind v4 uses `@theme { --color-background: hsl(var(--background)); }` to map CSS vars
- App.tsx applies themes via `document.documentElement.classList.add('dark')` and `setAttribute('data-theme', theme)`

**Lazy Loading File Trees:**
- FileTree component should accept `onLoadChildren: (path: string) => Promise<FileNode[]>`
- Load children on folder expand, not upfront
- Cache loaded children in component state to avoid refetching

**Worktree File Synchronization:**
- Agent writes files to worktree: `.pfactory/worktrees/tasks/{spec-id}/.pfactory/specs/{spec-id}/`
- Frontend reads from main spec: `.pfactory/specs/{spec-id}/`
- `agent_service.py` syncs files every 3 seconds during task execution (`_sync_worktree_files()` method)
- Synced files: `test_plan.json`, `build-progress.txt`, `context.json`, `qa_report.md`, `spec.md`, `requirements.json`
- Final sync occurs when task completes
- Location: `apps/web-server/server/services/agent_service.py:207-303`

**Running Backend Scripts from Web Server:**
- Use service pattern (see `roadmap_service.py` as example)
- Set `PYTHONPATH` to include backend directory for imports to work
- Set `cwd` to backend directory when running scripts
- Use `sys.executable` (web server's Python) which shares dependencies
- Parse stdout for phase patterns to emit WebSocket progress events
- Example phases: `PHASE 1:`, `PHASE 2:`, etc. detected via regex

**Keyboard Shortcuts:**
- New terminal: `Ctrl+Shift+E` (all platforms) - not `Ctrl+T` which conflicts with browser
- Close terminal: `Ctrl+W` / `Cmd+W`

## Running the Application

**As a standalone CLI tool**:
```bash
cd apps/backend
python run.py --spec 001
```

**With the Web interface**:
```bash
# Start backend (port 3114)
cd apps/web-server && source .venv/bin/activate && python -m server.main

# Start frontend (port 3115)
cd apps/frontend-web && npm run dev
```

**Project data storage:**
- `.pfactory/specs/` - Per-project data (specs, plans, QA reports, memory) - gitignored
- `~/.pfactory/` - Web interface data (projects, settings, token) - for web UI only
