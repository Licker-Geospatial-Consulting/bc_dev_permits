# Reference: local Ollama field extraction (PDFs only)

Runs entirely on your machine — no data leaves it. One JSON contract
(`references/extraction_prompt.md`) is reused for PDF text input.
The implementation is `bc_dev_permits/modeling/predict.py`.

Scope: Ollama is used only for PDFs, on the fallback path. HTML pages and map 
attributes are parsed deterministically by `bc_dev_permits/features.py` — never sent to a model. 
Reach for this reference only when a row was flagged `needs_pdf_extraction=true`.

## When it runs

- **PDF path** — a page had no usable prose, so only PDF links were stored
  (`needs_pdf_extraction=true`). Download the PDF, then call `extract_from_pdf(path)`.
- `extract_from_pdf` internally calls `extract_from_text` on the PDF-derived text. 
   That helper is for PDF text only — do not call it on HTML page prose 
   (use `bc_dev_permits/features.py` for that).

## How it works

1. `pdfplumber` extracts text page by page.
2. Pages with real text go to a **local text model** (default `qwen2.5:7b-instruct`).
3. Pages that are image-only (scanned) are rasterized with PyMuPDF and sent to a
   **local vision model** (`llama3.2-vision`) for OCR-style reading.
4. Ollama's `format=<json schema>` constrains the model to emit valid JSON matching the
   schema — no brittle regex on model output.

## Setup

```bash
# install Ollama from ollama.com, then:
ollama pull qwen2.5:7b-instruct     # text extraction (swap for llama3.1:8b if preferred)
ollama pull llama3.2-vision         # only needed for scanned PDFs
pip install requests pdfplumber pymupdf
```

Ollama serves at `http://localhost:11434` by default; the script points there. Change
`TEXT_MODEL` / `VISION_MODEL` at the top of the script to taste.

## Usage

```bash
# from a PDF on disk
python -m bc_dev_permits.modeling.predict path/to/DP-24-031.pdf

# from prose piped in (e.g. a page paragraph)
echo "…6-storey purpose built rental… 40 residential units…" | python -m bc_dev_permits.modeling.predict -
```

Or import it in the pipeline:

```python
from bc_dev_permits.modeling.predict import extract_from_text, extract_from_pdf

fields = extract_from_text(page_prose)          # prose path
fields = extract_from_pdf("staff_report.pdf")   # PDF path (auto text/vision)
```

## Wiring the result into the DB

- Copy the returned fields onto the `dev_permit` row.
- Set `extraction_method='ollama_text'` or `'ollama_pdf'`, and
  `extraction_confidence = fields['confidence']`.
- If `confidence < 0.6` → `needs_review = true`.
- Keep the source text in `raw_text` so you can re-run extraction later with a stronger
  model without re-downloading anything.
- Mark the `dev_document` row `extracted = true`.

## Cost / performance notes

- A 7–8B text model handles these permit descriptions well and runs fast on a modern
  laptop/GPU. Reserve the vision model for genuinely scanned PDFs — it's slower.
- `temperature=0` keeps extraction deterministic and repeatable for the same input.
- Batch: process the `needs_pdf_extraction=true` / `extracted=false` queue in the
  background so harvesting never blocks on the LLM.
