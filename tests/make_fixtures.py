"""Write synthetic, non-sensitive acceptance fixtures; never publishes them."""
import argparse
import io
import json
from pathlib import Path
import sys
import time

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import convert


def document(pages=1, width=1440, height=810, hidden=False, picture_only=False, dense=False):
    image = Image.new('RGB', (300, 180))
    image.putdata([(x*255//300, y*255//180, 130) for y in range(180) for x in range(300)])
    png=io.BytesIO();image.save(png,format='PNG')
    doc=pymupdf.open()
    for number in range(pages):
        page=doc.new_page(width=width,height=height)
        page.draw_rect(page.rect, fill=(.05,.15,.12), color=None)
        page.insert_image(pymupdf.Rect(700,250,1000,430),stream=png.getvalue())
        for n in range(4):
            page.draw_rect(pymupdf.Rect(100+n*100,500-n*40,160+n*100,700), fill=(.1,.8,.4),color=None)
        if not picture_only:
            page.insert_text((80,120), f'Slide {number+1}',fontsize=48,fontname='hebo',color=(1,1,1),render_mode=3 if hidden else 0)
            page.insert_text((80,200), 'Editable body text',fontsize=24,fontname='helv',color=(1,1,1),render_mode=3 if hidden else 0)
            if dense:
                for y in range(250,750,15):
                    page.insert_text((80,y), 'Dense conversion fixture '*5,fontsize=10,color=(1,1,1))
    return doc


def write(directory: Path, calibrate=False):
    directory.mkdir(parents=True,exist_ok=True)
    normal=document(3);normal.save(directory/'normal.pdf')
    rotated=document();rotated[0].set_rotation(90);rotated.save(directory/'rotated.pdf')
    document(hidden=True).save(directory/'scan_hidden.pdf')
    document(picture_only=True).save(directory/'scan_plain.pdf')
    locked=document();locked.save(directory/'locked.pdf',encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw='fixture-owner',user_pw='fixture-reader')
    (directory/'page.html').write_text('<html>This is not a PDF.</html>')
    (directory/'big.pdf').write_bytes(normal.tobytes()+b'\n%'+b' '*50_000_001+b'\n')
    many=pymupdf.open()
    for _ in range(201):many.new_page().insert_text((20,30),'Page limit fixture')
    many.save(directory/'many_pages.pdf')
    huge=pymupdf.open();huge.new_page(width=14400,height=14400);huge.save(directory/'huge_page.pdf')
    pages=10
    metadata={'slow_calibrated':False}
    if calibrate:
        sample=document(2,dense=True).tobytes();start=time.monotonic();convert.convert(sample)
        seconds_per_page=(time.monotonic()-start)/2
        pages=max(2,min(180,int(12/max(seconds_per_page,.001))))
        slow=document(pages,dense=True).tobytes();start=time.monotonic();convert.convert(slow);duration=time.monotonic()-start
        if not 5 <= duration <= 100:
            raise RuntimeError(f'Slow fixture calibration outside 5–100 seconds: {duration:.2f}; do not use for live restart tests.')
        metadata={'slow_calibrated':True,'slow_seconds':round(duration,3),'slow_pages':pages}
    else:slow=document(pages,dense=True).tobytes()
    (directory/'slow.pdf').write_bytes(slow)
    (directory/'fixtures.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps(metadata))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    parser.add_argument('--calibrate-slow',action='store_true')
    args=parser.parse_args();write(args.directory,args.calibrate_slow)
