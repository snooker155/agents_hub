"""
Chat channels: the shape every two-way messaging connector shares.

Telegram (``connectors/telegram``) was the first channel and grew its own
store, poller and command set. Slack, Discord, Microsoft Teams and email
need the same three things, so they are written once here and each channel
only supplies the transport:

- ``store``    — one document per channel: config (tokens are write-only),
                 enabled flag, the chat allowlist, bindings and a cursor.
- ``turns``    — the bridge from an inbound message to the chat pipeline and
                 back: the same ``run_chat_pipeline`` the web Chat page uses,
                 the same conversation history rebuild, the same entity links.
- ``service``  — the base class for a channel's background loop (poll or
                 websocket), with per-chat locks and the status dict the
                 Connectors page shows; registered on the singleton
                 supervisor so it runs on one replica.
- ``commands`` — the text commands (``/agent``, ``/flow``, ``/reset`` ...)
                 a user can type in any channel, with the same rule Telegram
                 enforces: a chat's workspace is set by an operator from the
                 dashboard, never from the chat itself.
- ``registry`` — the channels the hub knows about, so one set of routes
                 (``dashboard/backend/routes/channels.py``) and one outbound
                 notification path serve all of them.
"""
