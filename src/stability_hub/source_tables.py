"""Immutable full-table archives and a narrow adapter into existing result workflow."""
import hashlib
import json
from pathlib import Path

from .models import RawObservation, digest
from .parsing import ParsedDocument, HEADER_ALIASES
from .permissions import Capability, require
from .source_docx import archive_tables
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, uid




class SourceTables:
    def __init__(self, workflow):
        self.workflow, self.store = workflow, workflow.store

    def _row(self, conn, import_id):
        row = conn.execute('SELECT * FROM source_imports WHERE id=?', (import_id,)).fetchone()
        if not row:
            raise WorkflowError('not_found', '来源归档不存在', 404)
        return row

    def ingest(self, project, filename, data, package, key, *, actor):
        require(actor, Capability.PREVIEW)
        if not key.strip():
            raise ValueError('需要幂等键')
        filename = Path(filename.replace('\\', '/')).name
        if Path(filename).suffix.lower() != '.docx':
            raise ValueError('来源归档仅支持DOCX')
        sha = hashlib.sha256(data).hexdigest()
        if package.source_hash != sha:
            raise ValueError('原件哈希与转换包不一致')
        tables = archive_tables(data)
        if tables != [t.model_dump() for t in package.tables]:
            raise ValueError('转换包不完整或来源内容/坐标不符，请按已发布格式重新提取')
        index = {t['section']: t for t in tables}
        if set(package.categories) - {t['table_id'] for t in tables}:
            raise ValueError('分类引用未知表')
        for projection in package.projections:
            if set(projection.evidence) - set(HEADER_ALIASES):
                raise ValueError('未知检测字段')
            if 'result' not in projection.evidence:
                raise ValueError('检测候选缺少结果来源')
            for loc in projection.evidence.values():
                self.cell(index, loc)
        request_hash = digest({'package': package.model_dump(), 'filename': filename})
        frozen = self.store.home / 'sources' / (sha + '.docx')
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            project_row(conn, project)
            prior = conn.execute('SELECT * FROM source_imports WHERE project=? AND created_by=? AND client_key=?',
                                 (project, actor.id, key)).fetchone()
            if prior:
                if prior['request_hash'] != request_hash:
                    raise WorkflowError('idempotency_conflict', '幂等键已用于其他归档', 409)
                return self._view(prior)
            if frozen.exists():
                if hashlib.sha256(frozen.read_bytes()).hexdigest() != sha:
                    raise WorkflowError('source_changed', '原件保存副本哈希异常', 409)
            else:
                frozen.write_bytes(data)
            conn.execute('INSERT OR IGNORE INTO documents VALUES (?,?,?)', (sha, filename, str(frozen)))
            import_id = uid('archive')
            conn.execute('INSERT INTO source_imports(id,project,document_hash,filename,package_json,request_hash,created_by,client_key) VALUES (?,?,?,?,?,?,?,?)',
                         (import_id, project, sha, filename, package.model_dump_json(), request_hash, actor.id, key))
            event(conn, project, import_id, 'archive_source', actor, '全表来源归档；未确认检测事实', {'source_hash': sha, 'tables': len(tables)})
            bump(conn, project)
            return self._view(self._row(conn, import_id))

    @staticmethod
    def cell(index, loc):
        try:
            if loc.format != 'docx':
                raise ValueError('来源必须是DOCX')
            cell = index[loc.section]['rows'][loc.row-1][loc.column-1]
            if cell['continuation']:
                raise ValueError('请使用合并起始格坐标')
            return cell
        except (KeyError, IndexError) as exc:
            raise ValueError('来源格坐标不存在') from exc

    def _view(self, row, include_tables=True):
        package = json.loads(row['package_json'])
        tables = package['tables']
        for table in tables:
            table.pop('xml', None)
            for cells in table['rows']:
                for cell in cells:
                    cell.pop('xml', None)
                    cell['unmapped_reason'] = '未建立检测映射'
            table['category'] = package['categories'].get(table['table_id'], 'unclassified')
            table['category_label'] = table['category']
        result = {'import_id': row['id'], 'project': row['project'], 'source_filename': row['filename'],
                  'source_hash': row['document_hash'], 'created_at': row['created_at'], 'state': 'archived',
                  'summary': {'total_tables': len(tables), 'total_cells': sum(len(r) for t in tables for r in t['rows']),
                              'proposed_results': len(package['projections']), 'source_coverage': 'complete'}}
        if include_tables:
            result['tables'] = tables
        return result

    def list(self, project, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            project_row(conn, project)
            return [self._view(r, False) for r in conn.execute('SELECT * FROM source_imports WHERE project=? ORDER BY rowid DESC', (project,))]

    def get(self, import_id, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            view = self._view(self._row(conn, import_id))
            cells = {(t['section'], c['row'], c['column']): c for t in view['tables'] for r in t['rows'] for c in r}
            for candidate in conn.execute("""SELECT c.id,c.raw_json,c.lifecycle FROM workflow_candidates c
                    JOIN workflow_uploads u ON u.id=c.upload_id WHERE u.project=? AND u.source_hash=?""",
                    (view['project'], view['source_hash'])):
                if candidate['lifecycle'] == 'withdrawn':
                    continue
                raw = json.loads(candidate['raw_json'])
                for loc in raw.get('evidence', {}).values():
                    cell = cells.get((loc['section'], loc['row'], loc['column']))
                    if cell is not None:
                        cell['mapped'] = True
                        cell.setdefault('candidate_ids', [])
                        if candidate['id'] not in cell['candidate_ids']:
                            cell['candidate_ids'].append(candidate['id'])
            return view

    def table(self, import_id, table_id, offset, limit, *, actor):
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError('分页范围无效')
        source = self.get(import_id, actor=actor)
        table = next((t for t in source['tables'] if t['table_id'] == table_id), None)
        if table is None:
            raise WorkflowError('not_found', '表不存在', 404)
        return {**table, 'total': len(table['rows']), 'offset': offset, 'limit': limit,
                'rows': table['rows'][offset:offset+limit]}

    def original(self, import_id, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            row = self._row(conn, import_id)
            path = self.store.home / 'sources' / (row['document_hash'] + '.docx')
            if hashlib.sha256(path.read_bytes()).hexdigest() != row['document_hash']:
                raise WorkflowError('source_changed', '原件哈希异常', 409)
            return path, row['filename']

    def asset(self, import_id, part, *, actor):
        require(actor, Capability.VIEW)
        from zipfile import ZipFile
        with self.store.connect() as conn:
            row = self._row(conn, import_id)
            package = json.loads(row['package_json'])
            allowed = {ref['part']: ref['sha256'] for t in package['tables'] for r in t['rows']
                       for cell in r for ref in cell['references'] if not ref['external'] and ref['sha256']}
            if part not in allowed:
                raise WorkflowError('not_found', '附件不存在', 404)
            path = self.store.home / 'sources' / (row['document_hash'] + '.docx')
            if hashlib.sha256(path.read_bytes()).hexdigest() != row['document_hash']:
                raise WorkflowError('source_changed', '原件哈希异常', 409)
            with ZipFile(path) as book:
                content = book.read(part)
            if hashlib.sha256(content).hexdigest() != allowed[part]:
                raise WorkflowError('source_changed', '附件哈希异常', 409)
            return content

    def preview(self, import_id, request, *, actor):
        require(actor, Capability.PREVIEW)
        with self.store.connect() as conn:
            row = dict(self._row(conn, import_id))
            # A retry of an existing preview may return its current revision.
            client_key = 'archive:' + import_id + ':' + request.idempotency_key
            prior = conn.execute('SELECT id FROM workflow_uploads WHERE project=? AND uploaded_by=? AND client_key=?',
                                 (row['project'], actor.id, client_key)).fetchone()
            if not prior:
                check_revision(conn, row['project'], request.base_revision)
        package = json.loads(row['package_json'])
        from .source_contracts import SourcePackage
        parsed_package = SourcePackage.model_validate(package)
        index = {t['section']: t for t in package['tables']}
        observations = []
        for p in parsed_package.projections:
            fields = {name: self.cell(index, loc)['text'] for name, loc in p.evidence.items()}
            observations.append(RawObservation(fields=fields, location=p.evidence['result'],
                                               evidence=p.evidence,
                                               hazards=('源格含公式或附件，须核对',) if any(self.cell(index, loc)['formula'] or self.cell(index, loc)['has_image'] for loc in p.evidence.values()) else ()))
        if not observations:
            raise ValueError('转换包尚无检测投影；来源已保存，需先准备有依据的检测映射包')
        data = (self.store.home / 'sources' / (row['document_hash'] + '.docx')).read_bytes()
        if hashlib.sha256(data).hexdigest() != row['document_hash']:
            raise WorkflowError('source_changed', '原件哈希异常', 409)
        return self.workflow.upload(row['project'], row['filename'], data, request.context, client_key,
                                    actor=actor, _parsed=ParsedDocument({}, observations))
