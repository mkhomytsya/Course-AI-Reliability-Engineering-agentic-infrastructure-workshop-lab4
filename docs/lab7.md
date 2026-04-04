# Lab 7 — Vin's Questions: AI Infrastructure Assessment

Answers based on the **abox** sandbox (kagent `0.7.23`, agentgateway `v2.2.1`, Flux CD, Gateway API, OCI-based GitOps) and the full course stack if implemented: llm-d, Arize Phoenix, Inference Gateway, A2A governance, MCP Sampling, Kyverno, and Variant Autoscaler.

---

## 1. How could we handle "agent got stuck" scenarios?

The architecture addresses this at multiple levels — guardrails prevent it, quarantine isolates it, trajectory evals detect it, and the controller + K8s reconciliation loop recover from it.

### Agent runtime level

- **EventHub quarantine** — events that trigger anomalous or looping behavior get isolated into a quarantine queue with a `DrainQ()` function, preventing them from re-entering the main processing pipeline
- **Controller `startAgent()` / `stopAgent()`** — explicit methods on the Config CRD allow the runtime (or an external operator) to kill a stuck agent programmatically
- **Embedded Guardrails** — safety net when the ADK's `Runner.Run()` loop doesn't converge naturally
- **EventHub dedup & batch** — prevents duplicate events from spawning parallel stuck loops

### Observability level

- **Trajectory evaluation** — the Evaluation Suite validates tool call sequences, detecting when an agent's calling pattern deviates from expected behavior (e.g., calling the same tool repeatedly without progress)
- **Online evals via Phoenix** — feeds trajectory anomalies into real-time alerts

### Infrastructure level

- **Kubernetes reconciliation loop** — acts as supervisor: if an agent pod enters CrashLoopBackOff or exceeds resource limits, K8s handles restart/termination automatically
- **kagent `maxSteps`** — hard cap on reasoning iterations per task, preventing infinite tool-call loops
- **Gateway-level timeouts** — `HTTPRoute` timeouts return 504 if an agent doesn't respond within the configured window
- **Human-in-the-loop** — kagent's `humanInTheLoop` flag pauses after each tool call for human approval, preventing runaway loops entirely

---

## 2. Any automatic timeout/circuit breaker patterns from this framework?

There's no single "enable circuit breaker" toggle — but the pattern emerges from multiple layers composing **timeout + circuit breaking + recovery** into a layered resilience stack.

### Timeout mechanisms

| Layer | Mechanism |
|---|---|
| **MCP connection timeout** | Built-in configuration (kagent's "Add MCP Server" UI exposes explicit "Connection Timeout e.g. 30s" field). SSE sessions include Progress Notifications as heartbeats — absence triggers timeout |
| **Inference Gateway queue depth** | When a model replica's queue saturates, the Inference Scheduler stops routing to it — acts as natural circuit breaker |
| **Gateway API** | `HTTPRoute.spec.rules[].timeouts` — per-route request/backend timeouts |
| **Flux CD** | HelmRelease `timeout` and `retries` — failed reconciliation triggers exponential backoff and automatic rollback |

### Circuit breaking (open-circuit → fallback)

| Layer | Behavior |
|---|---|
| **Model failover chain** | When primary provider fails, gateway redirects to secondary/tertiary — effectively open-circuit → fallback |
| **Endpoint Picker health filtering** | Inference Gateway filters out unhealthy replicas based on load reports — unhealthy endpoints are ejected |
| **Embedded Guardrails** | Agent runtime prevents runaway loops — application-level safety net |

### Recovery

| Layer | Mechanism |
|---|---|
| **Variant Autoscaler** | Responds to saturation measurements by scaling replicas up or down |
| **K8s liveness/readiness probes** | Health detection on vLLM/agent pods feeds into gateway routing decisions. Stuck containers are killed and restarted |
| **Flux reconciliation loop** | Continuously reconciles desired state — self-healing by design |

---

## 3. How does kgateway handle model failover?

kgateway handles failover at **two distinct scopes** — all declaratively configured, all observable through Model Telemetry, and all transparent to the agent making the request.

### Macro-level: LLM Consumption layer (provider failover)

The AI Gateway provides Model Failover, Model Telemetry, Prompt Guardrails, and Access Control. This is where the **OpenAI → Claude → local** failover chain lives — routing between external API providers based on availability, error rates, or policy rules.

- **Backend weight shifting** — `HTTPRoute.backendRefs` supports `weight` fields for traffic splitting across providers
- **Retry policies** — configurable attempts and backoff when a provider returns 5xx
- **Endpoint health filtering** — unhealthy upstreams are automatically ejected from the routing pool

### Micro-level: Inference layer (replica failover for self-hosted models)

For self-hosted models, the Gateway API Inference Extension handles failover more granularly:

```
Client request → Inference Gateway → Body-based routing (inspects model name in OAI-compatible body)
  → Inference Extensions → Endpoint Picker → optimal model replica
```

| K8s Resource | Role |
|---|---|
| **InferenceModel** CRD | Defines model with name and **criticality** level |
| **InferencePool** | Groups model replicas — failover happens at the pool level |
| **Variant Autoscaler** | Adjusts replica counts based on capacity bounds and saturation |
| **Endpoint Picker** | Selects optimal replica based on model server load reports |

Each model server reports its load back to the scheduler, enabling **predicted latency balancing** — not just round-robin selection.

> kgateway provides macro-level failover (switch between providers entirely) and micro-level failover (pick the best replica within a self-hosted pool).

---

## 4. Can we automatically switch from OpenAI to Claude to local model?

Yes — this is exactly what the **AI Gateway's model failover** capability is built for. The switch isn't just automatic, it's policy-driven and observable.

### How it works in the architecture

The AI Gateway layer handles LLM consumption with failover and policy enforcement, while kagent explicitly supports both **Hosted (API) and Self-Hosted (llm-d) Models** as Kubernetes resources. You define a failover chain declaratively:

```
Primary → OpenAI
Secondary → Claude  
Tertiary → local model (vLLM/llm-d InferencePool)
```

The gateway automatically switches when a provider:
- Returns errors or becomes unavailable
- Hits rate limits
- Exceeds latency SLOs
- Breaches your budget policy

### Why the switch is transparent

All three backends expose (or are fronted by) an **OpenAI-compatible API** — hosted providers natively, self-hosted vLLM/llm-d by design. The **LLM providers Auth** layer manages credentials for each provider independently. The **prompt enrichment** and **prompt guards** layers normalize the request before it hits any provider.

### Policy-driven routing by criticality

You can route by prompt criticality — not just failover:
- **High-priority** requests → best hosted model (e.g., GPT-5)
- **Low-priority / batch** tasks → cheaper local model via InferencePool

All decided by the Inference Gateway's scheduling policies, not by application code.

### In the current abox setup

kagent supports multiple provider definitions in the `ModelConfig` CRD:

```yaml
providers:
  default: openAI
  openAI:
    provider: OpenAI
    model: "gpt-5-mini"
    apiKeySecretRef: kagent-openai
  anthropic:
    provider: Anthropic
    model: "claude-sonnet-4-20250514"
    apiKeySecretRef: kagent-anthropic
  local:
    provider: OpenAI          # vLLM/llm-d exposes OpenAI-compatible API
    model: "meta-llama/Llama-3-8B"
    baseURL: "http://vllm.local-models.svc:8000/v1"
```

---

## 5. Could we seamlessly handle response formats from these providers?

Yes — you don't handle provider response format differences in your application code. The gateway stack absorbs them, and your agents only see a unified interface.

### Three layers of format normalization

| Layer | How it normalizes |
|---|---|
| **AI Gateway (agentgateway)** | Sits between agents and LLM providers (OpenAI, Claude, Gemini, etc.). Exposes an **OpenAI-compatible API** — agents always speak one format regardless of which backend provider actually serves the response. Model failover and policy enforcement happen here transparently |
| **MCP protocol** | Model-agnostic by design — JSON-RPC 2.0 uniform interface. Tool calls and responses follow the same structured format regardless of which LLM powers the agent underneath |
| **A2A protocol** | Inter-agent communication follows the A2A spec (Task → Message → Part → Artifact) regardless of each agent's internal LLM provider. JSON-RPC 2.0 over HTTP Streaming |

### Self-hosted models

vLLM and llm-d natively expose the OpenAI-compatible `/v1/chat/completions` endpoint, making them **drop-in replacements** for any agent expecting the OpenAI format. No adapter code needed.

### Where it matters in abox

- **kagent** abstracts the provider at the CRD level — the agent developer works with a single interface regardless of backend
- **Google ADK** (used by `coach-agent`) supports `openai/`, `anthropic/`, `google/` model prefixes and normalizes responses internally — switching models requires changing only the model string, no code changes

---

## 6. Can we version the agents built from kagent?

Yes, agents are versioned through multiple mechanisms:

- **OCI artifact versioning** — the entire `releases/` directory (including Agent CRDs) is published as an OCI artifact with a semver tag (e.g., `0.3.7`). Every change creates a new immutable version.
- **Git tags** — `make push` bumps the patch version and creates a git tag. Full history of every agent configuration change is preserved.
- **BYO agent container versioning** — custom agents like `coach-agent` have their own image tags (`ghcr.io/.../coach-agent:${coach_agent_version}`). The image reference in the Agent CRD is explicit.
- **Flux rollback** — if a new agent version breaks, Flux can roll back to the previous OCI artifact version. The `HelmRelease` supports `upgrade.remediation.retries` with automatic rollback.
- **Kubernetes-native** — Agent CRDs are standard K8s objects. You can use `kubectl diff` and GitOps audit logs to track every version.

```yaml
# Agent CRD references a specific container version
spec:
  byo:
    deployment:
      image: ghcr.io/owner/repo/coach-agent:1.2.3
```

---

## 7. Any blue/green or canary deployment patterns for agents?

Yes, the stack supports both:

### Canary via Gateway API traffic splitting

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: kagent-canary
  namespace: kagent
spec:
  parentRefs:
  - name: agentgateway-external
    namespace: agentgateway-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /api
    backendRefs:
    - name: kagent-controller-stable
      port: 8083
      weight: 90
    - name: kagent-controller-canary
      port: 8083
      weight: 10
```

### Blue/green via Flux

- Deploy the new version as a separate HelmRelease (e.g., `kagent-green`) in the same or different namespace.
- Test via a separate HTTPRoute.
- Switch the primary HTTPRoute's `backendRefs` to point to the green deployment.
- Remove the old (blue) release.

---

## 8. What's the fastmcp-python framework mentioned?

**FastMCP** is a Python framework for building MCP (Model Context Protocol) servers with minimal boilerplate. It's now the official Python SDK for MCP (merged into `mcp` package as `mcp.server.fastmcp`).

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-tools")

@mcp.tool()
def get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Sunny in {city}"

# Runs as stdio or HTTP Streaming MCP server
mcp.run()
```

Key features:
- **Decorator-based** — `@mcp.tool()`, `@mcp.resource()`, `@mcp.prompt()` to expose capabilities
- **Auto-generates** JSON schema from Python type hints
- **Transport-agnostic** — supports stdio (for local tools) and HTTP Streaming (for remote deployment)
- **Composable** — servers can be mounted into other servers

In the abox context, FastMCP tools can be deployed as K8s services and registered as `MCPServer` CRDs in kagent, making them available to any agent in the cluster.

---

## 9. Is it the easiest path to MCP?

Yes, FastMCP is currently the **lowest-friction path** to building an MCP server:

| Approach | Lines of code for a basic tool server | Complexity |
|---|---|---|
| **FastMCP (Python)** | ~10 lines | Minimal — decorators + type hints |
| Raw MCP SDK (Python) | ~50–80 lines | Manual schema definition, handler wiring |
| TypeScript MCP SDK | ~30–40 lines | Good, but requires TS toolchain |
| **kagent declarative tools** | ~15 lines YAML | Zero code — define tools as K8s CRDs |

For this sandbox, the easiest paths ranked:
1. **kagent built-in tools** — zero-code, just YAML (`MCPServer` CRD)
2. **FastMCP** — minimal Python, ideal for custom logic
3. **Google ADK** — used by `coach-agent`, slightly more structure but supports A2A + MCP

---

## 10. About FinOps: how much control can I have?

AI infrastructure is OpEx — control is distributed across architectural layers, not a single switch.

| Layer | FinOps Controls |
|---|---|
| **Gateway (L7/L8)** | Token-based rate limits, model failover policies, prompt criticality routing to prioritize GPU allocation |
| **Protocol (MCP)** | MCP Sampling — delegate LLM calls to the client, so they pay for tokens instead of you |
| **Inference (llm-d/vLLM)** | Optimize cost-per-token via intelligent scheduling, prefix-cache aware routing, prefill/decode disaggregation |
| **Platform (Kubernetes)** | Resource quotas, DRA (Dynamic Resource Allocation) for GPUs, variant autoscaling for inference pools |
| **Observability** | GPU/token utilization tracing, AI-centric SLOs (TTFT, TPOT), Arize Phoenix — you can only control what you can measure |

**In the abox stack specifically:**
- **Pod resource limits** — each agent declares `requests`/`limits` (coach-agent: 200m–1000m CPU, 512Mi–1Gi memory)
- **agentgateway rate limiting** — per-route request rate limits prevent excessive upstream API calls
- **Provider-level controls** — OpenAI/Anthropic dashboards provide spend limits, usage caps, alerts per API key
- **Prometheus + Grafana** — agentgateway exports metrics (request count, latency, error rate) for cost dashboards

**Strategic dimension: Build vs Buy vs Open Source** — shapes your cost structure between CapEx-heavy self-hosting (vLLM/llm-d on your GPUs) and pure OpEx API consumption (OpenAI/Anthropic). The abox stack supports both models and hybrid approaches.

> You can have a lot of control, but it's distributed across architectural layers rather than being a single switch.

---

## 11. Token level / per-agent level controls

The architecture supports both granularities as first-class concerns.

### Token-level controls

| Mechanism | What it does |
|---|---|
| **agentgateway token-based rate limits** | Enforces per-consumer token caps via JWT auth — cap how many tokens a specific user or team consumes |
| **Inference Gateway prompt criticality** | Scores prompt criticality and uses queue depth awareness to prioritize which requests get GPU resources first |
| **`max_tokens` / `max_completion_tokens`** | Per-request caps on LLM response length, set in agent's model configuration |
| **kagent `maxSteps`** | Indirectly limits token consumption by capping reasoning iterations |
| **Observability (llm-d metrics, Arize Phoenix)** | Traces token utilization per request — full visibility into input/output token spend |

### Per-agent level controls

Each agent in the abox ecosystem is a **discrete Kubernetes resource** (kagent CRD) with its own identity, giving fine-grained control:

- **RBAC & tool access** — restrict which MCP tools an agent can call, reducing unnecessary token-heavy tool invocations
- **A2A governance** — skill-based authorization controls which agents can delegate to which other agents
- **Separate Inference Pools** — route each agent through different pools with different models or LoRA adapters
- **Independent tracing** — each agent's entire tool-call trajectory is traced independently via OTLP traces to Phoenix
- **MCP Sampling** — choose per-agent whether LLM calls are paid by the server or delegated to the client
- **Resource isolation** — dedicated pod resources per agent with K8s `requests`/`limits`

```yaml
# Per-agent resource and identity isolation
spec:
  byo:
    deployment:
      resources:
        limits:
          cpu: "1000m"
          memory: "1Gi"
      env:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: agent-specific-key  # separate key = separate billing
```

> Tokens are metered and capped at the gateway, while agents are governed, traced, and resource-isolated as first-class Kubernetes objects.

---

## 12. Can I implement custom cost controls?

Absolutely — the Kubernetes-native, CRD-driven architecture practically invites you to build them. Multiple layers are available:

1. **Custom Kubernetes controller** — write a controller (using client-go, Informers, and the Reconciliation Loop pattern) that watches your own CRDs to enforce budget policies. For example, a `TokenBudget` CR per team or agent that the controller reconciles by disabling agents or scaling down Inference Pools when limits are breached.

2. **Gateway-layer token caps** — agentgateway already supports configurable token-based rate limits and JWT-scoped policies. Set per-consumer caps declaratively — no custom code needed.

3. **Inference Gateway custom scheduling** — the Inference Gateway's customizable scheduling policies (via Gateway API Inference Extension) let you define your own scorers and filterers for request routing based on cost signals like KV cache utilization and queue depth.

4. **Admission-time policy enforcement (Kyverno)** — validate or mutate agent CRDs before they're created. Example: reject any agent that doesn't have a cost label or resource limit. Declarative, no webhook code required.

5. **A2A governance layer** — traffic management and policy enforcement for inter-agent communication. Controls which agents can delegate to which, preventing runaway cost multiplication through uncontrolled delegation chains.

6. **Automated cost-control pipeline** — since all token consumption flows through OTLP traces to Phoenix/Grafana, you can build custom alerting:

```
OTLP traces → Phoenix/Grafana → Alertmanager webhooks → Event Hub → Triage Agent
```

This triggers automated cost-control actions when spend anomalies are detected — closing the loop from observability to remediation.

---

## 13. Per-agent budgets or depth of token limits

The architecture supports both dimensions, forming a **horizontal × vertical control matrix**.

### Per-agent budgets (horizontal axis)

Every agent is a first-class Kubernetes resource (kagent CRD) with its own identity (JWT auth), dedicated MCP tools list, and independent OTLP traces. This enables:

- **Agent Gateway token rate limits** — scoped to each agent's JWT identity, not just API keys
- **Cost attribution via observability** — Phoenix traces and llm-d GPU/token utilization metrics let you attribute cost precisely per agent
- **MCP Sampling** — delegate LLM costs to the client per agent, shifting who pays

### Depth of token limits (vertical axis)

Control happens at multiple layers of the inference chain:

| Layer | Depth Control |
|---|---|
| **Agent Gateway** | Caps total tokens per consumer across all requests |
| **AI Gateway** | Enforces policy on LLM consumption — including model failover to cheaper models when budgets are tight |
| **Inference Gateway** | Manages queue depth and prompt criticality — prioritize or throttle requests based on how deep into a multi-step workflow they are |
| **kagent `maxSteps`** | Hard cap on reasoning iterations per task |
| **A2A governance** | Action-level authorization and traffic management — limit how many downstream A2A calls (and therefore tokens) an orchestrating agent can trigger |

### The control matrix

```
                    Per-agent identity (horizontal)
                    Agent A    Agent B    Agent C
Depth (vertical)
  ├── Gateway       100K/day   50K/day    unlimited
  ├── AI Gateway    gpt-5      gpt-5-mini local-llama
  ├── Inference     high-pri   low-pri    batch
  ├── maxSteps      20         10         5
  └── A2A depth     3 hops     1 hop      0 (no delegation)
```

> Horizontal (per-agent identity) × vertical (depth of tool calls and inference chain), all observable and enforceable through the Kubernetes-native gateway stack.

---

## 14. vLLM suitable for agents with many back-and-forth tool calls, or is it better for single-shot inference?

**Both.** vLLM is built for high-throughput, high-concurrency production serving (1,000+ concurrent requests with strict SLAs) — not limited to single-shot inference at all. Deploy vLLM as the inference engine, llm-d as the intelligent scheduler on top. vLLM provides the raw serving power; llm-d makes it agentic-aware. Together they handle back-and-forth tool calls efficiently.

### vLLM's native features for multi-turn

- **PagedAttention** — manages KV cache like virtual memory, preventing GPU memory waste as conversation history grows across tool calls
- **Continuous batching** — new requests are inserted into running batches without waiting, keeping GPU utilization high even with sequential per-agent call patterns
- **Prefix caching** — shared system prompts (common across kagent agents) are cached in GPU memory, avoiding redundant computation

### What vLLM doesn't solve alone

vLLM treats each incoming request independently — it doesn't know that call #8 from the same agent session should go to the replica holding the KV cache from calls #1–7. That's where **llm-d** adds the intelligent scheduling layer on top (see Q15 for the scheduler deep-dive on prefix-cache routing, predicted latency balancing, and O(n²)→O(n) complexity reduction).

### When to consider SGLang instead

| Engine | Best for |
|---|---|
| **vLLM + llm-d** | Production serving with mixed workloads (single-shot + agentic), high concurrency, strict SLAs |
| **SGLang** | Heavy agentic branching, structured generation, complex prompt workflows, constrained decoding |

SGLang is purpose-built for "complex prompting workflows, agents" — if your workload is overwhelmingly agentic with heavy branching and structured output, it may be a more native fit.

---

## 15. llm-d's scheduler — helps when agents make 15 LLM calls?

**Yes, this is exactly where llm-d shines.**

When an agent makes 15 sequential LLM calls, each call carries the same system prompt plus a growing conversation history (system prompt → call 1 response → tool result 1 → call 2 response → ...). Without smart scheduling, every call would re-compute the full prefill from scratch across potentially different vLLM replicas. The llm-d Inference Scheduler solves this through three mechanisms:

### 1. Prefix-cache aware routing

All 15 calls from the same agent session are routed to the **same vLLM replica** that already holds the KV cache from previous turns. Only the new tokens (the latest tool result) need prefilling — not the entire history. Call #15 essentially gets the prefill "for free" on all tokens from calls #1–14.

### 2. Predicted latency balancing

The scheduler picks the replica with the **lowest expected latency** based on current load and KV cache state — not just round-robin. Each vLLM pod reports its Load and KV Cache state back to the Inference Scheduler.

### 3. Queue depth awareness

Prevents piling all requests onto one overloaded replica. Combined with prefix-cache routing, this balances cache affinity against load distribution.

The architecture uses an **extensible library of scrapers, scorers, and filterers** to make routing decisions — customizable via the Gateway API Inference Extension.

### Additionally: prefill/decode disaggregation

Prefill (processing the growing input context) and decode (generating tokens) run on **separate pools**. Long contexts from tool-call-heavy conversations don't starve other requests during the decode phase.

### Complexity impact

```
Without llm-d (vanilla vLLM):
  Call 1:  prefill 1K tokens  → 50ms
  Call 8:  prefill 8K tokens  → 300ms  (re-prefill entire history)
  Call 15: prefill 15K tokens → 600ms  (re-prefill entire history again)
  Compute complexity: O(n²) — each call re-prefills all prior context

With llm-d (KV cache routing to same replica):
  Call 1:  prefill 1K tokens  → 50ms
  Call 8:  incremental 1K     → 50ms   (prior context cached on same node)
  Call 15: incremental 1K     → 50ms   (still cached)
  Compute complexity: O(n) — only new tokens need prefilling
```

> The scheduler turns what would be O(n²) compute into roughly O(n).

### Integration with abox

llm-d is Kubernetes-native and integrates with Gateway API. Adding it to the abox stack follows the standard component pattern:

1. CRDs → `releases/crds/llm-d-crds.yaml`
2. App → `releases/llm-d.yaml` with `dependsOn` on CRD release
3. `InferencePool` CRD to define model serving pools
4. `HTTPRoute` to route agent traffic through agentgateway to llm-d endpoints

This makes llm-d the recommended choice for agentic workloads where LLM call count per task exceeds ~5.
