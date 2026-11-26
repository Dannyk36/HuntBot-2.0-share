"""Tests for the PDF rendering helpers.

The _write_pdf / Playwright path is skipped in unit tests — we test the HTML
generation logic which is the meaningful part (CSS template, content parsing).
"""
from huntbot2.tailoring.pdf import _cover_to_html, _esc


def test_cover_to_html_extracts_name():
    text = "Alex Example\n\n+1 202 555 0100 | alex@example.com\n\nDear Hiring Manager,\n\nBody text."
    html = _cover_to_html(text)
    assert "Alex Example" in html
    assert 'class="sender-name"' in html


def test_cover_to_html_contact_block_detected():
    text = "Jane Smith\n\njane@example.com | example.com/candidate/jane\n\nDear Manager,\n\nParagraph."
    html = _cover_to_html(text)
    assert 'class="sender-contact"' in html
    assert "jane@example.com" in html


def test_cover_to_html_body_as_paragraphs():
    text = "Name\n\nParagraph one.\n\nParagraph two."
    html = _cover_to_html(text)
    assert html.count("<p>") >= 2


def test_cover_to_html_escapes_ampersand():
    text = "Name\n\nRoles & Responsibilities here."
    html = _cover_to_html(text)
    assert "&amp;" in html
    assert "Roles & " not in html


def test_cover_to_html_no_contact_block_when_plain():
    text = "Name\n\nMay 1, 2026\n\nDear Hiring Manager,\n\nBody."
    html = _cover_to_html(text)
    # No contact block (no @ or | in first paragraph)
    assert 'class="sender-contact"' not in html


def test_esc_handles_special_chars():
    assert _esc("a & b") == "a &amp; b"
    assert _esc("<script>") == "&lt;script&gt;"
    assert _esc("normal") == "normal"
