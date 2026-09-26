from pathlib import Path

import pytest

from yaffo.site_agents.assistant.redact import EMAIL_MASK, MASK, Redactor

pytestmark = pytest.mark.unit


@pytest.fixture
def redact():
    return Redactor(home=Path("/Users/alex"))


def test_home_folder_becomes_tilde(redact):
    assert redact("Error reading /Users/alex/Pictures/2017/a.jpg") == "Error reading ~/Pictures/2017/a.jpg"
    # Only the home folder itself, not a longer name that starts with it.
    assert redact("/Users/alexandra/x") == "/Users/alexandra/x"


@pytest.mark.parametrize("secret", [
    # Fake keys, assembled at runtime so the secret-scanning pre-commit hook
    # doesn't flag the source.
    "-".join(["sk", "ant", "api03", "abcdefghijklmnopqrstuvwxyz0123"]),
    "-".join(["sk", "proj", "ABCDEFGHIJKLMNOPQRSTUV"]),
    "AIza" + "SyA1234567890abcdefghijklmnopqrstu",
    "ghp" + "_abcdefghijklmnopqrstuvwxyz0123456789",
])
def test_key_like_tokens_are_masked(redact, secret):
    assert redact(f"auth failed for {secret} today") == f"auth failed for {MASK} today"


def test_secret_assignments_and_bearer_tokens_are_masked(redact):
    assert redact('api_key="abcd1234efgh"') == f'api_key="{MASK}"'
    assert redact("password: hunter22") == f"password: {MASK}"
    assert redact("Authorization: Bearer abc.def.ghijklmnop") == f"Authorization: Bearer {MASK}"


def test_emails_are_masked(redact):
    assert redact("sent to jane.doe@example.com ok") == f"sent to {EMAIL_MASK} ok"


def test_gps_is_rounded(redact):
    assert redact("at 44.428013, -110.588455 in the park") == "at 44.4, -110.6 in the park"
    assert redact('{"latitude": 44.428013, "longitude": -110.58}') == '{"latitude": 44.4, "longitude": -110.6}'
    # Versions and short decimals are left alone.
    assert redact("Python 3.13.1, took 1.25s") == "Python 3.13.1, took 1.25s"


def test_people_names_are_optional():
    plain = Redactor(home=Path("/h"))
    assert plain("Chase is in 12 photos") == "Chase is in 12 photos"
    named = Redactor(home=Path("/h"), people={3: "Chase", 4: "Chase Turan", 5: "A"})
    assert named("chase and Chase Turan; Chasen") == "Person #3 and Person #4; Chasen"
