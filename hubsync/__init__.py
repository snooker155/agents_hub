"""Two-way sync between hubs, with optional end-to-end encryption (docs/hub-sync.md).

crypto.py seals records; adapters/ turn each kind of data into portable
documents; vault.py is the remote's record store; state.py is each end's
memory of what it last exchanged; engine.py runs a sync pass.
"""
