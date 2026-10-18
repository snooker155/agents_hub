"""
Container hours in a run's cost (common/pricing.py container_cost_usd): a
docker-mode run's container lived from started_at to finished_at, priced per
vCPU-hour against its effective cpu limit. Zero for a run that never executed
in a container.
"""
from __future__ import annotations

from common import pricing
from common.pricing import container_cost_usd, run_cost_usd

PRICES: dict = {}  # token pricing is irrelevant here; every test leaves tokens at 0


def _run(**over):
    run = {"provider": "", "model": "", "execution_mode": "docker",
           "started_at": "2026-10-03T10:00:00Z", "finished_at": "2026-10-03T11:00:00Z"}
    run.update(over)
    return run


def test_zero_for_a_run_that_never_executed_in_docker():
    assert container_cost_usd(_run(execution_mode="local", container_cpus="2")) == 0.0


def test_zero_without_both_timestamps():
    assert container_cost_usd(_run(finished_at=None, container_cpus="2")) == 0.0
    assert container_cost_usd(_run(started_at=None, container_cpus="2")) == 0.0


def test_zero_without_a_cpu_figure():
    assert container_cost_usd(_run()) == 0.0


def test_prices_per_cpu_hour():
    # 1 hour, 2 cpus, at the default per-cpu rate (0.05 USD/vCPU-h) -> 0.10.
    assert abs(container_cost_usd(_run(container_cpus="2")) - 0.10) < 1e-9


def test_half_hour_is_half_the_price():
    run = _run(container_cpus="1", finished_at="2026-10-03T10:30:00Z")
    assert abs(container_cost_usd(run) - 0.025) < 1e-9


def test_live_setting_overrides_the_default_price(monkeypatch):
    from common import config
    monkeypatch.setattr(config, "read_dot_env",
                        lambda: {"AGENTS_HUB_CONTAINER_HOUR_PER_CPU": "0.25"})
    assert abs(container_cost_usd(_run(container_cpus="2")) - 0.50) < 1e-9


def test_run_cost_usd_adds_the_container_line_on_top_of_tokens():
    run = _run(container_cpus="1",
               process={"token_usage": {"inbound_tokens": 0, "outbound_tokens": 0}})
    # No model priced (empty provider/model), so the whole cost is the container line.
    assert abs(run_cost_usd(run, PRICES) - 0.05) < 1e-9


def test_a_plain_local_run_is_unaffected():
    run = {"provider": "openai", "model": "gpt", "execution_mode": "local",
           "process": {"token_usage": {"inbound_tokens": 0, "outbound_tokens": 0}}}
    assert run_cost_usd(run, PRICES) == 0.0


def test_container_hour_price_fails_open_on_garbage(monkeypatch):
    from common import config
    monkeypatch.setattr(config, "read_dot_env",
                        lambda: {"AGENTS_HUB_CONTAINER_HOUR_PER_CPU": "not-a-number"})
    assert pricing.container_hour_price_per_cpu() == pricing.CONTAINER_HOUR_PRICE_PER_CPU_DEFAULT


def test_a_reported_cost_still_gets_the_container_hours():
    """A wrapped CLI reports what its model calls cost, not the container the
    hub ran it in: the run total adds the hours on top."""
    run = _run(container_cpus="1", reported_cost_usd=1.25)
    assert abs(pricing.run_total_cost_usd(run, PRICES) - 1.30) < 1e-9
