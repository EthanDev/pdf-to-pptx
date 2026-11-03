import asyncio, base64, contextlib, functools, hashlib, hmac, logging, os, re, time
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
import jobs

TOKEN = os.environ.get("API_TOKEN", "")
if len(TOKEN) < 24: raise SystemExit("Set API_TOKEN to a long random secret (24+ characters).")
LINK_KEY = (os.environ.get("LINK_KEY") or TOKEN).encode()
PUBLIC = os.environ.get("PUBLIC_URL", "").rstrip("/")
if not PUBLIC:
    logging.getLogger(__name__).warning('PUBLIC_URL is empty; download links will be relative and unusable from outside.')
MAX_INLINE = int(os.environ.get("MAX_INLINE_BYTES", "3000000"))
WAIT_S = float(os.environ.get("RUN_WAIT_S", "25"))
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

mcp = FastMCP("pdf-to-pptx", stateless_http=True, json_response=True, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

# FastMCP configures root logging; suppress full request URLs after construction.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)

def tool(fn):
    @functools.wraps(fn)
    async def wrapped(*a, **k):
        try: return await fn(*a, **k)
        except ToolError: raise
        except Exception as e: raise ToolError(f"{type(e).__name__}: {str(e)[:300]}")
    return mcp.tool()(wrapped)

def _sig(jid: str, name: str, exp: str) -> str: return hmac.new(LINK_KEY, f"{jid}/{name}/{exp}".encode(), hashlib.sha256).hexdigest()
def _link(j: dict) -> str:
    exp = str(int(time.time()) + 24 * 3600)
    return f"{PUBLIC}/files/{j['job_id']}/{j['file']}?exp={exp}&sig={_sig(j['job_id'], j['file'], exp)}"
def _view(j: dict) -> dict:
    out = {"job_id": j["job_id"], "state": j["state"], "created": j["created"]}
    if j["state"] == "failed": out["error"] = j.get("error", "Failed.")
    if j["state"] == "done": out |= {"pptx_url": _link(j), "links_expire_in": "24 hours", "size_bytes": j["size_bytes"], "report": j["report"]}
    return out

@tool
async def convert_pdf(url: str, wait_seconds: int = 25) -> dict[str, Any]:
    """Download the PDF at an https link and turn it into an editable PowerPoint (text becomes editable text boxes; shapes, charts and photos stay in a background picture). Waits up to wait_seconds (max 25); if not finished it returns state 'running' and a job_id: call job_status with it. Done: pptx_url is a download link valid 24 hours, report lists text boxes, any picture-only pages and warnings."""
    try: jid = jobs.start(url)
    except (jobs.TooManyJobs, jobs.ServiceFull) as e: raise ToolError(str(e))
    deadline = time.monotonic() + max(0, min(wait_seconds, WAIT_S))
    while True:
        j = jobs.get(jid)
        if j is None: raise ToolError("The job record could not be found. Start it again.")
        if j["state"] in ("done", "failed") or time.monotonic() >= deadline: break
        await asyncio.sleep(0.3)
    return _view(j)

@tool
async def job_status(job_id: str) -> dict[str, Any]:
    """State of a conversion: queued, running, done or failed. When done it has a fresh pptx_url."""
    j = jobs.get(job_id)
    if not j: raise ToolError("No such job. (If the service restarted, start it again.)")
    return _view(j)

@tool
async def get_pptx_file(job_id: str) -> dict[str, Any]:
    """The finished PowerPoint itself as base64, with its sha256. Files over MAX_INLINE_BYTES (3 MB) fail with too_large: use pptx_url."""
    j = jobs.get(job_id)
    if not j: raise ToolError("No such job.")
    if j["state"] == "failed": raise ToolError(f"That job failed: {j.get('error')}")
    if j["state"] != "done": raise ToolError("That job is not finished.")
    if j["size_bytes"] > MAX_INLINE: raise ToolError(f"too_large: the file is {j['size_bytes']} bytes (limit {MAX_INLINE}). Use pptx_url from job_status.")
    data = (jobs.DATA / job_id / j["file"]).read_bytes()
    return {"job_id": job_id, "filename": j["file"], "media_type": PPTX_TYPE, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "data_base64": base64.b64encode(data).decode()}

mcp_app = mcp.streamable_http_app()

@contextlib.asynccontextmanager
async def lifespan(app):
    jobs.recover()
    async with mcp.session_manager.run(): yield

app = FastAPI(title="PDF to PowerPoint", lifespan=lifespan)

def check(auth: str | None):
    given = (auth or "").strip()
    given = given[7:].strip() if given[:7].lower() == "bearer " else ""
    if not given or not hmac.compare_digest(given.encode("utf-8", "surrogatepass"), TOKEN.encode()): raise HTTPException(401, "Bad or missing token", headers={"WWW-Authenticate": "Bearer"})

@app.middleware("http")
async def mcp_auth(request, call_next):
    await asyncio.to_thread(jobs._clean)
    if request.url.path.rstrip("/") == "/mcp":
        try: check(request.headers.get("authorization"))
        except HTTPException as e:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": e.detail}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)

class ConvertReq(BaseModel):
    url: str

@app.get("/health")
async def health(): return {"ok": True}

@app.post("/convert", status_code=202)
def convert_start(req: ConvertReq, authorization: str | None = Header(None)):
    check(authorization)
    try: return {"job_id": jobs.start(req.url)}
    except jobs.TooManyJobs as e: raise HTTPException(429, str(e))
    except jobs.ServiceFull as e: raise HTTPException(503, str(e))

@app.get("/jobs/{job_id}")
def job(job_id: str, authorization: str | None = Header(None)):
    check(authorization); j = jobs.get(job_id)
    if not j: raise HTTPException(404, "No such job")
    return _view(j)

@app.get("/files/{job_id}/{name}")
def file(job_id: str, name: str, exp: str | None = None, sig: str | None = None, authorization: str | None = Header(None)):
    ok = bool(exp and sig and re.fullmatch(r"[0-9]{1,12}", exp) and re.fullmatch(r"[0-9a-fA-F]{64}", sig) and int(exp) > time.time() and hmac.compare_digest(sig.lower().encode("ascii"), _sig(job_id, name, exp).encode("ascii")))
    if not ok: check(authorization)
    j = jobs.get(job_id)
    if not j or j["state"] != "done" or j["file"] != name: raise HTTPException(404, "No such file")
    return FileResponse(jobs.DATA / job_id / name, media_type=PPTX_TYPE, filename=name)

app.mount("/", mcp_app)      # /mcp (the MCP endpoint); keep last so the routes above win
