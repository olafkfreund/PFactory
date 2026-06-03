# syntax=docker/dockerfile:1.7
# pfactory-runner-cloud — #133 / #137
#
# Cloud misconfiguration + IaC assessment runner. Bundles Prowler (the primary
# CSPM engine — AWS/Azure/GCP/K8s, 500+ CIS checks, OCSF JSON output) and
# Checkov (IaC / terraform shift-left). boto3 + the AWS CLI come in via Prowler.
#
# Pairs with the framework registry entries (#138):
#   frameworks/cloud-prowler/descriptor.yaml → runtime.image: pfactory-runner-cloud:latest
#
# Read-only by design: PFactory only ever runs assessment/inventory commands.
# Credentials are bind-mounted READ-ONLY at runtime (e.g. ~/.aws:/home/pfactory/.aws:ro)
# and the run uses --network=bridge (cloud APIs need egress) under the egress gate.
#
# Security posture:
#   - Non-root user (pfactory, uid 1002).
#   - Creds mounted read-only; nothing written back to the cloud account.
#
# Build (from repo root):
#   docker build -f docker/pfactory-runner-cloud/Dockerfile \
#                -t pfactory-runner-cloud:latest \
#                docker/pfactory-runner-cloud
#
# Run (read-only assessment of AWS via a mounted profile):
#   docker run --rm \
#       --network=bridge \
#       -v ~/.aws:/home/pfactory/.aws:ro \
#       -v <scratch>:/scratch:rw \
#       -e AWS_PROFILE=Calitii \
#       pfactory-runner-cloud:latest \
#       prowler aws --service iam --output-formats json-ocsf --output-directory /scratch

FROM python:3.12-slim AS runner

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# ── OS-level deps ─────────────────────────────────────────────────────────
# ca-certificates: TLS to cloud APIs. git: Checkov reads git metadata. curl:
# health/diagnostics. build-essential is avoided — Prowler/Checkov ship wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Cloud assessment tooling ──────────────────────────────────────────────
# prowler: CSPM engine (pulls boto3 + the cloud SDKs). checkov: IaC scanning.
RUN pip install "prowler>=5,<6" "checkov>=3,<4"

# ── Azure CLI ─────────────────────────────────────────────────────────────
# Prowler's azure provider authenticates with --az-cli-auth, which delegates to
# AzureCliCredential → it shells out to `az`. The host's read-only ~/.azure login
# is mounted in at runtime (AZURE_CONFIG_DIR), so the `az` binary must be present.
RUN pip install "azure-cli>=2.60,<3"

# ── Filesystem + non-root ─────────────────────────────────────────────────
# /repo    — bind-mounted ro at runtime (IaC / terraform for Checkov)
# /scratch — bind-mounted rw at runtime (OCSF JSON findings, reports)
RUN useradd -m -u 1002 pfactory \
    && mkdir -p /repo /scratch \
    && chown pfactory:pfactory /repo /scratch

USER pfactory
WORKDIR /repo
ENV HOME=/home/pfactory

CMD ["prowler", "--version"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/PFactory"
LABEL org.opencontainers.image.description="PFactory cloud assessment runner — Prowler + Checkov"
LABEL org.pfactory.runner.framework="cloud"
LABEL org.pfactory.runner.lanes="integration"
