"""
Workspace secrets: encrypted at rest, handed to a run only by name.

Before this module, a provider key or a GitHub token lived either in ``.env``
(one value for every workspace and every agent) or in a workspace's
``env_vars`` metadata (plain text in the database, readable by anyone who can
read a backup). Neither can say "this token belongs to *this* agent", and both
hand everything to every run. Here a secret is a row in the ``secrets`` table
(common/migrations/0009_identity.py), scoped to a workspace and optionally to
one agent and/or one user, and a run receives only the names its agent
declares in ``AgentSpec.secrets``.

Why the allowlist lives on the agent and not on the secret: the capability
guard (tools/capabilities.py) reasons about agents. An agent that declares a
secret holds private data, so the trifecta rule sees it the moment the
declaration is saved, and an agent that declares nothing receives nothing, no
matter how many secrets its workspace holds.

Why two backends behind one interface: ``local`` keeps the ciphertext in the
table itself, encrypted with Fernet under ``AGENTS_HUB_SECRET_KEY``, which is
enough for one host. ``vault`` keeps the value in HashiCorp Vault (KV v2) and
leaves only the metadata row here, so listing, scoping and precedence work the
same way and never need a round trip to Vault. Both talk plain HTTP or the
standard ``cryptography`` package; no Vault client library.

Why failures are quiet at launch time: a secrets problem (a missing key, Vault
down, a row encrypted under an old key) must not crash a run, and it must
never degrade into handing out *more* than the allowlist. ``env_for_run``
therefore logs a warning and returns nothing on any error. Values are never
logged, never returned by ``list``, and never written to the audit trail.

Secrets bound to hosts. A secret may name ``allowed_hosts`` (a host matches an
entry or is a subdomain of one, as in the egress allowlist). Such a secret
never reaches a run process in plain text: the run gets a placeholder
(``ahsec_...``), and the egress proxy (environments/egress.py, with
environments/secret_egress.py) swaps the real value into a request only on its
way to one of those hosts. That needs the proxy, so with
``AGENTS_HUB_EGRESS_PROXY`` off the launch is refused
(:class:`SecretEgressUnavailable`) unless the operator set
``AGENTS_HUB_SECRET_PLAINTEXT_FALLBACK=1``, which hands the value over in plain
text as before. This is the one secrets failure that stops a launch: quietly
handing the value out unbound would be the very leak the hosts were set to
prevent, and quietly handing out nothing would hide the reason a run fails.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import quote

from common import db

log = logging.getLogger(__name__)

#: A secret lands in a process environment under its own name, so it must be a
#: plausible environment variable name. Upper case keeps it apart from the
#: lower-case variables the hub itself reads, and 64 characters is plenty.
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

#: The version stamped on rows written with the current key derivation. A
#: future rotation scheme can tell old rows apart by it; today there is one.
KEY_VERSION = 1

#: Fixed salt for deriving a Fernet key from a passphrase. A per-install random
#: salt would have to be stored somewhere, and the only place that survives a
#: restore is the database the key protects; a fixed salt plus a strong
#: passphrase is the honest trade here. Use ``ah secrets keygen`` for a real key.
_PASSPHRASE_SALT = b"agents-hub/secrets/v1"
_PASSPHRASE_ITERATIONS = 200_000

NO_KEY_MESSAGE = ("no secret key is configured: set AGENTS_HUB_SECRET_KEY, "
                  "generate one with ah secrets keygen")


class SecretsError(ValueError):
    """A secret could not be stored or read. The message is safe to show."""


class SecretEgressUnavailable(SecretsError):
    """A secret bound to hosts cannot be handed out: the egress proxy is off.

    Raised from :func:`env_for_run` / :func:`env_for_flow` and never swallowed,
    so the launch fails with this message instead of starting a run that holds
    the value unbound."""


#: Hand a host-bound secret over in plain text when the egress proxy is off.
PLAINTEXT_FALLBACK_ENV = "AGENTS_HUB_SECRET_PLAINTEXT_FALLBACK"


def normalize_hosts(raw: Any) -> List[str]:
    """``allowed_hosts`` as a clean list: lower case, no scheme, port or path,
    no duplicates. Accepts a list or a comma or whitespace separated string."""
    items = raw if isinstance(raw, (list, tuple, set)) else re.split(r"[\s,]+", str(raw or ""))
    out: List[str] = []
    for item in items:
        host = str(item or "").strip().lower()
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.split("/", 1)[0].split("@")[-1]
        if host.count(":") == 1:
            host = host.split(":", 1)[0]
        host = host.strip("[]").strip(".")
        if not host:
            continue
        if not re.match(r"^[a-z0-9*][a-z0-9.\-:*]*$", host):
            raise SecretsError(f"invalid host '{item}' in allowed_hosts")
        host = host.lstrip("*.")
        if host and host not in out:
            out.append(host)
    return out


def _hosts_of(row: Dict[str, Any]) -> List[str]:
    try:
        return normalize_hosts(row.get("allowed_hosts") or "")
    except SecretsError:
        return []


class SecretKeyMissing(SecretsError):
    """``AGENTS_HUB_SECRET_KEY`` is empty and the local backend needs it."""

    def __init__(self) -> None:
        super().__init__(NO_KEY_MESSAGE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_name(name: str) -> str:
    """The name, or :class:`SecretsError` when it cannot be an env var."""
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise SecretsError(
            f"invalid secret name '{name}': use upper case letters, digits and "
            "underscores, starting with a letter (it becomes an environment variable)")
    return name


def make_hint(value: str) -> str:
    """What the UI may show of a value: its last four characters, or nothing
    for a value so short that four characters would give most of it away."""
    value = str(value or "")
    return value[-4:] if len(value) > 8 else "****"


def keygen() -> str:
    """A fresh Fernet key, for ``AGENTS_HUB_SECRET_KEY``."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")


def _fernet_key_from(configured: str) -> bytes:
    """Accept either a real Fernet key or any passphrase.

    A Fernet key is 32 bytes as 44 characters of urlsafe base64; anything else
    is treated as a passphrase and stretched with PBKDF2-HMAC-SHA256.
    """
    raw = configured.strip()
    if len(raw) == 44:
        try:
            if len(base64.urlsafe_b64decode(raw.encode("ascii"))) == 32:
                return raw.encode("ascii")
        except ValueError:
            pass
    derived = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), _PASSPHRASE_SALT,
                                  _PASSPHRASE_ITERATIONS, dklen=32)
    return base64.urlsafe_b64encode(derived)


_FERNET_CACHE: Dict[str, Any] = {}


def _fernet(configured: Optional[str] = None):
    """The Fernet instance for the configured key, or None when there is none.

    Cached per key string: the passphrase derivation costs 200k iterations and
    a run launch should not pay it every time.
    """
    if configured is None:
        from common.config import settings
        configured = str(getattr(settings, "secret_key", "") or "")
    configured = configured.strip()
    if not configured:
        return None
    cached = _FERNET_CACHE.get(configured)
    if cached is None:
        from cryptography.fernet import Fernet
        cached = Fernet(_fernet_key_from(configured))
        _FERNET_CACHE.clear()
        _FERNET_CACHE[configured] = cached
    return cached


def key_configured() -> bool:
    from common.config import settings
    return bool(str(getattr(settings, "secret_key", "") or "").strip())


# ── rows ─────────────────────────────────────────────────────────────────────

_COLUMNS = ("secret_id", "workspace", "name", "agent_id", "user_id", "ciphertext",
            "key_version", "hint", "created_by", "created_at", "updated_at", "allowed_hosts")


def _scope(agent_id: Optional[str], user_id: Optional[str]) -> Tuple[str, str]:
    return str(agent_id or "").strip(), str(user_id or "").strip()


def _row_dict(row) -> Dict[str, Any]:
    return {c: row[c] for c in _COLUMNS}


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    """A row as the API shows it: scopes and hint, never the ciphertext."""
    return {
        "name": row["name"], "agent_id": row["agent_id"] or "",
        "user_id": row["user_id"] or "", "hint": row["hint"] or "",
        "updated_at": row["updated_at"], "created_at": row["created_at"],
        "created_by": row["created_by"] or "",
        "allowed_hosts": _hosts_of(row),
    }


def _rank(row: Dict[str, Any]) -> int:
    """Precedence of a row for one run: both scopes > agent > user > workspace."""
    return (2 if row["agent_id"] else 0) + (1 if row["user_id"] else 0)


class SecretBackend:
    """Where values live. Metadata (names, scopes, hints) is always the
    ``secrets`` table, so listing and precedence are shared; subclasses only
    decide how a value is sealed into a row and read back out of it."""

    name = "base"

    # ---- the three hooks a backend implements ----

    def _seal(self, workspace: str, name: str, agent_id: str, user_id: str,
              value: str) -> str:
        raise NotImplementedError

    def _unseal(self, row: Dict[str, Any]) -> Optional[str]:
        raise NotImplementedError

    def _forget(self, row: Dict[str, Any]) -> None:
        """Drop whatever the backend holds outside the row."""

    def _check_writable(self) -> None:
        """Raise a :class:`SecretsError` when ``set`` cannot work at all."""

    # ---- the shared interface ----

    def _rows(self, workspace: str, *, name: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM secrets WHERE workspace = ?"
        params: List[Any] = [str(workspace)]
        if name is not None:
            sql += " AND name = ?"
            params.append(str(name))
        sql += " ORDER BY name, agent_id, user_id"
        return [_row_dict(r) for r in db.get_conn().execute(sql, tuple(params)).fetchall()]

    def _row(self, workspace: str, name: str, agent_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        row = db.get_conn().execute(
            f"SELECT {', '.join(_COLUMNS)} FROM secrets WHERE workspace = ? AND name = ? "
            "AND agent_id = ? AND user_id = ?",
            (str(workspace), str(name), agent_id, user_id)).fetchone()
        return _row_dict(row) if row else None

    def set(self, workspace: str, name: str, value: str, agent_id: Optional[str] = None,
            user_id: Optional[str] = None, *, created_by: Optional[str] = None,
            allowed_hosts: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """Store or replace one value. Returns the public row.

        ``allowed_hosts`` None keeps what a replaced row had (nothing for a
        new one); a list, empty included, sets it.
        """
        name = validate_name(name)
        if value is None or str(value) == "":
            raise SecretsError("a secret needs a value")
        hosts = None if allowed_hosts is None else ",".join(normalize_hosts(list(allowed_hosts)))
        self._check_writable()
        agent_id, user_id = _scope(agent_id, user_id)
        value = str(value)
        ciphertext = self._seal(str(workspace), name, agent_id, user_id, value)
        now = _now()
        with db.transaction() as conn:
            existing = self._row(workspace, name, agent_id, user_id)
            if existing:
                conn.execute(
                    "UPDATE secrets SET ciphertext = ?, key_version = ?, hint = ?, updated_at = ?, "
                    "allowed_hosts = ? WHERE secret_id = ?",
                    (ciphertext, KEY_VERSION, make_hint(value), now,
                     existing.get("allowed_hosts") or "" if hosts is None else hosts,
                     existing["secret_id"]))
            else:
                conn.execute(
                    f"INSERT INTO secrets ({', '.join(_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                    (uuid.uuid4().hex, str(workspace), name, agent_id, user_id, ciphertext,
                     KEY_VERSION, make_hint(value), created_by or "", now, now, hosts or ""))
        return _public(self._row(workspace, name, agent_id, user_id) or {})

    def set_hosts(self, workspace: str, name: str, allowed_hosts: Iterable[str],
                  agent_id: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Change only the hosts of an existing secret (the value stays)."""
        hosts = ",".join(normalize_hosts(list(allowed_hosts or [])))
        agent_id, user_id = _scope(agent_id, user_id)
        with db.transaction() as conn:
            existing = self._row(workspace, name, agent_id, user_id)
            if existing is None:
                raise SecretsError(f"no secret '{name}' at this scope")
            conn.execute("UPDATE secrets SET allowed_hosts = ?, updated_at = ? WHERE secret_id = ?",
                         (hosts, _now(), existing["secret_id"]))
        return _public(self._row(workspace, name, agent_id, user_id) or {})

    def get(self, workspace: str, name: str, agent_id: Optional[str] = None,
            user_id: Optional[str] = None) -> Optional[str]:
        """The value at exactly this scope (no precedence), or None."""
        agent_id, user_id = _scope(agent_id, user_id)
        row = self._row(workspace, name, agent_id, user_id)
        return self._unseal(row) if row else None

    def delete(self, workspace: str, name: str, agent_id: Optional[str] = None,
               user_id: Optional[str] = None) -> bool:
        agent_id, user_id = _scope(agent_id, user_id)
        row = self._row(workspace, name, agent_id, user_id)
        if row is None:
            return False
        self._forget(row)
        with db.transaction() as conn:
            conn.execute("DELETE FROM secrets WHERE secret_id = ?", (row["secret_id"],))
        return True

    def list(self, workspace: str) -> List[Dict[str, Any]]:
        """Names, scopes and hints. Never values, and works with no key."""
        return [_public(r) for r in self._rows(workspace)]

    def resolve_for_run(self, workspace: str, agent_id: Optional[str], user_id: Optional[str],
                        allowed_names: Iterable[str]) -> Dict[str, str]:
        """{name: value} for one run, most specific scope first."""
        return {name: value for name, (value, _hosts) in
                self.resolve_bound(workspace, agent_id, user_id, allowed_names).items()}

    def bound_hosts(self, workspace: str, agent_id: Optional[str], user_id: Optional[str],
                    names: Iterable[str]) -> Dict[str, List[str]]:
        """{name: allowed_hosts} of the row that wins for each name, for names
        bound to hosts only. Metadata alone: no value is read."""
        wanted = [n for n in dict.fromkeys(str(x).strip() for x in (names or [])) if n]
        if not wanted or not workspace:
            return {}
        agent_id, user_id = _scope(agent_id, user_id)
        placeholders = ", ".join("?" * len(wanted))
        rows = db.get_conn().execute(
            f"SELECT {', '.join(_COLUMNS)} FROM secrets WHERE workspace = ? "
            f"AND name IN ({placeholders}) AND agent_id IN ('', ?) AND user_id IN ('', ?)",
            (str(workspace), *wanted, agent_id, user_id)).fetchall()
        best: Dict[str, Dict[str, Any]] = {}
        for raw in rows:
            row = _row_dict(raw)
            current = best.get(row["name"])
            if current is None or _rank(row) > _rank(current):
                best[row["name"]] = row
        return {name: _hosts_of(row) for name, row in best.items() if _hosts_of(row)}

    def resolve_bound(self, workspace: str, agent_id: Optional[str], user_id: Optional[str],
                      allowed_names: Iterable[str]) -> Dict[str, Tuple[str, List[str]]]:
        """{name: (value, allowed_hosts)} for one run, most specific scope first.

        Only ``allowed_names`` are considered; an empty allowlist returns
        nothing. A row that cannot be read (wrong key, Vault unreachable)
        falls through to the next most specific scope for that name rather
        than failing the whole hand-out.
        """
        allowed = [n for n in dict.fromkeys(str(x).strip() for x in (allowed_names or [])) if n]
        if not allowed or not workspace:
            return {}
        agent_id, user_id = _scope(agent_id, user_id)
        placeholders = ", ".join("?" * len(allowed))
        rows = db.get_conn().execute(
            f"SELECT {', '.join(_COLUMNS)} FROM secrets WHERE workspace = ? "
            f"AND name IN ({placeholders}) AND agent_id IN ('', ?) AND user_id IN ('', ?)",
            (str(workspace), *allowed, agent_id, user_id)).fetchall()
        by_name: Dict[str, List[Dict[str, Any]]] = {}
        for raw in rows:
            row = _row_dict(raw)
            by_name.setdefault(row["name"], []).append(row)
        out: Dict[str, Tuple[str, List[str]]] = {}
        for name, candidates in by_name.items():
            for row in sorted(candidates, key=_rank, reverse=True):
                value = self._unseal(row)
                if value is not None:
                    out[name] = (value, _hosts_of(row))
                    break
        return out


class LocalBackend(SecretBackend):
    """Fernet ciphertext in the row itself, under ``AGENTS_HUB_SECRET_KEY``."""

    name = "local"

    def __init__(self, key: Optional[str] = None) -> None:
        self._key = key  # None: read settings at use, so a changed key applies

    def _check_writable(self) -> None:
        if _fernet(self._key) is None:
            raise SecretKeyMissing()

    def _seal(self, workspace, name, agent_id, user_id, value) -> str:
        fernet = _fernet(self._key)
        if fernet is None:
            raise SecretKeyMissing()
        return fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def _unseal(self, row) -> Optional[str]:
        fernet = _fernet(self._key)
        if fernet is None or not row.get("ciphertext"):
            return None
        from cryptography.fernet import InvalidToken
        try:
            return fernet.decrypt(str(row["ciphertext"]).encode("ascii")).decode("utf-8")
        except InvalidToken:
            # Encrypted under another key: rotated without re-setting. Name the
            # row, never the value, so the operator knows what to re-set.
            log.warning("secrets: %s in workspace %s (agent=%r user=%r) does not decrypt "
                        "with the configured key; set it again", row["name"],
                        row["workspace"], row["agent_id"], row["user_id"])
            return None


class VaultBackend(SecretBackend):
    """HashiCorp Vault KV v2 over plain HTTP; the row keeps only metadata."""

    name = "vault"
    TIMEOUT = 10

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None,
                 mount: Optional[str] = None) -> None:
        import os
        self.url = (url if url is not None else os.environ.get("AGENTS_HUB_VAULT_URL", "")).rstrip("/")
        self.token = token if token is not None else os.environ.get("AGENTS_HUB_VAULT_TOKEN", "")
        self.mount = (mount if mount is not None
                      else os.environ.get("AGENTS_HUB_VAULT_MOUNT", "") or "secret").strip("/")

    def path(self, workspace: str, name: str, agent_id: str, user_id: str) -> str:
        parts = ["agents-hub", workspace, agent_id or "_", user_id or "_", name]
        return "/".join(quote(str(p), safe="") for p in parts)

    def _endpoint(self, kind: str, path: str) -> str:
        return f"{self.url}/v1/{self.mount}/{kind}/{path}"

    def _headers(self) -> Dict[str, str]:
        return {"X-Vault-Token": self.token}

    def _check_writable(self) -> None:
        if not self.url or not self.token:
            raise SecretsError("the vault secret backend needs AGENTS_HUB_VAULT_URL "
                               "and AGENTS_HUB_VAULT_TOKEN")

    def _seal(self, workspace, name, agent_id, user_id, value) -> str:
        import requests
        response = requests.post(
            self._endpoint("data", self.path(workspace, name, agent_id, user_id)),
            json={"data": {"value": value}}, headers=self._headers(), timeout=self.TIMEOUT)
        if response.status_code >= 400:
            raise SecretsError(f"vault refused the write (HTTP {response.status_code})")
        return ""  # nothing sensitive in the row

    def _unseal(self, row) -> Optional[str]:
        if not self.url or not self.token:
            return None
        import requests
        try:
            response = requests.get(
                self._endpoint("data", self.path(row["workspace"], row["name"],
                                                 row["agent_id"], row["user_id"])),
                headers=self._headers(), timeout=self.TIMEOUT)
            if response.status_code != 200:
                log.warning("secrets: vault answered %s for %s", response.status_code, row["name"])
                return None
            value = ((response.json() or {}).get("data") or {}).get("data", {}).get("value")
            return None if value is None else str(value)
        except (requests.RequestException, ValueError) as exc:
            log.warning("secrets: vault read of %s failed: %s", row["name"], type(exc).__name__)
            return None

    def _forget(self, row) -> None:
        if not self.url or not self.token:
            return
        import requests
        try:
            # metadata/ removes every version, not just the latest.
            requests.delete(
                self._endpoint("metadata", self.path(row["workspace"], row["name"],
                                                     row["agent_id"], row["user_id"])),
                headers=self._headers(), timeout=self.TIMEOUT)
        except requests.RequestException as exc:
            log.warning("secrets: vault delete of %s failed: %s", row["name"], type(exc).__name__)


def backend() -> SecretBackend:
    """The backend ``AGENTS_HUB_SECRET_BACKEND`` names (``local`` by default)."""
    from common.config import settings
    kind = str(getattr(settings, "secret_backend", "local") or "local").strip().lower()
    if kind == "vault":
        return VaultBackend()
    return LocalBackend()


# ── module-level API ─────────────────────────────────────────────────────────

def set_secret(workspace: str, name: str, value: str, *, agent_id: Optional[str] = None,
               user_id: Optional[str] = None, created_by: Optional[str] = None,
               allowed_hosts: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    return backend().set(workspace, name, value, agent_id, user_id, created_by=created_by,
                         allowed_hosts=allowed_hosts)


def get_secret(workspace: str, name: str, *, agent_id: Optional[str] = None,
               user_id: Optional[str] = None) -> Optional[str]:
    return backend().get(workspace, name, agent_id, user_id)


def delete_secret(workspace: str, name: str, *, agent_id: Optional[str] = None,
                  user_id: Optional[str] = None) -> bool:
    return backend().delete(workspace, name, agent_id, user_id)


def list_secrets(workspace: str) -> List[Dict[str, Any]]:
    return backend().list(workspace)


def resolve_for_run(workspace: str, agent_id: Optional[str], user_id: Optional[str],
                    allowed_names: Iterable[str]) -> Dict[str, str]:
    return backend().resolve_for_run(workspace, agent_id, user_id, allowed_names)


def resolve_bound(workspace: str, agent_id: Optional[str], user_id: Optional[str],
                  allowed_names: Iterable[str]) -> Dict[str, Tuple[str, List[str]]]:
    return backend().resolve_bound(workspace, agent_id, user_id, allowed_names)


def _resolve_with_hosts(workspace: str, agent_id: Optional[str], user_id: Optional[str],
                        allowed: List[str]) -> Dict[str, Tuple[str, List[str]]]:
    """``{name: (value, hosts)}`` for a run: the values through
    :func:`resolve_for_run` (the one seam callers and tests replace), the
    hosts from the winning rows' metadata."""
    values = resolve_for_run(workspace, agent_id, user_id, allowed)
    hosts = backend().bound_hosts(workspace, agent_id, user_id, list(values)) if values else {}
    return {name: (value, hosts.get(name, [])) for name, value in values.items()}


def set_secret_hosts(workspace: str, name: str, allowed_hosts: Iterable[str], *,
                     agent_id: Optional[str] = None,
                     user_id: Optional[str] = None) -> Dict[str, Any]:
    return backend().set_hosts(workspace, name, allowed_hosts, agent_id, user_id)


def _plaintext_fallback() -> bool:
    try:
        from common.config import live_setting
        raw = live_setting(PLAINTEXT_FALLBACK_ENV, "")
    except Exception:  # noqa: BLE001 - config unavailable: the process environment decides
        import os
        raw = os.environ.get(PLAINTEXT_FALLBACK_ENV, "")
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _hand_out(resolved: Dict[str, Tuple[str, List[str]]], *, run_label: str) -> Dict[str, str]:
    """The environment entries for resolved secrets.

    A secret without hosts goes in as its value. A secret with hosts goes in
    as a placeholder sealed with the egress layer (environments/secret_egress.py),
    and the placeholders are listed under ``AGENTS_HUB_SECRET_PLACEHOLDERS`` so
    the launch routes the run through the proxy. With the proxy off: plain
    text when the operator allowed it, else :class:`SecretEgressUnavailable`.
    """
    out: Dict[str, str] = {}
    bound = {name: pair for name, pair in resolved.items() if pair[1]}
    for name, (value, _hosts) in resolved.items():
        if name not in bound:
            out[name] = value
    if not bound:
        return out
    from environments import egress, secret_egress
    if not egress.enabled():
        if _plaintext_fallback():
            log.warning("secrets: %s handed to %s in plain text: the egress proxy is off and %s "
                        "is set", ", ".join(sorted(bound)), run_label, PLAINTEXT_FALLBACK_ENV)
            out.update({name: pair[0] for name, pair in bound.items()})
            return out
        raise SecretEgressUnavailable(
            f"secret {', '.join(sorted(bound))} may only be sent to its allowed hosts, which "
            "needs the egress proxy, and the proxy is off. Turn it on with "
            f"AGENTS_HUB_EGRESS_PROXY=1, or set {PLAINTEXT_FALLBACK_ENV}=1 to hand such "
            "secrets over in plain text.")
    placeholders: List[str] = []
    for name, (value, hosts) in bound.items():
        placeholder = secret_egress.seal(name, value, hosts)
        out[name] = placeholder
        placeholders.append(placeholder)
    out[secret_egress.PLACEHOLDERS_ENV] = ",".join(placeholders)
    return out


def _github_app_bound(resolved: Dict[str, Tuple[str, List[str]]], allowed: Iterable[str],
                      workspace: str, agent_id: Optional[str],
                      user_id: Optional[str]) -> Dict[str, Tuple[str, List[str]]]:
    """:func:`_with_github_app` for the ``(value, hosts)`` shape: the app's
    token carries no hosts of its own."""
    plain = {name: pair[0] for name, pair in resolved.items()}
    filled = _with_github_app(plain, allowed, workspace, agent_id, user_id)
    out = dict(resolved)
    for name, value in filled.items():
        if name not in out:
            out[name] = (value, [])
    return out


def allowed_for_agent(agent_id: str) -> List[str]:
    """The agent's declared ``secrets`` list, empty when it has none."""
    from agents.registry import get_agent
    spec = get_agent(agent_id)
    return list(getattr(spec, "secrets", None) or []) if spec else []


#: The one name the GitHub App (connectors/git/github_app.py) can supply when
#: no secret of that name exists for the run's scope.
GITHUB_TOKEN_NAME = "GITHUB_TOKEN"


def _with_github_app(out: Dict[str, str], allowed: Iterable[str], workspace: str,
                     agent_id: Optional[str], user_id: Optional[str]) -> Dict[str, str]:
    """Fill a declared but unresolved ``GITHUB_TOKEN`` from the GitHub App.

    An explicit secret always wins, so an operator who stored a token keeps
    control. Imported lazily: this module must stay importable without the
    connector package, and ``token_for_run`` itself never raises.
    """
    if GITHUB_TOKEN_NAME in out or GITHUB_TOKEN_NAME not in allowed:
        return out
    try:
        from connectors.git import github_app
    except ImportError:
        return out
    token = github_app.token_for_run(workspace, agent_id, user_id)
    if token:
        out[GITHUB_TOKEN_NAME] = token
    return out


def _allowed_names(agent_id: str, extra_names: Optional[Iterable[str]]) -> List[str]:
    """The agent's allowlist plus the names a deployment attached to this one
    task (``Task.secrets``, docs/deployments.md "Resources"). Extras widen
    one task's runs, never the agent record: the capability guard checked
    them when the deployment was saved (plans.service._validate_resources)."""
    allowed = list(allowed_for_agent(agent_id))
    for name in (extra_names or ()):
        n = str(name or "").strip()
        if n and n not in allowed:
            allowed.append(n)
    return allowed


def env_for_run(workspace: str, agent_id: Optional[str],
                user_id: Optional[str] = None,
                extra_names: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """The environment entries one agent run receives. Never raises.

    Any failure hands out nothing: a secrets problem must not crash a launch,
    and it must never fall back to handing out everything. ``extra_names``
    are the task's own secrets on top of the agent's allowlist.
    """
    if not agent_id or not workspace:
        return {}
    try:
        allowed = _allowed_names(agent_id, extra_names)
        if not allowed:
            return {}
        resolved = _github_app_bound(_resolve_with_hosts(workspace, agent_id, user_id, allowed),
                                     allowed, workspace, agent_id, user_id)
        return _hand_out(resolved, run_label=f"agent {agent_id}")
    except SecretEgressUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - never raises (see docstring): a secrets problem must not crash a launch
        log.warning("secrets: nothing handed to %s in %s: %s", agent_id, workspace,
                    type(exc).__name__)
        return {}


def env_for_flow(workspace: str, flow_id: Optional[str],
                 user_id: Optional[str] = None) -> Dict[str, str]:
    """The environment a flow run receives. Never raises.

    Every node of a flow runs in one process, so one environment serves all of
    them and there is no way to keep agent A's own token away from agent B.
    A flow therefore gets the union of its agent nodes' allowlists, resolved
    at workspace and user scope only: agent-scoped values stay with
    single-agent runs, where they cannot reach another agent.
    """
    if not flow_id or not workspace:
        return {}
    try:
        from flow import store as flow_store
        flow = flow_store.get_flow(str(flow_id)) or {}
        names: List[str] = []
        for node_agent in flow_store.flow_agent_ids(flow):
            for name in allowed_for_agent(node_agent):
                if name not in names:
                    names.append(name)
        if not names:
            return {}
        # The app identity for a flow: one environment serves every node, so
        # no single agent's github_identity can speak for all of them.
        resolved = _github_app_bound(_resolve_with_hosts(workspace, "", user_id, names),
                                     names, workspace, None, user_id)
        return _hand_out(resolved, run_label=f"flow {flow_id}")
    except SecretEgressUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - never raises (see docstring): a secrets problem must not crash a launch
        log.warning("secrets: nothing handed to flow %s in %s: %s", flow_id, workspace,
                    type(exc).__name__)
        return {}


# ── in-process access ────────────────────────────────────────────────────────
#
# A chat turn runs its agent inside the backend process, where writing a
# secret into os.environ would hand it to every other request in flight.
# ``activate`` binds the run's scope to the current context instead, and
# ``get`` resolves one name against it, through the same allowlist.

_ACTIVE: ContextVar[Optional[Tuple[str, str, str]]] = ContextVar("agents_hub_secret_scope",
                                                                  default=None)


@contextmanager
def activate(workspace: str, agent_id: str, user_id: Optional[str] = None,
             extra_names: Optional[Iterable[str]] = None) -> Iterator[None]:
    """Bind the secret scope of one in-process run for the ``with`` block.
    ``extra_names`` are a task's own secrets (see ``env_for_run``)."""
    extras = tuple(str(n).strip() for n in (extra_names or ()) if str(n or "").strip())
    token = _ACTIVE.set((str(workspace or ""), str(agent_id or ""), str(user_id or ""), extras))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def active_scope() -> Optional[Tuple[str, str, str]]:
    scope = _ACTIVE.get()
    return None if scope is None else (scope[0], scope[1], scope[2])


def get(name: str, *, host: Optional[str] = None) -> Optional[str]:
    """One secret for the active in-process run, or None.

    None outside :func:`activate`, and None for a name the active agent does
    not declare, exactly as the environment of a subprocess run would lack it.
    A secret bound to hosts is returned only when ``host`` is given and is one
    of them: an in-process run has no proxy to swap a placeholder in, so the
    caller that knows where the value goes has to say so.
    """
    scope = _ACTIVE.get()
    if scope is None:
        return None
    workspace, agent_id, user_id = scope[0], scope[1], scope[2]
    extras = scope[3] if len(scope) > 3 else ()
    try:
        if name not in _allowed_names(agent_id, extras):
            return None
        resolved = _github_app_bound(_resolve_with_hosts(workspace, agent_id, user_id, [name]),
                                     [name], workspace, agent_id, user_id)
        pair = resolved.get(name)
        if pair is None:
            return None
        value, hosts = pair
        if hosts:
            from environments.egress import host_allowed
            if not host or not host_allowed(host, hosts):
                log.info("secrets: %s withheld in process: host %r is not among its allowed hosts",
                         name, host)
                return None
        return value
    except Exception as exc:  # noqa: BLE001 - an in-process secret read must not crash the caller
        log.warning("secrets: could not read %s: %s", name, type(exc).__name__)
        return None


__all__ = [
    "NAME_RE", "KEY_VERSION", "NO_KEY_MESSAGE", "SecretsError", "SecretKeyMissing",
    "SecretEgressUnavailable", "PLAINTEXT_FALLBACK_ENV", "normalize_hosts", "resolve_bound",
    "set_secret_hosts",
    "SecretBackend", "LocalBackend", "VaultBackend", "backend",
    "validate_name", "make_hint", "keygen", "key_configured",
    "set_secret", "get_secret", "delete_secret", "list_secrets", "resolve_for_run",
    "allowed_for_agent", "env_for_run", "env_for_flow", "activate", "active_scope", "get",
]
