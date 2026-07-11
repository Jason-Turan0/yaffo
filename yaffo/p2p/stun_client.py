from __future__ import annotations

import os
import socket
import struct
from typing import Optional

# RFC 5389, minimal subset: encode a Binding Request, decode a Binding
# Success Response's (XOR-)MAPPED-ADDRESS. Pure codec, no socket I/O — the
# transport-owned socket (see quic_transport.PunchAwareQuicServer) can't do
# independent blocking reads, so sending/receiving lives there instead.
STUN_MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS_RESPONSE = 0x0101
XOR_MAPPED_ADDRESS = 0x0020
MAPPED_ADDRESS = 0x0001
IPV4_FAMILY = 0x01


class StunError(Exception):
    pass


def build_binding_request() -> tuple[bytes, bytes]:
    """Returns (request_bytes, transaction_id) — the caller must remember

    transaction_id to match it against the eventual response.
    """
    transaction_id = os.urandom(12)
    header = struct.pack(">HHI", BINDING_REQUEST, 0, STUN_MAGIC_COOKIE) + transaction_id
    return header, transaction_id


def binding_request_transaction_id(data: bytes) -> Optional[bytes]:
    """Returns the transaction ID if this datagram is a STUN Binding
    Request, else None — the server-side counterpart of transaction_id_of,
    used by the relay to answer STUN queries on its own port.
    """
    if len(data) < 20:
        return None
    msg_type, _ = struct.unpack(">HH", data[:4])
    magic = struct.unpack(">I", data[4:8])[0]
    if msg_type != BINDING_REQUEST or magic != STUN_MAGIC_COOKIE:
        return None
    return data[8:20]


def build_binding_response(transaction_id: bytes, addr: tuple[str, int]) -> bytes:
    """Binding Success Response carrying addr as XOR-MAPPED-ADDRESS."""
    xor_port = addr[1] ^ (STUN_MAGIC_COOKIE >> 16)
    cookie_bytes = struct.pack(">I", STUN_MAGIC_COOKIE)
    addr_bytes = bytes(a ^ b for a, b in zip(socket.inet_aton(addr[0]), cookie_bytes))
    attr_value = struct.pack(">BBH", 0, IPV4_FAMILY, xor_port) + addr_bytes
    attrs = struct.pack(">HH", XOR_MAPPED_ADDRESS, len(attr_value)) + attr_value
    header = struct.pack(">HHI", BINDING_SUCCESS_RESPONSE, len(attrs), STUN_MAGIC_COOKIE) + transaction_id
    return header + attrs


def transaction_id_of(data: bytes) -> Optional[bytes]:
    """Peek at a STUN message's transaction ID without fully validating it —

    used to route an incoming datagram to the future that's waiting for
    this specific response, before parse_binding_response does real
    validation.
    """
    if len(data) < 20:
        return None
    magic = struct.unpack(">I", data[4:8])[0]
    if magic != STUN_MAGIC_COOKIE:
        return None
    return data[8:20]


def parse_binding_response(data: bytes, expected_transaction_id: bytes) -> tuple[str, int]:
    if len(data) < 20:
        raise StunError("response shorter than a STUN header")

    msg_type, msg_length = struct.unpack(">HH", data[:4])
    magic = struct.unpack(">I", data[4:8])[0]
    resp_transaction_id = data[8:20]

    if magic != STUN_MAGIC_COOKIE:
        raise StunError("response missing the STUN magic cookie")
    if resp_transaction_id != expected_transaction_id:
        raise StunError("STUN transaction ID mismatch — response to a different request")
    if msg_type != BINDING_SUCCESS_RESPONSE:
        raise StunError(f"unexpected STUN message type: {msg_type:#06x}")

    attrs = data[20 : 20 + msg_length]
    offset = 0
    fallback: Optional[tuple[str, int]] = None
    while offset + 4 <= len(attrs):
        attr_type, attr_len = struct.unpack(">HH", attrs[offset : offset + 4])
        value = attrs[offset + 4 : offset + 4 + attr_len]
        if attr_type == XOR_MAPPED_ADDRESS:
            return _parse_xor_mapped_address(value)
        if attr_type == MAPPED_ADDRESS and fallback is None:
            fallback = _parse_mapped_address(value)
        offset += 4 + attr_len
        if attr_len % 4:
            offset += 4 - (attr_len % 4)  # STUN attributes are padded to a 4-byte boundary

    if fallback is not None:
        return fallback
    raise StunError("STUN response had no (XOR_)MAPPED_ADDRESS attribute")


def _parse_xor_mapped_address(value: bytes) -> tuple[str, int]:
    _, family, xor_port = struct.unpack(">BBH", value[:4])
    if family != IPV4_FAMILY:
        raise StunError(f"unsupported address family: {family:#04x} (only IPv4 handled)")
    port = xor_port ^ (STUN_MAGIC_COOKIE >> 16)
    cookie_bytes = struct.pack(">I", STUN_MAGIC_COOKIE)
    addr_bytes = bytes(a ^ b for a, b in zip(value[4:8], cookie_bytes))
    return socket.inet_ntoa(addr_bytes), port


def _parse_mapped_address(value: bytes) -> tuple[str, int]:
    _, family, port = struct.unpack(">BBH", value[:4])
    if family != IPV4_FAMILY:
        raise StunError(f"unsupported address family: {family:#04x} (only IPv4 handled)")
    return socket.inet_ntoa(value[4:8]), port
