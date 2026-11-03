# Verification — 3 November 2026

## Scope and evidence

Local prototype only. No public service, GitHub publication or fixture upload. AEO Articles and its VM/resources are unchanged. The original test passed before edits. `.quality/before.json` records original malformed-input crashes, NAT64 acceptance and the separate-lookup downloader. `.quality/rotation-before.log` records the failing rotation regression before its fix. Not every defect has a separately saved pre-fix failing test; that part of T2's evidence is incomplete and must not be described as a full red/green history.

Python 3.14 on this Mac and Python 3.12 in Docker are checked separately. Evidence files under ignored `.quality/` are local, not GitHub CI or production acceptance. See `WORK_LOG.md` for the final measured results.

The reference Hilton file currently has **20 pages**, not 21. Conversion matches the actual source. The original plan is retained unchanged. For rotated pages, `get_text()` returns unrotated coordinates: the correct check transforms the line centre with `page.rotation_matrix` and checks the PowerPoint shape's centre and rotation. Comparing its unrotated top-left directly to the visible page would incorrectly accept the original bug.

## Full acceptance contract

The following table preserves the supplied plan. Rows requiring public fixture URLs remain pending until the owner approves publishing them. Row 5's owner editability check is also pending. A local in-process test does not prove a public connector works.

| # | Test | Expected |
|---|---|---|
| 1 | `GET BASE/health` | `{"ok":true}` |
| 2 | `POST BASE/mcp` with no token | 401 and `WWW-Authenticate: Bearer` |
| 3 | `tools/list` with the token and the headers of 3.3 | exactly `convert_pdf`, `job_status`, `get_pptx_file` |
| 4 | `convert_pdf` on fixture `normal.pdf` (3 pages, text, a photo, a chart) | `state` done (or `running`, then `done` via `job_status`); a `pptx_url`; `report.text_boxes` > 0 and equals `len(convert._lines(page)[0])` summed over the pages (horizontal lines with non-blank text) |
| 5 | Download `pptx_url` with no header | HTTP 200; starts with `PK`; reloads with python-pptx with the right slide count; and the owner can open it in PowerPoint, Keynote or Google Slides and click into the text (the owner does this one) |
| 6 | `get_pptx_file` | base64 decodes to the same bytes; `sha256` equals the reply's |
| 7 | `convert_pdf` with `http://example.com/a.pdf`, `https://127.0.0.1/x`, `https://169.254.169.254/`, `https://10.0.0.1/`, `https://localhost/`, `https://[64:ff9b::a9fe:a9fe]/` | each returns a job with `state: "failed"` and an `error` sentence that says the address or link is not allowed ("Only https links are allowed" or "private or internal address"); (nothing reaching the target is proven by the unit tests of T3(f) with fake resolver and transport, not by this live row) |
| 8 | A link to fixture `page.html` | failed: "did not give a PDF file" |
| 9 | A link to fixture `big.pdf` (over 50 MB; `make_fixtures.py` writes a valid PDF padded with a comment) | failed: "bigger than 50 MB" |
| 10 | Fixture `locked.pdf` (password protected) | failed: "password protected" |
| 11 | Fixture `scan_hidden.pdf` (page image plus hidden text layer) | `state` done; the page is in `report.picture_only_pages`; warning contains "scanned". Fixture `scan_plain.pdf` (page image only): done; picture only; warning contains "no editable text found" |
| 12 | `job_status` on an unknown id | tool error "No such job" |
| 13 | A signed link with one changed character; a link past its `exp` (make one with the test server's own `LINK_KEY`, or do it in the unit test); a non-ASCII `sig`; `exp=²`; no signature and no token | all 401 (never 500). The same file with a changed signature **and** a valid Bearer: 200 |
| 14 | The Hilton deck (section 6) through the live service, from a public https copy of the PDF (T1 did the same by calling `convert.convert` directly) | 21 slides; the pass rule of T1 holds; record the number of text boxes, the conversion time and the file size (no fixed threshold) |
| 15 | `POST /convert` with a token | 202 and a `job_id`; without a token 401. The 429 case is a unit test (`MAX_ACTIVE_JOBS=1`, a fake slow conversion); live, start `slow.pdf` twice quickly with `MAX_ACTIVE_JOBS=1` and the second is 429 |
| 16 | `GET /jobs/<unknown>` | 404 |
| 17 | `huge_page.pdf` and `many_pages.pdf` | each fails with its exact sentence from 3.4 ("A page is 14400 x 14400 points; the limit is 4000." and "The PDF has N pages; the limit is 200."); the service stays healthy |
| 18 | Two conversions started at the same moment | both finish |
| 21 | `rotated.pdf` | done; the slide is upright (the first text box's top-left is within 3 pt of the original line's top-left as `page.get_text("dict")` reports it on the rotated page) **and** no text is left in the background picture (redact as in `_picture`, then `get_text()` is empty; redaction rectangles on rotated pages may need converting with the page's rotation matrix, so check this) |
| 19 | Start a job on `slow.pdf`, and restart the container (`docker restart`) as soon as `job_status` says `running` | that job reads `failed` with the "Service restarted" sentence; a job finished before the restart still downloads |
| 20 | `docker logs` after the above | contains no token and no signed link (`sig=`); the empty-`PUBLIC_URL` warning appears when it is empty |

## Current interpretation

- Local HTTP container smoke: rows 1, 2, 3, 12, REST 202/401 in 15, 16 and log inspection in 20.
- Offline unit/integration tests cover the downloader refusals, conversion/report, signed-link download, inline checksum/size, malformed signatures, 429/503, concurrent jobs, cleanup, recovery, limits, child timeout and HTTP JSON-RPC.
- Hilton direct conversion/script/layout check is local T1, **not** row 14's public-link service run.
- Public URL rows, real partner MCP acceptance, live restart during a real fetch and the owner's click-to-edit check remain unverified.
- Hosting/licence, GitHub name/location, caller tokens and approval of defaults remain owner decisions. No cloud deployment or public repository is implied by passing tests.
