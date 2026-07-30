from __future__ import annotations

from get_cookie import _cookie_values, _has_new_or_updated_cookie


def test_session_capture_requires_a_new_or_rotated_cookie() -> None:
    initial = [{"name": "session", "domain": "example.test", "path": "/", "value": "old"}]
    previous_values = _cookie_values(initial)

    assert _has_new_or_updated_cookie(initial, previous_values) is False

    rotated = [{"name": "session", "domain": "example.test", "path": "/", "value": "new"}]
    assert _has_new_or_updated_cookie(rotated, previous_values) is True

    additional = initial + [{"name": "csrf", "domain": "example.test", "path": "/", "value": "token"}]
    assert _has_new_or_updated_cookie(additional, previous_values) is True
