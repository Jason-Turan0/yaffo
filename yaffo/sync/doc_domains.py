"""Registrable domains (eTLD+1), from the bundled Public Suffix List snapshot.

The service builder's web access is authorized per registrable domain: allowing
`flickr.com` covers `www.flickr.com` and `api.flickr.com`, while `alice.github.io` and
`bob.github.io` are different domains because `github.io` is a public suffix. The
decision has to be the same every time for the same host, so the list is a snapshot
shipped with the app (data/public_suffix_list.dat, MPL-2.0), never fetched at run time.

The matching follows publicsuffix.org's algorithm: the longest matching rule wins, a
`*.` rule matches any one label, an exception rule (`!`) wins over everything and
means its own name is registrable, and with no matching rule the suffix is the last
label.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from yaffo.common import BUNDLE_ROOT


def _list_path() -> Path:
    here = Path(__file__).resolve().parent / "data" / "public_suffix_list.dat"
    return here if here.is_file() else BUNDLE_ROOT / "yaffo" / "sync" / "data" / "public_suffix_list.dat"


@lru_cache(maxsize=1)
def _rules() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(plain rules, wildcard parents, exceptions), lowercased, IDNs as punycode."""
    plain, wildcards, exceptions = set(), set(), set()
    for raw in _list_path().read_text(encoding="utf-8").splitlines():
        line = raw.strip().split(" ", 1)[0]
        if not line or line.startswith("//"):
            continue
        line = line.encode("idna").decode("ascii").lower() if not line.isascii() else line.lower()
        if line.startswith("!"):
            exceptions.add(line[1:])
        elif line.startswith("*."):
            wildcards.add(line[2:])
        else:
            plain.add(line)
    return frozenset(plain), frozenset(wildcards), frozenset(exceptions)


def _ascii(host: str) -> str | None:
    host = host.strip().rstrip(".").lower()
    if not host:
        return None
    try:
        return host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        return None


def public_suffix(host: str) -> str | None:
    host = _ascii(host)
    if host is None:
        return None
    plain, wildcards, exceptions = _rules()
    labels = host.split(".")
    for i in range(len(labels)):
        candidate = ".".join(labels[i:])
        if candidate in exceptions:
            return ".".join(labels[i + 1:])
        if candidate in plain:
            return candidate
        parent = ".".join(labels[i + 1:])
        if i + 1 < len(labels) and parent in wildcards:
            return candidate
    return labels[-1]


def registrable_domain(host: str | None) -> str | None:
    """The host's registrable domain, or None for an IP address, a bare public
    suffix, or a name that isn't a proper host."""
    if not host:
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        pass
    ascii_host = _ascii(host)
    if ascii_host is None or ".." in ascii_host or not all(ascii_host.split(".")):
        return None
    suffix = public_suffix(ascii_host)
    if suffix is None or ascii_host == suffix:
        return None
    rest = ascii_host[: -len(suffix) - 1].split(".")
    return f"{rest[-1]}.{suffix}"
