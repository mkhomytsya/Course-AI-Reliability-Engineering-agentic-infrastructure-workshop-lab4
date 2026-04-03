# abox

> One command. Full AI infrastructure.

> **Note:** This is a copy of [den-vasyliev/abox](https://github.com/den-vasyliev/abox) made for educational purposes.

## Demo

▶️ ![Demo](docs/demo.gif)

`make run` gives you a local Kubernetes cluster with everything an AI project needs: an AI-aware API gateway, an agent runtime, observability, distributed tracing, and an eval harness — ready to use.

## What's included

| Component | Role |
|---|---|
| **agentgateway v2.2.1** | AI-aware API gateway (Gateway API–native, MCP-aware) |
| **kagent** | Kubernetes-native AI agent framework |
| **Flux CD 2.x** | GitOps/GitLessOps operator — keeps the cluster in sync with OCI artifacts |
| **KinD** | Local Kubernetes (1 control-plane + 2 workers) - can be any k8s |
| **cloud-provider-kind** | LoadBalancer support so gateway gets a real IP for local development |

## Quickstart

```bash
make run
```

That's it. Installs OpenTofu and k9s, provisions the cluster, bootstraps Flux, and reconciles all components. When it finishes:

```bash
kubectl get gateway,httproute -A        # gateway is up
kubectl get agents -n kagent            # agent runtime is up
kubectl get svc -n agentgateway-system  # grab the LoadBalancer IP
```

Point your AI app at the gateway IP on port 80.

## LLM secret setup (lab1 style)

This lab uses `kagent` with OpenAI provider credentials from a Kubernetes Secret.

1. Export your OpenAI API key:

```bash
read -s OPENAI_API_KEY && export OPENAI_API_KEY
```

2. Create or update secret `kagent-openai` in namespace `kagent`:

```bash
kubectl create secret generic kagent-openai \
  --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY" \
  -n kagent \
  --dry-run=client -o yaml | kubectl apply -f -
```

3. Ensure provider config in `releases/kagent.yaml` references the secret:

```yaml
providers:
  default: openAI
  openAI:
    provider: OpenAI
    model: "gpt-5-mini"
    apiKeySecretRef: kagent-openai
    apiKeySecretKey: OPENAI_API_KEY
```

4. Restart kagent so it picks up the new secret:

```bash
kubectl rollout restart deployment/kagent-controller -n kagent
```

5. Verify the setup:

```bash
kubectl get secret -n kagent kagent-openai
kubectl get pods -n kagent
kubectl get modelconfig -n kagent -o yaml
```

If your key is invalid or missing, kagent agents may start but LLM calls will fail at runtime.

## UI access

After `make run`, use the commands below to open the current web UIs.

### Flux UI

Flux UI is exposed by `flux-operator` on service port `9080`.

```bash
kubectl port-forward -n flux-system svc/flux-operator 19080:9080
# open http://localhost:19080/
```

### Kagent UI

Kagent UI is routed through `agentgateway-external` on path `/`.

```bash
kubectl get svc -n agentgateway-system agentgateway-external
# open http://<EXTERNAL-IP>/
```

If you prefer localhost access:

```bash
kubectl port-forward -n kagent svc/kagent-ui 18080:8080
# open http://localhost:18080/
```

### AgentGateway UI

The agentgateway proxy embeds a dashboard on port `15000`.

```bash
kubectl port-forward -n agentgateway-system deploy/agentgateway-external 15000:15000
# open http://localhost:15000/ui
```

From there you can inspect Listeners, Routes, Backends, Policies, and use the Playground.

## How it works

```
make run  →  scripts/setup.sh
  → tofu apply (bootstrap/)
      → KinD cluster
      → Flux Operator + FluxInstance
      → ResourceSetInputProvider   polls oci://ghcr.io/mkhomytsya/course-ai-reliability-engineering-agentic-infrastructure-workshop-lab2/releases
      → ResourceSet                creates OCIRepository + 2 Kustomizations
          → releases/crds/    gateway-api-crds, agentgateway-crds, kagent-crds
          → releases/         agentgateway (Gateway + GatewayClass)
                              kagent (agent runtime + HTTPRoute)
```

Everything after the cluster is **gitless GitOps via OCI**: no Git polling, no deploy keys. CI publishes `releases/` as an OCI artifact on every version tag. The cluster reconciles from that artifact automatically.

## Releasing

```bash
make push   # bumps patch version, tags, pushes → CI publishes OCI artifact → cluster reconciles
```

> **Note:** RSIP tag sorting is lexicographic. If the patch version would exceed 9, bump the minor instead: `git tag vX.Y+1.0`.

## Directory layout

| Path | Purpose |
|---|---|
| `bootstrap/` | OpenTofu: KinD + Flux bootstrap (operator, instance, RSIP, ResourceSet) |
| `releases/crds/` | CRD HelmReleases: gateway-api, agentgateway, kagent |
| `releases/` | App HelmReleases + Gateway + HTTPRoutes |
| `scripts/setup.sh` | Full setup script (`make run`) |
| `.github/workflows/flux-push.yaml` | CI: publish `releases/` as OCI artifact on `v*` tags |

## Adding components

1. Put CRD charts in `releases/crds/` as HelmReleases.
2. Put app charts in `releases/` as HelmReleases.
3. Run `make push` — the cluster reconciles automatically.

The CRD kustomization runs first (`wait: true`), apps run after (`dependsOn: releases-crds`). This ordering is enforced by Flux and must be preserved.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).

## License

Apache 2.0 — see [LICENSE](./LICENSE).
