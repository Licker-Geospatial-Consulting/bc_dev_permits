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
# install Ollama from ollama.com, then pull the models:
ollama pull qwen2.5:7b-instruct     # text extraction (letters / reports)
ollama pull qwen2.5vl:7b            # vision OCR for plan sets (default, ~6 GB VRAM)
ollama pull qwen2.5vl:32b           # vision, far better on dense plan tables (~24 GB VRAM)
pip install requests pymupdf        # or: pip install -e ".[pdf]"
```

Ollama serves at `http://localhost:11434` by default. Model choice comes from environment
variables read in `bc_dev_permits/config.py` (no code edits needed):

- `OLLAMA_TEXT_MODEL`   default `qwen2.5:7b-instruct`
- `OLLAMA_VISION_MODEL` default `qwen2.5vl:7b`
- `OLLAMA_NUM_CTX`      default `8192` - context window sent with every request
- `OLLAMA_TIMEOUT`      default `600` - seconds per request
- `OLLAMA_VISION_DPI`   default `220` - plan-sheet render resolution

The run prints these up front, e.g. `[models] text=… vision=… num_ctx=8192 dpi=220`.

### Context size (HTTP 400 "exceeds the available context size")

A rendered plan sheet tokenizes to ~4.5k image tokens. If Ollama's server default context is
small (some builds cap at 4096), the request is rejected with HTTP 400 and the extraction comes
back empty. We send `num_ctx` explicitly (default 8192) so this does not depend on the server
default. If you still see the 400 (very large sheets), raise it or lower the image size:

```powershell
$env:OLLAMA_NUM_CTX = "12288"      # bigger context (uses more VRAM/RAM)
$env:OLLAMA_VISION_DPI = "150"     # smaller image -> fewer tokens, less memory
```

### Enabling the 32B vision model

The vision model runs only on large-format plan sets (letters use the text model). To use the
stronger `qwen2.5vl:32b`, set the env var **in the same shell that runs the harvest** so the
Python process inherits it, then confirm the run logs `vision route via qwen2.5vl:32b`. A
different terminal will not see it.

PowerShell (this session only):
```powershell
$env:OLLAMA_VISION_MODEL = "qwen2.5vl:32b"
python -m bc_dev_permits.dataset --municipality victoria --pdf-enrich --out json --limit 20
```

Command Prompt / cmd (this session only):
```bat
set OLLAMA_VISION_MODEL=qwen2.5vl:32b
python -m bc_dev_permits.dataset --municipality victoria --pdf-enrich --out json --limit 20
```

Git Bash / Linux / macOS (this session only):
```bash
export OLLAMA_VISION_MODEL=qwen2.5vl:32b
python -m bc_dev_permits.dataset --municipality victoria --pdf-enrich --out json --limit 20
```

Persist it for all future terminals on Windows (takes effect in NEW shells, not the current one):
```powershell
setx OLLAMA_VISION_MODEL "qwen2.5vl:32b"
```

Verify it is set before running:
```powershell
echo $env:OLLAMA_VISION_MODEL     # PowerShell -> qwen2.5vl:32b
```
```bat
echo %OLLAMA_VISION_MODEL%        # cmd        -> qwen2.5vl:32b
```

Note: `qwen2.5vl:32b` needs roughly 24 GB of VRAM to stay on-GPU. On a smaller card
(e.g. 12 GB) it still runs but spills to CPU and is much slower (minutes per plan set).

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
