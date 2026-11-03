# PDF to PowerPoint

Send a direct HTTPS link to a PDF and get an editable PowerPoint. Text becomes one text box per line. Photos, charts, table grids, shapes and logos remain in the slide's background picture. No AI, paid key or OCR is used.

**Status: locally verified prototype; Cloud Run storage migration is under review.** The owner selected a separate Google Cloud project, its provided HTTPS address and public AGPL source in `EthanDev/pdf-to-pptx`. No Cloud Run runtime is deployed yet. See [verification results](docs/verification.md) and the owner decisions below. This project is separate from AEO Articles and uses none of its credentials or cloud resources.

## Run locally

Python 3.12 or 3.14:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create local secrets without printing them. This refuses to overwrite an existing `.env`:

```sh
.venv/bin/python - <<'PY'
import os, secrets
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write('API_TOKEN=' + secrets.token_hex(32) + '\n')
    f.write('LINK_KEY=' + secrets.token_hex(32) + '\n')
    f.write('PUBLIC_URL=http://localhost:8080\n')
PY
set -a
. ./.env
set +a
PYTHONPATH=app .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

Keep exactly **one process and one instance running**. Jobs are stored on that instance's disk. Several instances would disagree about job IDs; a host that pauses idle instances can interrupt jobs.

The HTTP API and MCP tools require `Authorization: Bearer <token>`. `/health` is public. Download links are signed and work without the token for 24 hours; treat them as secrets. Set `LINK_KEY` separately so changing the API token does not invalidate download links.

MCP's built-in Host/DNS-rebinding check is disabled so a public reverse proxy can forward the service's hostname; this does **not** disable the PDF downloader's address checks. Only expose this behind HTTPS, with the Bearer token required. This service has no token-in-URL route.

## Docker

```sh
docker build -t pdf-to-pptx .
docker run -d --name pdf-to-pptx --env-file .env \
  -p 127.0.0.1:8080:8080 -v pptx-data:/data pdf-to-pptx
docker inspect --format '{{.State.Health.Status}}' pdf-to-pptx
```

The image uses Python 3.12, user 10001, one uvicorn worker and a health check. Use a named volume: a new one inherits `/data` ownership. An old root-owned volume needs an ownership migration or replacement after checking its contents; do not delete data blindly. The commands above are local-only HTTP. A future public deployment needs HTTPS and secret-store credentials.

## Use it

See [MCP and API guide](docs/mcp-spec.md) for Claude Code, TypeScript, Python and curl examples.

1. Call `convert_pdf(url, wait_seconds=25)` with a direct HTTPS PDF link.
2. Keep the `job_id`. If still queued or running, call `job_status` every few seconds.
3. When done, download `pptx_url`. For small files, `get_pptx_file` returns base64 and a SHA-256 checksum.
4. Check the report's warnings. A failed job has an `error` sentence.

Downloading, conversion and JSON-RPC are separate steps. A tool call can succeed while its returned job has `state: "failed"`. An unknown job, a full service or a too-large inline download is a tool error.

## Limits and settings

MB means 1,000,000 bytes. Limits reject values strictly greater than the setting.

| Setting | Default | Meaning |
|---|---:|---|
| `API_TOKEN` | required | At least 24 characters; startup fails without it |
| `PUBLIC_URL` | empty | Public base URL, without `/mcp`; empty logs a warning and gives relative download links |
| `LINK_KEY` | API token | Separate download-signing key recommended |
| `DATA_DIR` | `./data` (`/data` in Docker) | Job storage |
| `RUN_WAIT_S` | 25 | Longest tool wait; `wait_seconds` can only lower it |
| `WORKERS` | 2 | Job threads, each running one conversion child |
| `MAX_INLINE_BYTES` | 3000000 | Largest raw file returned as base64 |
| `MAX_PDF_MB` | 50 | Download size |
| `MAX_PAGES` | 200 | Pages per PDF |
| `MAX_PAGE_PT` | 4000 | Width or height of every page; PowerPoint's 4032 pt maximum also applies |
| `MAX_PAGE_PIXELS` | 20000000 | Background pixels per page; reduce resolution from 144 down to 36 dpi before refusing |
| `MAX_PPTX_MB` | 300 | Output deck size |
| `CONVERT_TIMEOUT_S` | 120 | Conversion time; the child is killed when exceeded |
| `MAX_ACTIVE_JOBS` | 20 | Queued and running jobs; REST returns 429 if full |
| `MAX_DATA_MB` | 2000 | Refuse new jobs with REST 503 when existing job storage exceeds this size |

Downloads have one overall 60-second deadline across DNS, connection, redirects, headers and body. Only HTTPS port 443 is allowed. Every resolved address must be public, and the connection is pinned to a vetted IP with the original hostname retained for TLS checks. Each of at most three redirects is checked again. Proxy environment variables are ignored. Private addresses, cloud metadata, embedded private IPv4 forms and missing/unexpected connected addresses are refused. There is no production private-address override.

Finished and failed jobs expire 24 hours after completion. Cleanup runs when requests arrive and before new jobs. A restart marks unfinished jobs failed and removes their temporary files; finished decks remain available. Storage is checked at admission; this is not a filesystem quota, so current jobs can take it over the threshold. Use a bounded disk and memory/CPU limits on the eventual host.

## What will look different

- Only words are editable. Table words are editable, but grids and charts are pictures.
- Text is editable line by line, not as one paragraph. Text drawn as outlines stays in the picture.
- Rotated text within a page stays in the picture. If deleting editable text could also delete overlapping rotated text, the whole page stays a picture to preserve its content. PDF page rotation is preserved for editable lines.
- Scans stay picture-only; there is no OCR.
- Fonts are **not embedded**. When any text boxes exist, the report warns which fonts to install. Missing fonts can change the look or make text overrun a line's box.
- Links, bookmarks, forms, annotations, notes and layers are not transferred.
- The first page sets slide size. Later pages are scaled and centred.
- A partly hidden OCR layer can duplicate text when fewer than half its characters are hidden.
- Backgrounds are normally 144 dpi PNGs; photo-heavy decks can be large.

The checked-in reference Hilton PDF currently has **20 pages**, not the plan's stated 21. Local conversion produced **20 slides, 618 editable text boxes, 1,335,138 bytes (1.34 MB), in 1.995 seconds** on this Mac. All positions were within 3 pt, character counts matched and the redacted backgrounds had no remaining horizontal text. LibreOffice pages 1, 5 and 15 retained layout and graphics; fonts were substituted. These measurements describe one local fixture, not a speed guarantee.

## Tests

```sh
.venv/bin/python -W ignore tests/test_all.py
.venv/bin/python -W ignore tests/test_hardening.py
.venv/bin/python -W ignore tests/test_mcp_client.py
docker run --rm pdf-to-pptx python tests/test_all.py
docker run --rm pdf-to-pptx python tests/test_hardening.py
.venv/bin/python tests/container_smoke.py
.venv/bin/python tests/make_fixtures.py .quality/fixtures --calibrate-slow
```

Tests use fake transports and a loopback fixture server. They make no external PDF requests and no paid AI calls. `allow_private` is a Python-only test seam, never an API or environment setting. `tests/container_smoke.py` uses a temporary token, a unique named volume and loopback port; it removes only its own container and volume. Pass `--context NAME` for a non-default Docker engine.

## Licence and Cloud Run migration

This service source uses **AGPL-3.0-only**; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party packages retain their own licences. PyMuPDF/MuPDF use their AGPL option; no commercial licence is claimed.

The owner selected a separate Google Cloud project, Cloud Run's provided HTTPS hostname, public source in `EthanDev/pdf-to-pptx`, one shared API token and the tested conversion defaults. The current disk-backed implementation has not yet been migrated or deployed.

The [storage review](docs/cloud-run-storage-review.md) proposes durable job records/files and queued workers with no project roles. It explicitly covers retry races, upload isolation, storage reservations, expiry and platform log handling. Some caller-visible details change: direct Storage download links, accurate remaining retention and stricter accounted storage admission. The proposed evaluations are not test results. Public fixture publication and owner click-to-edit acceptance remain separate pending steps.
