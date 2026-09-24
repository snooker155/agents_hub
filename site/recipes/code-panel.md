---
title: "Code Panel"
description: "Work with runnable code snippets inside Chat"
---

# Code Panel

When an agent produces code in Chat, it opens in a dedicated side panel instead of sitting inside a code fence. Edit it, run it in the sandbox, save versions, save to a project, and discuss changes with the agent.

## What you get

A **Code panel** on the right side of Chat showing every code snippet from the conversation, grouped by filename. The current snippet has an editor with syntax highlighting, copy and download buttons, a run button, version history and a diff viewer.

## Before you start

- An agent with `create_view` tool (most system agents have it)
- Chat open in a workspace
- A question that prompts code generation

## Steps

1. Open **Chat** and pick an agent.

2. Ask for code: "Write a Python script that calculates Fibonacci numbers" or similar.

3. If the agent returns code, the **Code panel** appears on the right, grouped by filename.

4. Click a snippet to switch to it.

5. In the editor:
   - **Copy:** Copy code to your clipboard.
   - **Download:** Save as a file.
   - **Run:** Execute in the sandbox (Python, Node.js or Bash). Output appears below.
   - **Discuss:** Puts the snippet into the message box as context, so your next question is about this code.
   - **Edit:** Asks the agent for a revised version; the reply arrives as the next version of the same file. Your own changes in the editor become a version when you save.
   - **Save to project:** Write code into a project folder.
   - **History:** See all versions and who made each edit.
   - **Diff:** Compare any two versions.

## Where to read more

Learn about views and the code kind in [Views](/guide/views). See Chat features in [Chat](/guide/chat).

![Code panel workflow](/screenshots/recipes/code-panel.png)
