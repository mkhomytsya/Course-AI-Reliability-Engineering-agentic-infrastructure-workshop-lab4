# abox — Workspace Instructions

abox is a **local AI infrastructure sandbox** — Kubernetes manifests, OpenTofu, and shell scripts only. No application code.

## Build & Operate

```bash
make run      # Install tools, provision cluster, bootstrap Flux, reconcile all
make down     # Destroy cluster
make push     # Bump patch version, tag, push → CI publishes OCI artifact
make apply    # cd bootstrap && tofu apply -auto-approve
```

**Platform**: Darwin-arm64 uses `bootstrap-darwin/` (k3d); others use `bootstrap/` (KinD).

**Verify after changes:**
```bash
flux get all                             # all resources must be Ready
kubectl get gateway,httproute -A        # gateway up
kubectl get agents -n kagent            # agent runtime up
```

## Architecture

See [CODEBASE.md](../CODEBASE.md) for full architecture, component roles, and directory layout.

**Two-phase reconciliation** (non-negotiable ordering):
1. `releases-crds` (`path: ./crds`, `wait: true`) — CRD HelmReleases
2. `releases` (`path: ./`, `dependsOn: releases-crds`) — App HelmReleases

**Release pipeline**: `make push` → git tag `v*` → CI (`flux-push.yaml`) publishes `releases/` as OCI artifact → RSIP detects new semver tag → ResourceSet reconciles.

## Adding a New Component

1. CRDs → `releases/crds/` with `install.crds: CreateReplace`
2. App → `releases/` with `dependsOn: [name: <crd-release>, namespace: <ns>]`
3. Define `Namespace` in the same file as the HelmRelease
4. Cross-namespace HTTPRoute → add `ReferenceGrant` in app namespace
5. Verify `flux get all` Ready, then `make push`

## Forbidden Patterns

| Pattern | Why |
|---|---|
| `ref.tag: latest` in any HelmRelease | Non-reproducible; Flux won't detect updates |
| App HelmRelease without `dependsOn` to CRD release | "no matches for kind" at reconcile time |
| HTTPRoute referencing gateway without ReferenceGrant | Route silently rejected |
| Namespace only in `releases/crds/` but HelmRelease in `releases/` | Namespace won't exist when app reconciles |
| Patch version > 9 without bumping minor | RSIP lexicographic sort breaks (`0.3.10 < 0.3.9`) |
| `hashicorp/kubernetes` provider for RSIP/ResourceSet | Breaks single-pass `tofu apply` — use `gavinbunney/kubectl` |
| Pushing without verifying `flux get all` Ready | Broken releases auto-reconciled into the cluster |
| `kagent` upgraded past 0.7.23 without verifying label values | Newer versions embed `+` build metadata in labels — Kubernetes rejects them |

## Conventions

- **Commit style**: Conventional commits — `feat:`, `fix:`, `chore:`, `docs:`
- **PR reviews**: Follow [REVIEW.md](../REVIEW.md) — produce a single consolidated review grouped by file with severity labels
- **HelmRelease namespace**: Use the component's own namespace, not `flux-system` (OCIRepository sources stay in `flux-system`)
- All RSIP tag filters use `^\d+\.\d+\.\d+$` — only clean semver, no pre-release or build metadata
