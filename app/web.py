"""Single-page HTML import with DNS pinning, public-IP checks and bounded redirects."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin, urlunsplit, quote
from bs4 import BeautifulSoup
from app.documents import DocumentError, chunk_pages


def validate_url(url: str) -> tuple[str, str, int, str]:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in {80, 443} or any(ord(c) < 32 for c in url):
            raise ValueError()
        path = urlunsplit(("", "", quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"), quote(parts.query, safe="%/:?@!$&'()*+,;=-._~"), ""))
        return parts.scheme, host, port, path
    except (ValueError, UnicodeError) as exc:
        raise DocumentError("Нужна публичная HTTP/HTTPS-ссылка без пароля, на порт 80 или 443.") from exc


def public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise DocumentError("Не удалось определить адрес сайта.") from exc
    if not addresses:
        raise DocumentError("Адрес сайта не найден.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None):
            raise DocumentError("Локальные, служебные и приватные адреса запрещены.")
    return addresses


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, tls: bool):
        super().__init__(host, port, timeout=15)
        self.address = address
        self.tls = tls

    def connect(self):
        # Connect to the already validated IP, never resolve the hostname again.
        sock = socket.create_connection((self.address, self.port), self.timeout)
        if self.tls:
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            except Exception:
                sock.close()
                raise
        self.sock = sock


def fetch_page(url: str, max_bytes: int = 5 * 1024 * 1024) -> tuple[str, str, list]:
    current = url.strip()
    for redirect in range(6):
        scheme, host, port, path = validate_url(current)
        address = public_addresses(host, port)[0]
        connection = PinnedConnection(host, port, address, scheme == "https")
        try:
            connection.request("GET", path, headers={"User-Agent": "DocumentRAG/1.0", "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect == 5:
                    raise DocumentError("Слишком много перенаправлений или некорректная ссылка.")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise DocumentError(f"Сайт вернул HTTP {response.status}.")
            content_type = response.getheader("Content-Type", "").lower()
            if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml+xml")):
                raise DocumentError("Ссылка должна вести на HTML-страницу или текст, не на файл.")
            if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise DocumentError("Сайт вернул неподдерживаемое сжатие.")
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise DocumentError("Страница слишком большая (лимит 5 МБ).")
        except DocumentError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise DocumentError("Не удалось загрузить сайт. Проверьте ссылку и доступность страницы.") from exc
        finally:
            connection.close()
        if "text/plain" in content_type:
            return host, current, chunk_pages([(None, data.decode("utf-8-sig", errors="replace"))])
        soup = BeautifulSoup(data, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else host
        for element in soup.select("script,style,noscript,nav,footer,header,aside,form,svg"):
            element.decompose()
        body = soup.find("main") or soup.find("article") or soup.body or soup
        text = body.get_text(" ", strip=True)
        return title[:200], current, chunk_pages([(None, text)])
    raise DocumentError("Не удалось загрузить страницу.")
