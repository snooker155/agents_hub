---
title: "Chat"
description: "Ask any agent a focused question without setting up a task"
---

# Chat

Chat is the front door: talk to an agent directly without creating a task or configuring nodes. Each conversation is a fresh run, recorded in session history. Use it for investigation, planning, brainstorming, or quick help.

## What you get

A conversation with an agent that lives in session history. The agent sees the turn history and its system prompt but nothing from other conversations. If the agent produces runnable code (a `code` view), it opens in a Code panel where you can run it, save it, edit versions, and diff them.

## Before you start

- `AGENT_EXECUTION_MODE=local`
- Workspace override (optional) to use a different model than the global default

## Steps

1. Open **Chat** from the main menu.
2. Pick an agent from the dropdown. Start with Main Agent.
3. Pick a workspace.
4. Ask a focused question:
   - "Summarize the current project architecture"
   - "Explain how task execution logs are stored"
   - "Review this API design and suggest improvements"
5. If you have a code snippet or document, attach it by pasting or uploading.
6. Wait for the response. Watch the streaming output.
7. If the agent produces code, the **Code panel** opens on the right side. Click on a snippet to open it.
8. In the Code panel, copy it, download it, run it with the Run button, save it to a project, or send it back to the agent for discussion.
9. All versions are tracked. Click History to see previous edits and diff them.

## Where to read more

Learn how Chat works, including streaming, attachments and code panels in [Chat](/guide/chat). See code view details in [Views](/guide/views).

![Chat workflow](/screenshots/recipes/chat.png)
