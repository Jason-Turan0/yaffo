from __future__ import annotations

import pytest

from yaffo.p2p.transfers import TransferAborted, TransferBatch, TransferFile, TransferManager

pytestmark = pytest.mark.unit


def _batch(tmp_path, sizes: list[int]) -> TransferBatch:
    return TransferBatch(
        id="demo-batch",
        peer_device_id="peer",
        peer_name="Family Mac",
        media_dir_id="media",
        scope="Trips/Chicago",
        label="Chicago Weekend",
        filters={},
        destination_root=tmp_path,
        collection_path="Trips/Chicago",
        files=[
            TransferFile(
                media_item_id=index,
                relative_path=f"photo-{index}.jpg",
                name=f"photo-{index}.jpg",
                size=size,
                mtime=1.0,
            )
            for index, size in enumerate(sizes, start=1)
        ],
    )


def test_demo_transfer_rejects_too_many_files(monkeypatch, tmp_path):
    monkeypatch.setattr("yaffo.p2p.transfers.demo_mode_enabled", lambda: True)
    monkeypatch.setattr("yaffo.p2p.transfers.DEMO_TRANSFER_MAX_FILES", 2)
    manager = TransferManager(object())

    with pytest.raises(TransferAborted, match="limited to 2 files"):
        manager._validate_demo_batch(_batch(tmp_path, [1, 1, 1]))


def test_demo_transfer_rejects_batch_byte_overage(monkeypatch, tmp_path):
    monkeypatch.setattr("yaffo.p2p.transfers.demo_mode_enabled", lambda: True)
    monkeypatch.setattr("yaffo.p2p.transfers.DEMO_TRANSFER_MAX_FILES", 5)
    monkeypatch.setattr("yaffo.p2p.transfers.DEMO_TRANSFER_MAX_BATCH_BYTES", 10)
    manager = TransferManager(object())

    with pytest.raises(TransferAborted, match="transfer-size limit"):
        manager._validate_demo_batch(_batch(tmp_path, [6, 5]))


def test_demo_transfer_counts_existing_downloads_against_volume(monkeypatch, tmp_path):
    monkeypatch.setattr("yaffo.p2p.transfers.demo_mode_enabled", lambda: True)
    monkeypatch.setattr("yaffo.p2p.transfers.DEMO_TRANSFER_MAX_FILES", 5)
    monkeypatch.setattr("yaffo.p2p.transfers.DEMO_TRANSFER_MAX_BATCH_BYTES", 100)
    monkeypatch.setattr("yaffo.p2p.transfers.DEMO_DOWNLOAD_VOLUME_BYTES", 10)
    (tmp_path / "already-downloaded.jpg").write_bytes(b"123456")
    manager = TransferManager(object())

    with pytest.raises(TransferAborted, match="reached its quota"):
        manager._validate_demo_batch(_batch(tmp_path, [5]))
