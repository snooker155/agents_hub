"""Secrets bound to hosts: placeholders in the run, real values at egress.

Why. A secret handed to a run as an environment variable is readable by
everything the run does: a prompt injected into a fetched page can make the
agent send ``$GITHUB_TOKEN`` anywhere. A secret with ``allowed_hosts``
(common/secrets.py) therefore reaches the run as a placeholder
(``ahsec_<32 random characters>``) and the egress proxy (environments/egress.py)
puts the real value into a request only on its way to one of those hosts.
Anywhere else a placeholder the proxy can see is refused and logged: a
placeholder on its way to a foreign host is what exfiltration looks like.

The parts, all in this module:

* **The sealed values** (:func:`seal`, :func:`unseal`): placeholder to
  ``{name, hosts, value}``, kept in this process and in the ``egress_secrets``
  DocStore with the value encrypted under ``AGENTS_HUB_SECRET_KEY`` (the same
  Fernet key as the secrets table), so a proxy in a worker process can serve a
  launch prepared elsewhere. Without a key the record stays in memory only.
* **Substitution** (:func:`substitute_head`): placeholders in the request
  target and the headers, inside ``Authorization: Basic`` too (a git push
  carries its token there, base64 encoded). Bodies are left alone.
* **The hub CA** (:func:`ensure_ca`, :func:`leaf_context`): the proxy can see
  headers only when it terminates TLS, so for the hosts of a run's bound
  secrets (and only those) it answers ``CONNECT`` with a certificate minted on
  the fly under a hub CA. The CA key lives under the hub data dir
  (``<AGENTS_HUB_ROOT>/egress_ca``, or ``AGENTS_HUB_EGRESS_CA_DIR``) with mode
  0600. The run trusts it through a bundle (:func:`ca_bundle_path`): the
  system bundle, ``certifi`` when installed, and the hub CA, so every other
  host still verifies as before. The proxy verifies the real upstream normally.
* **Routing the run** (:func:`route_env`): a run holding placeholders gets the
  proxy variables (an ``open`` token when no environment fenced its network,
  else the environment's token gains the secret hosts) and the CA variables
  ``SSL_CERT_FILE``, ``REQUESTS_CA_BUNDLE``, ``CURL_CA_BUNDLE``,
  ``GIT_SSL_CAINFO`` and ``NODE_EXTRA_CA_CERTS``.
"""
from __future__ import annotations

import base64
import datetime as _dt
import ipaddress
import logging
import os
import re
import secrets as _random
import ssl
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

PLACEHOLDER_PREFIX = "ahsec_"
PLACEHOLDER_RE = re.compile(r"ahsec_[A-Za-z0-9]{32}")
#: The run's placeholders, comma separated, so the launch can route it.
PLACEHOLDERS_ENV = "AGENTS_HUB_SECRET_PLACEHOLDERS"
CA_DIR_ENV = "AGENTS_HUB_EGRESS_CA_DIR"
#: Every variable a client library reads its trust store from.
CA_BUNDLE_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO")
NODE_CA_VAR = "NODE_EXTRA_CA_CERTS"
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

CA_DAYS = 3650
LEAF_DAYS = 365
_LEAF_CACHE_MAX = 256

_STORE_NAME = "egress_secrets"


# ── sealed values ────────────────────────────────────────────────────────────

_SEALED: Dict[str, Dict[str, Any]] = {}
_SEALED_LOCK = threading.Lock()


def _docstore():
    from common.docstore import DocStore
    return DocStore(_STORE_NAME)


def _fernet():
    try:
        from common.secrets import _fernet as fernet
        return fernet()
    except Exception:  # noqa: BLE001 - no key configured or no cryptography: memory only
        log.debug("secret egress: no Fernet key, sealed values stay in this process", exc_info=True)
        return None


def _ttl() -> int:
    try:
        from environments.egress import token_ttl
        return token_ttl()
    except Exception:  # noqa: BLE001 - a day, the token default
        return 86400


def seal(name: str, value: str, hosts: Iterable[str], *, ttl: Optional[int] = None) -> str:
    """A fresh placeholder for ``value``, substitutable only toward ``hosts``."""
    placeholder = PLACEHOLDER_PREFIX + "".join(
        _random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
        for _ in range(32))
    record = {"name": str(name), "hosts": [str(h).lower() for h in hosts if h],
              "expires_at": time.time() + (ttl or _ttl())}
    with _SEALED_LOCK:
        now = time.time()
        for key in [k for k, v in _SEALED.items() if v.get("expires_at", 0) < now]:
            _SEALED.pop(key, None)
        _SEALED[placeholder] = {**record, "value": str(value)}
    fernet = _fernet()
    if fernet is not None:
        try:
            token = fernet.encrypt(str(value).encode("utf-8")).decode("ascii")
            _docstore().put(placeholder, {**record, "value_enc": token})
        except Exception:  # noqa: BLE001 - the in-process copy still serves a proxy here
            log.debug("secret egress: could not persist a sealed value", exc_info=True)
    return placeholder


def unseal(placeholder: str) -> Optional[Dict[str, Any]]:
    """``{name, hosts, value}`` for a live placeholder, or None."""
    if not placeholder or not PLACEHOLDER_RE.fullmatch(placeholder):
        return None
    with _SEALED_LOCK:
        record = _SEALED.get(placeholder)
    if record is None:
        try:
            doc = _docstore().get(placeholder)
        except Exception:  # noqa: BLE001 - no database here: in-process records only
            doc = None
        fernet = _fernet() if isinstance(doc, dict) else None
        if isinstance(doc, dict) and fernet is not None:
            try:
                value = fernet.decrypt(str(doc.get("value_enc") or "").encode("ascii")).decode("utf-8")
            except Exception:  # noqa: BLE001 - another key, or a damaged row: not substitutable
                log.warning("secret egress: placeholder for %s does not decrypt", doc.get("name"))
                return None
            record = {"name": doc.get("name"), "hosts": list(doc.get("hosts") or []),
                      "expires_at": float(doc.get("expires_at") or 0), "value": value}
            with _SEALED_LOCK:
                _SEALED[placeholder] = record
    if record is None or float(record.get("expires_at") or 0) < time.time():
        return None
    return record


def placeholders_in(env: Dict[str, str]) -> List[str]:
    raw = str((env or {}).get(PLACEHOLDERS_ENV) or "")
    return [p for p in (x.strip() for x in raw.split(",")) if PLACEHOLDER_RE.fullmatch(p)]


def hosts_for(placeholders: Iterable[str]) -> List[str]:
    out: List[str] = []
    for placeholder in placeholders:
        record = unseal(placeholder)
        for host in (record or {}).get("hosts") or []:
            if host not in out:
                out.append(host)
    return out


# ── substitution ─────────────────────────────────────────────────────────────

class Refused(Exception):
    """A placeholder was on its way to a host it is not bound to."""

    def __init__(self, reason: str, names: List[str]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.names = names


def _swap(text: str, host: str, allowed: Optional[Iterable[str]]) -> Tuple[str, List[str]]:
    """``text`` with every placeholder replaced; raises :class:`Refused` for a
    placeholder this run does not hold, an expired one, or one not bound to
    ``host``. Returns the text and the names swapped in."""
    from environments.egress import host_allowed
    held = set(allowed) if allowed is not None else None
    used: List[str] = []

    def repl(match: "re.Match[str]") -> str:
        placeholder = match.group(0)
        record = unseal(placeholder) if (held is None or placeholder in held) else None
        if record is None:
            raise Refused("a secret placeholder this run does not hold", [])
        if not host_allowed(host, record.get("hosts") or []):
            raise Refused(f"secret {record.get('name')} is not bound to {host}",
                          [str(record.get("name") or "")])
        used.append(str(record.get("name") or ""))
        return str(record["value"])

    return PLACEHOLDER_RE.sub(repl, text), used


def _swap_basic(value: str, host: str, allowed: Optional[Iterable[str]]) -> Tuple[str, List[str]]:
    """``Basic <base64>`` with placeholders swapped inside the decoded pair."""
    scheme, _, encoded = value.partition(" ")
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value, []
    if not PLACEHOLDER_RE.search(decoded):
        return value, []
    swapped, used = _swap(decoded, host, allowed)
    return f"{scheme} {base64.b64encode(swapped.encode('utf-8')).decode('ascii')}", used


def substitute_head(target: str, headers: List[Tuple[str, str]], host: str,
                    allowed: Optional[Iterable[str]] = None
                    ) -> Tuple[str, List[Tuple[str, str]], List[str]]:
    """The request target and headers with placeholders swapped for values.

    ``allowed`` is the set of placeholders the requesting run holds (None:
    any live one). Raises :class:`Refused` when a placeholder may not go to
    ``host``; nothing is swapped then, the whole request is refused.
    """
    used: List[str] = []
    new_target, names = _swap(target, host, allowed)
    used += names
    out: List[Tuple[str, str]] = []
    for name, value in headers:
        if name.lower() in ("authorization", "proxy-authorization") and \
                value[:6].lower() == "basic ":
            value, names = _swap_basic(value, host, allowed)
            used += names
        if PLACEHOLDER_RE.search(value):
            value, names = _swap(value, host, allowed)
            used += names
        out.append((name, value))
    return new_target, out, used


def head_has_placeholder(target: str, headers: List[Tuple[str, str]]) -> bool:
    if PLACEHOLDER_RE.search(target or ""):
        return True
    for name, value in headers:
        if PLACEHOLDER_RE.search(value):
            return True
        if name.lower() == "authorization" and value[:6].lower() == "basic ":
            try:
                if PLACEHOLDER_RE.search(base64.b64decode(value[6:].strip()).decode("utf-8", "replace")):
                    return True
            except ValueError:
                continue
    return False


# ── the hub CA and leaf certificates ─────────────────────────────────────────

def _root() -> Path:
    from common.paths import AGENTS_HUB_ROOT
    return Path(AGENTS_HUB_ROOT)


def ca_dir() -> Path:
    explicit = (os.environ.get(CA_DIR_ENV) or "").strip()
    return Path(explicit).expanduser() if explicit else _root() / "egress_ca"


def _public_dir() -> Path:
    """Where the public bundle goes: inside the state dir, which a docker run
    has mounted, so the same file serves local and container runs."""
    return _root() / "egress"


_CA_LOCK = threading.Lock()


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        log.debug("secret egress: could not tighten %s", path.parent, exc_info=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def _name(common_name: str):
    from cryptography.x509.oid import NameOID
    from cryptography import x509
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Agents Hub"),
                      x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _ca_key_password() -> Optional[bytes]:
    """The password the CA key is stored under: the hub's secret key
    (``AGENTS_HUB_SECRET_KEY``), which lives in the backend's environment and
    never in the state folder a container mounts, so a run that can read that
    folder still cannot sign a certificate with the CA. None without one."""
    from common.config import settings
    from common.secrets import _fernet_key_from
    configured = str(getattr(settings, "secret_key", "") or "").strip()
    return _fernet_key_from(configured) if configured else None


def generate_ca(directory: Path) -> Tuple[Path, Path]:
    """A new CA key (0600) and certificate in ``directory``."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    now = _dt.datetime.now(_dt.timezone.utc)
    name = _name("Agents Hub egress CA")
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(ski, critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = directory / "ca.key", directory / "ca.pem"
    password = _ca_key_password()
    encryption = (serialization.BestAvailableEncryption(password) if password
                  else serialization.NoEncryption())
    _write_private(key_path, key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8,
                                               encryption))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def ensure_ca(directory: Optional[Path] = None) -> Tuple[Path, Path]:
    """``(key_path, cert_path)`` of the hub CA, generated on first use."""
    directory = directory or ca_dir()
    key_path, cert_path = directory / "ca.key", directory / "ca.pem"
    with _CA_LOCK:
        if not (key_path.exists() and cert_path.exists()):
            generate_ca(directory)
        elif key_path.stat().st_mode & 0o077:
            os.chmod(key_path, 0o600)
    return key_path, cert_path


def _load_ca(directory: Optional[Path] = None):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    key_path, cert_path = ensure_ca(directory)
    data = key_path.read_bytes()
    password = _ca_key_password()
    try:
        key = serialization.load_pem_private_key(data, password=password)
    except TypeError:
        # A key written before the hub had a secret key is not encrypted.
        key = serialization.load_pem_private_key(data, password=None)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return key, cert


def mint_leaf(host: str, directory: Optional[Path] = None) -> Tuple[bytes, bytes]:
    """``(cert_pem, key_pem)`` for ``host``, signed by the hub CA."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID

    ca_key, ca_cert = _load_ca(directory)
    key = ec.generate_private_key(ec.SECP256R1())
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        san: Any = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)
    ca_ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(host)).issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False,
                                     content_commitment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=False, crl_sign=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
                       critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))


_LEAVES: Dict[str, ssl.SSLContext] = {}
_LEAVES_LOCK = threading.Lock()


def leaf_context(host: str, directory: Optional[Path] = None) -> ssl.SSLContext:
    """A server-side TLS context presenting a hub-signed certificate for ``host``.

    Cached per host. The pair goes through a 0600 temporary file only for the
    moment ``load_cert_chain`` needs a path.
    """
    key = f"{directory or ''}|{host}"
    with _LEAVES_LOCK:
        cached = _LEAVES.get(key)
    if cached is not None:
        return cached
    cert_pem, key_pem = mint_leaf(host, directory)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_alpn_protocols(["http/1.1"])
    fd, path = tempfile.mkstemp(prefix="ahsec-leaf-", suffix=".pem")
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(cert_pem + key_pem)
        ctx.load_cert_chain(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            log.debug("secret egress: temporary leaf file left behind", exc_info=True)
    with _LEAVES_LOCK:
        if len(_LEAVES) >= _LEAF_CACHE_MAX:
            _LEAVES.clear()
        _LEAVES[key] = ctx
    return ctx


def _system_bundles() -> List[Path]:
    out: List[Path] = []
    paths = ssl.get_default_verify_paths()
    for candidate in (paths.cafile, paths.openssl_cafile):
        if candidate and Path(candidate).is_file() and Path(candidate) not in out:
            out.append(Path(candidate))
    try:
        import certifi
        where = Path(certifi.where())
        if where.is_file() and where not in out:
            out.append(where)
    except ImportError:
        pass
    return out


def ca_bundle_path(directory: Optional[Path] = None) -> Path:
    """The trust bundle a routed run gets: system CAs, certifi, the hub CA.

    Rewritten when the hub CA is newer than the bundle, so a regenerated CA
    reaches the next run.
    """
    _key_path, cert_path = ensure_ca(directory)
    bundle = _public_dir() / "ca-bundle.pem"
    hub_ca = _public_dir() / "hub-ca.pem"
    with _CA_LOCK:
        fresh = bundle.exists() and bundle.stat().st_mtime >= cert_path.stat().st_mtime \
            and hub_ca.exists()
        if not fresh:
            bundle.parent.mkdir(parents=True, exist_ok=True)
            parts = [p.read_bytes().rstrip() + b"\n" for p in _system_bundles()]
            ca_pem = cert_path.read_bytes()
            parts.append(ca_pem)
            bundle.write_bytes(b"".join(parts))
            hub_ca.write_bytes(ca_pem)
    return bundle


def hub_ca_cert_path() -> Path:
    ca_bundle_path()
    return _public_dir() / "hub-ca.pem"


# ── routing a run through the proxy ──────────────────────────────────────────

def _token_of(url: str) -> str:
    try:
        return urlsplit(url).username or ""
    except ValueError:
        return ""


def route_env(env: Dict[str, str], *, execution_mode: Optional[str] = None,
              workspace: Optional[str] = None) -> Dict[str, str]:
    """Route a run that holds placeholders through the proxy. Mutates and
    returns ``env``; a run without placeholders is returned untouched.

    The environment's own proxy token (a ``limited`` or ``none`` network)
    gains the secret hosts, so its allowlist still holds; without one an
    ``open`` token is registered that allows every host and terminates TLS
    only for the secret hosts. Called where a launch assembles the child's
    environment, again after later layers may have replaced the proxy URL.
    """
    placeholders = placeholders_in(env)
    if not placeholders:
        return env
    from environments import egress
    hosts = hosts_for(placeholders)
    mode = execution_mode
    if mode not in ("local", "docker"):
        try:
            from environments.launch import _workspace_mode
            mode = _workspace_mode(workspace or env.get("AGENT_WORKSPACE"))
        except Exception:  # noqa: BLE001 - local is the safe guess for a proxy URL
            mode = "local"
    token = _token_of(env.get("HTTPS_PROXY") or env.get("HTTP_PROXY") or "")
    if not (token and egress.attach_secrets(token, hosts, placeholders)):
        token = egress.register([], open_network=True, secret_hosts=hosts,
                                placeholders=placeholders,
                                owner={"kind": "secrets", "workspace": env.get("AGENT_WORKSPACE", "")})
    url = egress.proxy_url(token, mode or "local")
    for key in PROXY_VARS:
        env[key] = url
    from environments.launch import NO_PROXY
    env.setdefault("NO_PROXY", NO_PROXY)
    env.setdefault("no_proxy", NO_PROXY)
    bundle = str(ca_bundle_path())
    for key in CA_BUNDLE_VARS:
        env[key] = bundle
    env[NODE_CA_VAR] = str(hub_ca_cert_path())
    return env


__all__ = [
    "CA_BUNDLE_VARS", "NODE_CA_VAR", "PLACEHOLDERS_ENV", "PLACEHOLDER_PREFIX", "PLACEHOLDER_RE",
    "Refused", "ca_bundle_path", "ca_dir", "ensure_ca", "generate_ca", "head_has_placeholder",
    "hosts_for", "hub_ca_cert_path", "leaf_context", "mint_leaf", "placeholders_in", "route_env",
    "seal", "substitute_head", "unseal",
]
