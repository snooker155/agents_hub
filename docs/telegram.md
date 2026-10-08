# Telegram

A Telegram bot can be bound to this service, so a conversation continues from a
phone and notifications arrive where you are.

## What the binding does

A Telegram chat is bound to a workspace and an agent. Messages arriving there
run that agent, in that workspace, and the reply goes back to the chat.

## Voice messages

A voice note, an audio file or a round video message is downloaded through the
Bot API and transcribed with the transcription model of the bound workspace,
the same one the assistant's microphone uses (Models page, Special models tab).
The bot answers with `Heard: ...` and runs the turn with what was said as the
message; a caption typed with the recording goes first. The prompt tells the
agent the message was spoken, so a misheard word reads as one. Without a transcription
model in the workspace the bot says how to add one (or to press the ready local
set button on the Local tab, which installs Whisper). The limits are the
assistant's: `AGENTS_HUB_VOICE_MAX_SECONDS` (120 s) and
`AGENTS_HUB_VOICE_MAX_BYTES` (8 MB). The transcription is a `voice` run with
its price, like the assistant's. Answers still go back as text.

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

A message in a chat with no agent or flow bound gets the help text as before,
and also wakes every [proactive agent](proactive.md) listening for Telegram
(a `telegram` trigger on its profile), so somebody can look at it.

Related: [chat](chat.md), [scheduling](scheduling.md), [proactive](proactive.md).
