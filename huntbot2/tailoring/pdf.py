"""Convert tailored resume/cover letter content to PDF via Playwright.

The visual template (CSS + page layout) is locked here. Only the text content
changes per job. Playwright renders the HTML to a Letter-size PDF.
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown as _md_lib

# ── Locked visual templates ────────────────────────────────────────────────

_RESUME_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Segoe UI', Arial, Helvetica, sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #111;
}
h1 {
    font-size: 17pt;
    font-weight: 700;
    letter-spacing: 0.3px;
    margin-bottom: 3px;
}
h2 {
    font-size: 9.5pt;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    font-weight: 700;
    border-bottom: 1.5px solid #222;
    padding-bottom: 2px;
    margin: 13px 0 4px;
}
h3 { font-size: 10.5pt; font-weight: 600; margin: 8px 0 2px; }
ul  { margin: 3px 0 6px 20px; }
li  { margin-bottom: 2px; }
p   { margin: 3px 0 6px; }
strong { font-weight: 600; }
a   { color: #111; text-decoration: none; }
"""

_COVER_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Georgia', 'Times New Roman', serif;
    font-size: 11pt;
    line-height: 1.65;
    color: #111;
}
.sender-name    { font-size: 14.5pt; font-weight: bold; margin-bottom: 3px; }
.sender-contact { font-size: 9.5pt; color: #555; margin-bottom: 20px; line-height: 1.5; }
p { margin-bottom: 13px; }
"""


# ── Public API ─────────────────────────────────────────────────────────────

def render_resume_to_pdf(md_text: str, output_path: Path) -> Path:
    """Convert a tailored resume (markdown) to a professionally styled PDF."""
    body_html = _md_lib.markdown(md_text, extensions=["extra", "nl2br"])
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{_RESUME_CSS}</style></head>
<body>{body_html}</body></html>"""
    _write_pdf(html, output_path)
    return output_path


def render_cover_to_pdf(letter_text: str, output_path: Path) -> Path:
    """Convert a filled cover letter (plain text) to a professionally styled PDF."""
    html = _cover_to_html(letter_text)
    _write_pdf(html, output_path)
    return output_path


# ── Internal helpers ───────────────────────────────────────────────────────

def _cover_to_html(text: str) -> str:
    """Wrap a plain-text cover letter in styled HTML.

    Expects the letter to start with the sender's name on the first non-empty
    line, followed optionally by a contact block (contains '@' or '|'), then
    the rest of the letter body as double-newline-separated paragraphs.
    """
    lines = text.strip().splitlines()

    # Extract sender name from the first non-empty line
    name = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            name = line.strip()
            body_start = i + 1
            break

    remaining = "\n".join(lines[body_start:]).strip()
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", remaining) if p.strip()]

    # If next paragraph looks like contact info, pull it out as styled header
    contact_html = ""
    if paragraphs and ("@" in paragraphs[0] or "|" in paragraphs[0] or "linkedin" in paragraphs[0].lower()):
        contact_lines = paragraphs.pop(0).replace("\n", "<br>")
        contact_html = f'<div class="sender-contact">{_esc(contact_lines)}</div>'

    body_html = "\n".join(
        f"<p>{_esc(p.replace(chr(10), '<br>'))}</p>" for p in paragraphs
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{_COVER_CSS}</style></head>
<body>
<div class="sender-name">{_esc(name)}</div>
{contact_html}
{body_html}
</body></html>"""


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _write_pdf(html: str, output_path: Path) -> None:
    """Render HTML to PDF, with graceful fallback when browser deps are missing.

    In minimal/CI environments Playwright may be unavailable. In that case we
    still emit an artifact at `output_path` so pipeline stages can complete and
    surface a clear remediation note instead of hard-failing the run.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        fallback = (
            "HUNTBOT2 PDF FALLBACK\n"
            "Playwright is not installed in this environment.\n"
            "Install optional browser deps: pip install -e '.[browser]'\n"
            "--- BEGIN HTML ---\n"
            f"{html}\n"
            "--- END HTML ---\n"
        )
        output_path.write_text(fallback, encoding="utf-8")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="domcontentloaded")
            page.pdf(
                path=str(output_path),
                format="Letter",
                margin={"top": "1.8cm", "bottom": "1.8cm",
                        "left": "2.2cm", "right": "2.2cm"},
                print_background=True,
            )
        finally:
            browser.close()
