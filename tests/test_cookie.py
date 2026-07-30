from __future__ import annotations

from get_cookie import _cookie_values, _has_new_or_updated_cookie, _user_id_from_login_payload


def test_session_capture_requires_a_new_or_rotated_cookie() -> None:
    initial = [{"name": "session", "domain": "example.test", "path": "/", "value": "old"}]
    previous_values = _cookie_values(initial)

    assert _has_new_or_updated_cookie(initial, previous_values) is False

    rotated = [{"name": "session", "domain": "example.test", "path": "/", "value": "new"}]
    assert _has_new_or_updated_cookie(rotated, previous_values) is True

    additional = initial + [{"name": "csrf", "domain": "example.test", "path": "/", "value": "token"}]
    assert _has_new_or_updated_cookie(additional, previous_values) is True


def test_login_payload_extracts_the_target_user_id() -> None:
    assert _user_id_from_login_payload({"data": {"id": 42}}) == "42"
    assert _user_id_from_login_payload({"data": {"user_id": "account-42"}}) == "account-42"
    assert _user_id_from_login_payload({"data": {"username": "person"}}) is None
