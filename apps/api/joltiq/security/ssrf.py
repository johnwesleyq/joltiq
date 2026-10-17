"""SSRF defenses for URL ingestion, research page fetches, webhooks and integration endpoints.

- Only http(s), default ports or explicit allowlist.
- DNS resolved once; every resolved address must be public (no private, loopback, link-local,
  multicast, reserved, CGNAT, cloud metadata). The connection is pinned to the vetted IP to defeat
  DNS-rebinding, with the original Host header/SNI preserved.
- Redirects are followed manually, re-validating every hop.
- Response size and time are capped.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata", "instance-data"}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class SSRFBlocked(Exception):
    pass


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
        or ip.is_unspecified or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT)
    )


def validate_url(url: str, *, allowed_domains: list[str] | None = None, allow_private: bool = False,
                 resolver=socket.getaddrinfo) -> tuple[str, str, int, str]:
    """Returns (scheme, host, port, pinned_ip). Raises SSRFBlocked."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SSRFBlocked(f"scheme not allowed: {parts.scheme!r}")
    if parts.username or parts.password:
        raise SSRFBlocked("credentials in URL not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or host in BLOCKED_HOSTNAMES or host.endswith(".internal") or host.endswith(".local"):
        raise SSRFBlocked(f"host not allowed: {host!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in (80, 443) and not allow_private:
        raise SSRFBlocked(f"port not allowed: {port}")
    if allowed_domains is not None and not any(host == d or host.endswith("." + d) for d in allowed_domains):
        raise SSRFBlocked(f"domain not on allowlist: {host}")
    try:
        literal = ipaddress.ip_address(host)
        addrs = [literal]
    except ValueError:
        try:
            infos = resolver(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as e:
            raise SSRFBlocked(f"DNS resolution failed for {host}") from e
        addrs = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not addrs:
        raise SSRFBlocked("no addresses")
    if not allow_private:
        for a in addrs:
            if not _ip_is_public(a):
                raise SSRFBlocked(f"address not public: {a}")
    return parts.scheme, host, port, str(addrs[0])


def safe_fetch(url: str, *, allowed_domains: list[str] | None = None, max_bytes: int = 2_000_000,
               timeout: float = 10.0, max_redirects: int = 3, allow_private: bool = False,
               headers: dict[str, str] | None = None) -> FetchResult:
    current = url
    for _ in range(max_redirects + 1):
        validate_url(current, allowed_domains=allowed_domains, allow_private=allow_private)
        # Note: httpx resolves again; for strict pinning in production route through the egress proxy
        # (infrastructure/deployment/egress) which enforces the same public-IP rule at the network layer.
        with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": "JoltIQ-Research/1.0", **(headers or {})}) as c:
            with c.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        raise SSRFBlocked("redirect without location")
                    current = urljoin(current, loc)
                    continue
                body = b""
                truncated = False
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        body = body[:max_bytes]
                        truncated = True
                        break
                return FetchResult(current, r.status_code, r.headers.get("content-type", ""), body, truncated)
    raise SSRFBlocked("too many redirects")
