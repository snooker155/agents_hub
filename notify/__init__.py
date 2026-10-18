"""Alert rules: in-app notifications a workspace raises on its own.

``store``
    Per-workspace configuration: which alert rules are armed.
``rules``
    Deciding whether a finished run should raise a notification on its own,
    a failure or spend past a threshold, without an agent or a person asking
    for one.

A rule fires into the notification inbox (the bell, ``plans.service.
create_notification``); there is no outbound channel.
"""
