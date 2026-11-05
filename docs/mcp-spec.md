# MCP and API guide

This is a separate PDF-to-PowerPoint service. It does not call AEO Articles or AI models. Its address is not selected yet: replace `BASE` with the base URL, without a trailing slash. Local testing uses `http://localhost:8080`; public use requires HTTPS.

## Connect

MCP endpoint: `BASE/mcp`, Streamable HTTP, stateless, plain JSON responses.

Send these headers:

```text
Authorization: Bearer <API_TOKEN>
Content-Type: application/json
Accept: application/json, text/event-stream
```

A missing or wrong token returns HTTP 401 with `WWW-Authenticate: Bearer`. A missing suitable Accept header can return 406. There is no OAuth flow or token-in-URL route. SDK clients should initialize as normal; raw stateless JSON-RPC calls can list/call tools directly.

## Three tools

| Tool | Arguments | Result |
|---|---|---|
| `convert_pdf` | `url`: direct HTTPS PDF link; `wait_seconds`: integer, default 25 | Job, waiting at most `RUN_WAIT_S` |
| `job_status` | `job_id`: string | Job with a fresh download link when done |
| `get_pptx_file` | `job_id`: string | Filename, media type, raw size, SHA-256 and base64 file |

Job:

```json
{"job_id":"a1b2c3d4e5f6","state":"running","created":"2026-11-03T12:00:00Z"}
```

`state` is `queued`, `running`, `done` or `failed`. Done adds `pptx_url`, `links_expire_in: "24 hours"`, `size_bytes` and `report`. The report contains `pages`, `text_boxes`, `picture_only_pages`, `warnings`, `slide_size_pt` and `fonts`. Failed adds `error` with a plain sentence. Show warnings to the user.

An invalid URL or conversion failure returns a **failed job**, since work happens in the background. Unknown/unfinished jobs, a full service or `get_pptx_file` over its limit return an **MCP tool error**, with `isError: true`. `too_large` means use the signed download link instead. Never retry a conversion blindly after a connection error: first check the job if its ID was received.

SDK tool results expose `structuredContent`; raw JSON-RPC puts it under `result.structuredContent`. Some clients expose only JSON in `result.content[0].text`. Treat an HTTP 200 with `isError: true` as a tool failure.

Keep job IDs, not signed URLs. Files are deleted 24 hours after completion. A fresh link does not extend that retention. Restarted unfinished jobs fail with “Service restarted while running. Start it again.” Finished files survive if the same data disk is retained.

## Claude Code

Set `BASE` and load `API_TOKEN` from your secret store/environment; never put the token in a message or commit. This command stores the connection credential in Claude's local configuration:

```sh
claude mcp add --transport http pdf-to-pptx "$BASE/mcp" \
  --header "Authorization: Bearer $API_TOKEN"
```

Ask Claude to list tools, then convert a public PDF link, poll `job_status` and give you its download link. This server needs no paid AI call itself; using an AI client can have that client's usual charges.

## TypeScript

Install the MCP SDK (`npm install @modelcontextprotocol/sdk`) and run this in an ESM TypeScript project:

```ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const base = process.env.BASE!;
const token = process.env.API_TOKEN!;
const client = new Client({ name: "pdf-client", version: "1.0.0" });
await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`), {
  requestInit: { headers: { Authorization: `Bearer ${token}` } },
}));
try {
  console.log((await client.listTools()).tools.map(t => t.name));
  const result = await client.callTool({ name: "convert_pdf", arguments: {
    url: process.env.PDF_URL!, wait_seconds: 0,
  }});
  // Read result.structuredContent; if queued/running, poll job_status every 2–5 s.
  // Keep the result private: completed jobs contain a signed download link.
  if (result.isError) throw new Error("Conversion tool failed; inspect its private result.");
} finally {
  await client.close();
}
```

## Python

Install `mcp>=1.30,<2` and `httpx`:

```python
import asyncio, os
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
        timeout=60,
    ) as http:
        async with streamable_http_client(os.environ["BASE"] + "/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = [t.name for t in (await client.list_tools()).tools]
                print(names)
                result = await client.call_tool("convert_pdf", {
                    "url": os.environ["PDF_URL"], "wait_seconds": 0,
                })
                if result.isError:
                    raise RuntimeError("Conversion tool failed; inspect its private result.")
                job = result.structuredContent
                while job["state"] in ("queued", "running"):
                    await asyncio.sleep(2)
                    result = await client.call_tool("job_status", {"job_id": job["job_id"]})
                    if result.isError:
                        raise RuntimeError("Status tool failed; inspect its private result.")
                    job = result.structuredContent
                # Use job privately; do not log its signed URL.
                print(job["state"])

if __name__ == "__main__":
    asyncio.run(main())
```

## curl

These commands assume `BASE`, `API_TOKEN` and `PDF_URL` are already set. Avoid shell tracing (`set -x`). Tools/list has no signed download links:

```sh
curl --fail-with-body -sS "$BASE/health"
curl --fail-with-body -sS "$BASE/mcp" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Build the conversion request safely and save its response privately, since it may contain a signed URL:

```sh
umask 077
python3 - <<'PY' > request.json
import json, os
print(json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
    "name":"convert_pdf","arguments":{"url":os.environ["PDF_URL"],"wait_seconds":0}}}))
PY
curl --fail-with-body -sS "$BASE/mcp" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-binary @request.json -o response.json
```

## Plain web API

| Route | Expected |
|---|---|
| `GET /health` | 200 `{"ok":true}`, no token |
| `POST /convert` with `{"url":"https://.../file.pdf"}` | Bearer required; 202 with job ID; 429 queue full; 503 storage full |
| `GET /jobs/{job_id}` | Bearer required; job or 404 |
| `GET /files/{job_id}/{name}` | Valid signed query **or** Bearer; deck or 404 |

Signed query: `exp` is Unix expiry seconds, `sig` is a 64-character hex HMAC-SHA256 signature. A malformed/expired/changed query without valid Bearer returns 401, never a server error. Valid Bearer still works if the signature is bad. Do not construct signatures in clients; use the returned link.

For REST, send JSON to `$BASE/convert` with the same Authorization and Content-Type headers. Store its job ID, GET `$BASE/jobs/<id>` until done, then download the exact `pptx_url`. `get_pptx_file` is MCP-only; it returns raw size and SHA-256, not a checksum of the base64 text.

## Acceptance

The complete pass/fail table and current local-versus-public evidence are in [verification results](verification.md). Read the [README](../README.md) for defaults, limitations and the outstanding hosting/licence decisions. The local Python and TypeScript SDK versions may differ; run the smoke test before relying on a new client version.


## Cloud backend

Cloud mode keeps jobs in Firestore and results in private Cloud Storage. `pptx_url` is a direct signed Storage URL with the exact object generation; it needs no Authorization header. **Never forward your API Bearer token to the download host.** `links_expire_in` describes the actual returned URL lifetime (up to fifteen minutes), and numeric Unix `expires_at` describes the job's completion-plus-24-hour retention deadline. Polling can refresh a URL while retention remains, but cannot extend retention.

The compatibility `/files` route still accepts Bearer or its existing HMAC query and streams the result. All job/file reads enforce retention even with a valid token. The cloud storage budget reserves staging/final/retry space before granting uploads, so work can be refused before the twenty-job ceiling. Source and deployed revision are exposed by `GET /source`. See [implementation and release checks](cloud-run-storage-implementation.md); live release is not yet claimed.
