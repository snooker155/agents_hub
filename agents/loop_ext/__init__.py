"""
Policies applied inside the agent loop (see agents/agent_loop.py).

One module per policy, each exposing ``extension_for(agent)`` that returns a
:class:`agents.agent_loop.LoopExtension` when the policy applies to that agent
and None when it does not:

- ``steering``: messages a person sends while the run works reach the model
  before its next step.
- ``compaction``: old tool results leave the context before it fills up;
  Anthropic's server-side context management where available.
- ``view_focus``: a view agent sees the tools of the view kind it is on
  (the Studio's view, or the one the run created) and not the other kinds'.
- ``tool_search``: an agent with many tools sees a short list and a
  ``search_tools`` tool; Anthropic's native deferred loading where available.
- ``structured``: strict tool schemas where the provider supports them, and
  the final answer validated against the agent's output schema
  (``finalize_output``).
- ``fallback``: other models behind the agent's own on refusals, rate limits
  and server errors, with the answering model recorded on the run.
"""
