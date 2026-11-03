"""Offline tests, no network beyond 127.0.0.1. Run from the project root:   .venv/bin/python tests/test_all.py"""
import asyncio, hashlib, io, json, os, sys, tempfile, threading, time, base64
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
os.environ.update(API_TOKEN="t" * 32, DATA_DIR=tempfile.mkdtemp(), PUBLIC_URL="http://x")
import pymupdf
from pptx import Presentation
import convert, fetch, jobs, main

def pdf(pages=1, w=1440, h=810, hidden=False, rotated=False):
    d = pymupdf.open()
    for n in range(pages):
        p = d.new_page(width=w, height=h)
        p.draw_rect(pymupdf.Rect(0, 0, w, h), color=None, fill=(0, 0.17, 0.07))
        p.draw_rect(pymupdf.Rect(100, 300, 600, 500), color=None, fill=(0, 0.92, 0.36))
        p.insert_text((100, 200), f"Title {n+1}", fontsize=60, fontname="helv", color=(1, 1, 1), render_mode=3 if hidden else 0)
        p.insert_text((120, 400), "Bold body", fontsize=28, fontname="hebo", color=(0, 0.17, 0.07), render_mode=3 if hidden else 0)
        if rotated: p.insert_text((800, 700), "Sideways", fontsize=30, fontname="helv", rotate=90)
    return d.tobytes()

def run_tests():
    # --- converter
    out, rep = convert.convert(pdf(2))
    prs = Presentation(io.BytesIO(out))
    assert len(prs.slides) == 2 and (prs.slide_width, prs.slide_height) == (1440 * 12700, 810 * 12700), "slide size follows the page"
    shapes = list(prs.slides[0].shapes)
    assert shapes[0].shape_type == 13 and (shapes[0].left, shapes[0].top) == (0, 0), "background picture first"
    boxes = [s for s in shapes if s.has_text_frame]
    assert [b.text_frame.text for b in boxes] == ["Title 1", "Bold body"] and rep["text_boxes"] == 4
    run = boxes[1].text_frame.paragraphs[0].runs[0]
    assert run.font.bold and run.font.size.pt == 28 and str(run.font.color.rgb) == "002B12", (run.font.bold, run.font.size, run.font.color.rgb)
    assert abs(boxes[0].left - 100 * 12700) < 3 * 12700, "text sits where it was in the PDF"
    # the picture has no text left in it: redact the same way and check nothing is extractable
    d = pymupdf.open(stream=pdf(), filetype="pdf"); pg = d[0]; lines, _ = convert._lines(pg); convert._picture(pg, lines, 72); assert pg.get_text().strip() == "", "text removed from the picture"

    out, rep = convert.convert(pdf(hidden=True)); assert rep["picture_only_pages"] == [1] and any("scanned" in w for w in rep["warnings"]), rep
    out, rep = convert.convert(pdf(rotated=True)); assert any("rotated" in w for w in rep["warnings"]) and rep["text_boxes"] == 2, rep
    d = pymupdf.open(); d.new_page(width=1440, height=810); d.new_page(width=720, height=405).insert_text((50, 100), "Small", fontsize=20)
    out, rep = convert.convert(d.tobytes()); p2 = Presentation(io.BytesIO(out)).slides[1]
    assert len(p2.shapes) == 2 and p2.shapes[1].text_frame.paragraphs[0].runs[0].font.size.pt == 40, "a smaller page is scaled to the slide"
    assert rep["picture_only_pages"] == [1]
    for bad, msg in ((b"not a pdf", "Could not read"), (pdf(3), "limit")):
        try: convert.convert(bad, max_pages=2); raise SystemExit("should have failed")
        except convert.ConvertError as e: assert msg in str(e), e
    enc = pymupdf.open(); enc.new_page(); eb = enc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    try: convert.convert(eb); raise SystemExit("should have failed")
    except convert.ConvertError as e: assert "password" in str(e)
    assert convert.family("ABCDEF+Poppins-SemiBold") == "Poppins" and convert.family("Helvetica-Bold") == "Helvetica" and convert.family("") == "Arial"

    # --- downloader: what it must refuse (no network needed: these stop before connecting)
    for url, msg in (("http://example.com/a.pdf", "https"), ("https://user:pw@example.com/a.pdf", "user name"), ("https://example.com:8443/a.pdf", "port"),
                     ("https://127.0.0.1/a.pdf", "private"), ("https://localhost/a.pdf", "private"), ("https://169.254.169.254/latest", "private"),
                     ("https://10.1.2.3/a.pdf", "private"), ("https://[::1]/a.pdf", "private"), ("https://100.64.0.1/a.pdf", "private"), ("https://[::ffff:127.0.0.1]/a.pdf", "private"),
                     ("file:///etc/passwd", "https"), ("https:///nohost", "host"), ("https://" + "a" * 3000, "too long")):
        try: fetch.fetch_pdf(url); raise SystemExit(f"should have refused {url}")
        except fetch.FetchError as e: assert msg in str(e), (url, e)
    assert fetch._public("8.8.8.8") and not fetch._public("192.168.0.1") and not fetch._public("fe80::1") and not fetch._public("0.0.0.0")

    # --- a local server stands in for "a link someone sent" (allowed only through the test switch)
    GOOD, BIG = pdf(2), b"%PDF-" + b"0" * 3000
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/ok.pdf": self.send_response(200); self.end_headers(); self.wfile.write(GOOD)
            elif self.path == "/go": self.send_response(302); self.send_header("Location", "/ok.pdf"); self.end_headers()
            elif self.path == "/html": self.send_response(200); self.end_headers(); self.wfile.write(b"<html>hi</html>")
            elif self.path == "/big": self.send_response(200); self.end_headers(); self.wfile.write(BIG)
            else: self.send_response(404); self.end_headers()
    srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    B = f"http://127.0.0.1:{srv.server_port}"
    data, name = fetch.fetch_pdf(B + "/go", allow_private=True); assert data == GOOD and name == "ok.pdf", "redirect followed"
    for path, msg, kw in (("/html", "did not give a PDF", {}), ("/big", "bigger than", {"max_bytes": 1000}), ("/missing", "404", {})):
        try: fetch.fetch_pdf(B + path, allow_private=True, **kw); raise SystemExit("should have failed " + path)
        except fetch.FetchError as e: assert msg in str(e), (path, e)

    # --- web API and MCP tools
    from fastapi.testclient import TestClient
    jobs.ALLOW_PRIVATE = True
    AUTH = {"Authorization": "Bearer " + "t" * 32}
    with TestClient(main.app) as c:
        assert c.get("/health").json() == {"ok": True}
        assert c.post("/convert", json={"url": B + "/ok.pdf"}).status_code == 401
        assert c.post("/convert", json={"url": B + "/ok.pdf"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.post("/mcp", json={}).status_code == 401 and c.post("/mcp", json={}).headers["www-authenticate"] == "Bearer"
        jid = c.post("/convert", json={"url": B + "/ok.pdf"}, headers=AUTH).json()["job_id"]
        for _ in range(100):
            j = c.get(f"/jobs/{jid}", headers=AUTH).json()
            if j["state"] in ("done", "failed"): break
            time.sleep(0.1)
        assert j["state"] == "done" and j["report"]["text_boxes"] == 4 and j["report"]["pages"] == 2, j
        link = j["pptx_url"].replace("http://x", "")
        r = c.get(link); assert r.status_code == 200 and r.content[:2] == b"PK" and len(Presentation(io.BytesIO(r.content)).slides) == 2, "signed link works with no token"
        assert c.get(link[:-3] + "abc").status_code == 401, "bad signature"
        assert c.get(link.split("?")[0]).status_code == 401, "no signature, no token"
        assert c.get(link.split("?")[0], headers=AUTH).status_code == 200, "token works instead of a signature"
        assert c.get(f"/jobs/{jid}").status_code == 401 and c.get("/jobs/zzzzzzzzzzzz", headers=AUTH).status_code == 404

        # HTTP-level MCP, in the same lifespan as the REST checks.
        headers = AUTH | {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        def rpc(method, params=None):
            result = c.post("/mcp", headers=headers, json={"jsonrpc":"2.0","id":1,"method":method,"params":params or {}})
            assert result.status_code == 200, result.text
            return result.json()["result"]
        assert sorted(t["name"] for t in rpc("tools/list")["tools"]) == ["convert_pdf", "get_pptx_file", "job_status"]
        converted = rpc("tools/call", {"name":"convert_pdf","arguments":{"url":B+"/ok.pdf"}})
        assert not converted.get("isError") and converted["structuredContent"]["state"] == "done", converted
        for exp, sig in [("²", "a"*64), ("123", "é"), ("9"*13, "a"*64), ("1", "a"*64)]:
            assert c.get(link.split("?")[0], params={"exp":exp,"sig":sig}).status_code == 401
            assert c.get(link.split("?")[0], params={"exp":exp,"sig":sig}, headers=AUTH).status_code == 200
        import unittest.mock
        from mcp.server.fastmcp.exceptions import ToolError
        with unittest.mock.patch.object(main, "MAX_INLINE", 1):
            try: asyncio.run(main.get_pptx_file(jid)); raise AssertionError("expected too_large")
            except ToolError as e: assert "too_large" in str(e)
        with unittest.mock.patch.object(main.time, "time", return_value=time.time()+2):
            assert asyncio.run(main.job_status(jid))["pptx_url"] != j["pptx_url"]
        with unittest.mock.patch.object(jobs, "MAX_ACTIVE", 0):
            assert c.post("/convert", json={"url":B+"/ok.pdf"}, headers=AUTH).status_code == 429
        with unittest.mock.patch.object(jobs, "MAX_DATA_MB", 0):
            assert c.post("/convert", json={"url":B+"/ok.pdf"}, headers=AUTH).status_code == 503
        with unittest.mock.patch.object(jobs, "start", return_value="a"*12), unittest.mock.patch.object(jobs, "get", return_value=None):
            try: asyncio.run(main.convert_pdf("https://fixture.example/a.pdf")); raise AssertionError("expected missing record error")
            except ToolError as e: assert "record could not be found" in str(e)
        assert c.get(link.split("?")[0].rsplit("/",1)[0]+"/wrong.pptx", headers=AUTH).status_code == 404
        ids = [jobs.start(B+"/ok.pdf") for _ in range(2)]
        for _ in range(150):
            states = [jobs.get(identity)["state"] for identity in ids]
            if all(state in ("done", "failed") for state in states): break
            time.sleep(.1)
        assert states == ["done", "done"], states

        async def tools():
            names = sorted(t.name for t in await main.mcp.list_tools()); assert names == ["convert_pdf", "get_pptx_file", "job_status"], names
            res = (await main.mcp.call_tool("convert_pdf", {"url": B + "/ok.pdf"}))[1]; assert res["state"] == "done" and "pptx_url" in res, res
            f = (await main.mcp.call_tool("get_pptx_file", {"job_id": res["job_id"]}))[1]
            raw = base64.b64decode(f["data_base64"]); assert hashlib.sha256(raw).hexdigest() == f["sha256"] and raw[:2] == b"PK"
        asyncio.run(tools())
        # a refused link ends as a failed job with a plain reason
        jobs.ALLOW_PRIVATE = False
        refused = asyncio.run(main.convert_pdf("https://127.0.0.1/x.pdf"))
        jid = refused["job_id"]
        for _ in range(50):
            j = jobs.get(jid)
            if j["state"] == "failed": break
            time.sleep(0.1)
        assert j["state"] == "failed" and "private" in j["error"], j
        try: asyncio.run(main.job_status("zzzzzzzzzzzz")); raise SystemExit("should have failed")
        except Exception as e: assert "No such job" in str(e)
    print("OK: converter, safe downloader, web API and MCP tools")


if __name__ == "__main__":
    run_tests()
