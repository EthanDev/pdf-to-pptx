"""Validated settings. MB always means 1,000,000 bytes."""
import math
import os


def number(name, default, integer=False):
    try:
        value = int(os.getenv(name, default)) if integer else float(os.getenv(name, default))
    except ValueError:
        raise SystemExit(f'{name} must be a positive number.') from None
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(f'{name} must be a positive number.')
    return value


LIMITS = {
    'max_pdf_mb': number('MAX_PDF_MB', 50),
    'max_pages': number('MAX_PAGES', 200, True),
    'max_page_pt': number('MAX_PAGE_PT', 4000),
    'max_page_pixels': number('MAX_PAGE_PIXELS', 20_000_000, True),
    'max_pptx_mb': number('MAX_PPTX_MB', 300),
    'convert_timeout_s': number('CONVERT_TIMEOUT_S', 120),
}
MAX_DATA_MB = number('MAX_DATA_MB', 2000)
MAX_ACTIVE = number('MAX_ACTIVE_JOBS', 20, True)
WORKERS = number('WORKERS', 2, True)
