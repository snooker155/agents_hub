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

## Temperature

Each model can carry its own temperature, set in its row. An empty field means
the global temperature and shows it as the placeholder. Resolution, most
specific first:

1. the agent's own temperature (its model override)
2. the model's temperature on this page
3. the global temperature: Settings, Models, Temperature (`LLM_TEMPERATURE` in `.env`,
   0.0 unless set), applied by the running backend at once

There is no workspace level.

Some models refuse a temperature. OpenAI's reasoning models take none at all
while they reason, so the field is disabled for gpt-5, gpt-5-mini, gpt-5-nano
and the o-series. gpt-5.1 and later take one only at reasoning effort `none`,
which is what an agent with thinking off sends them (below), so for those it
applies only while thinking is off. Claude takes none while it thinks either.

## Reasoning

The Reasoning column shows two things per model: the effort the provider applies
when a request names none, and what the hub sends when an agent's thinking
level is off. They differ because OpenAI's reasoning models keep reasoning when
nothing is asked for, billed and invisible:

| Family | Provider default | Sent for off |
|---|---|---|
| gpt-5, gpt-5-mini, gpt-5-nano | medium | minimal |
| gpt-5.1 to gpt-5.4 | none | none |
| gpt-5.5, gpt-5.6 | medium | none |
| o1, o3, o4 | medium | low (cannot go lower) |
| gpt-5-pro | high | nothing (cannot go lower) |
| Claude | off | nothing |

The table was measured against the API (`providers/reasoning_profile.py`);
providers without one show a dash. A call that never chose a level, such as a
chat title or an eval judge, sends nothing and keeps the provider default. For
what a positive level shows in the chat, see [agents](agents.md).

## Providers

API keys and base URLs live in [settings](settings.md), not here. This page is
about which models and what they cost. Local providers (Ollama, LM Studio) and
custom OpenAI-compatible backends are configured the same way.

## Local models

An Ollama you run, and the hub's own llama.cpp runtime, are managed from this
page too: pull and delete Ollama models, download GGUF files from Hugging
Face, load and unload them. A model the runtime loads is added here under the
provider `hub-local`, enabled, and disabled again when it is unloaded. See
[local models](local-models.md).

## Special models

Images, video, speech, transcription and models of a workspace's own are not
chat models and are not in the catalog. The tab **Special models** picks them
for the workspace chosen in the header, the same form as that workspace's
settings. See [special models](special-models.md).

Related: [settings](settings.md), [costs](costs.md), [local models](local-models.md), [special models](special-models.md).
