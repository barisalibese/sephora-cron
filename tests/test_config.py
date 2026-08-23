import pytest

from watcher.config import parse_config
from watcher.errors import ConfigError


def test_parses_a_minimal_target():
    config = parse_config({"targets": [{"name": "a", "url": "https://x.test"}]})
    target = config.targets[0]
    assert (target.name, target.condition, target.repeat) == ("a", "changed", False)
    assert target.request_headers()["User-Agent"].startswith("Mozilla/")


def test_custom_headers_override_the_default_user_agent():
    config = parse_config(
        {"targets": [{"name": "a", "url": "u", "headers": {"User-Agent": "bot/1"}}]}
    )
    assert config.targets[0].request_headers()["User-Agent"] == "bot/1"


def test_numeric_condition_coerces_value_to_float():
    config = parse_config(
        {"targets": [{"name": "p", "url": "u", "condition": "lt", "value": "1500"}]}
    )
    assert config.targets[0].value == 1500.0


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ({"targets": []}, "at least one"),
        ({"targets": [{"url": "u"}]}, "'name' is required"),
        ({"targets": [{"name": "a"}]}, "'url' is required"),
        ({"targets": [{"name": "a", "url": "u", "condition": "nope"}]}, "unknown condition"),
        ({"targets": [{"name": "a", "url": "u", "condition": "contains"}]}, "requires 'value'"),
        (
            {"targets": [{"name": "a", "url": "u", "condition": "lt", "value": "abc"}]},
            "numeric 'value'",
        ),
        (
            {"targets": [{"name": "a", "url": "u"}, {"name": "a", "url": "v"}]},
            "duplicate target name",
        ),
    ],
)
def test_rejects_invalid_config(raw, fragment):
    with pytest.raises(ConfigError) as exc:
        parse_config(raw)
    assert fragment in str(exc.value)
