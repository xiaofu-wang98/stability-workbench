"""Source archive payloads; source text and proposed interpretations stay separate."""
from typing import Literal

from pydantic import Field

from .models import ImportContext, Location, Model


class SourceProjection(Model):
    evidence: dict[str, Location]
    reason: str = Field(min_length=1)


class SourceReference(Model):
    id: str
    target: str
    external: bool
    part: str
    sha256: str | None


class SourceCell(Model):
    coord: str
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    grid_column: int = Field(ge=1)
    text: str
    colspan: int = Field(ge=1)
    rowspan: int = Field(ge=1)
    continuation: bool
    anchor: str | None
    xml_sha256: str
    references: list[SourceReference]
    formula: str
    has_image: bool
    is_blank: bool


class SourceTable(Model):
    table_id: str
    table_index: int
    part: str
    section: str
    parent_table_id: str | None
    caption: str
    rows: list[list[SourceCell]]
    headers: list = Field(max_length=0)
    footnotes: list[str]
    xml_sha256: str


class SourcePackage(Model):
    format_version: Literal[1] = 1
    source_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    extractor_version: str = Field(min_length=1)
    tables: list[SourceTable]
    categories: dict[str, Literal['results', 'standards', 'statistics', 'notes', 'mixed', 'unclassified']] = Field(default_factory=dict)
    projections: list[SourceProjection] = Field(default_factory=list)


class SourcePreviewRequest(Model):
    idempotency_key: str = Field(min_length=1, max_length=200)
    base_revision: int = Field(ge=0)
    context: ImportContext = Field(default_factory=ImportContext)


class ArchiveSummary(Model):
    total_tables: int
    total_cells: int
    proposed_results: int
    source_coverage: Literal['complete']


class ArchiveCell(SourceCell):
    mapped: bool = False
    unmapped_reason: str = ''
    candidate_ids: list[str] = Field(default_factory=list)


class ArchiveTable(SourceTable):
    rows: list[list[ArchiveCell]]
    category: str
    category_label: str


class ArchiveInfo(Model):
    import_id: str
    project: str
    source_filename: str
    source_hash: str
    created_at: str
    state: Literal['archived']
    summary: ArchiveSummary


class ArchiveView(ArchiveInfo):
    tables: list[ArchiveTable]


class ArchiveTablePage(ArchiveTable):
    total: int
    offset: int
    limit: int
