import sys
from pathlib import Path
from paddleocr import PPStructureV3


def main(pdf_path: str):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Could not find: {pdf_path}")

    output_dir = Path("structurev3_output")
    output_dir.mkdir(exist_ok=True)

    print(f"Loading PP-StructureV3 pipeline (first run downloads model weights)...")
    pipeline = PPStructureV3(
    enable_mkldnn=False,              # CPU bug workaround
    use_doc_orientation_classify=False,  # skip page-rotation detection
    use_doc_unwarping=False,             # skip dewarping (only needed for curved/photographed pages)
    use_textline_orientation=False,      # skip per-line rotation detection
    use_formula_recognition=False,       # no math formulas in lab reports
    use_seal_recognition=False,          # no stamps/seals to detect
    use_chart_recognition=False,         # no charts/graphs to parse
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    cpu_threads=8
    )

    print(f"Running on: {pdf_path}")
    # predict() handles PDFs natively (splits into pages internally)
    results = pipeline.predict(str(pdf_path))

    for i, page_result in enumerate(results):
        # Save structured JSON (this is what you'd feed into your NER/normalization step)
        json_path = output_dir / f"page_{i}.json"
        page_result.save_to_json(save_path=str(json_path))

        # Save Markdown (good for quickly eyeballing whether tables came out clean)
        md_path = output_dir / f"page_{i}.md"
        page_result.save_to_markdown(save_path=str(md_path))

        # Optional: save the visualized layout (boxes drawn over the page image)
        # useful for debugging table/layout detection quality
        img_dir = output_dir / f"page_{i}_visualization"
        page_result.save_to_img(save_path=str(img_dir))

        print(f"Page {i}: saved JSON -> {json_path}, Markdown -> {md_path}")

    print(f"\nDone. Check the '{output_dir}' folder:")
    print("  - open the .md files first, fastest way to see if tables parsed correctly")
    print("  - the .json files are the structured data you'd pipe into your NER/normalization step")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_structurev3.py /path/to/report.pdf")
        sys.exit(1)
    main(sys.argv[1])