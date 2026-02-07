from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Any, Dict

import yaml
from loguru import logger

from domains.telecom.controller.an_agent_core.coordinator import Coordinator, CoordinatorConfig
from domains.telecom.controller.an_agent_core.events import EventBus
from domains.telecom.controller.an_agent_core.lifecycle import AppLifecycle
from domains.telecom.controller.an_agent_core.types import Event, EventType

from interfaces.telemetry.ran_adapter import MockRANTelemetryAdapter, RANAdapterConfig
from interfaces.telemetry.kpi_logger import KPILogger, KPILoggerConfig
from interfaces.southbound.mock import MockSouthboundConfig, MockSouthboundExecutor

from knowledge.long_term_memory.vector_store import VectorStore, VectorStoreConfig
from knowledge.long_term_memory.graph_store import GraphStore, GraphStoreConfig
from knowledge.long_term_memory.rag import Retriever, RAGConfig
from knowledge.long_term_memory.relational_store import RelationalStore, RelationalStoreConfig
from knowledge.rules.constraints import ConstraintsConfig, ConstraintsProvider

from models.situation_awareness.filters import Kalman1D, KalmanConfig, Smoother, SmoothingConfig
from models.situation_awareness.bler_lstm import BLERPredictor, BLERLSTMConfig
from models.policy.goal_mlp import GoalScorer, GoalMLPConfig
from models.policy.reward_shaping import RewardShaper, RewardShapingConfig

from runtimes.reactive.perception import Perception, PerceptionConfig
from runtimes.reactive.goal_generation import GoalGenerator, GoalGenConfig, GoalSelector
from runtimes.reactive.planner import Planner, PlannerConfig
from runtimes.reactive.validator import Validator, ValidatorConfig
from runtimes.reactive.runtime import ReactiveRuntime, ReactiveRuntimeConfig

from runtimes.proactive.runtime import ProactiveRuntime, ProactiveRuntimeConfig
from runtimes.proactive.self_awareness import SelfAwareness, SelfAwarenessConfig
from runtimes.proactive.choice_making import ChoiceMaker, ChoiceMakingConfig
from runtimes.proactive.scheduler import Scheduler, SchedulerConfig


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_system(config_path: str) -> Coordinator:
    cfg = _load_yaml(config_path)

    # --- stores ---
    os.makedirs("./data", exist_ok=True)

    rel = RelationalStore(RelationalStoreConfig(url=cfg.get("storage", {}).get("relational", {}).get("url", "sqlite:///./data/an_agent.db")))

    vector_cfg = cfg.get("vector_store", {}) or cfg.get("rag", {}).get("vector_store", {}) or {}
    vector = VectorStore(VectorStoreConfig(
        backend=str(vector_cfg.get("backend", "inmemory")),
        dim=int(vector_cfg.get("dim", 8)),
        normalize=bool(vector_cfg.get("normalize", True)),
    ))

    graph_cfg = cfg.get("graph_store", {}) or cfg.get("rag", {}).get("graph_store", {}) or {}
    graph = GraphStore(GraphStoreConfig(
        backend=str(graph_cfg.get("backend", "inmemory")),
        uri=str(graph_cfg.get("uri", "bolt://localhost:7687")),
        user=str(graph_cfg.get("user", "neo4j")),
        password=str(graph_cfg.get("password", "neo4j")),
        database=str(graph_cfg.get("database", "neo4j")),
    ))

    rag_cfg = cfg.get("rag", {}) or {}
    retriever = Retriever(RAGConfig(
        enabled=bool(rag_cfg.get("enabled", True)),
        top_k=int(rag_cfg.get("top_k", 8)),
        min_score=float(rag_cfg.get("min_score", 0.15)),
        max_context_items=int(rag_cfg.get("max_context_items", 10)),
    ), vector_store=vector)

    # --- rules/constraints ---
    safety = cfg.get("safety", {}) or {}
    cons = safety.get("constraints", {}) or {}
    bler = cons.get("bler", {}) or {}
    cons_cfg = ConstraintsConfig(
        mcs_min=int(cons.get("mcs", {}).get("min_index", 0)),
        mcs_max=int(cons.get("mcs", {}).get("max_index", 27)),
        mcs_max_step=int(cons.get("mcs", {}).get("max_step", 2)),
        mimo_allowed=tuple(int(x) for x in cons.get("mimo_rank", {}).get("allowed", [1, 2, 4])),
        embb_bler_max=float(bler.get("embb_max", 0.10)),
        urllc_bler_max=float(bler.get("urllc_max", 0.001)),
    )
    constraints_provider = ConstraintsProvider(cons_cfg)

    # --- models ---
    models_cfg = cfg.get("models", {}) or {}
    sa_cfg = models_cfg.get("situation_awareness", {}) or {}
    smoothing_cfg = sa_cfg.get("smoothing", {}) or {}
    smoother = Smoother(SmoothingConfig(window=int(smoothing_cfg.get("window", 20))))

    kal_cfg = sa_cfg.get("sinr_kalman", {}) or {}
    sinr_filter = Kalman1D(KalmanConfig(
        process_var=float(kal_cfg.get("process_var", 1e-3)),
        meas_var=float(kal_cfg.get("meas_var", 1e-2)),
        init_est=0.0,
        init_var=1.0,
    ))

    bler_cfg = sa_cfg.get("bler_lstm", {}) or {}
    bler_predictor = BLERPredictor(BLERLSTMConfig(
        enabled=bool(bler_cfg.get("enabled", False)),
        history_ttis=int(bler_cfg.get("history_ttis", 100)),
        horizon_ttis=int(bler_cfg.get("horizon_ttis", 5)),
        weights_path=str(bler_cfg.get("weights_path", "./data/models/bler_lstm.pt")),
    ))

    pol_cfg = models_cfg.get("policy", {}) or {}
    mlp_cfg = pol_cfg.get("goal_mlp", {}) or {}
    goal_scorer = GoalScorer(GoalMLPConfig(
        enabled=bool(mlp_cfg.get("enabled", True)),
        temperature=float(mlp_cfg.get("temperature", 1.0)),
    ))

    rs_cfg = models_cfg.get("reward_shaping", {}) or {}
    reward_shaper = RewardShaper(RewardShapingConfig(
        mode=str(rs_cfg.get("mode", "embb")),
        embb_max_bler=float(rs_cfg.get("embb", {}).get("max_bler", 0.10)),
        urllc_max_bler=float(rs_cfg.get("urllc", {}).get("max_bler", 0.001)),
        embb_w_tpt=float(rs_cfg.get("embb", {}).get("w_tpt", 1.0)),
        embb_w_bler=float(rs_cfg.get("embb", {}).get("w_bler", -0.5)),
        urllc_w_bler=float(rs_cfg.get("urllc", {}).get("w_bler", -2.0)),
        urllc_w_tpt=float(rs_cfg.get("urllc", {}).get("w_tpt", 0.2)),
    ))

    # --- runtimes: reactive ---
    runt_cfg = cfg.get("runtimes", {}) or {}
    reactive_cfg = runt_cfg.get("reactive", {}) or {}
    perception = Perception(
        PerceptionConfig(enable_rag=bool(reactive_cfg.get("enable_rag", True))),
        smoother=smoother,
        sinr_filter=sinr_filter,
        rag=retriever,
        graph=graph,
        constraints_provider=constraints_provider,
        memory_writer=vector,
        predictor=bler_predictor,
    )

    goal_gen = GoalGenerator(GoalGenConfig(max_goals=8), goal_scorer=goal_scorer)
    goal_selector = GoalSelector()
    planner = Planner(PlannerConfig(mcs_step=1, default_mimo_rank=1))
    validator = Validator(ValidatorConfig(enforce_mcs_step=True))

    reactive = ReactiveRuntime(
        ReactiveRuntimeConfig(
            enable_rag=bool(reactive_cfg.get("enable_rag", True)),
            enable_llm=bool(reactive_cfg.get("enable_llm", False)),
            fallback_on_deadline=str(safety.get("fallback", {}).get("on_deadline_exceeded", "rules_only")),
        ),
        perception=perception,
        goal_gen=goal_gen,
        goal_selector=goal_selector,
        planner=planner,
        validator=validator,
    )

    # --- runtimes: proactive ---
    proactive = None
    if bool(cfg.get("coordinator", {}).get("enable_proactive", True)) and bool(runt_cfg.get("proactive", {}).get("enabled", True)):
        pro_cfg = runt_cfg.get("proactive", {}) or {}
        sched_cfg = pro_cfg.get("schedule", {}) or {}

        self_awareness = SelfAwareness(SelfAwarenessConfig())
        choice_maker = ChoiceMaker(
            ChoiceMakingConfig(default_mode=str(rs_cfg.get("mode", "embb"))),
            reward_shaper=reward_shaper,
            constraints_provider=constraints_provider,
        )
        scheduler = Scheduler(SchedulerConfig(periodic_check_sec=int(sched_cfg.get("periodic_check_sec", 5))))
        proactive = ProactiveRuntime(
            ProactiveRuntimeConfig(
                enable_llm=bool(pro_cfg.get("enable_llm", False)),
                periodic_check_sec=int(sched_cfg.get("periodic_check_sec", 5)),
            ),
            self_awareness=self_awareness,
            choice_maker=choice_maker,
            scheduler=scheduler,
        )

    # --- interfaces ---
    tel_cfg = cfg.get("interfaces", {}).get("telemetry", {}) or {}
    adapter = MockRANTelemetryAdapter(
        RANAdapterConfig(
            poll_interval_ms=int(tel_cfg.get("poll_interval_ms", 5)),
            mode=str(rs_cfg.get("mode", "embb")),
        )
    )

    sb = MockSouthboundExecutor(MockSouthboundConfig(enabled=True), telemetry=adapter)

    # --- bus + KPI logger hook ---
    bus = EventBus()
    kpi_logger = KPILogger(KPILoggerConfig(enabled=True, log_every=20), store=rel)

    def _on_obs(ev: Event) -> None:
        obs = ev.payload.get("observation")
        if obs is not None:
            kpi_logger.on_observation(obs)

    bus.subscribe(EventType.OBSERVATION, _on_obs)

    # coordinator config (merge default + experiment config)
    coord_cfg = cfg.get("coordinator", {}) or {}
    cc = CoordinatorConfig(
        tick_hz=float(coord_cfg.get("tick_hz", 100.0)),
        reactive_deadline_ms=int(coord_cfg.get("reactive_deadline_ms", 10)),
        proactive_tick_hz=float(coord_cfg.get("proactive_tick_hz", 2.0)),
        enable_proactive=bool(coord_cfg.get("enable_proactive", True)),
    )

    coordinator = Coordinator(
        cfg=cc,
        event_bus=bus,
        telemetry=adapter,
        reactive=reactive,
        southbound=sb,
        proactive=proactive,
    )

    # обёртка: после apply логируем decision
    orig_apply = sb.apply

    def _apply_and_log(decision):
        orig_apply(decision)
        kpi_logger.on_decision(decision)

    sb.apply = _apply_and_log  # type: ignore[method-assign]

    return coordinator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to experiment YAML config")
    parser.add_argument("--ticks", type=int, default=500, help="How many ticks to run")
    args = parser.parse_args()

    coordinator = build_system(args.config)
    coordinator.run(max_ticks=int(args.ticks))


if __name__ == "__main__":
    main()
