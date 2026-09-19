Use this agent when the prompt is going somewhere else: your own API calls, another team's
application, a template in your repository.

Good fits:
- "Write a system prompt that turns support emails into structured JSON"
- "Here is a prompt that keeps drifting off-format — fix it and tell me what was wrong"
- "I need the same prompt for a small local model, it is too vague for it"

Poor fits:
- Changing how an agent *in this product* behaves — use the Agent Creator from that agent's
  Config tab, which edits the real files rather than handing you text
- Anything not actually asking for a prompt; it will answer plainly instead

How to invoke:
- Describe the task, and say which model it will run against if you know
- Say what the output feeds into; if something parses it, say so, that changes the prompt
- Say what going wrong looks like. That is usually the most useful sentence you can give it

It returns the prompt in the chat, in a block you can copy whole, with its assumptions listed
underneath. Ask if you want it saved to a file.
