import sqlite3

import pytest

from stability_hub import migrations
from stability_hub.storage import Store


def legacy_database(home):
    home.mkdir()
    database = home / "history.sqlite"
    with sqlite3.connect(database) as conn:
        for statement in migrations.MIGRATIONS[1]:
            conn.execute(statement)
        conn.execute("PRAGMA user_version=1")
        conn.execute("INSERT INTO documents VALUES ('hash', 'original.xlsx', '/original/path')")
    return database


def test_upgrade_preserves_existing_rows_and_backup(tmp_path):
    home = tmp_path / "old"
    legacy_database(home)
    store = Store(home)
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.CURRENT_VERSION
        assert conn.execute("SELECT filename FROM documents").fetchone()[0] == "original.xlsx"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = list((home / "migration-backups").glob("*.sqlite"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT filename FROM documents").fetchone()[0] == "original.xlsx"
    Store(home)
    assert list((home / "migration-backups").glob("*.sqlite")) == backups


def test_failed_migration_rolls_back_all_changes(tmp_path, monkeypatch):
    home = tmp_path / "old"
    database = legacy_database(home)
    monkeypatch.setitem(migrations.MIGRATIONS, 2, (
        "CREATE TABLE must_rollback (id INTEGER)", "INVALID SQL",
    ))
    with pytest.raises(sqlite3.OperationalError):
        Store(home)
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT filename FROM documents").fetchone()[0] == "original.xlsx"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchall() == []


def test_future_version_is_never_downgraded(tmp_path):
    home = tmp_path / "future"
    database = legacy_database(home)
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        Store(home)
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99


def test_fresh_database_has_current_version_and_constraints(tmp_path):
    store = Store(tmp_path / "new")
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.CURRENT_VERSION
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO effective VALUES ('slot', 'missing-observation')")
