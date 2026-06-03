# PFactory Docker Container - Test & Deploy Steps

## Build & Run

```bash
cd <your-clone-dir>          # the directory you cloned PFactory into

# Build and start (clean)
sudo docker compose down -v && sudo docker compose build && sudo docker compose up -d

# Start (no rebuild)
sudo docker compose up -d
```

## Access

- **URL:** `http://localhost:${HOST_PORT:-3102}`
- **Token:** Auto-generated on first run, retrieve with:

```bash
sudo docker exec pfactory cat /home/nonroot/.pfactory/.token
```

## Useful Commands

```bash
# Check container status
sudo docker compose ps

# View logs (last 30 lines)
sudo docker logs pfactory --tail 30

# Follow logs in real time
sudo docker logs pfactory -f

# Shell into container (as pfactory user)
sudo docker exec -it pfactory bash

# Shell as root
sudo docker exec -it -u root pfactory bash

# Check Claude Code CLI inside container
sudo docker exec pfactory bash -l -c "claude --version"
```

## Stop & Clean Up

```bash
# Stop container (keeps volumes)
sudo docker compose down

# Stop and remove volumes (full reset)
sudo docker compose down -v

# Remove image too
sudo docker compose down -v --rmi all
```

## Environment Variables

Set in `docker-compose.yml` or `.env` file. Key vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_HOST` | `0.0.0.0` | Listen address |
| `APP_PORT` | `3102` | Server port |
| `APP_API_TOKEN` | (auto-generated) | Auth token for login |
| `APP_DEBUG` | `false` | Enable Swagger docs at `/docs` |
| `APP_DEFAULT_SHELL` | `/bin/bash` | Default terminal shell |
| `APP_MAX_TERMINALS` | `20` | Max concurrent terminals |

## Architecture

- **Base image:** Ubuntu 24.04
- **Runtime user:** `pfactory` (non-root)
- **Python venv:** `/home/projects/PFactory/.venv`
- **Node.js:** Copied from build stage (for frontend build + npm available at runtime)
- **Frontend:** Pre-built static files served from `apps/web-server/static/`
- **Data directory:** `/home/pfactory/.pfactory/` (persisted via Docker volume)

## Onboarding Flow

1. Login with token
2. Onboarding wizard launches automatically
3. Install Claude Code CLI (installs Node.js via fnm if needed, then `npm install -g @anthropic-ai/claude-code`)
4. Configure OAuth: runs `claude setup-token` in embedded terminal, auto-detects token from output
5. Add a project (default browse path: `/home`)
