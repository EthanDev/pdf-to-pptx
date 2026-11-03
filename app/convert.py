"""PDF -> editable PowerPoint.

Per page: the real text becomes editable text boxes (one per line, same position, size, font name, colour, bold/italic);
everything else (shapes, lines, charts, photos, logos) stays in a background picture of the page with the text taken out.
Pages we cannot do this for (scanned pages, text only drawn as outlines) become picture-only slides, and the report says so.
"""
import io, math, re
import pymupdf
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Emu, Pt

EMU_PER_PT = 12700
SUBSET = re.compile(r"^[A-Z]{6}\+")                     # "ABCDEF+Poppins-Bold" -> "Poppins-Bold"
STYLE_WORDS = re.compile(r"[-,_ ]?(bold|italic|oblique|regular|roman|medium|semibold|black|extrabold|light|thin|book|mt|ps|psmt)\b", re.I)

class ConvertError(ValueError): pass

def family(font: str) -> str:
    """A name PowerPoint can look up: subset tag and style words removed ('ABCDEF+Poppins-SemiBold' -> 'Poppins')."""
    name = SUBSET.sub("", font or "")
    base = STYLE_WORDS.sub("", name).strip(" -,_")
    return base or name or "Arial"

def _rgb(c: int) -> RGBColor: return RGBColor((c >> 16) & 255, (c >> 8) & 255, c & 255)

def _invisible_share(page) -> float:
    """Share of characters drawn invisibly (the hidden text layer a scanner's OCR adds). Those pages must stay pictures."""
    try: trace = page.get_texttrace()
    except Exception: return 0.0
    total = sum(len(s.get("chars", ())) for s in trace)
    hidden = sum(len(s.get("chars", ())) for s in trace if s.get("type") == 3)
    return hidden / total if total else 0.0

def _lines(page):
    """Horizontal text lines worth turning into text boxes, and how many lines were left in the picture (rotated)."""
    keep, skipped, protected = [], 0, []
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []) if b.get("type") == 0 else []:
            spans = [s for s in ln["spans"] if s["text"].strip() or s["text"]]
            if not "".join(s["text"] for s in spans).strip(): continue
            dx, dy = ln.get("dir", (1, 0))
            if abs(dy) > 0.01 or dx < 0:
                skipped += 1
                protected.extend(pymupdf.Rect(span["bbox"]) for span in spans)
                continue
            keep.append((pymupdf.Rect(ln["bbox"]), spans))
    # A redaction removes every intersecting character, even a neighbour that
    # should remain in the picture. Preserve this page as a picture on conflict.
    if any(rect.intersects(other) for rect, _ in keep for other in protected):
        return [], skipped + len(keep)
    return keep, skipped

def _picture(page, lines, dpi):
    """The page as a PNG with the given text lines taken out (nothing else is touched)."""
    for r, _ in lines: page.add_redact_annot(r, fill=False)
    if lines: page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")

def convert(pdf: bytes, max_pages: int = 200, dpi: int = 144, *, max_page_pt: float = 4000, max_page_pixels: int = 20_000_000, max_pptx_mb: float = 300) -> tuple[bytes, dict]:
    """Returns (pptx bytes, report). Raises ConvertError for a file that is not a usable PDF."""
    try: doc = pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as e: raise ConvertError(f"Could not read the file as a PDF: {str(e)[:120]}")
    if doc.needs_pass: raise ConvertError("The PDF is password protected.")
    if doc.page_count == 0: raise ConvertError("The PDF has no pages.")
    if doc.page_count > max_pages: raise ConvertError(f"The PDF has {doc.page_count} pages; the limit is {max_pages}.")
    for page in doc:
        if max(page.rect.width, page.rect.height) > min(max_page_pt, 4032):
            raise ConvertError(f"A page is {math.ceil(page.rect.width)} x {math.ceil(page.rect.height)} points; the limit is {min(max_page_pt, 4032):g}.")
    first = doc[0].rect
    W, H = first.width, first.height
    prs = Presentation(); prs.slide_width, prs.slide_height = Emu(round(W * EMU_PER_PT)), Emu(round(H * EMU_PER_PT))
    blank = prs.slide_layouts[6]
    rep = {"pages": doc.page_count, "text_boxes": 0, "picture_only_pages": [], "warnings": [], "slide_size_pt": [round(W), round(H)]}
    fonts = set()
    for i, page in enumerate(doc, 1):
        pw, ph = page.rect.width, page.rect.height
        s = min(W / pw, H / ph)                                            # pages of another size are scaled to fit the first page's slide
        ox, oy = (W - pw * s) / 2, (H - ph * s) / 2
        scanned = _invisible_share(page) > 0.5
        lines, skipped = ([], 0) if scanned else _lines(page)
        if skipped: rep["warnings"].append(f"Page {i}: {skipped} rotated or overlapping text line(s) stay in the picture and are not editable.")
        if scanned: rep["warnings"].append(f"Page {i}: looks scanned (hidden text layer); kept as a picture only.")
        elif not lines: rep["warnings"].append(f"Page {i}: no editable text found; kept as a picture only.")
        if not lines: rep["picture_only_pages"].append(i)
        page_dpi = dpi
        while math.ceil(pw * page_dpi / 72) * math.ceil(ph * page_dpi / 72) > max_page_pixels and page_dpi > 36:
            page_dpi -= 1
        if math.ceil(pw * page_dpi / 72) * math.ceil(ph * page_dpi / 72) > max_page_pixels:
            raise ConvertError("A page is too large to render safely, even at 36 dpi.")
        if page_dpi < dpi:
            rep["warnings"].append(f"Page {i}: background reduced to {page_dpi} dpi to stay within the pixel limit.")
        png = _picture(page, lines, page_dpi)
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(io.BytesIO(png), Emu(round(ox * EMU_PER_PT)), Emu(round(oy * EMU_PER_PT)), Emu(round(pw * s * EMU_PER_PT)), Emu(round(ph * s * EMU_PER_PT)))
        for r, spans in lines:
            # Extraction/redaction use unrotated PDF coordinates. The PNG uses
            # visible page coordinates. Rotate each editable line around its
            # transformed centre so it stays aligned with that PNG.
            visible = r * page.rotation_matrix
            cx, cy = (visible.x0 + visible.x1) / 2, (visible.y0 + visible.y1) / 2
            left, top = cx - r.width / 2, cy - r.height / 2
            tb = slide.shapes.add_textbox(Emu(round((ox + left * s) * EMU_PER_PT)), Emu(round((oy + top * s) * EMU_PER_PT)),
                                          Emu(round((r.width * s) * EMU_PER_PT)), Emu(round((r.height * s) * EMU_PER_PT)))
            tb.rotation = page.rotation
            tf = tb.text_frame
            tf.word_wrap = False; tf.auto_size = MSO_AUTO_SIZE.NONE; tf.vertical_anchor = MSO_ANCHOR.TOP
            tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
            p = tf.paragraphs[0]
            for sp in spans:
                if not sp["text"]: continue
                run = p.add_run(); run.text = sp["text"]
                f = run.font; f.name = family(sp["font"]); fonts.add(f.name)
                f.size = Pt(max(1.0, round(sp["size"] * s * 2) / 2))
                f.bold = bool(sp["flags"] & 16) or bool(re.search(r"bold|black|heavy", sp["font"], re.I))
                f.italic = bool(sp["flags"] & 2) or bool(re.search(r"italic|oblique", sp["font"], re.I))
                f.color.rgb = _rgb(sp["color"])
            rep["text_boxes"] += 1
    rep["fonts"] = sorted(fonts)
    if fonts: rep["warnings"].append("Fonts are not embedded: install " + ", ".join(sorted(fonts)[:6]) + (" and others" if len(fonts) > 6 else "") + " on the computer that opens the file, or PowerPoint substitutes another font.")
    out = io.BytesIO(); prs.save(out)
    doc.close()
    if out.tell() > max_pptx_mb * 1_000_000:
        raise ConvertError(f"The converted deck is bigger than {max_pptx_mb:g} MB.")
    return out.getvalue(), rep
