"""Offline attack, deadline and resource regressions. No external requests."""
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import httpx
import pymupdf
from pptx import Presentation
import convert
import fetch


def pdf(pages=1, width=400, height=300):
    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page(width=width, height=height).insert_text((30, 60), 'Hello world')
        return doc.tobytes()


class Peer:
    def __init__(self, address='8.8.8.8'):
        self.address = address
    def get_extra_info(self, name):
        return (self.address, 443) if name == 'server_addr' else None


def reply(request, status=200, data=b'%PDF-test', headers=None, peer='8.8.8.8'):
    return httpx.Response(status, stream=httpx.ByteStream(data), headers=headers,
                          extensions={'network_stream': Peer(peer)} if peer else {})


class Downloader(unittest.TestCase):
    def download(self, handler, **kwargs):
        return fetch.fetch_pdf('https://pdf.example/a.pdf', resolver=lambda *_: ['8.8.8.8'],
                               transport=httpx.MockTransport(handler), **kwargs)

    def test_pinned_host_and_sni(self):
        def handler(request):
            self.assertEqual(request.url.host, '8.8.8.8')
            self.assertEqual(request.headers['host'], 'pdf.example')
            self.assertEqual(request.extensions['sni_hostname'], 'pdf.example')
            return reply(request)
        self.assertEqual(self.download(handler)[0], b'%PDF-test')

    def test_all_addresses_vetted_before_request(self):
        called = []
        with self.assertRaisesRegex(fetch.FetchError, 'private'):
            fetch.fetch_pdf('https://pdf.example/a', resolver=lambda *_: ['8.8.8.8', '127.0.0.1'],
                            transport=httpx.MockTransport(lambda r: called.append(r)))
        self.assertEqual(called, [])

    def test_redirect_private_never_sent(self):
        calls = []
        def handler(request):
            calls.append(request)
            return reply(request, 302, headers={'location': 'https://internal.example/x'})
        with self.assertRaisesRegex(fetch.FetchError, 'private'):
            fetch.fetch_pdf('https://pdf.example/a', resolver=lambda host, _: ['127.0.0.1' if host == 'internal.example' else '8.8.8.8'],
                            transport=httpx.MockTransport(handler))
        self.assertEqual(len(calls), 1)

    def test_redirect_cap(self):
        calls = []
        def handler(request):
            calls.append(request)
            return reply(request, 302, headers={'location': '/next'})
        with self.assertRaisesRegex(fetch.FetchError, 'Too many redirects'):
            self.download(handler)
        self.assertEqual(len(calls), 4)

    def test_malformed_inputs(self):
        for url in ['https://example.com:abc/a', 'https://[abc/', 'https://'+'a'*64+'.com/a']:
            with self.subTest(url=url), self.assertRaises(fetch.FetchError):
                fetch.fetch_pdf(url)
        for length in ['abc', '-1', '9'*5000]:
            with self.subTest(length=length[:10]), self.assertRaisesRegex(fetch.FetchError, 'invalid file size'):
                self.download(lambda r: reply(r, headers={'content-length': length}))

    def test_embedded_ipv4(self):
        for address in ['64:ff9b::a9fe:a9fe', '2002:a9fe:a9fe::', '::ffff:127.0.0.1', '64:ff9b:1::1', '2001::1']:
            self.assertFalse(fetch._public(address), address)
        self.assertTrue(fetch._public('2606:4700:4700::1111'))

    def test_size_magic_and_peer(self):
        for handler, options, message in [
            (lambda r: reply(r, headers={'content-length': '1001'}), {'max_bytes': 1000}, 'bigger than'),
            (lambda r: reply(r, data=b'%PDF-'+b'x'*1001), {'max_bytes': 1000}, 'bigger than'),
            (lambda r: reply(r, data=b'html'), {}, 'did not give a PDF'),
            (lambda r: reply(r, peer='127.0.0.1'), {}, 'private'),
            (lambda r: reply(r, peer=None), {}, 'private'),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(fetch.FetchError, message):
                self.download(handler, **options)
        self.assertEqual(self.download(lambda r: reply(r, data=b'junk%PDF-test'))[0], b'junk%PDF-test')

    def test_deadline_headers_and_dns(self):
        async def headers(request):
            await asyncio.sleep(1)
            return reply(request)
        start = time.monotonic()
        with self.assertRaisesRegex(fetch.FetchError, 'took too long'):
            self.download(headers, timeout_s=0.03)
        self.assertLess(time.monotonic()-start, 0.5)
        def resolver(*_):
            time.sleep(1)
            return ['8.8.8.8']
        start = time.monotonic()
        with self.assertRaisesRegex(fetch.FetchError, 'took too long'):
            fetch.fetch_pdf('https://pdf.example/a', resolver=resolver, timeout_s=0.03)
        self.assertLess(time.monotonic()-start, 0.5)

    def test_clock_deadline_across_hops(self):
        tick = [0]
        def clock():
            tick[0] += 1
            return tick[0]
        with self.assertRaisesRegex(fetch.FetchError, 'took too long'):
            self.download(lambda r: reply(r, 302, headers={'location': '/again'}), clock=clock, timeout_s=4)

    def test_address_fallback_and_public_ipv6(self):
        seen = []
        def handler(request):
            seen.append(request.url.host)
            if len(seen) == 1:
                raise httpx.ConnectError('test')
            return reply(request, peer='2606:4700:4700::1111')
        fetch.fetch_pdf('https://pdf.example/a', resolver=lambda *_: ['8.8.8.8', '2606:4700:4700::1111'], transport=httpx.MockTransport(handler))
        self.assertEqual(seen, ['8.8.8.8', '2606:4700:4700::1111'])


class Limits(unittest.TestCase):
    def test_pages_every_page_dimensions_and_output(self):
        cases = [(pdf(3), {'max_pages': 2}, 'The PDF has 3 pages; the limit is 2.'),
                 (pdf(width=14400, height=14400), {}, 'A page is 14400 x 14400 points; the limit is 4000.'),
                 (pdf(), {'max_pptx_mb': 0.001}, 'The converted deck is bigger than 0.001 MB.')]
        for source, options, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(convert.ConvertError, message.replace('.', r'\.')):
                convert.convert(source, **options)
        with pymupdf.open(stream=pdf(), filetype='pdf') as doc:
            doc.new_page(width=5000, height=400)
            with self.assertRaisesRegex(convert.ConvertError, '5000 x 400'):
                convert.convert(doc.tobytes())

    def test_pixel_limit(self):
        deck, report = convert.convert(pdf(), max_page_pixels=100_000)
        prs = Presentation(io.BytesIO(deck))
        picture = prs.slides[0].shapes[0]
        width, height = picture.image.size
        self.assertLessEqual(width*height, 100_000)
        self.assertTrue(any('reduced' in w for w in report['warnings']))
        with self.assertRaisesRegex(convert.ConvertError, '36 dpi'):
            convert.convert(pdf(), max_page_pixels=1)

    def test_redaction_preserves_overlapping_rotated_text(self):
        with pymupdf.open() as doc:
            page=doc.new_page(width=400,height=300)
            page.insert_text((50,100),'Horizontal line',fontsize=20)
            page.insert_text((90,130),'ROTATED',fontsize=20,rotate=90)
            lines, skipped=convert._lines(page)
            convert._picture(page,lines,36)
            self.assertIn('ROTATED',page.get_text())
            self.assertTrue(skipped)

    def test_page_rotation(self):
        for angle in (90, 180, 270):
            with pymupdf.open(stream=pdf(), filetype='pdf') as doc:
                page=doc[0];page.set_rotation(angle)
                rectangle=convert._lines(page)[0][0][0]
                visible=rectangle * page.rotation_matrix
                deck,_=convert.convert(doc.tobytes())
                box=Presentation(io.BytesIO(deck)).slides[0].shapes[1]
                self.assertEqual(box.rotation, angle)
                self.assertAlmostEqual((box.left+box.width/2)/12700, (visible.x0+visible.x1)/2, places=3)
                self.assertAlmostEqual((box.top+box.height/2)/12700, (visible.y0+visible.y1)/2, places=3)
                convert._picture(page,convert._lines(page)[0],36)
                self.assertEqual(page.get_text().strip(),'')

    def test_empty_public_warning(self):
        env = os.environ | {'PYTHONPATH': str(ROOT/'app'), 'API_TOKEN': 't'*32, 'PUBLIC_URL': ''}
        result = subprocess.run([sys.executable, '-c', 'import main'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PUBLIC_URL is empty', result.stderr)
        self.assertNotIn('t'*32, result.stderr)

    def test_atomic_records_and_timeout_and_recovery(self):
        os.environ.setdefault('API_TOKEN', 't'*32)
        import jobs
        with tempfile.TemporaryDirectory() as directory, patch.object(jobs, 'DATA', Path(directory)/'jobs'):
            jobs.DATA.mkdir()
            jid='a'*12
            jobs._p(jid).mkdir()
            jobs._write(jid, {'job_id': jid, 'state': 'running'})
            failures=[]
            def reader():
                for _ in range(500):
                    if jobs.get(jid) is None:
                        failures.append(True)
            thread=threading.Thread(target=reader); thread.start()
            for _ in range(50):
                jobs._write(jid, {'job_id': jid, 'state': 'running'})
            thread.join(); self.assertEqual(failures, [])
            with patch.object(jobs, 'MAX_ACTIVE', 1), self.assertRaises(jobs.TooManyJobs):
                jobs.start('https://fixture.example/x')
            (jobs._p(jid)/'in.pdf').write_bytes(b'leftover')
            jobs.recover()
            self.assertEqual(jobs.get(jid)['state'], 'failed')
            self.assertFalse((jobs._p(jid)/'in.pdf').exists())
            with patch.object(jobs.fetch, 'fetch_pdf', return_value=(pdf(), 'normal.pdf')):
                timeout_id = jobs.start('https://fixture.example/x', limits={'convert_timeout_s': 0.001})
                for _ in range(150):
                    record = jobs.get(timeout_id)
                    if record['state'] in ('done', 'failed'):
                        break
                    time.sleep(.02)
            self.assertEqual(record['state'], 'failed')
            self.assertIn('was stopped', record['error'])
            self.assertFalse((jobs._p(timeout_id)/'in.pdf').exists())
            self.assertFalse((jobs._p(timeout_id)/'out.pptx').exists())
            record['finished_at']=time.time()-86401; jobs._write(timeout_id, record)
            jobs._clean(); self.assertIsNone(jobs.get(timeout_id))
            with patch.object(jobs, 'MAX_DATA_MB', .000001), self.assertRaisesRegex(jobs.ServiceFull, 'service is full'):
                jobs.start('https://fixture.example/x')


if __name__ == '__main__':
    unittest.main()
