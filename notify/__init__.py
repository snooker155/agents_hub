"""Outbound notifications (webhooks, Slack) and inbound alert rules.

Three modules, one direction each:

``store``
    Per-workspace configuration: which endpoints exist, which alert rules are
    armed, and the small table that makes an inbound delivery idempotent.
``outbound``
    Delivering an event to an endpoint: signing it, POSTing it, retrying once,
    never raising, never blocking the caller.
``rules``
    Deciding whether a finished run should raise a notification on its own —
    a failure, or spend past a threshold — without an agent or a person
    asking for one.
``inbound``
    Verifying that an inbound webhook is who it claims to be, and that this
    hub has not already processed it.

See ``docs/notifications.md`` for the event shape, headers and signature
scheme shared by both directions.
"""
