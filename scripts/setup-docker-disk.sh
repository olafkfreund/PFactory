#!/usr/bin/env bash
# =============================================================================
# PFactory — Create a size-limited loopback disk for container data
# =============================================================================
# Creates a 2 GB ext4 disk image and mounts it under ~/docker-disks/.
# The container bind-mounts this directory, enforcing a hard disk quota.
#
# Path: ~/docker-disks/<name>-disk.img  →  ~/docker-disks/<name>-data
#
# NOTE: Uses ~/docker-disks/ (not /opt/) because snap-based Docker can only
# access paths under /home/. If Docker is installed via apt, /opt/ works too.
#
# Usage:
#   sudo bash scripts/setup-docker-disk.sh <name>            # Create & mount
#   sudo bash scripts/setup-docker-disk.sh <name> teardown   # Unmount & remove
#
# The <name> matches the INSTANCE_NAME in docker-compose (default: pfactory).
#
# Examples:
#   sudo bash scripts/setup-docker-disk.sh pfactory        # Default instance
#   sudo bash scripts/setup-docker-disk.sh client-acme        # Second instance
#   sudo bash scripts/setup-docker-disk.sh client-acme teardown
#
# The script is idempotent — safe to re-run.
# =============================================================================

set -euo pipefail

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------
NAME="${1:-}"
ACTION="${2:-setup}"

if [[ -z "$NAME" ]]; then
    echo "Usage: $0 <instance-name> [teardown]" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  $0 pfactory           # Create disk for default instance" >&2
    echo "  $0 client-acme          # Create disk for a second instance" >&2
    echo "  $0 client-acme teardown # Remove disk for that instance" >&2
    exit 1
fi

# Validate name (alphanumeric, hyphens, underscores only)
if [[ ! "$NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "Error: Instance name must contain only letters, numbers, hyphens, and underscores." >&2
    exit 1
fi

# Use ~/docker-disks/ so snap Docker can access the mount (snap has /home/ access)
BASE_DIR="${SUDO_USER:+$(eval echo ~$SUDO_USER)}/docker-disks"
DISK_IMG="${BASE_DIR}/${NAME}-disk.img"
MOUNT_DIR="${BASE_DIR}/${NAME}-data"
SIZE_MB=2048  # 2 GB

# Ensure base directory exists
mkdir -p "$BASE_DIR"

# --------------------------------------------------------------------------
# Teardown
# --------------------------------------------------------------------------
if [[ "$ACTION" == "teardown" ]]; then
    echo "Tearing down disk for instance '$NAME'..."
    if mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
        umount "$MOUNT_DIR"
        echo "  Unmounted $MOUNT_DIR"
    fi
    if [[ -f "$DISK_IMG" ]]; then
        rm -f "$DISK_IMG"
        echo "  Removed $DISK_IMG"
    fi
    if [[ -d "$MOUNT_DIR" ]]; then
        rmdir "$MOUNT_DIR" 2>/dev/null || echo "  $MOUNT_DIR not empty, kept"
    fi
    # Remove fstab entry
    if grep -q "$DISK_IMG" /etc/fstab 2>/dev/null; then
        sed -i "\|$DISK_IMG|d" /etc/fstab
        echo "  Removed fstab entry"
    fi
    echo "Done."
    exit 0
fi

# --------------------------------------------------------------------------
# Must run as root
# --------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (sudo)." >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Create disk image
# --------------------------------------------------------------------------
if [[ -f "$DISK_IMG" ]]; then
    echo "Disk image already exists: $DISK_IMG"
else
    echo "Creating ${SIZE_MB} MB disk image at $DISK_IMG ..."
    dd if=/dev/zero of="$DISK_IMG" bs=1M count="$SIZE_MB" status=progress
    echo "Formatting as ext4..."
    mkfs.ext4 -q "$DISK_IMG"
fi

# --------------------------------------------------------------------------
# Mount
# --------------------------------------------------------------------------
mkdir -p "$MOUNT_DIR"

if mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
    echo "Already mounted at $MOUNT_DIR"
else
    echo "Mounting $DISK_IMG at $MOUNT_DIR ..."
    mount -o loop "$DISK_IMG" "$MOUNT_DIR"
fi

# --------------------------------------------------------------------------
# Persist in fstab (survives reboot)
# --------------------------------------------------------------------------
if ! grep -q "$DISK_IMG" /etc/fstab 2>/dev/null; then
    echo "Adding fstab entry for auto-mount on boot..."
    echo "$DISK_IMG  $MOUNT_DIR  ext4  loop,defaults  0  0" >> /etc/fstab
fi

# --------------------------------------------------------------------------
# Set ownership to match container user (UID 1001 = pfactory)
# --------------------------------------------------------------------------
chown 1001:1001 "$MOUNT_DIR"

echo ""
echo "Done! Instance '$NAME' — disk quota: ${SIZE_MB} MB"
echo "  Image:  $DISK_IMG"
echo "  Mount:  $MOUNT_DIR"
echo ""
echo "Use in docker-compose:"
echo "  INSTANCE_NAME=$NAME docker compose up -d"
echo ""
df -h "$MOUNT_DIR"
