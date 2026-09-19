# Telegram

A Telegram bot can be bound to this service, so a conversation continues from a
phone and notifications arrive where you are.

## What the binding does

A Telegram chat is bound to a workspace and an agent. Messages arriving there
run that agent, in that workspace, and the reply goes back to the chat.

## Notifications

`notify_user` and `schedule_notification` deliver to the dashboard inbox by
default. Setting `telegram=true` also sends to the bound chats.

## The security consequence

Inbound Telegram messages are attacker-controllable text: anyone who can reach
the bot can put words into an agent's context. That exposure exists at the
**channel** level, before any tool is involved, so runs on this channel are
evaluated with `ingests_untrusted` added to whatever their tools grant.

In practice: an agent that is safe in the dashboard can be an unsafe combination
on Telegram, because the channel supplies the ingest leg of the trifecta for
free. See [tools-and-capabilities](tools-and-capabilities.md).

Related: [chat](chat.md), [scheduling](scheduling.md).
