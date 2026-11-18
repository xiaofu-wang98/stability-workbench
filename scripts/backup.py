"""Create a self-contained, verified copy; never modify or overwrite the source home."""
import argparse
import hashlib
import json
import re
import shutil
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
                locators = {'documents': 'stored_path', 'workflow_exports': 'path'}
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")') if r[1] != locators.get(name)]
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
        deliveries = []
        if 'workflow_exports' in before:
            for entry in conn.execute('SELECT id,path,sha256 FROM workflow_exports').fetchall():
                old = Path(entry['path']).resolve()
                if not re.fullmatch(r'[a-zA-Z0-9_-]+', entry['id']):
                    raise ValueError('Invalid export identifier')
                if hashlib.sha256(old.read_bytes()).hexdigest() != entry['sha256']:
                    raise ValueError(f'Frozen export hash mismatch: {old}')
                folder = target / 'workflow-exports' / entry['id']
                folder.mkdir(parents=True)
                new = folder / old.name
                shutil.copyfile(old, new)
                if hashlib.sha256(new.read_bytes()).hexdigest() != entry['sha256']:
                    raise ValueError(f'Copied export hash mismatch: {new}')
                for name in ('snapshot.json', 'trend.json', 'trend.png'):
                    companion = old.parent / name
                    if companion.is_file() and companion != old:
                        shutil.copyfile(companion, folder / name)
                        if (folder / name).read_bytes() != companion.read_bytes():
                            raise ValueError(f'Copied export companion mismatch: {name}')
                conn.execute('UPDATE workflow_exports SET path=? WHERE id=?', (str(new), entry['id']))
                deliveries.append({'id': entry['id'], 'sha256': entry['sha256']})
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        after = {name: conn.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0] for name in before}
        assert before == after
        # Only storage locators may differ. All business tables and document identities must match.
        assert original_business == business_snapshot()
    scans = []
    scan_root = source / 'pdf-ocr'
    for folder in scan_root.iterdir() if scan_root.is_dir() else ():
        pdf = folder / 'source.pdf'
        if not pdf.is_file():
            continue
        raw = pdf.read_bytes()
        if hashlib.sha256(raw).hexdigest() != folder.name:
            raise ValueError(f'Frozen PDF hash mismatch: {pdf}')
        copy = target / 'pdf-ocr' / folder.name
        copy.mkdir(parents=True)
        (copy / 'source.pdf').write_bytes(raw)
        shutil.copyfile(folder / 'name.json', copy / 'name.json')
        state_file = folder / 'state'
        state = state_file.read_text(encoding='ascii') if state_file.is_file() else 'archived'
        pages = folder / 'pages.jsonl'
        if pages.is_file():
            complete_pages = [line for line in pages.read_bytes().splitlines(keepends=True) if line.endswith(b'\n')]
            if complete_pages:
                (copy / 'pages.jsonl').write_bytes(b''.join(complete_pages))
            for review in folder.glob('review-*.json'):
                if re.fullmatch(r'review-\d+\.json', review.name):
                    shutil.copyfile(review, copy / review.name)
        (copy / 'state').write_text('interrupted' if state == 'running' else state, encoding='ascii')
        scans.append(folder.name)
    report = {'source': str(source), 'target': str(target), 'tables': before, 'sources': log,
              'deliveries': deliveries, 'scan_hashes': scans,
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
