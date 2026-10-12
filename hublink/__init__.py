"""
Hub link: one hub attached to another (docs/hub-link.md).

Two directions, both optional, both set up from one record
(:mod:`hublink.store`):

* **Uplink** (:mod:`hublink.link`, :mod:`hublink.mirror`): this hub uses a
  hub elsewhere, usually a shared one in multi-user mode, as a provider of
  models (its ``/v1`` becomes the custom backend ``hub-remote``) and of
  agents (its ``/v1/mcp`` is attached as an MCP server), and sends copies of
  what happens here (finished runs, conversations, personal notes) to it, one
  way, through an outbox. Nothing is ever read back: the remote hub is a
  mirror and a supplier, never a source.
* **Node** (:mod:`hublink.node_client` here, :mod:`hublink.nodes` and
  :mod:`hublink.jobs` on the other side): this hub serves the remote one as
  a node: it long-polls for work and answers with its own local models and
  its own sandbox, so the remote hub's agents can run snippets on this
  machine and use the models loaded here. A node accepts no connection; it
  dials out, so a laptop behind NAT qualifies.

The same code is on both sides: every hub can be linked to one hub and can
have any number of nodes.
"""
