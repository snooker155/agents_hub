"""
The consent portal: an end user of a widget or a chat channel lets an agent
act on their own Google or Microsoft account (docs/consent.md).

A widget visitor or a Slack, Telegram or mail correspondent has no hub
account, so the hub's own connections (connectors/google, connectors/microsoft)
are the operator's, not theirs. Here the agent asks for theirs instead:

1. The turn knows its end user as a principal string, bound next to the
   secret scope (common/secrets.py ``set_end_user``) by widgets/turn.py and
   the channel turns.
2. ``request_account_access`` (:mod:`.tools`) creates a request
   (``consent_requests``, migration 0039) and returns a signed, single-use
   link to a public page (:mod:`.links`, :mod:`.page`, the routes in
   dashboard/backend/routes/consent.py).
3. The page says in plain words what is asked; Continue goes to the provider
   with a state nonce and PKCE (:mod:`.flow`); the callback stores the
   refresh token as a personal secret of that end user, bound to the
   workspace and the agent (:mod:`.access`).
4. In that end user's later turns the Google client and a delegated Graph
   client use their token, and an agent whose settings name the provider
   never falls back to the hub's account for them.
5. The end user takes it back with ``revoke_account_access``, the operator
   from the agent's Account access card; Google's grant is revoked at Google
   too.

Modules: :mod:`.catalog` (providers, access kinds, plain words), :mod:`.store`
(rows), :mod:`.links`, :mod:`.flow`, :mod:`.access`, :mod:`.tools`, :mod:`.page`.
"""
