"""Spawn entry point: read input, write deck, then publish the result."""
import json
from pathlib import Path

import convert


def run(job_dir: str, limits: dict):
    if limits.get("parent_death"):
        from cloud_worker import parent_death
        parent_death(limits["expected_parent"])
    directory = Path(job_dir)
    try:
        opts = {key: limits[key] for key in ('max_pages', 'max_page_pt', 'max_page_pixels', 'max_pptx_mb')}
        deck, report = convert.convert((directory / 'in.pdf').read_bytes(), **opts)
        (directory / 'out.pptx').write_bytes(deck)
        result = {'ok': True, 'report': report}
    except convert.ConvertError as exc:
        result = {'ok': False, 'error': str(exc)}
    except Exception:
        result = {'ok': False, 'error': 'The conversion stopped unexpectedly (it may have run out of memory or time).'}
    (directory / 'result.json').write_text(json.dumps(result), encoding='utf-8')
