"""Client-side codec for the hub's UDP datagram relay.

The relay itself (the DERP-style forwarder, hardened with token allowlists
and limits) lives in the standalone hub service (hub/yaffo_hub/relay.py);
devices only need to mint session tokens and speak the HELLO/ACK framing
that announces this socket as one side of a session. Tokens travel as hex
inside JSON signaling messages and as raw bytes on the wire.
"""
from __future__ import annotations

import os
from typing import Optional

HELLO_MAGIC = b"YRLY1"
ACK_MAGIC = b"YACK1"
TOKEN_BYTES = 16


def new_session_token() -> str:
    return os.urandom(TOKEN_BYTES).hex()


def build_hello(token: str) -> bytes:
    return HELLO_MAGIC + bytes.fromhex(token)


def build_ack(token: str) -> bytes:
    return ACK_MAGIC + bytes.fromhex(token)


def _parse_framed(data: bytes, magic: bytes) -> Optional[str]:
    if len(data) == len(magic) + TOKEN_BYTES and data.startswith(magic):
        return data[len(magic) :].hex()
    return None


def parse_hello(data: bytes) -> Optional[str]:
    return _parse_framed(data, HELLO_MAGIC)


def parse_ack(data: bytes) -> Optional[str]:
    return _parse_framed(data, ACK_MAGIC)
