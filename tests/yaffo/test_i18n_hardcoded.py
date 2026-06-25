from pathlib import Path

import pytest

from yaffo.scripts.i18n_hardcoded import (
    BASELINE_PATH,
    HardcodedText,
    new_findings,
    read_baseline,
    scan_javascript,
    scan_jinja,
    scan_sources,
)

pytestmark = pytest.mark.unit


def test_jinja_scanner_finds_visible_text_and_accessible_attributes(tmp_path):
    template = tmp_path / "page.html"
    template.write_text(
        """
        <h1>Hard-coded heading</h1>
        <input placeholder="Search photos" aria-label="{{ _('Search') }}">
        <p>{{ _("Translated text") }}</p>
        {% trans %}Translated block{% endtrans %}
        """,
        encoding="utf-8",
    )

    findings = scan_jinja(template, tmp_path)

    assert {(finding.kind, finding.text) for finding in findings} == {
        ("jinja-text", "Hard-coded heading"),
        ("jinja-attribute:placeholder", "Search photos"),
    }


def test_javascript_scanner_finds_user_interface_sinks(tmp_path):
    script = tmp_path / "page.js"
    script.write_text(
        """
        notification.error('Could not save');
        button.textContent = 'Saving…';
        const selector = '.not-user-facing';
        const confirmed = confirmDialog({
            title: 'Delete photo',
            confirmText: i18n.t('common.delete')
        });
        container.innerHTML = `<button aria-label="Remove tag">Remove</button>`;
        """,
        encoding="utf-8",
    )

    findings = scan_javascript(script, tmp_path)

    assert {(finding.kind, finding.text) for finding in findings} == {
        ("javascript-notification", "Could not save"),
        ("javascript-dom-text", "Saving…"),
        ("javascript-ui-option", "Delete photo"),
        ("javascript-html-attribute:aria-label", "Remove tag"),
        ("javascript-html-text", "Remove"),
    }


def test_baseline_tracks_duplicate_occurrences():
    finding = HardcodedText(
        path="yaffo/static/example.js",
        line=1,
        kind="javascript-notification",
        text="Could not save",
    )

    assert new_findings([finding, finding], {finding.fingerprint: 1}) == [finding]


def test_no_new_hard_coded_user_facing_text():
    added = new_findings(scan_sources(), read_baseline(BASELINE_PATH))

    assert added == [], "\n".join(
        f"{finding.path}:{finding.line}: {finding.kind}: {finding.text}"
        for finding in added
    )
