from __future__ import annotations

import json
import os
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Literal

import asyncio
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

TOPOLOGY_FILE = os.environ.get("TOPOLOGY_FILE", "topology.json")
SAVE_INTERVAL_SEC = 5
SIM_TICK_SEC = 10
MAX_LOGS_PER_NODE = 1000

lock = threading.RLock()

def _atomic_write_json(path: str, data: dict):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def load_topology() -> dict:
    if not os.path.exists(TOPOLOGY_FILE):
        return {"nodes": {}}
    with open(TOPOLOGY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_topology(data: dict):
    _atomic_write_json(TOPOLOGY_FILE, data)

topology: Dict[str, dict] = load_topology()
if "nodes" not in topology:
    topology = {"nodes": topology}

sim_flags: Dict[str, bool] = defaultdict(lambda: False)
logs: Dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_LOGS_PER_NODE))

# Ensure all nodes loaded from topology are simulation-disabled by default
for _nid in list(topology.get("nodes", {}).keys()):
    sim_flags[_nid] = False

EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None
subscribers_all: set[asyncio.Queue] = set()

FAULT_CATALOG: Dict[str, Dict[str, str]] = {
    "router": {
        "overheating": "Device temperature exceeded threshold",
        "packet_loss": "Excessive packet loss",
        "dns_failure": "DNS resolution failed",
        "firmware_crash": "Firmware exception occurred",
        "disconnect": "Physical link is down",
    },
    "switch": {
        "overheating": "Device temperature exceeded threshold",
        "packet_loss": "Excessive packet loss",
        "firmware_crash": "Firmware exception occurred",
        "disconnect": "Physical link is down",
    },
    "access_point": {
        "overheating": "Device temperature exceeded threshold",
        "packet_loss": "Excessive packet loss",
        "disconnect": "Radio link is down",
        "firmware_crash": "Firmware exception occurred",
    },
}

class Node(BaseModel):
    id: str
    type: str
    links: List[str] = Field(default_factory=list)
    config: Optional[Dict] = Field(default_factory=dict)
    status: Literal["healthy", "degraded", "error"] = "healthy"
    traffic_speed: float = 100.0
    traffic_volume: int = 0
    error: Optional[str] = None

class NodeCreate(BaseModel):
    id: str
    type: str
    links: List[str] = Field(default_factory=list)
    config: Optional[Dict] = Field(default_factory=dict)

class NodeUpdate(BaseModel):
    type: Optional[str] = None
    links: Optional[List[str]] = None
    config: Optional[Dict] = None

class NodeStatusUpdate(BaseModel):
    status: Literal["healthy", "degraded", "error"]
    error: Optional[str] = None

class FaultRequest(BaseModel):
    type: str

class LogEntry(BaseModel):
    ts: float
    node_id: str
    node_type: str
    status: str
    traffic_speed: float
    traffic_volume: int
    error: Optional[str] = None
    reasoning: Optional[str] = None

class ReasoningRequest(BaseModel):
    reasoning: str
    status: Optional[Literal["healthy","degraded","error"]] = None
    error: Optional[str] = None

app = FastAPI(title="Topology & Faults Backend", version="0.1.4-ws")

# CORS for local dev frontends (Vite/Next/etc.)
ALLOWED_ORIGINS = [
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # specify exact origins if using credentials
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _ensure_node_exists(node_id: str) -> dict:
    node = topology["nodes"].get(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node

def _broadcast_ws(message: dict):
    if EVENT_LOOP is None:
        return
    for q in list(subscribers_all):
        try:
            asyncio.run_coroutine_threadsafe(q.put(message), EVENT_LOOP)
        except RuntimeError:
            pass

def _broadcast_all(message: dict):
    _broadcast_ws(message)

def _broadcast_status(node: dict):
    msg = {
        "type": "status",
        "node_id": node["id"],
        "status": node["status"],
        "error": node.get("error"),
        "ts": time.time(),
    }
    _broadcast_all(msg)

# --- State snapshot broadcast ---

def _collect_state() -> dict:
    """Return a compact snapshot of the whole network state."""
    return {
        "nodes": {
            nid: {
                "type": n["type"],
                "status": n["status"],
                "error": n.get("error"),
                "traffic_speed": n.get("traffic_speed", 0.0),
                "traffic_volume": n.get("traffic_volume", 0),
                "links": n.get("links", []),
            }
            for nid, n in topology["nodes"].items()
        },
        "links": {nid: n.get("links", []) for nid, n in topology["nodes"].items()},
    }


def _broadcast_state():
    with lock:
        snap = _collect_state()
    _broadcast_all({"type": "state", "ts": time.time(), **snap})


def _append_log(node: dict, reasoning: Optional[str] = None):
    entry = LogEntry(ts=time.time(), node_id=node["id"], node_type=node["type"], status=node["status"], traffic_speed=node.get("traffic_speed", 0.0), traffic_volume=node.get("traffic_volume", 0), error=node.get("error"), reasoning=reasoning)
    data = entry.model_dump() if hasattr(entry, "model_dump") else entry.dict()
    logs[node["id"]].append(data)
    _broadcast_all({"type": "log", "entry": data})

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # Filters via query params: ?node_id=<id>&history=100
    await ws.accept()
    params = ws.query_params
    node_filter = params.get("node_id")
    try:
        history = int(params.get("history", "0"))
    except ValueError:
        history = 0

    # Per-connection queue
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    subscribers_all.add(q)

    # Greeting + optional history snapshot
    await ws.send_json({"type": "hello", "ts": time.time(), "node_filter": node_filter})
    # send initial state snapshot so client immediately sees current topology & nodes
    with lock:
        _state_snap = _collect_state()
    await ws.send_json({"type": "state", "ts": time.time(), **_state_snap})
    if history > 0:
        with lock:
            if node_filter:
                hist = list(logs[node_filter])[-history:]
                for e in hist:
                    await ws.send_json({"type": "log", "entry": e})
            else:
                for nid, dq in list(logs.items()):
                    for e in list(dq)[-history:]:
                        await ws.send_json({"type": "log", "entry": e})

    async def sender():
        while True:
            msg = await q.get()
            # If node_filter is set, forward only matching events
            if node_filter:
                n = (
                    msg.get("node_id")
                    or (msg.get("entry") or {}).get("node_id")
                    or msg.get("a")
                )
                if n != node_filter and msg.get("type") not in {"hello"}:
                    continue
            await ws.send_json(msg)

    async def receiver():
        # Optional: handle pings from client; respond with pong
        while True:
            try:
                _ = await ws.receive_text()
                await ws.send_json({"type": "pong", "ts": time.time()})
            except Exception:
                break

    async def keepalive():
        # Server-side keepalive to traverse proxies/load balancers
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send_json({"type": "ping", "ts": time.time()})
            except Exception:
                break

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    ka_task = asyncio.create_task(keepalive())
    try:
        await asyncio.wait(
            {sender_task, receiver_task, ka_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
    except WebSocketDisconnect:
        pass
    finally:
        for t in (sender_task, receiver_task, ka_task):
            t.cancel()
        subscribers_all.discard(q)

# Alias for clients that connect to root path by mistake
@app.websocket("/")
async def websocket_root_alias(ws: WebSocket):
    await websocket_endpoint(ws)



# -------------
# Link helpers
# -------------

def _link_nodes(a: str, b: str):
    if a == b:
        return
    if b not in topology["nodes"][a]["links"]:
        topology["nodes"][a]["links"].append(b)
    if a not in topology["nodes"][b]["links"]:
        topology["nodes"][b]["links"].append(a)
    _broadcast_all({"type": "link_added", "a": a, "b": b, "ts": time.time()})
    _broadcast_state()


def _unlink_nodes(a: str, b: str):
    if b in topology["nodes"][a]["links"]:
        topology["nodes"][a]["links"].remove(b)
    if a in topology["nodes"][b]["links"]:
        topology["nodes"][b]["links"].remove(a)
    _broadcast_all({"type": "link_removed", "a": a, "b": b, "ts": time.time()})
    _broadcast_state()


# =====================
# Topology Endpoints (REST)
# =====================

@app.get("/topology")
def get_topology():
    with lock:
        return {
            "nodes": list(topology["nodes"].keys()),
            "links": {nid: n["links"] for nid, n in topology["nodes"].items()},
        }


@app.get("/nodes")
def get_all_nodes():
    with lock:
        return topology["nodes"]


@app.get("/nodes/{node_id}")
def get_node(node_id: str):
    with lock:
        return _ensure_node_exists(node_id)


@app.post("/nodes", status_code=201)
def add_node(node: NodeCreate):
    with lock:
        if node.id in topology["nodes"]:
            raise HTTPException(status_code=400, detail="Node already exists")
        if node.type not in FAULT_CATALOG:
            raise HTTPException(status_code=400, detail="Unknown node type")
        new_node = Node(id=node.id, type=node.type, links=[], config=node.config)
        topology["nodes"][node.id] = new_node.model_dump() if hasattr(new_node, "model_dump") else new_node.dict()
        for other in node.links:
            if other in topology["nodes"]:
                _link_nodes(node.id, other)
        sim_flags[node.id] = False
        save_topology(topology)
        _append_log(topology["nodes"][node.id], reasoning="Node created")
        _broadcast_state()
        return {"status": "added", "node": node.id}


# Backward-compat alias for old clients
@app.post("/nodes/add", status_code=201)
def add_node_legacy(node: NodeCreate):
    return add_node(node)


@app.patch("/nodes/{node_id}")
def patch_node(node_id: str, update: NodeUpdate):
    with lock:
        node = _ensure_node_exists(node_id)
        if update.type:
            if update.type not in FAULT_CATALOG:
                raise HTTPException(status_code=400, detail="Unknown node type")
            node["type"] = update.type
        if update.config is not None:
            node["config"] = update.config
        if update.links is not None:
            for other in list(node["links"]):
                _unlink_nodes(node_id, other)
            for other in update.links:
                if other in topology["nodes"]:
                    _link_nodes(node_id, other)
        save_topology(topology)
        _append_log(node, reasoning="Node updated")
        _broadcast_state()
        return {"status": "updated", "node": node_id}


@app.delete("/nodes/{node_id}")
def delete_node(node_id: str):
    with lock:
        node = _ensure_node_exists(node_id)
        for other in list(node["links"]):
            _unlink_nodes(node_id, other)
        topology["nodes"].pop(node_id)
        sim_flags.pop(node_id, None)
        logs.pop(node_id, None)
        save_topology(topology)
        _broadcast_all({"type": "node_deleted", "node_id": node_id, "ts": time.time()})
        _broadcast_state()
        return {"status": "deleted", "node": node_id}


# ---------
# Links API
# ---------

@app.post("/links/{a}/{b}")
def add_link(a: str, b: str):
    with lock:
        _ensure_node_exists(a)
        _ensure_node_exists(b)
        _link_nodes(a, b)
        save_topology(topology)
        return {"status": "linked", "a": a, "b": b}


@app.delete("/links/{a}/{b}")
def remove_link(a: str, b: str):
    with lock:
        _ensure_node_exists(a)
        _ensure_node_exists(b)
        _unlink_nodes(a, b)
        save_topology(topology)
        return {"status": "unlinked", "a": a, "b": b}


# ============
# Faults & Status
# ============

@app.get("/faults")
def list_faults(node_type: Optional[str] = Query(None)):
    with lock:
        if node_type:
            if node_type not in FAULT_CATALOG:
                raise HTTPException(status_code=400, detail="Unknown node type")
            return {node_type: FAULT_CATALOG[node_type]}
        return FAULT_CATALOG


@app.get("/nodes/{node_id}/faults")
def list_node_faults(node_id: str):
    with lock:
        node = _ensure_node_exists(node_id)
        return FAULT_CATALOG.get(node["type"], {})


@app.post("/nodes/{node_id}/fault")
def enable_fault(node_id: str, fault: FaultRequest):
    with lock:
        node = _ensure_node_exists(node_id)
        allowed = FAULT_CATALOG.get(node["type"], {})
        if fault.type not in allowed:
            raise HTTPException(status_code=400, detail="Unknown/unsupported fault for node type")
        node["status"] = "error"
        node["error"] = allowed[fault.type]
        save_topology(topology)
        _append_log(node, reasoning=f"Fault enabled: {fault.type}")
        _broadcast_status(node)
        _broadcast_state()
        return {"status": "error", "node": node_id, "error": node["error"]}


@app.delete("/nodes/{node_id}/fault")
def clear_fault(node_id: str):
    with lock:
        node = _ensure_node_exists(node_id)
        node["error"] = None
        node["status"] = "healthy"
        save_topology(topology)
        _append_log(node, reasoning="Fault cleared")
        _broadcast_status(node)
        return {"status": "healthy", "node": node_id}


@app.get("/nodes/{node_id}/status")
def get_status(node_id: str):
    with lock:
        node = _ensure_node_exists(node_id)
        return {"id": node_id, "status": node["status"], "error": node.get("error")}


@app.post("/nodes/{node_id}/status")
def update_status(node_id: str, update: NodeStatusUpdate):
    with lock:
        node = _ensure_node_exists(node_id)
        node["status"] = update.status
        node["error"] = update.error
        save_topology(topology)
        _append_log(node, reasoning="Status updated via API")
        _broadcast_status(node)
        return {"status": "updated", "node": node_id}


# =========
# Logs & Reasoning API
# =========

@app.get("/nodes/{node_id}/logs")
def get_logs(node_id: str, limit: int = Query(100, ge=1, le=MAX_LOGS_PER_NODE), since_ts: Optional[float] = None):
    with lock:
        _ensure_node_exists(node_id)
        entries = list(logs[node_id])
        if since_ts:
            entries = [e for e in entries if e["ts"] >= since_ts]
        return entries[-limit:]


@app.post("/nodes/{node_id}/reasoning")
def post_reasoning(node_id: str, body: ReasoningRequest):
    with lock:
        node = _ensure_node_exists(node_id)
        # Optionally update status/error per agent insight
        if body.status is not None:
            node["status"] = body.status
        if body.error is not None:
            node["error"] = body.error
        save_topology(topology)
        _append_log(node, reasoning=body.reasoning)
        # Broadcast status only if it changed or error provided
        _broadcast_status(node)
        _broadcast_state()
        return {"ok": True, "node": node_id}


# ===================
# Simulation controls
# ===================

@app.post("/nodes/{node_id}/simulate")
def toggle_simulation(node_id: str, enabled: bool):
    with lock:
        print(node_id)
        _ensure_node_exists(node_id)
        sim_flags[node_id] = enabled
        # _append_log(topology["nodes"][node_id], reasoning=f"Simulation {'enabled' if enabled else 'disabled'}")
        return {"simulation_enabled": enabled, "node": node_id}


# =====================
# Simulation Engine
# =====================

HEALTHY_PROB = 1
DEGRADED_PROB = 0.02
ERROR_PROB = 0.01
TRAFFIC_SPEED_RANGE = (50.0, 1000.0)
TRAFFIC_VOLUME_RANGE = (10, 200)


def _simulate_tick(node: dict):
    if node["status"] == "error":
        node["traffic_speed"] = max(0.0, node.get("traffic_speed", 100.0) * random.uniform(0.2, 0.8))
        node["traffic_volume"] = int(node.get("traffic_volume", 0) * random.uniform(0.1, 0.5))
        _append_log(node, reasoning="Tick: node in error state, degraded traffic")
        return
    node["traffic_speed"] = round(random.uniform(*TRAFFIC_SPEED_RANGE), 2)
    node["traffic_volume"] = int(random.uniform(*TRAFFIC_VOLUME_RANGE))
    r = random.random()
    prev_status = node["status"]
    if r < ERROR_PROB:
        faults = FAULT_CATALOG.get(node["type"], {})
        if faults:
            fkey = random.choice(list(faults.keys()))
            node["status"] = "error"
            node["error"] = faults[fkey]
            _append_log(node, reasoning=f"Tick: random fault occurred: {fkey}")
            _broadcast_status(node)
            return
        else:
            node["status"] = "error"
            node["error"] = "Unknown error"
    elif r < ERROR_PROB + DEGRADED_PROB:
        node["status"] = "degraded"
        node["error"] = None
    else:
        node["status"] = "healthy"
        node["error"] = None
    if node["status"] != prev_status:
        _append_log(node, reasoning=f"Tick: status changed to {node['status']}")
        _broadcast_status(node)
    else:
        return
        # _append_log(node, reasoning="Tick: status unchanged")


def _simulation_loop(stop_evt: threading.Event):
    last_save = time.time()
    while not stop_evt.is_set():
        with lock:
            for node_id, node in topology["nodes"].items():
                if sim_flags.get(node_id, False):
                    _simulate_tick(node)
        now = time.time()
        if now - last_save >= SAVE_INTERVAL_SEC:
            with lock:
                save_topology(topology)
            last_save = now
        _broadcast_state()
        stop_evt.wait(SIM_TICK_SEC)


stop_event = threading.Event()
thr = threading.Thread(target=_simulation_loop, args=(stop_event,), daemon=True)
thr.start()


# Health endpoint
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat() + "Z"}


# Lifespan: capture event loop for thread-safe broadcasts and stop simulator on shutdown
try:
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global EVENT_LOOP
        EVENT_LOOP = asyncio.get_running_loop()
        yield
        stop_event.set()
        thr.join(timeout=1.0)

    app.router.lifespan_context = lifespan  # type: ignore
except Exception:
    pass
