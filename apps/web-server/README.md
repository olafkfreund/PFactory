# AIFactory Web Server

FastAPI-based backend server that provides REST API and WebSocket endpoints for the AIFactory web interface.

## Overview

The web server enables running AIFactory without the Electron desktop app, allowing:
- Remote access from any browser
- Server-based deployments
- Headless operation

## Requirements

- Python 3.12+
- Claude Code CLI installed on the server

## Quick Start

```bash
# Install dependencies
cd apps/web-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env

# Start the server
python -m server.main
```

The server will:
1. Generate an API token on first run (saved to `~/.aifactory/.token`)
2. Start on `http://0.0.0.0:3102`
3. Print the token to console

## Configuration

Copy `.env.example` to `.env` and customize:

```bash
# Server settings
APP_HOST=0.0.0.0
APP_PORT=3102
APP_DEBUG=true

# SSL/HTTPS (optional)
APP_SSL_ENABLED=false
# APP_SSL_CERTFILE=/path/to/cert.pem
# APP_SSL_KEYFILE=/path/to/key.pem

# Authentication (auto-generated if not set)
# APP_API_TOKEN=your-secure-token-here

# CORS origins (for frontend access)
APP_CORS_ORIGINS=["http://localhost:3100"]

# Paths
# APP_BACKEND_PATH=/path/to/apps/backend
# APP_PROJECTS_DATA_DIR=/path/to/data

# Terminal
APP_DEFAULT_SHELL=/bin/bash
APP_MAX_TERMINALS=20

# Task execution
APP_MAX_CONCURRENT_TASKS=5
```

## HTTPS Support

Enable HTTPS for secure connections:

```bash
# Using auto-generated self-signed certificate
APP_SSL_ENABLED=true python -m server.main

# Using custom certificates
APP_SSL_ENABLED=true \
APP_SSL_CERTFILE=/path/to/cert.pem \
APP_SSL_KEYFILE=/path/to/key.pem \
python -m server.main
```

When SSL is enabled without custom certificates, self-signed certificates are auto-generated at `~/.aifactory/ssl/`. Your browser will show a security warning for self-signed certs.

## API Documentation

When `APP_DEBUG=true`, API docs are available at:
- Swagger UI: `http://localhost:3102/docs`
- ReDoc: `http://localhost:3102/redoc`

## API Endpoints

### Response Format

**Important:** API endpoints return data directly (not wrapped in `{success, data}`). The frontend `api-client.ts` automatically wraps responses in the `IPCResult` format:

```javascript
// Backend returns:
{ "installed": "2.0.76", "path": "/usr/bin/claude" }

// Frontend receives (after api-client.ts wrapping):
{ "success": true, "data": { "installed": "2.0.76", "path": "/usr/bin/claude" } }
```

### Core Routes

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check (no auth required) |
| `GET /api/projects` | List all projects |
| `POST /api/projects` | Add a project |
| `GET /api/projects/{id}/tasks` | Get project tasks |
| `POST /api/tasks/{id}/start` | Start a task |

### Settings Routes

| Endpoint | Description |
|----------|-------------|
| `GET /api/settings` | Get app settings |
| `PUT /api/settings` | Update settings |
| `GET /api/settings/tab-state` | Get saved tab state |
| `GET /api/settings/claude-profiles` | Get Claude profiles |
| `GET /api/settings/cli-tools` | Check installed CLI tools |

### File & Project Discovery Routes

| Endpoint | Description |
|----------|-------------|
| `GET /api/files/discover` | Discover projects in a folder |
| `GET /api/files/{projectId}/list` | List directory contents |
| `GET /api/files/{projectId}/read` | Read file content |
| `PUT /api/files/{projectId}/write` | Write file content |
| `GET /api/files/{projectId}/search` | Search files with ripgrep |

### Integration Routes

| Endpoint | Description |
|----------|-------------|
| `GET /api/github/cli/check` | Check GitHub CLI status |
| `GET /api/gitlab/cli/check` | Check GitLab CLI status |
| `GET /api/git/branches` | Get git branches |
| `GET /api/claude-code/version` | Check Claude Code CLI |
| `GET /api/ollama/status` | Check Ollama status |

### Project-Specific Routes

| Endpoint | Description |
|----------|-------------|
| `GET /api/projects/{id}/roadmap` | Get project roadmap |
| `GET /api/projects/{id}/ideation` | Get ideation data |
| `GET /api/projects/{id}/changelog` | Get changelog |
| `GET /api/projects/{id}/insights` | Get insights session |
| `GET /api/projects/{id}/context` | Get project context |
| `GET /api/projects/{id}/github/issues` | Get GitHub issues |
| `GET /api/projects/{id}/gitlab/issues` | Get GitLab issues |

### WebSocket Endpoints

| Endpoint | Description |
|----------|-------------|
| `WS /ws/events` | Global event broadcasting |
| `WS /ws/terminal/{id}` | Terminal I/O |
| `WS /ws/tasks/{id}/logs` | Task log streaming |
| `WS /ws/tasks/{id}/progress` | Task progress updates |

## Authentication

All API endpoints (except `/api/health`) require Bearer token authentication:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:3102/api/projects
```

WebSocket connections pass the token as a query parameter:
```
ws://localhost:3102/ws/events?token=YOUR_TOKEN
```

## Project Structure

```
apps/web-server/
├── server/
│   ├── main.py           # FastAPI app entry point
│   ├── config.py         # Settings management
│   ├── auth.py           # Token authentication
│   ├── routes/
│   │   ├── projects.py   # Project management
│   │   ├── tasks.py      # Task operations
│   │   ├── settings.py   # App settings
│   │   ├── files.py      # File operations
│   │   ├── terminal.py   # Terminal management
│   │   ├── github.py     # GitHub integration
│   │   ├── gitlab.py     # GitLab integration
│   │   ├── roadmap.py    # Roadmap/Ideation
│   │   ├── changelog.py  # Changelog/Insights
│   │   ├── context.py    # Context/Memory
│   │   └── git.py        # Git/Ollama/MCP/Claude CLI
│   ├── websockets/
│   │   ├── events.py     # Global event broadcast
│   │   ├── terminal.py   # Terminal WebSocket
│   │   ├── logs.py       # Log streaming
│   │   └── progress.py   # Progress updates
│   └── services/
│       └── ...           # Business logic
├── static/               # Built frontend (after npm run build)
├── requirements.txt
└── .env.example
```

## Remote Access

The server listens on all interfaces (`0.0.0.0`) by default. For remote access:

1. Ensure port 3102 is open in your firewall
2. Access via `http://YOUR_SERVER_IP:3102`
3. Use the frontend at `http://YOUR_SERVER_IP:3100` (dev) or serve built files

## Data Storage

Project data is stored in `~/.aifactory/`:
- `projects.json` - Registered projects
- `settings.json` - App settings
- `tab-state.json` - UI tab state
- `claude-profiles.json` - Claude profiles
- `.token` - API authentication token
- `ssl/` - Auto-generated SSL certificates (when HTTPS enabled)

## Development

```bash
# Run with auto-reload
APP_DEBUG=true python -m server.main

# Run tests
pytest tests/
```

## Integration with Frontend

The web server is designed to work with `apps/frontend-web`. In development:

1. Start the backend: `python -m server.main` (port 3102)
2. Start the frontend: `cd ../frontend-web && npm run dev` (port 3100)
3. Frontend proxies API calls to backend via Vite config

For production, build the frontend and it will be served from `static/`:
```bash
cd ../frontend-web
npm run build  # Outputs to ../web-server/static/
```
