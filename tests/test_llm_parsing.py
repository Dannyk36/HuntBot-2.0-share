import json

from huntbot2.llm.parsing import extract_json_text


def test_plain_json_passes_through():
    assert json.loads(extract_json_text('{"a": 1}')) == {"a": 1}


def test_fenced_json_with_language_tag():
    raw = '```json\n{"a": 1, "b": "x"}\n```'
    assert json.loads(extract_json_text(raw)) == {"a": 1, "b": "x"}


def test_fenced_json_without_language_tag():
    raw = '```\n{"a": 2}\n```'
    assert json.loads(extract_json_text(raw)) == {"a": 2}


def test_preserves_values_starting_with_json_letters():
    # The old `.lstrip("json")` idiom corrupted values whose first key/letter was
    # one of {j,s,o,n}; the extractor must keep them intact.
    raw = '```json\n{"name": "Jon", "status": "open"}\n```'
    assert json.loads(extract_json_text(raw)) == {"name": "Jon", "status": "open"}


def test_unterminated_fence_drops_opening_tag():
    raw = '```json\n{"a": 3}'
    assert json.loads(extract_json_text(raw)) == {"a": 3}


def test_prose_around_fence_is_ignored():
    raw = 'Here you go:\n```json\n{"a": 4}\n```\nHope that helps!'
    assert json.loads(extract_json_text(raw)) == {"a": 4}
