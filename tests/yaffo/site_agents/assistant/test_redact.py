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


def test_people_names_are_left_alone():
    assert Redactor(home=Path("/h"))("Chase is in 12 photos") == "Chase is in 12 photos"


def test_configured_folders_become_labels_keeping_the_path_inside():
    redact = Redactor(home=Path("/Users/alex"), roots={
        "data folder": Path("/Users/alex/Pictures"),
        "media folder m1": Path("/Users/alex/Pictures/family"),
        "media folder m2": Path("/Volumes/Photos"),
    })
    # The longest root wins, so a media folder inside the data folder keeps its label.
    assert redact("Could not read /Users/alex/Pictures/family/2019/a.HEIC") == \
        "Could not read [media folder m1]/2019/a.HEIC"
    assert redact("/Volumes/Photos/b.jpg and /Users/alex/Pictures/yaffo.log") == \
        "[media folder m2]/b.jpg and [data folder]/yaffo.log"
    # Whole folder names only; the home folder still covers everything else.
    assert redact("/Volumes/Photos2/c") == "/Volumes/Photos2/c"
    assert redact("/Users/alex/Desktop/z") == "~/Desktop/z"


def test_a_symlinked_folder_matches_as_configured_and_resolved(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    redact = Redactor(home=Path("/nowhere"), roots={"media folder m1": link})
    assert redact(f"{link}/a.jpg {real.resolve()}/b.jpg") == "[media folder m1]/a.jpg [media folder m1]/b.jpg"


def test_install_roots_name_each_configured_folder(monkeypatch):
    from types import SimpleNamespace
    from yaffo.site_agents.assistant import redact as module
    monkeypatch.setattr(module, "get_media_dir_entries",
                        lambda session: [SimpleNamespace(id="m1", path=Path("/Volumes/Photos"))])
    monkeypatch.setattr(module, "get_thumbnail_dir", lambda session: Path("/Users/alex/thumbs"))
    roots = module.install_roots(session=None)
    assert roots["media folder m1"] == Path("/Volumes/Photos")
    assert roots["thumbnail folder"] == Path("/Users/alex/thumbs")
    assert roots["data folder"] == Path(module.ROOT_DIR)
    redact = module.redactor_for(session=None)
    assert redact("/Volumes/Photos/x.jpg") == "[media folder m1]/x.jpg"


def test_attached_context_is_redacted_like_tool_results():
    from yaffo.background_tasks.tasks.assistant_run import _redacted_context
    redact = Redactor(home=Path("/nowhere"), roots={"media folder m1": Path("/Volumes/Photos")})
    context = {"page": "/utilities/index-photos", "error": "Could not read /Volumes/Photos/a.HEIC"}
    assert _redacted_context(context, redact) == {
        "page": "/utilities/index-photos", "error": "Could not read [media folder m1]/a.HEIC"}
    assert _redacted_context(None, redact) is None
