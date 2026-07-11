"""Peer-to-peer device sharing engine (docs/development/p2p-sharing.md).

Identity is a keypair, not an account: each install generates an ECDSA P-256
keypair whose hash is its device ID, trust between two devices is established
once by a human-confirmed pairing code (TOFU, like SSH host keys), and every
exchange runs over pinned QUIC — relayed through the hub first, upgraded to a
direct path when a hole punch lands. The hub (hub/, deployed separately) only
forwards ciphertext and plays no role in any trust decision.

The engine runs as an asyncio loop in a daemon thread inside the web process
(see service.P2PService); Flask routes call into it thread-safely.
"""
