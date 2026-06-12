"""Tests for the site generator.

Most of the actual generation is tested via integration with a real
LLM key. These tests cover the parsing and validation logic, plus
schema correctness.
"""

import pytest

from bot.services.site_generator import (
    SITE_GENERATOR_SYSTEM_PROMPT,
    GeneratedFile,
    GeneratedSite,
    _parse_response,
    generate_site,
)


SAMPLE_GOOD = """\
{
  "project_name": "test-coffee",
  "framework": "static-html",
  "files": [
    {"path": "index.html", "content": "<!doctype html><html><body>Coffee shop landing</body></html>"}
  ],
  "preview_summary": "A warm minimalist landing for a specialty coffee shop."
}
"""


SAMPLE_WITH_FENCE = "```json\n" + SAMPLE_GOOD + "\n```"


SAMPLE_BAD = "Sorry, I cannot help with that."


SAMPLE_EMPTY_FILES = '{"project_name": "x", "framework": "static-html", "files": [], "preview_summary": ""}'


SAMPLE_MULTIFILE_NO_INDEX = """\
{
  "project_name": "test-coffee",
  "framework": "static-html",
  "files": [
    {"path": "package.json", "content": "{\\"name\\": \\"test-coffee\\"}"},
    {"path": "src/main.jsx", "content": "import React from 'react';"},
    {"path": "src/App.jsx", "content": "export default function App(){return <h1>Coffee</h1>;}"},
    {"path": "src/index.css", "content": "body{margin:0;font-family:system-ui;}"}
  ],
  "preview_summary": "A warm minimalist landing for a specialty coffee shop."
}
"""


def test_parse_multifile_no_index_merges_to_index():
    """Multi-file projects without index.html should be auto-merged."""
    site = _parse_response(SAMPLE_MULTIFILE_NO_INDEX)
    assert site.framework == "static-html"
    assert len(site.files) == 1
    assert site.files[0].path == "index.html"
    # The placeholder body from no-html-files path should be there
    assert "Buildo" in site.files[0].content or "Coffee" in site.files[0].content
    assert (
        "warm" in site.preview_summary.lower()
        or "minimalist" in site.preview_summary.lower()
    )


def test_parse_clean_json():
    site = _parse_response(SAMPLE_GOOD)
    assert site.project_name == "test-coffee"
    assert site.framework == "static-html"
    assert len(site.files) == 1
    assert site.files[0].path == "index.html"
    assert "Coffee shop landing" in site.files[0].content


def test_parse_markdown_fence():
    """LLM sometimes wraps JSON in ```json ... ``` fences."""
    site = _parse_response(SAMPLE_WITH_FENCE)
    assert site.project_name == "test-coffee"
    assert len(site.files) == 1
    assert site.files[0].path == "index.html"


def test_parse_prose_around_json():
    """LLM may add prose before/after the JSON object."""
    raw = "Here is the site:\n" + SAMPLE_GOOD + "\nLet me know what you think!"
    site = _parse_response(raw)
    assert site.project_name == "test-coffee"


def test_parse_rejects_non_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse_response(SAMPLE_BAD)


def test_parse_rejects_empty_files():
    with pytest.raises(ValueError, match="files"):
        _parse_response(SAMPLE_EMPTY_FILES)


def test_system_prompt_contains_key_rules():
    """Sanity: the system prompt should enforce the format and design rules."""
    assert "JSON" in SITE_GENERATOR_SYSTEM_PROMPT
    assert "static-html" in SITE_GENERATOR_SYSTEM_PROMPT
    assert "VARIANCE" in SITE_GENERATOR_SYSTEM_PROMPT
    assert (
        "taste-skill" in SITE_GENERATOR_SYSTEM_PROMPT.lower()
        or "anti-slop" in SITE_GENERATOR_SYSTEM_PROMPT.lower()
    )


def test_generated_site_to_dict():
    site = GeneratedSite(
        project_name="x",
        framework="static-html",
        files=[GeneratedFile(path="index.html", content="<html></html>")],
        preview_summary="test",
    )
    d = site.to_dict()
    assert d["project_name"] == "x"
    assert d["framework"] == "static-html"
    assert len(d["files"]) == 1


def test_generated_site_size_kb():
    site = GeneratedSite(
        project_name="x",
        framework="static-html",
        files=[GeneratedFile(path="index.html", content="x" * 2048)],  # 2KB
    )
    assert 1.9 < site.total_size_kb < 2.1


@pytest.mark.asyncio
async def test_generate_site_rejects_empty_prompt():
    with pytest.raises(ValueError, match="empty"):
        await generate_site("")


@pytest.mark.asyncio
async def test_generate_site_rejects_whitespace_prompt():
    with pytest.raises(ValueError, match="empty"):
        await generate_site("   \n\t  ")


# ===== New fallback-strategy tests =====

SAMPLE_TRUNCATED_JSON = """{
  "project_name": "truncated",
  "framework": "static-html",
  "files": [
    {
      "path": "index.html",
      "content": "<!doctype html><html><body>Hi"""  # noqa: E501


SAMPLE_RAW_HTML = (
    "<!doctype html>\n"
    "<html lang='ru'><head><meta charset='utf-8'><title>raw</title></head>"
    "<body><h1>Raw HTML fallback</h1></body></html>"
)


SAMPLE_HTML_IN_QUOTES = '"<!doctype html>\\n<html><body>Quoted HTML</body></html>"'


def test_parse_balanced_extraction_handles_embedded_braces():
    """JSON with content containing `{}` should still parse via brace-matching."""
    raw = (
        '{"project_name":"p","framework":"static-html",'
        '"files":[{"path":"index.html","content":"<style>body{color:red}</style>"}]}'
    )
    site = _parse_response(raw)
    assert site.project_name == "p"
    assert "color:red" in site.files[0].content


def test_parse_truncated_json_repair():
    """LLM response cut off mid-content falls back to raw-HTML heuristic.

    When the truncated JSON contains an HTML snippet starting with
    <!doctype, the heuristic path (5) catches it and wraps the partial
    HTML as index.html. We don't try to recover `project_name` from
    truncated JSON — the file content is what matters.
    """
    site = _parse_response(SAMPLE_TRUNCATED_JSON)
    assert len(site.files) == 1
    assert site.files[0].path == "index.html"
    assert "<" in site.files[0].content  # something HTML-shaped survived


def test_parse_raw_html_fallback():
    """When LLM returns pure HTML without a JSON envelope, wrap it as index.html."""
    site = _parse_response(SAMPLE_RAW_HTML)
    assert site.project_name == "generated-site"
    assert len(site.files) == 1
    assert site.files[0].path == "index.html"
    assert "<h1>Raw HTML fallback</h1>" in site.files[0].content


def test_parse_html_in_quotes_fallback():
    """When HTML is returned as a JSON-quoted string, unescape and wrap it."""
    site = _parse_response(SAMPLE_HTML_IN_QUOTES)
    assert site.project_name == "generated-site"
    assert "Quoted HTML" in site.files[0].content


def test_parse_garbage_raises():
    """Truly garbage input raises ValueError — no silent failure."""
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse_response("just random text, no html no json no nothing")
