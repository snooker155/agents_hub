"""apply_pending fills a gap in the ledger: a database that took a newer
migration before an older one existed (two branches landing out of numeric
order, a development server applying files as they are written) still gets
the older one on its next opening."""
from common import db
from common.migrations import apply_pending, applied_versions


def test_a_version_missing_below_the_maximum_is_applied():
    conn = db.get_conn()
    dialect = db.dialect()
    with db.transaction():
        conn.execute("DELETE FROM schema_migrations WHERE version = 21")
        conn.execute("DROP TABLE IF EXISTS workspace_file_uses")
        conn.execute("DROP TABLE IF EXISTS workspace_files")
    assert 21 not in applied_versions(conn, dialect)
    assert max(applied_versions(conn, dialect)) > 21

    with db.transaction():
        done = apply_pending(conn, dialect)
    assert done == [21]
    assert 21 in applied_versions(conn, dialect)
    conn.execute("SELECT COUNT(*) FROM workspace_files").fetchone()


def test_nothing_to_do_on_a_complete_ledger():
    conn = db.get_conn()
    with db.transaction():
        assert apply_pending(conn, db.dialect()) == []
