"""Create a self-contained, verified copy; never modify or overwrite the source home."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def backup(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise ValueError(f"Target already exists; refusing to overwrite: {target}")
    if not (source / 'history.sqlite').is_file():
        raise ValueError('Source history.sqlite is missing')
    target.mkdir(parents=True)
    with sqlite3.connect((source / 'history.sqlite').as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(target / 'history.sqlite') as dst:
            src.backup(dst)
    log = []
    with sqlite3.connect(target / 'history.sqlite') as conn:
        conn.row_factory = sqlite3.Row
        before = {r['name']: conn.execute('SELECT count(*) FROM "' + r['name'] + '"').fetchone()[0]
                  for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def business_snapshot():
            snapshot = {}
            for name in before:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")') if not (name == 'documents' and r[1] == 'stored_path')]
                selected = ','.join('"' + c + '"' for c in cols)
                snapshot[name] = sorted(repr(tuple(r)) for r in conn.execute(f'SELECT {selected} FROM "{name}"'))
            return snapshot
        original_business = business_snapshot()
        docs = list(conn.execute('SELECT * FROM documents'))
        (target / 'sources').mkdir()
        for doc in docs:
            old = Path(doc['stored_path'])
            data = old.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha != doc['sha256']:
                raise ValueError(f"Frozen source hash mismatch: {old}")
            new = target / 'sources' / old.name
            if new.exists() and new.read_bytes() != data:
                raise ValueError(f"Filename collision: {new}")
            new.write_bytes(data)
            conn.execute('UPDATE documents SET stored_path=? WHERE stored_path=?', (str(new), str(old)))
            log.append({'old': str(old), 'new': str(new), 'sha256': sha})
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        after = {name: conn.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0] for name in before}
        assert before == after
        # Only storage locators may differ. All business tables and document identities must match.
        assert original_business == business_snapshot()
    report = {'source': str(source), 'target': str(target), 'tables': before, 'sources': log,
              'business_rows_equal': True, 'integrity_check': 'ok'}
    (target / 'migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    result = backup(args.source, args.target)
    print(json.dumps({'target': result['target'], 'documents': len(result['sources']), 'verified': True}))
