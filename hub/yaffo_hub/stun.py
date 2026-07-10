from __future__ import annotations

import socket
import struct
from typing import Optional

# RFC 5389, server side only: recognize a Binding Request, answer with the
# observed source address as XOR-MAPPED-ADDRESS. The relay port answers STUN
# itself because it already sees each peer's NAT-mapped address — no
# third-party STUN dependency. (Codec kept identical to the device side's
# stun_client.py; the hub only ever needs the responder half.)

STUN_MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS_RESPONSE = 0x0101
XOR_MAPPED_ADDRESS = 0x0020
IPV4_FAMILY = 0x01


def binding_request_transaction_id(data: bytes) -> Optional[bytes]:
    """Returns the transaction ID if this datagram is a STUN Binding Request,
    else None."""
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
