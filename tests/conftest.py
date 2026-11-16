import pytest
from docx import Document
from openpyxl import Workbook

from stability_hub.models import ImportContext, ItemDefinition, ProjectConfig


@pytest.fixture
def config():
    return ProjectConfig(
        project="PROJECT-P",
        items=(
            ItemDefinition(
                id="protein", label="Protein content", aliases=("蛋白含量",), unit="mg/ml", method="UV"
            ),
            ItemDefinition(id="purity", label="Purity", unit="%", method="SEC-HPLC"),
            ItemDefinition(id="large_fragment", label="Large Fragment", unit="%", method="RP-HPLC"),
            ItemDefinition(id="n_terminal", label="N-terminal", unit="%", method="Peptide mapping"),
            ItemDefinition(id="appearance", label="Appearance", result_type="qualitative", method="Visual"),
        ),
    )


@pytest.fixture
def context():
    return ImportContext(
        defaults={
            "sample_type": "DP",
            "batch": "B001A1",
            "presentation": "pen-1mg",
            "study": "long_term",
            "study_id": "STUDY-P-01",
            "condition": "5C",
        }
    )


@pytest.fixture
def xlsx(tmp_path):
    def make(rows, name="input.xlsx", headers=None):
        book = Workbook()
        sheet = book.active
        sheet.title = "Results"
        sheet.append(headers or ["Test item", "Time point", "Result", "Unit"])
        for row in rows:
            sheet.append(row)
        path = tmp_path / name
        book.save(path)
        book.close()
        return path

    return make


@pytest.fixture
def coa(tmp_path):
    def make(name="coa.docx", batch="B001A1", result="2.10"):
        doc = Document()
        doc.add_heading("Synthetic COA for automated testing", 0)
        meta = doc.add_table(rows=0, cols=2)
        for k, v in [("Batch", batch), ("Sample type", "DP"), ("Presentation", "pen-1mg")]:
            cells = meta.add_row().cells
            cells[0].text, cells[1].text = k, v
        table = doc.add_table(rows=0, cols=4)
        for row in [("Test item", "Result", "Unit", "Method"), ("Protein content", result, "mg/ml", "UV")]:
            for cell, value in zip(table.add_row().cells, row, strict=True):
                cell.text = value
        path = tmp_path / name
        doc.save(path)
        return path

    return make
