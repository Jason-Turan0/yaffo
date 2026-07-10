from p2p_poc.identity import load_or_create_identity


def test_device_id_is_stable_across_reloads(tmp_path):
    first = load_or_create_identity(tmp_path)
    second = load_or_create_identity(tmp_path)
    assert first.device_id == second.device_id
    assert first.public_key_b64 == second.public_key_b64


def test_different_data_dirs_get_different_identities(tmp_path):
    device_a = load_or_create_identity(tmp_path / "a")
    device_b = load_or_create_identity(tmp_path / "b")
    assert device_a.device_id != device_b.device_id
