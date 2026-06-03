# docker

> Source: [docker/docker](https://github.com/docker/docker) | Stars: 68k | Installs: 12,000 | Weekly: 11,800 | First seen: Jan 10, 2024

---

# Docker Development

You are an expert in Docker containerization, image building, and container orchestration for production deployments.

## When to Activate

Use when working with containerized applications:

- Creating Dockerfiles for new services
- Optimizing Docker image size and build time
- Setting up Docker Compose for local development
- Configuring multi-stage builds for production
- Implementing container security best practices

## Core Principles

- Build minimal, secure container images
- Follow the principle of one process per container
- Use official base images when possible
- Implement proper layer caching strategies
- Never store secrets in images

## Dockerfile Best Practices

### Multi-Stage Builds

Use multi-stage builds to reduce final image size significantly.

```dockerfile
# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.12-slim AS production

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /root/.local /root/.local
COPY . .

# Run as non-root user
RUN adduser --disabled-password --gecos "" appuser
USER appuser

ENV PATH=/root/.local/bin:$PATH
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Node.js Multi-Stage Build

```dockerfile
# Build stage
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production

COPY . .
RUN npm run build

# Production stage
FROM node:20-alpine AS production

WORKDIR /app
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
COPY package.json .

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

EXPOSE 3000
CMD ["node", "dist/index.js"]
```

## Layer Optimization

- Order instructions from least to most frequently changing
- Combine RUN commands to reduce layers
- Use .dockerignore to exclude unnecessary files
- Clean up package manager caches in the same layer

```dockerfile
# Efficient layer caching
FROM ubuntu:22.04

# System deps (rarely change) — layer 1
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# App deps (change occasionally) — layer 2
COPY requirements.txt .
RUN pip install -r requirements.txt

# Source code (changes frequently) — layer 3
COPY . .
```

## .dockerignore

```
# Version control
.git
.gitignore

# Dependencies
node_modules
__pycache__
*.pyc
.venv

# Build outputs
dist
build

# Dev/test files
*.test.js
*.spec.ts
tests/
.pytest_cache/

# Environment files
.env
.env.*
!.env.example

# IDE
.vscode
.idea
*.swp
```

## Docker Compose

### Development Configuration

```yaml
# docker-compose.yml
version: "3.9"

services:
  api:
    build:
      context: .
      target: development
    ports:
      - "8000:8000"
    volumes:
      - .:/app
    environment:
      - DATABASE_URL=postgresql+asyncpg://user:password@db:5432/mydb
      - DEBUG=true
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: mydb
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user -d mydb"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
```

## Security Best Practices

- Run containers as non-root user
- Use read-only file systems where possible
- Implement health checks
- Scan images for vulnerabilities with `docker scout`
- Use secrets management, not environment variables for sensitive data
- Implement resource limits (CPU, memory)

```dockerfile
# Security-hardened example
FROM python:3.12-slim

WORKDIR /app

# Create non-root user before copying files
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app appuser

# Install deps as root
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY --chown=appuser:appgroup . .

# Switch to non-root
USER appuser

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Useful Commands

```bash
# Build with build args
docker build --build-arg VERSION=1.0 -t myapp:latest .

# Run with resource limits
docker run -m 512m --cpus=1 -p 8000:8000 myapp:latest

# Inspect image layers
docker history myapp:latest

# Scan for vulnerabilities
docker scout cves myapp:latest

# Prune unused resources
docker system prune -af --volumes
```

## Best Practices Summary

- Use specific version tags, not `latest` in production
- Prefer slim or alpine variants for smaller size
- Scan base images for vulnerabilities regularly
- Consider distroless images for production
- Use multi-stage builds to minimize final image size
- Always define HEALTHCHECK for production containers
- Use `.dockerignore` to exclude unnecessary files from context
