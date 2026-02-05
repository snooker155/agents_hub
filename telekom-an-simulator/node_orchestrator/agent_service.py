"""
Multi-agent service: one LangChain+Ollama agent per network node + an AgentManager that
handles inter-agent messaging and aggregation.

Capabilities
-----------
- Subscribes to backend WS `/ws` to receive `state` snapshots and events.
- Spawns isolated agent per node (own LLM + own memory + own incident store).
- When an agent detects an incident on its node, it:
  1) Posts reasoning to backend: POST /nodes/{id}/reasoning
  2) Broadcasts an `incident_start` message to all neighbor agents via in-process bus.
- Neighbor agents remember the incident until an `incident_resolved` message arrives,
  run local analysis with that context, and send `incident_opinion` back to the origin agent.
- AgentManager maintains a registry of neighbor topology and aggregates all incidents/opinions.

Run
---
    pip install langchain langchain-community langchain-ollama httpx websockets uvloop
    export OLLAMA_HOST=http://localhost:11434
    export BACKEND_BASE=http://localhost:8000
    python agent_service.py

Model
-----
Default Ollama model: "llama3.1:8b" (good fit for MacBook M1 Pro). Override with OLLAMA_MODEL.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
import websockets
from websockets.client import WebSocketClientProtocol

# LangChain imports
try:
    from langchain_ollama import ChatOllama  # preferred integration
except Exception:
    from langchain_community.chat_models import ChatOllama  # type: ignore

from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langchain.memory import ConversationBufferMemory


BACKEND_BASE = os.environ.get("BACKEND_BASE", "http://localhost:8000")
WS_URL = os.environ.get("BACKEND_WS", "ws://localhost:8000/ws")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Triggers & pacing
TRAFFIC_DROP_RATIO = 0.5
COOLDOWN_SEC = 30
MAX_RECENT_EVENTS = 12

# Messaging kinds
K_INCIDENT_START = "incident_start"
K_INCIDENT_OPINION = "incident_opinion"
K_INCIDENT_RESOLVED = "incident_resolved"


@dataclass
class NodeSnapshot:
    node_id: str
    node_type: str
    status: str
    error: Optional[str]
    traffic_speed: float
    traffic_volume: int
    links: List[str] = field(default_factory=list)


@dataclass
class Incident:
    id: str
    origin: str
    summary: str
    severity: str
    causes: List[str]
    suggested_actions: List[str]
    confidence: float
    ts: float


class NodeAgent:
    """Agent with isolated memory, LLM and neighbor incident store for a single node."""

    def __init__(self, node_id: str, node_type: str, manager: "AgentManager"):
        self.node_id = node_id
        self.node_type = node_type
        self.manager = manager
        self.llm = ChatOllama(model=OLLAMA_MODEL, temperature=0, base_url=OLLAMA_HOST)
        self.memory = ConversationBufferMemory(return_messages=True)
        self.last_snapshot: Optional[NodeSnapshot] = None
        self.last_reason_ts: float = 0.0
        self.recent_events: List[Dict[str, Any]] = []
        # Incident state
        self.own_incident_id: Optional[str] = None
        self.own_incident_payload: Optional[Dict[str, Any]] = None
        self.neighbor_incidents: Dict[str, Dict[str, Any]] = {}  # incident_id -> payload

    # ---------- utility ----------

    def _should_analyze(self, snap: NodeSnapshot) -> bool:
        now = time.time()
        if now - self.last_reason_ts < COOLDOWN_SEC:
            return False
        prev = self.last_snapshot
        if snap.status in ("error", "degraded"):
            return True
        if prev and prev.status != snap.status:
            return True
        if prev and prev.error != snap.error:
            return True
        if prev and prev.traffic_speed > 0 and snap.traffic_speed < prev.traffic_speed * (1 - TRAFFIC_DROP_RATIO):
            return True
        return False

    def _prompt(self, snap: NodeSnapshot, neighbor_ctx: Optional[Dict[str, Any]] = None) -> List[Any]:
        system = (
            "You are a telecom NOC diagnostics agent for a single node. "
            "Given the node snapshot, recent events and optional neighbor-incident context, "
            "identify the most likely problem and its causes. Output strictly minified JSON with keys: "
            "problem, likely_causes (list), severity(one of: low, medium, high), suggested_actions (list), confidence (0..1)."
        )
        snap_json = {
            "id": snap.node_id,
            "type": snap.node_type,
            "status": snap.status,
            "error": snap.error,
            "traffic_speed": snap.traffic_speed,
            "traffic_volume": snap.traffic_volume,
            "links": snap.links,
        }
        events = self.recent_events[-MAX_RECENT_EVENTS:]
        user = {
            "node_snapshot": snap_json,
            "recent_events": events,
            "neighbor_context": neighbor_ctx or {},
            "instructions": "Respond ONLY with minified JSON."
        }
        history = self.memory.load_memory_variables({}).get("history", [])
        return [SystemMessage(content=system), *history, HumanMessage(content=json.dumps(user))]

    def _parse_reasoning(self, text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except Exception:
            # Try to snip JSON from mixed text
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1:
                    return json.loads(text[start:end+1])
            except Exception:
                pass
        return {"problem": text[:160], "likely_causes": [], "severity": "low", "suggested_actions": [], "confidence": 0.3}

    def update_events(self, evt: Dict[str, Any]):
        t = evt.get("type")
        if t in ("status", "log"):
            if t == "log":
                entry = evt.get("entry", {})
                compact = {
                    "ts": entry.get("ts"),
                    "status": entry.get("status"),
                    "error": entry.get("error"),
                    "reasoning": entry.get("reasoning"),
                    "traffic_speed": entry.get("traffic_speed"),
                    "traffic_volume": entry.get("traffic_volume"),
                }
                self.recent_events.append({"type": "log", **compact})
            else:
                self.recent_events.append({k: evt.get(k) for k in ("type", "ts", "status", "error")})
        if len(self.recent_events) > 200:
            self.recent_events = self.recent_events[-200:]

    # ---------- core flows ----------

    async def analyze_and_report(self, snap: NodeSnapshot, http: httpx.AsyncClient):
        """Run self-diagnosis when needed; post reasoning; possibly start/resolve incident."""
        if not self._should_analyze(snap):
            self.last_snapshot = snap
            # auto-resolve if previously had incident and now healthy
            if self.own_incident_id and snap.status == "healthy" and not snap.error:
                await self._broadcast_resolved()
            return

        msgs = self._prompt(snap)
        try:
            resp = await self.llm.apredict_messages(msgs)  # type: ignore[attr-defined]
            text = resp.content if hasattr(resp, "content") else str(resp)
        except Exception:
            text = self.llm.predict_messages(msgs).content  # type: ignore
        reasoning = text.strip()
        self.memory.chat_memory.add_messages(msgs[-2:])
        self.memory.chat_memory.add_ai_message(AIMessage(content=reasoning))
        self.last_reason_ts = time.time()
        self.last_snapshot = snap

        payload: Dict[str, Any] = {"reasoning": reasoning}
        parsed = self._parse_reasoning(reasoning)
        sev = (parsed.get("severity") or "").lower()
        if sev in ("high", "medium") and snap.status == "healthy":
            payload["status"] = "degraded"
        if parsed.get("problem") and snap.status != "error" and "packet" in json.dumps(parsed).lower():
            payload["error"] = "Agent detected abnormal packet behavior"
        await http.post(f"{BACKEND_BASE}/nodes/{snap.node_id}/reasoning", json=payload)

        # Start or update an incident and notify neighbors
        if sev in ("high", "medium") or snap.status in ("error", "degraded"):
            await self._broadcast_incident(parsed)
        else:
            # if previously had an incident, consider resolve on improvement
            if self.own_incident_id and snap.status == "healthy" and not snap.error:
                await self._broadcast_resolved()

    async def _broadcast_incident(self, parsed: Dict[str, Any]):
        now = time.time()
        if not self.own_incident_id:
            self.own_incident_id = f"{self.node_id}:{int(now)}:{uuid.uuid4().hex[:6]}"
        self.own_incident_payload = {
            "id": self.own_incident_id,
            "origin": self.node_id,
            "summary": parsed.get("problem") or "unknown",
            "severity": parsed.get("severity", "medium"),
            "causes": parsed.get("likely_causes", []),
            "suggested_actions": parsed.get("suggested_actions", []),
            "confidence": float(parsed.get("confidence", 0.5)),
            "ts": now,
        }
        await self.manager.publish({
            "kind": K_INCIDENT_START,
            "incident": self.own_incident_payload,
        })

    async def _broadcast_resolved(self):
        if not self.own_incident_id:
            return
        incident_id = self.own_incident_id
        self.own_incident_id = None
        await self.manager.publish({
            "kind": K_INCIDENT_RESOLVED,
            "incident": {"id": incident_id, "origin": self.node_id, "ts": time.time()},
        })

    async def on_message(self, msg: Dict[str, Any]):
        kind = msg.get("kind")
        inc = msg.get("incident", {})
        if kind == K_INCIDENT_START and inc.get("origin") != self.node_id:
            # Remember and analyze with neighbor context, then reply back an opinion
            self.neighbor_incidents[inc["id"]] = inc
            await self._analyze_with_neighbor_context_and_reply(inc)
        elif kind == K_INCIDENT_RESOLVED and inc.get("origin") != self.node_id:
            # Forget neighbor incident
            self.neighbor_incidents.pop(inc.get("id"), None)
            # Add a small note to memory
            self.memory.chat_memory.add_user_message(
                f"Neighbor {inc.get('origin')} resolved incident {inc.get('id')}.")
        elif kind == K_INCIDENT_OPINION and inc.get("origin") == self.node_id:
            # Opinion addressed to me about my incident
            opinion = msg.get("opinion", {})
            # Add to memory and log a short note to backend as reasoning
            note = {"neighbor_opinion": opinion, "incident": inc}
            self.memory.chat_memory.add_ai_message(json.dumps(note))
            await self.manager.http.post(
                f"{BACKEND_BASE}/nodes/{self.node_id}/reasoning",
                json={"reasoning": json.dumps(note)}
            )

    async def _analyze_with_neighbor_context_and_reply(self, neighbor_incident: Dict[str, Any]):
        # build context from neighbor incidents (all we know)
        snap = self.last_snapshot
        if not snap:
            return
        ctx = {
            "neighbor_incident": neighbor_incident,
            "all_known_neighbor_incidents": list(self.neighbor_incidents.values())[:5],
        }
        msgs = self._prompt(snap, neighbor_ctx=ctx)
        try:
            resp = await self.llm.apredict_messages(msgs)  # type: ignore[attr-defined]
            text = resp.content if hasattr(resp, "content") else str(resp)
        except Exception:
            text = self.llm.predict_messages(msgs).content  # type: ignore
        analysis = self._parse_reasoning(text)
        # craft an opinion response back to origin
        opinion = {
            "from": self.node_id,
            "to": neighbor_incident.get("origin"),
            "incident_id": neighbor_incident.get("id"),
            "correlation": "possible" if analysis.get("confidence", 0.5) >= 0.5 else "unlikely",
            "assessment": analysis.get("problem"),
            "suggested_actions": analysis.get("suggested_actions", []),
            "confidence": analysis.get("confidence", 0.5),
            "ts": time.time(),
        }
        # log to my own node as well
        await self.manager.http.post(
            f"{BACKEND_BASE}/nodes/{self.node_id}/reasoning",
            json={"reasoning": json.dumps({"opinion_sent": opinion})}
        )
        await self.manager.publish({
            "kind": K_INCIDENT_OPINION,
            "incident": {"id": neighbor_incident.get("id"), "origin": neighbor_incident.get("origin")},
            "opinion": opinion,
        })


class AgentManager:
    def __init__(self):
        self.agents: Dict[str, NodeAgent] = {}
        self.snapshots: Dict[str, NodeSnapshot] = {}
        self.neighbors: Dict[str, List[str]] = {}  # node -> neighbors
        self.http = httpx.AsyncClient(timeout=10)
        self.ws: Optional[WebSocketClientProtocol] = None
        self.lock = asyncio.Lock()
        # Aggregation store
        self.active_incidents: Dict[str, Dict[str, Any]] = {}  # id -> {incident, opinions{nid:op}}

    def _get_or_create(self, node_id: str, node_type: str) -> NodeAgent:
        ag = self.agents.get(node_id)
        if ag is None:
            ag = NodeAgent(node_id, node_type, self)
            self.agents[node_id] = ag
        return ag

    # -------- topology / state handling --------

    async def _handle_state(self, msg: Dict[str, Any]):
        nodes = msg.get("nodes", {})
        # refresh neighbor map
        new_neighbors: Dict[str, List[str]] = {}
        for nid, nd in nodes.items():
            new_neighbors[nid] = list(nd.get("links", []))
            snap = NodeSnapshot(
                node_id=nid,
                node_type=nd.get("type", "unknown"),
                status=nd.get("status", "healthy"),
                error=nd.get("error"),
                traffic_speed=float(nd.get("traffic_speed", 0.0)),
                traffic_volume=int(nd.get("traffic_volume", 0)),
                links=list(nd.get("links", [])),
            )
            self.snapshots[nid] = snap
            agent = self._get_or_create(nid, snap.node_type)
            asyncio.create_task(agent.analyze_and_report(snap, self.http))
        self.neighbors = new_neighbors

    async def _handle_event(self, msg: Dict[str, Any]):
        t = msg.get("type")
        target_node = msg.get("node_id") or (msg.get("entry") or {}).get("node_id")
        if target_node and target_node in self.agents:
            self.agents[target_node].update_events(msg)

    # -------- in-process bus (multi-agent messaging) --------

    async def publish(self, message: Dict[str, Any]):
        kind = message.get("kind")
        if kind == K_INCIDENT_START:
            inc = message["incident"]
            self.active_incidents[inc["id"]] = {"incident": inc, "opinions": {}}
            # deliver to neighbors of origin
            origin = inc["origin"]
            for nb in self.neighbors.get(origin, []):
                if nb in self.agents:
                    asyncio.create_task(self.agents[nb].on_message(message))
        elif kind == K_INCIDENT_RESOLVED:
            inc = message["incident"]
            origin = inc.get("origin")
            for nb in self.neighbors.get(origin, []):
                if nb in self.agents:
                    asyncio.create_task(self.agents[nb].on_message(message))
            # mark resolved in aggregator
            if inc.get("id") in self.active_incidents:
                self.active_incidents[inc["id"]]["resolved_ts"] = time.time()
        elif kind == K_INCIDENT_OPINION:
            inc = message.get("incident", {})
            op = message.get("opinion", {})
            # record in aggregator
            if inc.get("id") in self.active_incidents:
                self.active_incidents[inc["id"]]["opinions"][op.get("from")] = op
            # route back to origin agent
            origin = inc.get("origin")
            if origin and origin in self.agents:
                asyncio.create_task(self.agents[origin].on_message(message))
        else:
            # ignore unknown
            return
        # Optionally, also post a manager note to the origin node's logs for visibility
        try:
            origin = (message.get("incident") or {}).get("origin")
            if origin:
                await self.http.post(
                    f"{BACKEND_BASE}/nodes/{origin}/reasoning",
                    json={"reasoning": json.dumps({"manager_bus": message})}
                )
        except Exception:
            pass

    # -------- main loop --------

    async def run(self):
        # health ping
        try:
            r = await self.http.get(f"{BACKEND_BASE}/health")
            r.raise_for_status()
        except Exception as e:
            print("Backend health check failed:", e)
        # connect WS and stream
        async for ws in websockets.connect(WS_URL, ping_interval=20):
            try:
                self.ws = ws
                await ws.send("ping")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    t = msg.get("type")
                    if t == "state":
                        await self._handle_state(msg)
                    else:
                        await self._handle_event(msg)
            except websockets.ConnectionClosed:
                print("WS disconnected, reconnecting in 2s...")
                await asyncio.sleep(2)
                continue
            except Exception as e:
                print("WS error:", e)
                await asyncio.sleep(2)
                continue


async def main():
    mgr = AgentManager()
    await mgr.run()


if __name__ == "__main__":
    try:
        import uvloop  # type: ignore
        uvloop.install()
    except Exception:
        pass
    asyncio.run(main())
