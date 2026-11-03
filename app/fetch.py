"""Download only from vetted public addresses, with a single overall deadline."""
import asyncio
import concurrent.futures
import ipaddress
import logging
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import httpx

# httpx's INFO logs include full URLs, which can contain private link signatures.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
_DNS_SLOTS = threading.BoundedSemaphore(20)


class FetchError(ValueError):
    pass


def _public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        if a.version == 6:
            embedded = a.ipv4_mapped or a.sixtofour
            if a in ipaddress.ip_network('64:ff9b::/96'):
                embedded = ipaddress.IPv4Address(int(a) & 0xffffffff)
            # Local-use translation, Teredo and scoped literals are unnecessary here.
            if a in ipaddress.ip_network('64:ff9b:1::/48') or a.teredo or '%' in ip:
                return False
            if embedded and not _public(str(embedded)):
                return False
        return a.is_global and not a.is_multicast
    except ValueError:
        return False


def _check_url(url: str, allow_private: bool):
    if len(url) > 2000:
        raise FetchError('The link is too long.')
    try:
        u = urlsplit(url.strip())
        port = u.port
        host = u.hostname
    except ValueError:
        raise FetchError('The link has an invalid host name or port.') from None
    if u.scheme != 'https' and not (allow_private and u.scheme == 'http'):
        raise FetchError('Only https links are allowed.')
    if not host:
        raise FetchError('The link has no host name.')
    if u.username is not None or u.password is not None:
        raise FetchError('Links with a user name or password are not allowed.')
    if not allow_private and port not in (None, 443):
        raise FetchError('Only the standard https port is allowed.')
    return u


def _resolve(host, port):
    return list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


async def _dns(resolver, host, port):
    # A timed-out getaddrinfo cannot be killed. Bound the number of lingering
    # daemon threads; do not let asyncio.run wait for them at shutdown.
    if not _DNS_SLOTS.acquire(blocking=False):
        raise FetchError('Host name lookup is busy. Try again later.')
    future = concurrent.futures.Future()
    def run():
        try:
            result = resolver(host, port)
            if not future.cancelled():
                future.set_result(result)
        except Exception as exc:
            if not future.cancelled():
                future.set_exception(exc)
        finally:
            _DNS_SLOTS.release()
    threading.Thread(target=run, daemon=True).start()
    try:
        return await asyncio.wrap_future(future)
    except (OSError, UnicodeError, ValueError):
        raise FetchError("The link's host name could not be found.") from None


async def _fetch(url, max_bytes, timeout_s, allow_private, resolver, transport, clock):
    deadline = clock() + timeout_s
    cur = url.strip()
    def remaining():
        left = deadline - clock()
        if left <= 0:
            raise FetchError('The download took too long.')
        return left
    try:
        # This also interrupts a connection or a slow stream of response headers.
        async with asyncio.timeout(timeout_s):
            async with httpx.AsyncClient(trust_env=False, transport=transport,
                                        headers={'User-Agent': 'pdf-to-pptx/1', 'Accept': 'application/pdf', 'Accept-Encoding': 'identity'}) as client:
                for hop in range(4):
                    u = _check_url(cur, allow_private)
                    addresses = await _dns(resolver, u.hostname, u.port or (443 if u.scheme == 'https' else 80))
                    remaining()
                    if not addresses or (not allow_private and not all(_public(ip) for ip in addresses)):
                        raise FetchError('That link points to a private or internal address, which is not allowed.')
                    response = None
                    for address in addresses:
                        # No second name lookup: httpcore connects to this literal IP.
                        original = httpx.URL(cur)
                        pinned = original.copy_with(host=address)
                        request = client.build_request('GET', pinned, headers={'Host': original.netloc.decode('ascii')},
                                                       timeout=remaining(), extensions={'sni_hostname': original.host})
                        try:
                            response = await client.send(request, stream=True, follow_redirects=False)
                            break
                        except (httpx.ConnectError, httpx.ConnectTimeout):
                            if address == addresses[-1]:
                                raise
                            remaining()
                    try:
                        stream = response.extensions.get('network_stream')
                        peer = stream.get_extra_info('server_addr') if stream else None
                        if not allow_private and (not peer or not _public(peer[0]) or
                                                  ipaddress.ip_address(peer[0]) != ipaddress.ip_address(address)):
                            raise FetchError('That link points to a private or internal address, which is not allowed.')
                        remaining()
                        if response.status_code in (301, 302, 303, 307, 308):
                            if hop == 3:
                                raise FetchError('Too many redirects.')
                            location = response.headers.get('location')
                            if not location:
                                raise FetchError('The link redirects nowhere.')
                            cur = urljoin(cur, location)
                            continue
                        if response.status_code != 200:
                            raise FetchError(f'The link answered HTTP {response.status_code}.')
                        length = response.headers.get('content-length')
                        if length is not None and (not length.isascii() or not length.isdecimal() or len(length) > 20):
                            raise FetchError('The link gave an invalid file size.')
                        if length and int(length) > max_bytes:
                            raise FetchError(f'The file is bigger than {max_bytes / 1_000_000:g} MB.')
                        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                            raise FetchError('The link gave a compressed response. Use a direct PDF link.')
                        buf = bytearray()
                        async for chunk in response.aiter_raw(65536):
                            remaining()
                            if len(buf) + len(chunk) > max_bytes:
                                raise FetchError(f'The file is bigger than {max_bytes / 1_000_000:g} MB.')
                            buf.extend(chunk)
                        remaining()
                        if b'%PDF-' not in buf[:1024]:
                            raise FetchError('The link did not give a PDF file.')
                        return bytes(buf), u.path.rsplit('/', 1)[-1] or 'document.pdf'
                    finally:
                        await response.aclose()
    except (TimeoutError, httpx.TimeoutException):
        raise FetchError('The download took too long.') from None
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        if isinstance(exc, FetchError):
            raise
        raise FetchError('The download failed. Check the PDF link and try again.') from None


def fetch_pdf(url: str, max_bytes: int = 50_000_000, timeout_s: float = 60,
              allow_private: bool = False, *, resolver=None, transport=None, clock=None) -> tuple[bytes, str]:
    """Return PDF bytes and filename. allow_private is a local test seam only."""
    _check_url(url, allow_private)
    return asyncio.run(_fetch(url, max_bytes, timeout_s, allow_private,
                              resolver or _resolve, transport, clock or time.monotonic))
