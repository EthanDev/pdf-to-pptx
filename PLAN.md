# Plan: "PDF to PowerPoint" MCP service

Written 3 Nov 2026, revised after two zero-context reviews. For Codex (an AI coding agent) or any engineer. Read it top to bottom once, then work through section 10. Every place to find something is a full path. Where the owner must decide, it is marked **DECISION** and listed in section 13: do not guess those.

---

## 0. Who and what is named in this plan

| Name | Meaning |
|---|---|
| **Owner** | Ethan Richardson, who owns this work. He wants short, plain wording in everything you write for him (README, messages, error texts) |
| **Codex** | the AI coding agent that will build and finish this service from this plan |
| **MCP** | Model Context Protocol: a standard way for an AI assistant to call tools on a server |
| **fde** | "FDE-in-a-Box" (also called Biscotte), an AI work platform the owner's company runs. It can install MCP servers and call their tools. It only talks to **public https** addresses, so this service must be public |
| **Peec audit server** | a different, already-live service the owner has, in `/Volumes/Main drive/Claude/Cashew/Peec Articles/AEO-articles`. It builds Tripadvisor AI-visibility reports. This new service copies its style but is **separate** from it |
| **Devika** | a colleague who owns a dashboard; she is only mentioned because her integration guide is a good model for the docs you write |

## 1. The goal, in plain words

A person sends a **link to a PDF** to a small web service. The service downloads the PDF and gives back an **editable PowerPoint (.pptx)** that looks like the PDF. "Editable" means the words are real text boxes you can click and retype, not a flat picture.

It must be usable two ways:
- as an **MCP server**, so Claude, fde and other AI agents can call a tool `convert_pdf`, and
- as a plain **web API**, so a script can call it.

It needs no AI and no paid keys. It is a free, fast, deterministic conversion (a few seconds per deck).

## 2. What already exists (a working prototype) and what is wrong with it

**Project folder:** `/Volumes/Main drive/Claude/Cashew/Peec Articles/pdf-to-pptx`

| Path (relative to the project folder) | What it is |
|---|---|
| `app/convert.py` | The converter: PDF bytes in, PPTX bytes plus a report out |
| `app/fetch.py` | The safe downloader for the link |
| `app/jobs.py` | Background jobs kept as files under `DATA_DIR/jobs/<id>/` (state in `job.json`, result `.pptx`) |
| `app/main.py` | FastAPI web API plus the FastMCP server (3 tools), bearer-token auth, signed download links |
| `tests/test_all.py` | One offline test file (no network except 127.0.0.1). Prints `OK: converter, safe downloader, web API and MCP tools` |
| `requirements.txt` | pymupdf, python-pptx, fastapi, uvicorn, mcp, httpx, pillow |
| `Dockerfile` | Python 3.12 slim image; runs uvicorn on port 8080 with `--no-access-log` |
| `.env.example`, `.gitignore` | Example settings; ignores `.venv/`, `data/`, `.env` |
| `.venv/` | A local Python **3.14** virtual environment (mcp 1.30.0, pymupdf 2.1, python-pptx 1.0.2, httpx 0.28.1, fastapi 0.162.0). Not committed |
| (git) | `git init` was done on branch `feat/initial-service`. **Nothing is committed.** No GitHub repository exists |

**How to confirm the prototype matches this plan:** from the project folder run `.venv/bin/python -W ignore tests/test_all.py`. It must print `OK: converter, safe downloader, web API and MCP tools`. The test asserts the exact tool list (`convert_pdf`, `get_pptx_file`, `job_status`), the 401 rules, the signed-link rules and the link refusals. If it does not print OK, the code and this plan have drifted: tell the owner before changing anything.

**What is proven:** that test passes. It covers text boxes with the right text, size, bold, colour and position; the text is removed from the background picture; hidden-text-layer and rotated-text fallbacks; pages of different sizes; bad, password-protected and too-long PDFs; 13 refused links; a redirect; a non-PDF; an oversized file; the web API with and without a token; signed links; and `convert_pdf` and `get_pptx_file` called in-process, and `job_status` for an unknown id only.

**What is NOT proven:** any real-world PDF (only tiny generated ones), the Docker image, Python 3.12 (the image's version; the tests have run only on 3.14), a real MCP client over HTTP, and everything in section 3.3.

### 2.1 Known defects in the prototype (the reviewers found these; all are to be fixed, ticket T2)

| # | Where | Defect | Fix |
|---|---|---|---|
| P1 | `app/fetch.py` (`_check_url`, `fetch_pdf`) | **Blind SSRF.** The address is checked, then httpx does its own separate lookup, and the connected address is checked only after the request was sent. A name that resolves to an internal address still receives the GET | Connect only to the address you vetted: resolve once, check all, then connect to that IP with a custom httpx transport that keeps the original host name for `Host` and TLS SNI (certificate name check). Keep the after-connect check as a second line |
| P2 | `app/fetch.py` | Several inputs raise instead of giving a plain `FetchError`: `u.port` on a bad port; `int(Content-Length)` on a non-number; `urlsplit` on `https://[abc/` (ValueError); `getaddrinfo` raising `UnicodeError` (not `gaierror`) for a host label over 63 characters | Catch all of them and raise `FetchError` with a plain sentence; add a test for each |
| P3 | `app/fetch.py` | The 60 s limit is checked only between chunks, not while connecting or waiting for headers over up to 4 hops | Pass one overall deadline to every hop (httpx timeouts computed from time left) |
| P4 | `app/fetch.py` | Possibly not blocked: IPv6 forms that embed an IPv4 address, namely NAT64 `64:ff9b::/96` and 6to4 `2002::/16` (whether `ipaddress.is_global` rejects them depends on the Python version) | Unwrap the embedded IPv4 yourself and test it; add tests for `64:ff9b::a9fe:a9fe` and `2002:a9fe:a9fe::` (both = 169.254.169.254) and run them on **both Python 3.12 and 3.14** |
| P5 | `app/main.py` (`file` route) | Two crashes that return HTTP 500: `hmac.compare_digest(sig, ...)` on a non-ASCII `sig` raises `TypeError`; and `exp.isdigit()` is true for characters such as `²` but `int(exp)` then raises | Require `exp` to be 1 to 12 ASCII digits and `sig` to be exactly 64 hex characters (`re.fullmatch`), compare as bytes, and return 401 for anything else. Test both |
| P6 | `app/jobs.py` (`_write`) | Not atomic: a reader can see a half-written `job.json`, `get` returns None, and `convert_pdf` then fails on `j["state"]` | Write to a temp file in the same folder and `os.replace` it. Also make `convert_pdf` handle `jobs.get` returning None |
| P7 | `app/convert.py`, `app/fetch.py`, `app/jobs.py` | The 200-page limit exists in code but is not a setting (`jobs.py` never passes `max_pages`); the 50 MB download cap is hardcoded in `fetch.py`; there is no limit on page size, pixels per page, output size or conversion time, so a hostile PDF can exhaust memory, CPU or disk | Add every limit of 3.4 as a setting and enforce it |
| P8 | `app/convert.py` | **Not a defect (kept so you do not re-fix it).** The code already gives two per-page messages: hidden text layer says "looks scanned (hidden text layer)", a page with no text says "no editable text found". `tests/test_all.py` already asserts the first | Nothing. Acceptance row 11 expects the right one per fixture |
| P9 | `app/convert.py` | The fonts warning appears only when at least one text box exists | Fine; document it as "when any text boxes exist" (done in 3.1) |
| P10 | `app/main.py` | `PUBLIC_URL` empty gives relative links that cannot be fetched from outside; nothing warns | Log one warning **at import time** of `main` (module level; T3(d) tests it by importing `main` in a subprocess). Add `import logging` (the project has no logging yet). Never log tokens or signed links |
| P11 | `requirements.txt` | `mcp>=1.2,<2` is wrong: `mcp.server.transport_security` needs a recent 1.x | Pin `mcp>=1.30,<2` (the audit server uses the same) |
| P12 | `app/jobs.py` | Cleanup only runs when a new job starts; no cap on total disk | Run cleanup at the start of every API request as well (a quick folder scan), and refuse new jobs when `DATA_DIR/jobs` exceeds `MAX_DATA_MB` (default 2000): HTTP 503 on the web API and a tool error on MCP, both with the sentence "The service is full. Try again later.". A per-caller rate limit is **not** part of the first version (backlog) |
| P13 | `Dockerfile` | Runs as root; `/data` ownership not handled | Add a non-root user (fixed uid 10001), create `/data` owned by it **before** the `VOLUME` line (a named volume copies the ownership only the first time it is created, so delete a `pptx-data` volume that an earlier root run made), add a `HEALTHCHECK` that calls `/health` with Python's `urllib` (the slim image has no curl), and copy the `tests/` folder into the image. In T4 use a **named** Docker volume (`-v pptx-data:/data`), because a bind-mounted folder keeps the host's ownership |
| P14 | `app/convert.py` | A rectangle redaction removes any character that touches a line's box. A neighbouring line (or rotated text that was meant to stay in the picture) can lose characters with no text box added for them | In T1, look for this on the Hilton deck; if it happens, shrink the redaction rectangle to the line's glyph area or redact by exact span rectangles |
| P15 | `tests/test_all.py` | Leftover lines near the end of the MCP test: an unused `bad = (await main.mcp.call_tool("convert_pdf", {"url": "https://127.0.0.1/x.pdf"}))[1]` that makes a real call, `jobs.ALLOW_PRIVATE` toggles around it, and `bad = asyncio.run(tools()) if False else None`. They test nothing | Remove them and replace with the proper refused-link test of T3(a) |
| P16 | `app/main.py` | `enable_dns_rebinding_protection=False` on the MCP transport | This is intentional (the audit server does the same, so it can be called by public name behind a proxy). Keep it and write one line in the README saying why |

## 3. How the whole process works

```
caller (Claude, fde, a script)
   │  MCP tool convert_pdf(url)          or   POST /convert {"url": ...}
   │  header  Authorization: Bearer <API_TOKEN>
   ▼
main.py   checks the token, creates a job (jobs.start), waits up to RUN_WAIT_S (25 s) for it
   ▼
jobs.py   background thread (WORKERS = 2):
   1. fetch.fetch_pdf(url)   download safely  ─► PDF bytes + file name
   2. convert.convert(pdf)   build the deck   ─► PPTX bytes + report
   3. write  DATA_DIR/jobs/<id>/<name>.pptx  and  job.json (state=done, report, size)
   ▼
main.py   returns {job_id, state, pptx_url (signed, 24 h), report}
          or, if still running after the wait, {job_id, state:"running"} and the caller polls job_status
caller    downloads pptx_url (no token needed), or calls get_pptx_file for the bytes as base64
```

**Important design fact:** jobs live as files on the local disk and run in threads inside one process. So the service must run as **exactly one instance with one uvicorn worker** (`--workers 1`, already in the Dockerfile) that stays on. See decision D1: a host that scales to zero or runs several instances would answer `job_status` with "No such job" from the wrong instance.

### 3.1 The conversion, per page (`app/convert.py`)

For each PDF page:
1. Read the page size in points (1 pt = 1/72 inch). The **first page sets the slide size** (12700 EMU per pt; EMU is PowerPoint's unit). A later page of another size is scaled to fit and centred.
2. If more than half of the page's text characters are drawn **invisibly** (`page.get_texttrace()` span `type == 3`: the hidden text layer a scanner's OCR adds), treat it as a scan: **picture only**, warning "looks scanned (hidden text layer)".
3. Otherwise read every text **line** (`page.get_text("dict")`). Lines that are not left-to-right horizontal are **left in the picture** and counted in a warning.
4. Remove those lines' text from the page with PyMuPDF redactions (`add_redact_annot(fill=False)`, then `apply_redactions` with images and line art left untouched), then render the page to a **PNG at 144 dpi**. This is the background picture: shapes, lines, charts, photos and logos stay in it.
5. Add the picture to a blank slide at full size (python-pptx's default template, `slide_layouts[6]`).
6. For each kept line add one **text box** at the line's rectangle (no margins, no wrapping, no auto-size, top anchored) with one **run per PDF span**: font family (`family()` strips the subset tag `ABCDEF+` and style words, so `ABCDEF+Poppins-SemiBold` becomes `Poppins`), size (rounded to 0.5 pt, scaled for page scaling), bold and italic (PDF font flags or words in the font name) and colour.
7. A page with no editable text becomes a picture-only slide and is listed in `report.picture_only_pages`, with warning "no editable text found; kept as a picture only" (or the scanned warning of step 2).

The report: `{pages, text_boxes, picture_only_pages, warnings, slide_size_pt, fonts}`. When any text box exists, a warning says fonts are not embedded and lists up to six of them (PowerPoint substitutes a font that is not installed where the deck is opened).

Page rotation and crop: PyMuPDF's `page.rect` already gives the visible, rotated page, so a PDF page stored with a /Rotate of 90 becomes a correctly oriented slide; a fixture for this is in section 11. Plain `ConvertError` sentences are raised for: unreadable file, password protected, zero pages, too many pages (default limit 200).

### 3.2 The safe download (`app/fetch.py`): what must be true after T2

The link comes from a stranger, so the service must not become a way to reach the owner's private network or the cloud metadata server (the "SSRF" risk).
- `https` only; port 443 only; no `user:password@`; link at most 2000 characters.
- The host name is resolved **once**; **every** address must be public (`ipaddress.is_global`, not multicast; IPv4-mapped and NAT64/6to4 forms unwrapped and tested). This rejects 127.x, 10.x, 172.16-31.x, 192.168.x, 169.254.x (cloud metadata server), 100.64.x, `::1`, `fe80::`, `localhost`.
- The connection goes **to the vetted IP only** (P1). After connecting, the address actually connected to is checked again; if it cannot be read, refuse.
- At most 3 redirects; each target is checked by every rule again. Proxy environment variables are ignored (`trust_env=False`).
- Size limit 50 MB (on `Content-Length` and while reading), one overall time limit of 60 s across connect, headers, redirects and body, and the first 1024 bytes must contain the text `%PDF-` (the PDF format allows a little junk before it).
- **Test seams.** `fetch_pdf(url, ..., resolver=None, transport=None, clock=None)`: `resolver(host, port)` returns a list of IP strings (default: `socket.getaddrinfo`), `transport` is an `httpx` transport used **instead of the real network only**: the IP pinning and every check of P1 sit above it and always run. A fake transport must put the connected address in its responses (`response.extensions["network_stream"].get_extra_info("server_addr")`), otherwise the after-connect rule refuses the request; tests supply it, and one test returns a private `server_addr` on purpose, `clock` returns seconds (default `time.monotonic`). T3(f) needs these.
- **DNS time:** `getaddrinfo` has no timeout of its own; run it in a worker thread and give up when the overall deadline is reached.
- **Several addresses:** try the vetted addresses in order, and allow public IPv6 literals. Keep the original host name for the `Host` header and, via httpx's `sni_hostname` request extension, for TLS so the certificate is checked against the real name.
- A parameter `allow_private` exists **for tests only** (it lets `http://127.0.0.1` through and skips the peer check). It must never be reachable from the web API, the MCP tools or an environment variable.

### 3.3 What a request looks like to a real MCP client

MCP over HTTP is JSON-RPC in a POST to `<BASE>/mcp` with three headers: `Authorization: Bearer <TOKEN>`, `Content-Type: application/json`, and **`Accept: application/json, text/event-stream`** (without that Accept header the server refuses). The server is stateless, so `initialize` is optional. Example:
```
curl -sS -X POST "$BASE/mcp" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"convert_pdf","arguments":{"url":"https://example.com/a.pdf"}}}'
```
The reply is `{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"<json>"}],"structuredContent":{...},"isError":false}}`. The data is `result.structuredContent` (or the JSON in `content[0].text`). A tool failure has `isError: true` and a text message. The unit tests call `main.mcp.call_tool` in-process; **add one HTTP-level test** with these headers (see T3).

### 3.4 Limits that protect the service (add in T2; defaults, all settings)

| Limit | Default | Setting |
|---|---|---|
| PDF download size | 50 MB | `MAX_PDF_MB` |
| Pages per PDF | 200 | `MAX_PAGES` |
| Page width or height | 4000 pt, applied to **every** page (PowerPoint refuses slides over 56 in = 4032 pt, and the first page sets the slide size) | `MAX_PAGE_PT` |
| Pixels in one rendered page | 20 million | `MAX_PAGE_PIXELS` (lower the dpi for a big page instead of failing; only if even 36 dpi is over the limit, fail) |
| Output .pptx size | 300 MB | `MAX_PPTX_MB` |
| Time for one conversion | 120 s | `CONVERT_TIMEOUT_S` (run the conversion in a subprocess so it can be killed; a thread cannot be) |
| Jobs queued or running | 20 | `MAX_ACTIVE_JOBS` |
| Total job disk | 2000 MB | `MAX_DATA_MB` |

**Units:** MB means 1,000,000 bytes everywhere. "Bigger than N MB" means strictly greater. The output size is only known after the deck is built, so the check runs after saving, and an oversize `.pptx` is deleted and the job fails.

**Failure sentences (use these words, so tests and callers can rely on them):** "The PDF has N pages; the limit is M." / "A page is W x H points; the limit is L." (W and H rounded up to whole points) / "The converted deck is bigger than N MB." / "The conversion took longer than N seconds and was stopped." / "The file is bigger than N MB." / "The service is full. Try again later."

**How the time limit works (the child-process contract).**
- `convert.convert(pdf_bytes, **limits)` stays a plain function (T1 and the tests call it directly). A new module `app/convert_worker.py` holds the child's entry function `run(job_dir: str, limits: dict)`.
- Each conversion runs in its own short-lived process started with `multiprocessing.get_context("spawn")`. Because `spawn` re-imports the starting program, **`tests/test_all.py` must have an `if __name__ == "__main__":` guard around its body** (put the tests in a `main()` function) or every child would re-run the whole test file.
- Limits reach the child **as the `limits` argument** (a plain dict of numbers), never through module variables (a changed module value does not cross `spawn`). `jobs.start(url, *, limits=None)` merges overrides over the settings; tests use it for a tiny `convert_timeout_s`.
- The parent writes the downloaded PDF to `DATA_DIR/jobs/<id>/in.pdf`, starts the child and waits up to `CONVERT_TIMEOUT_S`. The child reads `in.pdf`, converts, writes `out.pptx` and then `result.json`: `{"ok": true, "report": {...}}` or `{"ok": false, "error": "<plain sentence>"}` (a `ConvertError` or a limit sentence).
- If the child ends with no `result.json` (killed for memory, crashed), the job fails with "The conversion stopped unexpectedly (it may have run out of memory or time)." If it runs past the timeout the parent kills it and the job fails with the timeout sentence.
- The timeout covers conversion only (the download has its own 60 s). `WORKERS` is the number of worker **threads**; each thread runs one child process at a time, so it is also the number of conversions at once.
- `in.pdf` is deleted when the job ends, and `jobs.recover()` also deletes leftover `in.pdf` files after a restart.

## 4. The interface (keep exactly)

### 4.1 MCP tools (`<PUBLIC_URL>/mcp`, Streamable HTTP, stateless, plain JSON replies)

| Tool | Arguments | Returns |
|---|---|---|
| `convert_pdf` | `url` (string, https link to a PDF), `wait_seconds` (int, default 25; the wait is the smaller of this and `RUN_WAIT_S`) | A job object (below). `state` is `done`, `running`, `queued` or `failed` |
| `job_status` | `job_id` | The same job object. A done job gets a **fresh** `pptx_url` on every call |
| `get_pptx_file` | `job_id` | `{"job_id","filename","media_type","size_bytes","sha256","data_base64"}`. Over `MAX_INLINE_BYTES` (default 3,000,000) it fails with `too_large: ...` and the caller uses `pptx_url` |

Job object: `{"job_id","state","created"}` plus, when done, `{"pptx_url","links_expire_in":"24 hours","size_bytes","report":{...}}`, or when failed `{"error":"<plain sentence>"}`.

**Which failures are tool errors and which are failed jobs (decided):**
- A link that is refused, not a PDF, too big, unreadable or fails to convert is **a successful tool call whose job has `state: "failed"` and an `error` sentence**, because the work happens in the background.
- Asking about an unknown job, fetching the file of an unfinished or failed job, a too-large file, too many jobs, or a full disk ("The service is full. Try again later.") is **an MCP tool error** (`isError: true`) with a plain sentence. Unexpected crashes in a tool become `"<ErrorType>: <message>"` cut to 300 characters, with no stack trace.

### 4.2 Web API

| Route | Auth | Does |
|---|---|---|
| `GET /health` | none | `{"ok": true}` |
| `POST /convert` body `{"url": "..."}` | Bearer | 202 and `{"job_id"}`. 429 if too many jobs. 503 "The service is full. Try again later." when job disk is over `MAX_DATA_MB`. 401 without a token |
| `GET /jobs/{job_id}` | Bearer | the job object; 404 if unknown; 401 without a token |
| `GET /files/{job_id}/{name}` | valid signed link **or** Bearer | the .pptx file. Bad or expired signature **and** no valid Bearer: 401. A bad signature **with** a valid Bearer: 200 (the Bearer wins). Wrong name or unfinished job: 404 |
| `POST /mcp` | Bearer | the MCP server. No valid token: 401 with header `WWW-Authenticate: Bearer`. (The server is stateless; `/mcp` is the only MCP route.) |

Signed link: `?exp=<unix seconds>&sig=<hex HMAC-SHA256 of "job_id/name/exp">`, valid 24 hours, key `LINK_KEY` (falls back to `API_TOKEN`), compared with `hmac.compare_digest` on bytes (P5).

### 4.3 Settings (environment variables)

| Name | Default | Meaning |
|---|---|---|
| `API_TOKEN` | required, 24+ characters, else the service refuses to start | the bearer token |
| `PUBLIC_URL` | empty | used to build download links. If empty, links are relative and unusable from outside: log a warning at start (P10) |
| `LINK_KEY` | uses `API_TOKEN` | key for signed links; set it separately so a token change does not break links |
| `DATA_DIR` | `./data` (Docker: `/data`) | where job folders live |
| `RUN_WAIT_S` | 25 | the longest `convert_pdf` waits (this is the cap; the tool's `wait_seconds` can only lower it) |
| `WORKERS` | 2 | conversion threads |
| `MAX_INLINE_BYTES` | 3000000 | cap for `get_pptx_file` |
| the limits in 3.4 | see 3.4 | |

Jobs and files are deleted 24 hours after they finish. A restart marks queued and running jobs `failed` ("Service restarted while running. Start it again.").

## 5. Decisions already made (do not reopen)

1. **Editable text, picture for everything else.** Not a pure picture-per-slide deck (not editable), not a full vector rebuild (too lossy and large).
2. **One text box per line**, not per paragraph. It keeps each line exactly where it was in the PDF even when the viewer's font differs a little. Cost: editing a multi-line paragraph means editing line by line. Merging lines into paragraphs is in the backlog.
3. **A separate small service**, not part of the Peec audit server.
4. **No AI, no OCR** in the first version.
5. **Python:** PyMuPDF to read and draw, python-pptx to write, FastAPI + FastMCP (`mcp>=1.30,<2`; version 2 renamed `FastMCP`, do not upgrade) for the interface, bearer-token auth like the audit server.
6. **Link only** in the first version; no file upload.
7. **One instance, one worker** (section 3).

## 6. Reference material (read, do not edit)

All inside `/Volumes/Main drive/Claude/Cashew/Peec Articles/AEO-articles`:

| Path | Why read it |
|---|---|
| `app/main.py` | The pattern this service copies: FastMCP with `stateless_http=True, json_response=True`, the `tool()` wrapper that turns errors into short `ToolError`s, constant-time token compare, the `/mcp` mount. It may have changed since; read the current one |
| `docs/mcp-spec.md` | A finished example of an MCP interface document for calling agents. Write this service's `docs/mcp-spec.md` in the same style |
| `docs/devika-integration.md` | A finished integration guide with copy-paste client code (Claude Code, TypeScript, Python, curl) and a pass/fail test table. Reuse its four client recipes with this service's address and tools |
| `app/pptx_sink.py` | A **different** way of making a PowerPoint (it mirrors the drawing while a PDF is built). It cannot convert an existing PDF. Read only its font table (`SAFE`, `FONTS`) if you want better font mapping |
| `examples/hilton/hilton-ai-visibility-audit.pdf` | A real 21-page test PDF: 1440 x 810 pt slides, Poppins/Figtree-style fonts, photos, charts, tables |
| `Dockerfile`, `deploy/` | How the audit server is packaged and deployed to a Google Cloud VM |
| `AGENTS.md` | The working rules of that repo; section 9 here is derived from it |

Library docs: PyMuPDF (`import pymupdf`: `Page.get_text("dict")`, `get_texttrace`, `add_redact_annot`, `apply_redactions`, `get_pixmap`), python-pptx (`Presentation`, `slide_layouts[6]`, `shapes.add_picture`, `shapes.add_textbox`), MCP Python SDK v1 (`mcp.server.fastmcp.FastMCP`), httpx (custom transport for pinned connections).

## 7. Known limits to state honestly in the README

- Only text lines are editable. **Charts, tables, shapes, lines, icons, photos and logos are inside the background picture**, so they are not editable. A table's words are editable; its grid is not.
- Text drawn as outlines has no text to extract and stays in the picture. Rotated text stays in the picture.
- Fonts are **not embedded**. If a font is not installed where the deck opens, PowerPoint swaps it, and because boxes do not wrap, a wider font can run past the original width.
- Hyperlinks, bookmarks, form fields, annotations, notes and layers are not carried over.
- Scanned PDFs become picture-only slides. No OCR.
- Each slide's background is a 144 dpi PNG, so a deck can be large. Measure it in T1 and state the real number.
- The first page sets the slide size; mixed-size PDFs are scaled to fit.
- A page where less than half of the text is hidden still gets text boxes for its hidden spans (so a partly OCR'd page can show some text twice). Known limit; not fixed in the first version.
- **Licence:** PyMuPDF is under the AGPL (or a paid licence from Artifex). Running it as a public service has obligations. See decision D5.

## 8. Words used

- **Slide / pt / EMU:** 1 pt is 1/72 inch; 12700 EMU is 1 pt.
- **Span / line:** a line of text in a PDF is made of spans, each with one font, size and colour.
- **Redaction:** PyMuPDF's way of deleting content from a page; used only to remove text before drawing the background picture.
- **DPI:** dots per inch; how sharp the background picture is.
- **FastMCP / Streamable HTTP / stateless:** the library that makes an MCP server, the way it talks over HTTP, and "no session is kept between calls".
- **Bearer token:** a secret sent in the header `Authorization: Bearer <secret>`.
- **HMAC:** a keyed fingerprint used to sign a download link so it cannot be forged.
- **SSRF:** tricking a server into fetching an internal address. Sections 3.2 and P1 are the defence.
- **Hidden text layer:** invisible text a scanner's OCR places over the scanned picture of a page.

## 9. Rules of engagement (from the owner)

- **Git:** never commit straight to `main` or `master`. The project is on branch `feat/initial-service` (no commits yet). Add files **by name**, not `git add -A`. Never commit `.env`, `.venv/`, `data/` or any key.
- **Allowed without asking:** editing files in the project folder, running the tests, `docker build` and `docker run` on the owner's computer, reading the reference files.
- **Ask the owner first:** running `soffice` (LibreOffice) for the visual check (the owner interrupted one attempt, so wait for a clear yes), creating a GitHub repository, pushing, opening a pull request, merging, deploying, creating cloud resources, spending money. A yes for one does not cover the next.
- **Secrets:** never print, log or commit a token or key. Do not type passwords for the owner. If `gcloud` says "Reauthentication failed", ask the owner to run `gcloud auth login`.
- **Do not touch** the Peec audit server, its VM or its Google Cloud resources, or the project `hotels-pilot-personal-261013`.
- **Dockerfile comments** go on their own line, never after `CMD [...]` (a trailing comment made Docker read the line as shell form in the audit server and the container never started).
- **Wording:** short, plain, layman. Explain any technical word in a few words.
- **Review:** have a reviewer with no context read your work before you call it done.

## 10. The work, in order (tickets with "done when")

**T1. Real-PDF visual check and fixes.** Needs the owner's yes for LibreOffice (`/opt/homebrew/bin/soffice` exists). While waiting for the yes, do T2 and T3, which do not need it. Steps: convert the Hilton PDF (section 6) with `convert.convert`; save the .pptx; render it with `soffice --headless --convert-to pdf`; rasterise pages 1, 5 and 15 of both the original and the result with PyMuPDF at about 50 dpi; compare by eye (the eye check is for finding problems to report, not a pass/fail gate; the script checks below are the gate). LibreOffice substitutes fonts that are not installed, so judge **position and size, not glyph shape**. Pass rule: slide count equals page count; for every text box, its top-left is within 3 pt of the original line's top-left (check by script against `get_text("dict")`); no text left in the picture (extract text from the redacted page: empty, except rotated lines); no characters lost to neighbouring redactions (P14: the multiset (counts per character) of characters in the text boxes equals that of the original page's horizontal lines). Record the conversion time, the .pptx size and the number of text boxes in the README. **Done when:** the pass rule holds and the numbers are written down.

**T2. Fix the defects P1 to P16 and add the limits of 3.4.** The fixes are made here; the tests that prove them are written in T3. Done when each defect's T3 test failed before its fix and passes after (P13 and P16 are checked by reading).

**T3. Tests.** All offline (the web tests need no network; they use a local test server and the seams of 3.2). Add: (a) refused link through `convert_pdf` gives a `failed` job with the plain reason; (b) `get_pptx_file` returns `too_large` (patch `main.MAX_INLINE` to a tiny value); (c) `job_status` gives a fresh link; (d) empty `PUBLIC_URL` logs a warning (import `main` in a **subprocess** with the setting empty, because `main` reads settings at import); (e) the page, pixel, size and time limits (build a hostile PDF with a huge page and a many-page PDF); (f) every rule in 3.2 has an assertion: redirect to a private address (use a fake resolver and a fake transport), the 3-redirect cap, the `Content-Length` path, the time limit, and the after-connect check (use a fake stream that reports a private `server_addr`); (g) the 401, 404, 429 and bad-signature cases of 4.2, including a non-ASCII `sig`; (h) one **HTTP-level MCP test**: `tools/list` and `convert_pdf` as raw JSON-RPC with the three headers of 3.3, inside the **one existing** `with TestClient(main.app)` block of `tests/test_all.py` (the MCP session manager can start only once per process, so do not open a second TestClient); (i) two conversions at once; (j) a job older than 24 hours is deleted; (k) a restart (call `jobs.recover()`) marks a running job failed. Also create `tests/make_fixtures.py` that writes the fixture PDFs of section 11 into a folder. Also add tests for the new P2, P4 and P5 inputs and for the rotated-page fixture. **Done when:** the test file prints OK on Python 3.14 here; the Python 3.12 run is done inside the image in T4.

**T4. Docker.** Build (`docker build -t pdf-to-pptx .` in the project folder) and run with `-e API_TOKEN=<24+ random characters> -e PUBLIC_URL=http://localhost:8080 -p 8080:8080 -v pptx-data:/data`. Run the tests inside the image (this proves Python 3.12): `docker run --rm pdf-to-pptx python tests/test_all.py` (the image copies `tests/`). Run acceptance rows 1, 2, 3, 12, 15 (first part), 16 against `http://localhost:8080`. Every other row fetches a link, and the service cannot fetch `localhost` (by design), so those rows wait until the fixtures are at a public https URL (T7, or earlier if the owner provides one). **Done when:** `docker ps` shows the container `healthy`, the tests pass inside it, and `docker logs` shows no token or signed link.

**T5. Documentation.** Write `README.md` (what it is, how to run, how to call it, limits, why DNS-rebinding protection is off, the licence note) and `docs/mcp-spec.md` in the style of the reference files in section 6, with four copy-paste client recipes (Claude Code `claude mcp add --transport http ...`, TypeScript, Python, curl using the headers of 3.3) and the pass/fail table of section 11. Write for someone who has never seen the project. **Done when:** a reviewer with no context can call the service from the README alone.

**T6. Commit** on the branch, by file name, after a secrets scan of the staged diff. Then report to the owner. **Stop and ask** before creating a GitHub repository, pushing or opening a PR.

**T7a. Live acceptance run (needs a public host for the fixtures and the owner's yes to publish them).** The fixtures are generated, non-sensitive files, but putting them on the internet is publishing, so ask once. Acceptable places: a public GitHub gist or repository file, or a temporary public bucket, deleted afterwards. Then run rows 4 to 11, 13 to 15, 17 to 19 and 21 of section 11 against the running container (T4) or the deployed service (T7). Row 15's live 429 needs the container started with `MAX_ACTIVE_JOBS=1`. **Done when:** every row passes and the results are written in the README.

**T7. Hosting and deploy (after the owner answers D1 to D5, and only with an explicit yes).** Needs: public https; exactly one always-on instance (see section 3); `API_TOKEN` and `LINK_KEY` from a secret store, never in the image; a disk for `DATA_DIR` (it may be temporary, jobs last 24 hours); a health check on `/health`; no access log; an uptime check. Because the service fetches arbitrary links, the runtime must have **no powerful identity**: on Google Cloud the service account must have no roles (check with `gcloud projects get-iam-policy <project>` and look for the account), since the metadata-server block is a safety net, not the only defence. On Cloud Run that means `--min-instances 1 --max-instances 1 --no-cpu-throttling` and a dedicated service account with no roles.

## 11. Acceptance test (the whole thing works when these pass)

`BASE` is the address, `TOKEN` the token (never print it). **Fixtures:** `tests/make_fixtures.py` (T3) writes these PDFs (all generated with PyMuPDF and Pillow, no downloads): `normal.pdf` (3 pages, 1440 x 810 pt, a coloured background, text lines in two fonts incl. bold, a photo-like gradient PNG inserted, and a bar chart drawn with rectangles); `rotated.pdf` (a page with /Rotate 90); `scan_hidden.pdf`, `scan_plain.pdf`, `locked.pdf`, `big.pdf` (a valid PDF padded to over 50 MB), `page.html`, `many_pages.pdf` (exactly 201 pages of one text line), `slow.pdf` (dense pages; `make_fixtures.py` times a conversion on the current computer and picks the page count so it takes 5 to 100 seconds, under the 120 s limit, for rows 15 and 19), `huge_page.pdf` (one page of 14400 x 14400 pt, the PDF format's maximum, which is over `MAX_PAGE_PT`). For the live rows the owner or Codex puts them at any public https URL (a public GitHub file, a temporary public bucket) and they can be deleted afterwards.

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

## 12. Backlog (not for the first version; do not build unless asked)

Merge lines into paragraphs (option); keep hyperlinks; JPEG backgrounds for photo-heavy pages to shrink files; an adjustable `dpi` argument; an upload route for private PDFs; share-link support (Google Drive, Dropbox, OneDrive links turned into direct links); OCR for scans; per-caller tokens and rate limits; a rebuilt-table option.

## 13. Decisions for the owner (ask, do not guess)

- **D1. Where does it run?** It must be **one always-on instance** (section 3). Options: (a) Google Cloud Run with min and max instances both 1 and CPU always on (no machine to look after, https built in); (b) a second container on the existing audit VM (cheapest, but shares a machine with the audit server); (c) a new small VM. Also: which Google Cloud project, and which domain. Recommendation: (a) in a project of its own, if the owner has one; otherwise (c).
- **D2. Who may call it?** One shared token, or separate tokens per caller.
- **D3. GitHub:** a new repository (name?) or inside another repository.
- **D4. Limits:** are 50 MB, 200 pages and the other defaults in 3.4 right?
- **D5. Licence:** PyMuPDF is AGPL. Either publish this service's source (AGPL obligation), buy Artifex's commercial licence, or switch the PDF reader to a permissive library such as `pypdfium2` (a rewrite of the reading part of `convert.py` and a worse text API). Recommendation: ask the company's legal contact before launching publicly; for private internal use the obligation is lighter but still ask.
