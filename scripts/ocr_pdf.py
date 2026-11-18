"""Run local PDF OCR in the dedicated Windows OCR environment."""
import argparse
import json
import os
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory


def recognize(source, first, last):
    import pypdfium2 as pdfium
    from paddleocr import PPStructureV3

    with closing(pdfium.PdfDocument(source)) as document:
        end = len(document) if last is None else last
        if not 1 <= first <= end <= len(document):
            raise ValueError(f"Page range must be between 1 and {len(document)}")
        pipeline = PPStructureV3(
            device="cpu",
            enable_mkldnn=False,
            cpu_threads=min(4, os.cpu_count() or 1),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            use_seal_recognition=False,
            use_formula_recognition=False,
            use_chart_recognition=False,
            use_region_detection=False,
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
        )
        with TemporaryDirectory(prefix="workbench-ocr-") as temp:
            image = Path(temp) / "page.png"
            for number in range(first, end + 1):
                page = document[number - 1]
                try:
                    bitmap = page.render(scale=200 / 72)
                    try:
                        rendered = bitmap.to_pil()
                        width, height = rendered.size
                        rendered.save(image)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                result = next(iter(pipeline.predict(str(image)))).json["res"]
                overall = result["overall_ocr_res"]
                yield {
                    "page": number,
                    "width": width,
                    "height": height,
                    "lines": [
                        {"text": text, "score": score, "polygon": polygon}
                        for text, score, polygon in zip(
                            overall["rec_texts"], overall["rec_scores"], overall["rec_polys"], strict=True
                        )
                    ],
                    "tables": [
                        {"html": table["pred_html"], "cell_boxes": table["cell_box_list"],
                         "layout_block": table.get("table_region_id")}
                        for table in result.get("table_res_list", [])
                    ],
                    "blocks": [
                        {"kind": block["block_label"], "box": block["block_bbox"],
                         "text": block["block_content"]}
                        for block in result.get("parsing_res_list", [])
                    ],
                }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path, help="UTF-8 JSONL output, one recognized page per line")
    parser.add_argument("--first", type=int, default=1)
    parser.add_argument("--last", type=int)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("source and output must be different files")
    with args.output.open("x", encoding="utf-8") as stream:
        for page in recognize(args.source, args.first, args.last):
            stream.write(json.dumps(page, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"Recognized page {page['page']}", flush=True)


if __name__ == "__main__":
    main()
