import pytest

from watcher.config import Target
from watcher.errors import ExtractionError
from watcher.fetcher import extract

HTML = """
<html><body>
  <div class="price">1.499,90 TL</div>
  <div class="price">2.999,00 TL</div>
  <span id="stock">   Stokta   var  </span>
  <a id="link" href="/urun/42">detay</a>
  <p id="mixed">Son güncelleme: 14:30 · v2.1</p>
</body></html>
"""


def _target(**kwargs):
    return Target(name="t", url="https://x.test", **kwargs)


def test_extracts_first_match_by_default():
    assert extract(HTML, _target(selector=".price")) == "1.499,90 TL"


def test_index_selects_a_later_match():
    assert extract(HTML, _target(selector=".price", index=1)) == "2.999,00 TL"


def test_collapses_whitespace():
    assert extract(HTML, _target(selector="#stock")) == "Stokta var"


def test_reads_an_attribute():
    assert extract(HTML, _target(selector="#link", attr="href")) == "/urun/42"


def test_regex_extract_narrows_the_value():
    target = _target(selector="#mixed", regex_extract=r"(\d{2}:\d{2})")
    assert extract(HTML, target) == "14:30"


def test_without_selector_falls_back_to_whole_page_text():
    assert "Stokta var" in extract(HTML, _target())


@pytest.mark.parametrize(
    "target, fragment",
    [
        (_target(selector=".missing"), "matched no element"),
        (_target(selector=".price", index=9), "out of range"),
        (_target(selector="#link", attr="data-nope"), "no attribute"),
        (_target(selector="#mixed", regex_extract=r"(ZZZ)"), "matched nothing"),
    ],
)
def test_extraction_failures_are_explicit(target, fragment):
    with pytest.raises(ExtractionError) as exc:
        extract(HTML, target)
    assert fragment in str(exc.value)


# --- json source ----------------------------------------------------------

import pytest as _pytest  # noqa: E402

from watcher.fetcher import read_value  # noqa: E402

PAYLOAD = {
    "first_step": {
        "slug": "ticket-resale-market-waiting-list",
        "data": {"ticket-market-rates": {"rates": [], "errors": ["unavailable"]}},
    },
    "widget_events": [{"event": {"name": "SEPHORiA"}}],
}


def _json_target(**kwargs):
    return Target(name="t", url="https://api.test", source="json", **kwargs)


@_pytest.mark.parametrize(
    "path, mode, expected",
    [
        ("first_step.data.ticket-market-rates.rates", "count", "0"),
        ("first_step.data.ticket-market-rates.errors", "count", "1"),
        ("first_step.slug", "text", "ticket-resale-market-waiting-list"),
        ("widget_events[0].event.name", "text", "SEPHORiA"),
        ("first_step.data.ticket-market-rates.rates", "raw", "[]"),
    ],
)
def test_json_path_resolution(path, mode, expected):
    assert read_value(PAYLOAD, _json_target(json_path=path, json_mode=mode)) == expected


def test_raw_mode_is_stable_across_key_order():
    a = read_value({"x": {"b": 1, "a": 2}}, _json_target(json_path="x"))
    b = read_value({"x": {"a": 2, "b": 1}}, _json_target(json_path="x"))
    assert a == b == '{"a":2,"b":1}'


@_pytest.mark.parametrize(
    "path, fragment",
    [
        ("first_step.nope", "not found"),
        ("first_step.nope", "Available keys: data, slug"),
        ("widget_events[9]", "out of range"),
        ("first_step.slug.deeper", "expects an object"),
        ("first_step[0]", "not a list"),
    ],
)
def test_json_path_failures_name_the_broken_segment(path, fragment):
    with _pytest.raises(ExtractionError) as exc:
        read_value(PAYLOAD, _json_target(json_path=path))
    assert fragment in str(exc.value)


def test_count_mode_rejects_a_number():
    with _pytest.raises(ExtractionError) as exc:
        read_value({"n": 42}, _json_target(json_path="n", json_mode="count"))
    assert "needs a list/object/string" in str(exc.value)


def test_count_mode_measures_a_string_length():
    assert read_value({"s": "abcd"}, _json_target(json_path="s", json_mode="count")) == "4"
