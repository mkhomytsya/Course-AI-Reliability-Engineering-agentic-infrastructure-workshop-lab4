"""
MuckeligCoachAgent — A2A Protocol Agent
Spec: https://a2a-protocol.org/latest/specification/
Well-Known URI: GET /.well-known/agent-card.json (RFC 8615)
"""
import uuid
import json
import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os

app = FastAPI(title="MuckeligCoachAgent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8080")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "mkhomytsya")
LMS_REPO_URL = f"https://github.com/{GITHUB_OWNER}/MuckeligAgentLMS"

AGENT_CARD = {
    "name": "MuckeligCoachAgent",
    "description": (
        "AI learning coach for MuckeligAgentLMS. "
        "Explains concepts, quizzes learners, and tracks progress."
    ),
    "url": f"{BASE_URL}/a2a",
    "version": "1.0.0",
    "documentationUrl": LMS_REPO_URL,
    "provider": {
        "organization": "MuckeligAgentLMS",
        "url": LMS_REPO_URL
    },
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": True
    },
    "preferredTransport": "JSONRPC",
    "defaultInputModes": ["text/plain", "application/json"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": [
        {
            "id": "explain-concept",
            "name": "Explain Concept",
            "description": "Explains a topic at the learner's level.",
            "tags": ["learning", "explanation"],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
            "examples": ["Explain the A2A protocol", "What is kagent?"]
        },
        {
            "id": "quiz-learner",
            "name": "Quiz Learner",
            "description": "Generates quiz questions on course material.",
            "tags": ["quiz", "assessment"],
            "inputModes": ["text/plain"],
            "outputModes": ["application/json"],
            "examples": ["Quiz me on MCP basics"]
        },
        {
            "id": "track-progress",
            "name": "Track Progress",
            "description": "Summarizes learning progress and recommends next steps.",
            "tags": ["progress", "analytics"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
            "examples": ["What should I study next?"]
        }
    ]
}

tasks: dict = {}


# ── Well-Known URI (RFC 8615) ─────────────────────────────────────────────────
@app.get("/.well-known/agent-card.json")
async def get_agent_card():
    return JSONResponse(
        content=AGENT_CARD,
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=3600",
        }
    )


# ── A2A JSON-RPC 2.0 ──────────────────────────────────────────────────────────
@app.post("/a2a")
async def a2a_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _error(None, -32700, "Parse error")

    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    if body.get("jsonrpc") != "2.0":
        return _error(rpc_id, -32600, "Invalid JSON-RPC version")

    if method == "tasks/send":
        return await _tasks_send(rpc_id, params)
    elif method == "tasks/get":
        return await _tasks_get(rpc_id, params)
    elif method == "tasks/cancel":
        return await _tasks_cancel(rpc_id, params)
    elif method == "agent/getCard":
        return _result(rpc_id, AGENT_CARD)
    else:
        return _error(rpc_id, -32601, f"Method not found: {method}")


# ── SSE Streaming ─────────────────────────────────────────────────────────────
@app.post("/a2a/stream")
async def a2a_stream(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    params = body.get("params", {})
    task_id = str(uuid.uuid4())
    context_id = params.get("contextId", str(uuid.uuid4()))
    text = _extract_text(params)

    async def generate() -> AsyncGenerator[str, None]:
        yield _sse({"type": "task.status", "taskId": task_id,
                    "status": {"state": "TASK_STATE_SUBMITTED", "timestamp": _now()}})
        await asyncio.sleep(0.1)
        yield _sse({"type": "task.status", "taskId": task_id,
                    "status": {"state": "TASK_STATE_WORKING", "timestamp": _now()}})
        await asyncio.sleep(0.2)

        response = _coach_response(text)
        words = response.split()
        buf = []
        for i, word in enumerate(words):
            buf.append(word)
            if len(buf) >= 5 or i == len(words) - 1:
                yield _sse({"type": "artifact.chunk", "taskId": task_id,
                             "parts": [{"kind": "text", "text": " ".join(buf) + " "}],
                             "lastChunk": i == len(words) - 1})
                buf = []
                await asyncio.sleep(0.05)

        yield _sse({"type": "task.status", "taskId": task_id,
                    "status": {"state": "TASK_STATE_COMPLETED", "timestamp": _now()}})
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Handlers ──────────────────────────────────────────────────────────────────
async def _tasks_send(rpc_id, params):
    task_id = str(uuid.uuid4())
    text = _extract_text(params)
    skill_id = params.get("metadata", {}).get("skillId", "explain-concept")
    response_text = _coach_response(text, skill_id)
    task = {
        "id": task_id,
        "contextId": params.get("contextId", str(uuid.uuid4())),
        "status": {"state": "TASK_STATE_COMPLETED", "timestamp": _now()},
        "artifacts": [{"artifactId": f"artifact-{task_id}", "name": "response",
                       "parts": [{"kind": "text", "text": response_text}]}]
    }
    tasks[task_id] = task
    return _result(rpc_id, {"task": task})


async def _tasks_get(rpc_id, params):
    task_id = params.get("id")
    if not task_id or task_id not in tasks:
        return _error(rpc_id, -32001, f"Task not found: {task_id}")
    return _result(rpc_id, {"task": tasks[task_id]})


async def _tasks_cancel(rpc_id, params):
    task_id = params.get("id")
    if task_id in tasks:
        tasks[task_id]["status"]["state"] = "TASK_STATE_CANCELED"
    return _result(rpc_id, {"taskId": task_id, "canceled": True})


# ── Coaching Logic ────────────────────────────────────────────────────────────
def _coach_response(message: str, skill_id: str = "explain-concept") -> str:
    msg = message.lower()
    if "quiz" in msg or skill_id == "quiz-learner":
        return (
            "📝 What is the primary difference between MCP and A2A?\n"
            "A) MCP is for agent-to-agent, A2A for tools\n"
            "B) A2A is for agent-to-agent, MCP for tools/data\n"
            "C) They serve the same purpose\n"
            "Reply A/B/C for feedback!"
        )
    elif "progress" in msg or skill_id == "track-progress":
        return json.dumps({
            "completed": ["MCP Intro", "kagent Basics", "Gateway API"],
            "inProgress": ["A2A Protocol"],
            "recommended": ["A2A Agent Implementation", "Inventory"],
            "completion": "42%"
        }, indent=2)
    elif "a2a" in msg:
        return (
            "🤖 A2A is an open standard for agent-to-agent communication. "
            "Agents advertise via Agent Cards at /.well-known/agent-card.json (RFC 8615), "
            "communicate via JSON-RPC 2.0, and support SSE streaming. "
            "Key flow: Agent Card → Task → Message → Part → Artifact. "
            "Unlike MCP (agent↔tools), A2A bridges agents across org boundaries."
        )
    elif "kagent" in msg:
        return (
            "⚙️ kagent is a Kubernetes-native agent framework. "
            "It manages Agents, MCPServers, and ModelConfigs as K8s CRDs. "
            "Uses Gateway API for routing and supports declarative + custom agents."
        )
    elif "mcp" in msg:
        return (
            "🔌 MCP (Model Context Protocol) connects AI agents to tools and data. "
            "Uses JSON-RPC 2.0 over Stdio or HTTP Streaming. "
            "Complements A2A: MCP for tools, A2A for inter-agent collaboration."
        )
    return (
        f"👋 I'm your MuckeligLMS Coach! You asked: '{message}'. "
        "I can explain concepts, quiz you, or track your progress. "
        "Try: 'Explain A2A', 'Quiz me on MCP', or 'Show my progress'."
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_text(params: dict) -> str:
    parts = params.get("message", {}).get("parts", [])
    for part in parts:
        if part.get("kind") == "text":
            return part.get("text", "")
    return params.get("text", "Hello!")

def _result(rpc_id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})

def _error(rpc_id, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "healthy", "agent": AGENT_CARD["name"], "version": AGENT_CARD["version"]}

@app.get("/")
async def root():
    return {"agentCard": "/.well-known/agent-card.json", "a2a": "/a2a", "stream": "/a2a/stream"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")