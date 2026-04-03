# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

abox is a local AI infrastructure sandbox. `make run` provisions a KinD/k3d cluster and reconciles a full AI stack via Flux GitOps. There is no application code — the repo is entirely Kubernetes manifests, OpenTofu, and shell scripts.

## Commands

```bash
make run      # Full setup: install tools, provision cluster, bootstrap Flux, reconcile all
make down     # Destroy cluster
make push     # Bump patch version, tag, push (triggers CI to publish OCI artifact)
make tools    # Install OpenTofu & k9s only
make tofu     # cd bootstrap && tofu init
make apply    # cd bootstrap && tofu apply -auto-approve
```

**Platform detection**: Darwin-arm64 uses `bootstrap-darwin/` (k3d), others use `bootstrap/` (KinD).

**Verification commands**:
```bash
kubectl get gateway,httproute -A           # verify gateway is up
kubectl get agents -n kagent               # verify agent runtime is up
flux get all                               # verify all Flux resources are Ready
```

## Architecture

### Bootstrap Flow

```
tofu apply (bootstrap/)
  → KinD cluster (1 control-plane + 2 workers)
  → helm: flux-operator
  → helm: flux-instance (wait=true)
  → kubectl_manifest: RSIP (polls OCI registry for semver tags)
  → kubectl_manifest: ResourceSet (creates OCIRepository + 2 Kustomizations)
```

### Gitless GitOps via OCI

No Git polling. CI publishes `releases/` as OCI artifact on v* tags. RSIP detects new tags, ResourceSet reconciles.

### Two-Phase Reconciliation

1. **releases-crds** (path: `./crds`, wait: true) — CRD HelmReleases
2. **releases** (path: `./`, dependsOn: releases-crds) — App HelmReleases

This ordering is non-negotiable. Apps reference CRD types that must exist first.

### Component Layout

| Component | Namespace | Role |
|---|---|---|
| agentgateway | `agentgateway-system` | AI-aware API gateway (v2.2.1) |
| Gateway `agentgateway-external` | `agentgateway-system` | Ingress point, port 80, allows routes from all namespaces |
| kagent | `kagent` | AI agent runtime (0.7.23 pinned) |
| HTTPRoute `kagent` | `kagent` | Routes `/api` → MCP (8083), `/` → UI (8080) |

## Key Design Decisions

**`gavinbunney/kubectl` provider required** — For RSIP and ResourceSet manifests because it skips CRD schema validation at plan time. Using `hashicorp/kubernetes` breaks single-pass `tofu apply`.

**kagent pinned to 0.7.23** — Newer versions embed `+` build metadata in label values, which Kubernetes rejects. Do not upgrade without verifying label values are clean semver.

**No github_token in Terraform** — Flux bootstrapped via Helm, not `flux_bootstrap_git`. Avoids deploy keys/PATs in state.

**RSIP uses lexicographic tag sorting** — `0.3.10` < `0.3.9` alphabetically. Bump minor (not patch) when patch would exceed 9.

## Forbidden Patterns

| Pattern | Why |
|---|---|
| `ref.tag: latest` in any HelmRelease | Non-reproducible; Flux won't detect updates |
| App HelmRelease without `dependsOn` to CRD release | CRD may not exist when app reconciles → "no matches for kind" |
| HTTPRoute referencing gateway in another namespace without ReferenceGrant | Route silently rejected |
| Namespace only in `releases/crds/` but HelmRelease in `releases/` | Namespace won't exist when app reconciles |
| Patch version > 9 without bumping minor | RSIP lexicographic sort breaks |
| `hashicorp/kubernetes` for RSIP/ResourceSet | Breaks single-pass apply |
| Pushing without verifying `flux get all` shows Ready | Broken releases published and auto-reconciled |

## Adding a New Component

1. **CRDs**: Add HelmRelease to `releases/crds/` with `install.crds: CreateReplace`
2. **App**: Add HelmRelease to `releases/` with `dependsOn: [name: <crd-release>, namespace: <ns>]`
3. **Namespace**: Define in the same file as the HelmRelease (both kustomizations reconcile separately)
4. **Cross-namespace routing**: Add ReferenceGrant in app namespace when HTTPRoute references the gateway
5. **Test**: `make run`, verify `flux get all` shows Ready
6. **Release**: `make push`

## Commit Style

Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`
