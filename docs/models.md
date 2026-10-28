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

A workspace with a [model policy](#model-policy-per-workspace) narrows all of
this to the models the policy allows.

## Model policy per workspace

An administrator can fix which models a workspace runs, and with what
settings, so that its members cannot change them. It is set on the
workspace's page (Settings tab), or with
`PUT /api/workspaces/{name}/model-policy`:

```json
{"models": ["openai/gpt-5", "anthropic/claude-sonnet-5-5"],
 "default": "openai/gpt-5",
 "temperature": 0.2, "effort": "medium", "max_tokens": 8000}
```

`models` are enabled catalog models; `default` must be one of them (the first
when left out). `temperature`, `effort` and `max_tokens` are optional. An
empty `models` list removes the policy. In `multi` mode only an administrator
writes it, owner or not; every member reads it (`GET` on the same path, and
`policy` in `GET /api/workspaces/{name}/model`).

With a policy in place:

- the workspace's default model is the policy's, and the workspace default
  route refuses to change it;
- the header's model picker offers only the policy's models; the global
  default is not one of them;
- an agent's own model may be set only to an allowed model of its workspace
  (and of the workspace the page is on);
- a model switch sent to a running run, an agent's fallback models, the
  advisor and a delegation with a named model pick only among the allowed
  models (`tools/delegation.py` `enabled_models`, `resolve_model`);
- at run time, a model that is not allowed (an agent pinned before the policy,
  a shared or system agent pinned for another workspace) runs on the policy's
  default instead, with that provider's own endpoint and key
  (`agents/agent_factory.py`);
- `temperature`, `effort` and `max_tokens`, when set, beat the agent's own
  values and the header's effort.

A policy of one model makes that model the workspace's global model: its
default, the only choice in the header, and the only model any agent there
runs on.

The policy also makes the workspace's [special models](special-models.md)
(images, video, speech, transcription and its own models) the
administrator's to set: members see them read-only. Send
`"lock_special_models": false` to leave them to the members.

A member's personal workspace with no policy of its own follows the `default`
workspace's (`inherited_from: "default"` in the answer), so working in one's
own workspace does not step around the hub's policy. An administrator can
give a personal workspace a policy of its own, which then applies instead.

Setting a policy clears the header's choice when the policy does not allow
it. The model catalog itself (which models are enabled, their prices,
temperatures and efforts) is the administrator's to change in `multi` mode.

## A first key switches on a model

Saving a provider key (Settings, the welcome window, or the assistant's
connection card) used to leave a trip to this page before the first run: enable
a model and set its price. Now, when the provider has no enabled model yet, the
hub enables one default model for it (`common/default_model.py`, one table:
`gpt-5.4-mini` for OpenAI, `claude-sonnet-5-5` for Anthropic,
`gemini-2.5-flash` for Google), fills its catalog price, stars it as the
provider's default and, if the hub has no global default provider or the
current one has no key, makes this provider the global default and writes
`DEFAULT_PROVIDER` and the provider's `*_MODEL` to `.env`. It never overrides a
choice: a provider with an enabled model is left alone, and a global default
that has a key stays. A key saved in another workspace enables the model but
does not touch the global default. Settings shows what was switched on, with
the price and a link here, and you change it on this page as usual.

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
so for those it applies only at that effort. Claude takes none while it thinks
either.

## Reasoning effort

How much a model reasons before it answers is a model setting, like the
temperature. Resolution, most specific first:

1. the agent's own effort (its Model tab)
2. the effort chosen in the header's model picker for the workspace, for agents
   that inherit the model too (an agent with a model of its own skips it)
3. the model's effort on this page (the Reasoning effort column)
4. the provider's default, sent explicitly so the reasoning summary is asked for

The header's choice is stored apart from the model (`model_effort` in the
workspace record), so it outlives a model switch. A personal workspace that
chose none takes `default`'s.

What a value may be depends on the model, so every picker offers only the
efforts that model accepts, and a value carried over from another model is
clamped: to the nearest accepted effort below, else the model's lowest. `off`
is the model's lowest (OpenAI reasoning models never stop reasoning when
nothing is sent; they fall back to their default, billed and invisible):

| Family | Efforts | Provider default | Off sends |
|---|---|---|---|
| gpt-5, gpt-5-mini, gpt-5-nano | minimal, low, medium, high | medium | minimal |
| gpt-5.1 | none, low, medium, high | none | none |
| gpt-5.2 to gpt-5.4 | none to xhigh | none | none |
| gpt-5.5, gpt-5.6, gpt-6 | none to xhigh | medium | none |
| o1, o3, o4 | low, medium, high | medium | low |
| gpt-5-pro | high | high | nothing |
| Claude Opus 5.5 | low to max | medium | low (thinking cannot stop) |
| Claude Opus 5, Sonnet 5, Sonnet 5.5, Fable | low to max | high | low |
| Claude Opus 4.7, 4.8 | off, low to max | off | nothing |
| Claude Opus 4.6, Sonnet 4.6 | off, low, medium, high, max | off | nothing |
| Older Claude (Haiku 4.5) | off, low, medium, high (token budget) | off | nothing |
| Ollama reasoning families, LM Studio, custom backends | off, low, medium, high | off | nothing |

Claude gets the effort as `output_config.effort` with adaptive thinking; older
Claude models think on a token budget sized from it. The OpenAI defaults were
measured against the API (`providers/reasoning_profile.py`); gpt-6 is assumed
to behave like gpt-5.6 until measured. Google models take no effort and show a
dash. A call that never chose an effort, such as a chat title or an eval
judge, sends nothing and keeps the provider default. For what a reasoning
model shows in the chat, see [agents](agents.md).

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
