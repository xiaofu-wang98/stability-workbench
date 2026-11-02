"""Current-sheet business notes, independent of display or adoption decisions."""

import re

from .models import digest
from .source_notes import review_text, informational_reference

BUSINESS = re.compile(
    r"\b(?:AR|AE)[-（(]|\bOOS\b|\bOOE\b|暂停|终止|停检|偏差|复测|更正|不汇报|未汇报|异常|未通过|报错|调查|改为", re.I
)
HELP = re.compile(r"^\s*(?:填表说明|填写说明|操作说明|软件帮助|系统提示|修改日志)[:：]")


def collect(sheets):
    groups = {}
    for sheet in sheets:
        for cell in [*sheet["cells"],*sheet.get("notes",[])]:
            for original in [cell["text"], cell.get("comment", "")]:
                text = review_text(original)
                if not text or not BUSINESS.search(text) or HELP.search(text):
                    continue
                key = sheet["name"], text
                item = groups.setdefault(
                    key,
                    {
                        "id": digest(key),
                        "section": sheet["name"],
                        "text": text,
                        "original": original,
                        "evidence": [],
                    },
                )
                location = {
                    "format": "xlsx",
                    "section": sheet["name"],
                    "row": cell["row"],
                    "column": cell["column"],
                }
                if location not in item["evidence"]:
                    item["evidence"].append(location)
    return list(groups.values())


def annotate(notes, preview, tasks):
    pending = {(e.section, e.row, e.column) for t in tasks if t.blockers for e in t.evidence}
    from collections import defaultdict
    origins = defaultdict(set)
    order = {c.id: index for index, c in enumerate(preview.candidates)}
    for c in preview.candidates:
        for e in [c.location, *c.evidence.values()]:
            origins[e.section, e.row, e.column].add(c.id)
    result = []
    for note in notes:
        positions = {(e["section"], e["row"], e["column"]) for e in note["evidence"]}
        ids = sorted({cid for position in positions for cid in origins[position]}, key=order.__getitem__)
        result.append(
            {
                **note,
                "members": ids,
                "scope": "本表AR提醒；不要求关联批次或时间点，不改变采用"
                if informational_reference(note["text"])
                else "明确来源引用范围"
                if ids
                else "需确认作用范围",
                "pending": bool(positions & pending),
                "identifiers": re.findall(r"(?:AR|AE)-[A-Za-z0-9-]+|OOS|OOE", note["text"], re.I),
            }
        )
    return sorted(
        result, key=lambda n: (not n["pending"], not n["identifiers"], n["section"], n["evidence"][0]["row"])
    )
