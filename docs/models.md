# Models

The Models page is the source of truth for which models exist, which are
offered, what they cost, and which is default per provider.

## Catalog vs. enabled

A provider exposes many models. The catalog curates that raw list down to the
ones you actually want offered: **only enabled models appear in the picker**.

## Defaults and overrides

Resolution order, most specific first:

1. the agent's own pinned model
2. the workspace's explicit model override
3. the workspace's default model
4. the global default provider and model

An agent with nothing pinned inherits, which is usually what you want: change
the workspace default and the whole workspace moves.

## Pricing

Each model carries three prices in USD per million tokens: **input**, **cached
input** and **output**. They turn recorded token counts into estimated spend on
the [costs](costs.md) page. A model with no price set contributes tokens but
no cost, so a zero total means "unpriced", not "free".

Cached input is its own price because the provider bills it as its own line. An
agent loop re-sends the whole conversation every step, and the provider serves
all but the first copy of that prefix from its prompt cache at a fraction of the
input rate. A model with no explicit figure is given a tenth of its input price,
which is what the major providers charge; set the field where yours differs.

Each row also records **where its price came from**: `auto` for a figure filled
in from the shipped table at discovery, `manual` once you have edited it. An
edited price is never overwritten by a later discovery, and the page marks it.

## Context window

A model can carry its context window, and that number is what the chat's context
meter draws against. A window of `0` means unknown, and the meter then draws
nothing rather than inventing a ceiling.

## Providers

API keys and base URLs live in [settings](settings.md), not here. This page is
about which models and what they cost. Local providers (Ollama, LM Studio) and
custom OpenAI-compatible backends are configured the same way.

Related: [settings](settings.md), [costs](costs.md).
