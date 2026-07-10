from yaffo_hub.auth import device_id_from_pubkey, new_challenge_nonce, verify_auth


def test_device_id_format_and_stability(device):
    device_id = device.device_id
    groups = device_id.split("-")
    assert len(groups) == 4 and all(len(g) == 4 for g in groups)
    assert device_id_from_pubkey(device.pubkey_bytes) == device_id


def test_valid_auth_accepted(device):
    nonce = new_challenge_nonce()
    assert verify_auth(device.device_id, device.pubkey_b64, nonce, device.sign_nonce(nonce))


def test_claimed_device_id_must_match_pubkey_hash(device, other_device):
    """Device-ID squatting: presenting a real key + valid signature but
    claiming someone else's ID must fail — IDs are self-authenticating."""
    nonce = new_challenge_nonce()
    assert not verify_auth(other_device.device_id, device.pubkey_b64, nonce, device.sign_nonce(nonce))


def test_signature_must_be_by_the_presented_key(device, other_device):
    nonce = new_challenge_nonce()
    assert not verify_auth(device.device_id, device.pubkey_b64, nonce, other_device.sign_nonce(nonce))


def test_signature_over_a_different_nonce_rejected(device):
    """Replay: a signature captured for one challenge is useless for another."""
    nonce, stale_nonce = new_challenge_nonce(), new_challenge_nonce()
    assert not verify_auth(device.device_id, device.pubkey_b64, nonce, device.sign_nonce(stale_nonce))


def test_garbage_inputs_rejected_not_crashing(device):
    nonce = new_challenge_nonce()
    assert not verify_auth(device.device_id, "not-base64!!", nonce, device.sign_nonce(nonce))
    assert not verify_auth(device.device_id, device.pubkey_b64, nonce, "not-base64!!")
    assert not verify_auth("", "", nonce, "")
