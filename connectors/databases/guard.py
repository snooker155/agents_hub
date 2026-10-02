"""
guard — the read-only statement check every query goes through before it
reaches a database connection (connectors/databases/drivers.py), used by both
the db_query tool (tools/databases.py) and the operator's own query route
(dashboard/backend/routes/databases.py).

check_read_only is the first line of defence; the database session itself
(opened read-only, with default_transaction_read_only, SET SESSION
TRANSACTION READ ONLY or readonly=1 depending on the driver, see drivers.py)
is the second. Neither alone is enough: a session flag can be bypassed by a
statement the driver does not expect, and a statement check alone trusts the
connection string never to belong to a superuser.

Checked, in order:
  - exactly one statement (a single trailing semicolon is allowed and
    stripped by clean_statement; any other one rejects the whole query);
  - the statement must start, after comments and whitespace, with SELECT,
    WITH, SHOW, DESCRIBE, DESC, EXPLAIN or VALUES;
  - it must not contain, outside string literals and comments, a write or
    administrative keyword, or a call to a function that reads files or
    object storage from inside the database engine.
"""
from __future__ import annotations

import re
from typing import Optional


class DatabaseError(Exception):
    """Raised for a rejected statement or a driver/connection failure.

    The message is written to be shown as-is in the UI or returned as a tool
    result, so it never carries a raw driver traceback.
    """


_ALLOWED_START = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "VALUES"}

# Write and administrative keywords, rejected as whole words anywhere in the
# statement (not just a leading one), so a CTE that ends in a write
# ("WITH cte AS (...) INSERT INTO ...") is caught the same as a bare one.
_WORD_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "CALL", "EXEC", "EXECUTE", "ATTACH", "DETACH",
    "PRAGMA", "LOAD",
)

# Function-shaped reads of the filesystem or object storage: postgres'
# pg_read_file/pg_read_binary_file, mysql's LOAD_FILE, clickhouse's
# file()/url()/s3() table functions. Only rejected when actually called
# (followed by an opening paren), so an ordinary column or alias named
# "file" or "url" is left alone.
_FUNC_KEYWORDS = (
    "PG_READ_FILE", "PG_READ_BINARY_FILE", "LOAD_FILE", "FILE", "URL", "S3",
)

_PHRASES = ("INTO OUTFILE", "INTO DUMPFILE")

_WORD_RE = re.compile(r"\b(" + "|".join(_WORD_KEYWORDS) + r")\b", re.IGNORECASE)
_FUNC_RE = re.compile(r"\b(" + "|".join(_FUNC_KEYWORDS) + r")\s*\(", re.IGNORECASE)
_PHRASE_RE = re.compile(
    r"\b(" + "|".join(p.replace(" ", r"\s+") for p in _PHRASES) + r")\b", re.IGNORECASE,
)
_LEADING_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _mask(sql: str) -> str:
    """``sql`` with every comment and string/identifier literal blanked out,
    same length, so a position in the mask is still the same position in the
    original. The keyword scan below runs over this, so a comment or a quoted
    value that happens to contain a forbidden word never trips the guard, and
    a semicolon inside a string literal is never mistaken for a second
    statement."""
    out = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        if c in ("'", '"', "`"):
            quote = c
            j = i + 1
            while j < n:
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            else:
                j = n
            out.append(" " * (j - i))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_read_only(sql: str, kind: Optional[str] = None) -> None:
    """Raise :class:`DatabaseError` unless ``sql`` is a single read-only statement.

    ``kind`` is accepted for callers that have it on hand but is not used to
    relax the check: the forbidden list already names every engine's
    dangerous functions together (pg_read_file, LOAD_FILE, file()/url()/s3()),
    so a statement is rejected the same way regardless of which engine it was
    meant for.
    """
    raw = sql or ""
    if not raw.strip():
        raise DatabaseError("empty statement")

    masked = _mask(raw)
    semicolons = [i for i, ch in enumerate(masked) if ch == ";"]
    if len(semicolons) > 1:
        raise DatabaseError("only one statement is allowed")
    if len(semicolons) == 1 and masked[semicolons[0] + 1:].strip():
        raise DatabaseError("only one statement is allowed")

    lead = masked.lstrip()
    match = _LEADING_WORD_RE.match(lead)
    first_word = match.group(0).upper() if match else ""
    if first_word not in _ALLOWED_START:
        shown = first_word or raw.strip()[:20]
        raise DatabaseError(
            "only read statements are allowed (SELECT, WITH, SHOW, DESCRIBE, "
            f"DESC, EXPLAIN, VALUES); this one starts with {shown!r}"
        )

    word_hit = _WORD_RE.search(masked)
    if word_hit:
        raise DatabaseError(f"statement contains a disallowed keyword: {word_hit.group(1).upper()}")
    func_hit = _FUNC_RE.search(masked)
    if func_hit:
        raise DatabaseError(f"statement calls a disallowed function: {func_hit.group(1).upper()}()")
    phrase_hit = _PHRASE_RE.search(masked)
    if phrase_hit:
        raise DatabaseError("statement contains a disallowed clause: INTO OUTFILE/DUMPFILE")


def clean_statement(sql: str) -> str:
    """``sql`` with one trailing semicolon removed, once :func:`check_read_only`
    has already confirmed that is the only one present."""
    s = (sql or "").strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    return s


__all__ = ["DatabaseError", "check_read_only", "clean_statement"]
