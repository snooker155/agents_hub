---
title: "Specialize an Agent"
description: "Build a specialist that extends a system agent's prompt and tools instead of starting from a blank page"
---

# Specialize an Agent

A system agent already has a working prompt, a sensible tool set and a model
picked for its job. Starting a new specialist from scratch throws that away
and copies the parts that still apply by hand, which drifts the moment the
original is improved. Extending it instead keeps the two connected: edit the
parent later, and every agent built on it picks the change up on its next
run, with no file to re-copy.

## What you get

A new agent that starts from a parent's effective prompt, tools and model,
with only your own additions, replacements and removals stored on it. The
agent page shows where each field comes from and offers a one-click reset
back to following the parent for anything you overrode by mistake.

## Before you start

- A system agent whose job is close to the one you need (Analyst, Verifier,
  Sourcer and Screener are good starting points)
- The editor role in the workspace you are creating the agent in

## Steps

1. Go to **Agents** and click **Create**.
2. In **Based on**, pick the system agent to start from — **Analyst**, for a
   specialist that still queries the workspace's connected data the same
   way. Leave the version on **latest** so the child keeps following
   Analyst as it changes; pick a specific version only if you want this
   agent frozen against future edits to the parent.
3. The prompt field is now **Your additions**: write only what is particular
   to this specialist. A heading that matches one of Analyst's own (for
   instance `## How to work`) replaces that section; anything else is
   appended after it. Leave it empty if the parent's prompt already says
   everything this agent needs.
4. On the Tools tab, add or remove only what differs from Analyst's tool
   set — the Inheritance card marks each tool as inherited or your own, with
   a toggle for each.
5. Save. Open the agent page: the header reads "Inherits from Analyst ·
   latest", linking back to it, and the Inheritance card lists every field,
   where it comes from, and "Reset to inherited" for anything you changed.
6. Run the agent once from Chat to see the merged prompt and tool set in
   action.
7. Go back to Analyst and tweak one line of its prompt. Reopen your
   specialist's Inheritance card: the change is already there, with nothing
   to re-save on this agent's side.

## Where to read more

What inherits and what never does, how the prompt actually merges section by
section, pinning a child to a specific parent version, and the capability
guard's rule that a security exemption is never inherited, are in
[Agent inheritance](/guide/agent-inheritance). The finance and recruiting
[Industry Kits](/recipes/industry-kits) are built this way — `finance_analyst`
extends Analyst, `finance_reviewer` extends Verifier — and
[`ah apply`](/guide/apply) declares the same `extends:` field in a file, with
`+tool` / `-tool` entries for a child's own additions and removals.
