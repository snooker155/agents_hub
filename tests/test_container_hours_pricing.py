"""
Container hours in a run's cost (common/pricing.py container_cost_usd,
fifth-cycle stage 3): a docker-mode run's container lived from started_at to
finished_at, priced per the sandbox size its environment named
(environments/models.py SIZE_PRESETS) or, with no size, per vCPU-hour against
its effective cpu limit. Zero for a run that never executed in a container.
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
    assert container_cost_usd(_run(execution_mode="local", container_size="small")) == 0.0


def test_zero_without_both_timestamps():
    assert container_cost_usd(_run(finished_at=None, container_size="small")) == 0.0
    assert container_cost_usd(_run(started_at=None, container_size="small")) == 0.0


def test_zero_with_neither_size_nor_cpus():
    assert container_cost_usd(_run()) == 0.0


def test_sized_sandbox_uses_the_default_per_size_price():
    # 1 hour at the medium default (0.10 USD/h).
    assert abs(container_cost_usd(_run(container_size="medium")) - 0.10) < 1e-9
    # Case-insensitive, like the environment model itself.
    assert abs(container_cost_usd(_run(container_size="LARGE")) - 0.20) < 1e-9


def test_custom_profile_prices_per_cpu_hour():
    # 1 hour, 2 cpus, at the default per-cpu rate (0.05 USD/vCPU-h) -> 0.10,
    # same price as the "medium" preset (2 cpus), by design.
    assert abs(container_cost_usd(_run(container_cpus="2")) - 0.10) < 1e-9


def test_half_hour_is_half_the_price():
    run = _run(container_size="small", finished_at="2026-10-03T10:30:00Z")
    assert abs(container_cost_usd(run) - 0.025) < 1e-9


def test_unknown_size_name_prices_as_zero_fail_open():
    assert container_cost_usd(_run(container_size="xlarge")) == 0.0


def test_live_setting_overrides_the_default_price(monkeypatch):
    from common import config
    monkeypatch.setattr(config, "read_dot_env",
                        lambda: {"AGENTS_HUB_CONTAINER_HOUR_SMALL": "1.00"})
    assert abs(container_cost_usd(_run(container_size="small")) - 1.00) < 1e-9
    monkeypatch.setattr(config, "read_dot_env",
                        lambda: {"AGENTS_HUB_CONTAINER_HOUR_PER_CPU": "0.25"})
    assert abs(container_cost_usd(_run(container_cpus="2")) - 0.50) < 1e-9


def test_run_cost_usd_adds_the_container_line_on_top_of_tokens():
    run = _run(container_size="small",
               process={"token_usage": {"inbound_tokens": 0, "outbound_tokens": 0}})
    # No model priced (empty provider/model), so the whole cost is the container line.
    assert abs(run_cost_usd(run, PRICES) - 0.05) < 1e-9


def test_a_plain_local_run_is_unaffected():
    run = {"provider": "openai", "model": "gpt", "execution_mode": "local",
           "process": {"token_usage": {"inbound_tokens": 0, "outbound_tokens": 0}}}
    assert run_cost_usd(run, PRICES) == 0.0


def test_container_hour_price_helpers_fail_open_on_garbage(monkeypatch):
    from common import config
    monkeypatch.setattr(config, "read_dot_env",
                        lambda: {"AGENTS_HUB_CONTAINER_HOUR_SMALL": "not-a-number"})
    assert pricing.container_hour_price("small") == pricing.CONTAINER_HOUR_PRICE_DEFAULT["small"]
    assert pricing.container_hour_price("unknown-size") == 0.0


def test_a_reported_cost_still_gets_the_container_hours():
    """A wrapped CLI reports what its model calls cost, not the container the
    hub ran it in: both routes that read a run's spend add the hours."""
    from dashboard.backend.routes import accounting, costs
    run = _run(container_size="small", reported_cost_usd=1.25)
    assert abs(costs._run_cost(run, PRICES) - 1.30) < 1e-9
    assert abs(accounting._run_cost(run, PRICES) - 1.30) < 1e-9
