#!/bin/bash
set -euo pipefail

LOG=/tmp/setup-darwin.log
exec > >(tee -a "$LOG") 2>&1

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "=== abox setup (darwin) start ==="

# Verify required tools
for cmd in k3d kubectl tofu; do
  if ! command -v "$cmd" &>/dev/null; then
    log "ERROR: $cmd not found. Please install it first."
    exit 1
  fi
done

# Initialize Tofu
log "Running tofu init..."
cd bootstrap-darwin
tofu init
log "tofu init done"

log "Running tofu apply..."
tofu apply -auto-approve
log "tofu apply done"

export KUBECONFIG=~/.kube/config

cd ..

log "=== setup (darwin) complete ==="
