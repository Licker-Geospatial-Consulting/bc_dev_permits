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
3. Image-only PDFs (e.g. architectural plan sets whose project-data table is a drawing,
   not text) go to the **tiled vision path** (`extract_from_pdf_vision`, default model
   `minicpm-v`): each sheet is cut into overlapping **high-DPI tiles** with PyMuPDF, each
   tile is OCR'd on its own (one image per request), and the fields are merged across
   tiles. Sending a whole large-format sheet at once does NOT work — the table downscales
   to unreadable and the model hallucinates (we saw `units=100`, `floor_area=12345`);
   tiling keeps the cell text legible so it reads the real values. Split counts the model
   transcribes verbatim into `parking_notes` (e.g. `9+1 VISITOR`, `7 SHORT TERM : 18 LONG
   TERM`) are summed deterministically into the integer columns (10, 25).
4. Output is requested in Ollama's lightweight JSON mode (`format="json"`) with the field
   list given in the prompt, then `json.loads`d. NOTE: a full schema-grammar (`format=<json
   schema>`) was tried first but its constrained decoder **stalls on long prompts** - a
   ~4k-token letter hung past 600s on a warm GPU, while JSON mode finishes in ~9s and
   extracts more fields. So we do not pass the schema as a grammar.
5. `llama3.2-vision` was evaluated but its `mllama` architecture failed to load on this
   Ollama build; `minicpm-v` handles the high-resolution table OCR and is the default.

**Reliability note (tiled vision) — treat output as REVIEW-REQUIRED, not trusted.**
The image path uses a **sliding-window** sweep (`_sliding_clips`): overlapping windows
whose vertical step is smaller than the window height, so every table row appears WHOLE in
at least one window (a fixed grid could bisect a row like "PARKING STALLS 9+1 VISITOR" and
lose it). Fields merge across windows by **majority vote**, so a value several windows
agree on beats a lone misread, and split counts are summed deterministically.

This improves coverage (validation recovered floor area 1321 m2 and unit mix 9 = four
4-bed + five 3-bed), but does NOT eliminate the core limitation: a local 7B vision model
(minicpm-v) still **hallucinates** the occasional cell with high self-confidence — in one
run it fabricated a bicycle-parking figure that was not on the sheet and reported
`confidence: 1.0`. Because of this, `extract_from_pdf_vision` **caps its confidence at 0.4**
so every vision-derived row trips `needs_review` and is never trusted as final. For
reliable numbers, use a stronger model (a larger VLM or a cloud document-AI such as Azure
Document Intelligence / Textract) — the sliding-window plumbing is model-agnostic and would
carry a better model directly. The deterministic pieces (window geometry / no-bisection
guarantee, majority-vote merge, split-count summing) are unit-tested in
`tests/test_predict_vision.py`; the OCR itself needs a live model and is not.

## Setup

```bash
# install Ollama from ollama.com, then:
ollama pull qwen2.5:7b-instruct     # text extraction (swap for llama3.1:8b if preferred)
ollama pull minicpm-v               # vision OCR for image-only PDFs (tiled path)
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
