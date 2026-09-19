Use this agent when you want to know whether a change actually helped, rather than whether it
feels like it did.

Good fits:
- "I rewrote this agent's prompt — did it get better or just different?"
- "Build me a set that catches the formatting failure we keep hitting"
- "Compare this agent on the small local model against the big one"
- "We have a bug report — turn it into a case so it cannot come back quietly"

Poor fits:
- Fixing the agent. It measures; use the Agent Creator on that agent's Config tab to change it.
- Questions a single manual try would answer. An eval set is worth building when the answer has
  to hold over many inputs.

How to invoke:
- Say what "better" would mean, concretely. That is the hard part and it is yours to decide.
- Mention the failure you are worried about; it makes a better case than an invented one.
- Expect to be asked for approval before anything runs, with the projected cost attached. A
  sweep is cases times configs of model calls, and an LLM judge adds one more per cell.
