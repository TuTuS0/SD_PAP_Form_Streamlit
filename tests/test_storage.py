import copy
import hashlib
import hmac
import json
from unittest.mock import Mock

import pytest
import requests

from storage import GoogleDriveCSVStore, PreviewStore, StorageError, SupabaseStore, create_store, export_csv


def response(data):
    result = Mock()
    result.json.return_value = data
    return result


def test_server_secret_uses_apikey_header_and_requires_receipt(monkeypatch):
    post = Mock(return_value=response("batch-id"))
    monkeypatch.setattr(requests, "post", post)
    store = SupabaseStore("https://study.supabase.co", "sb_secret_private")
    payload = {"p_batch": {"batch_id": "batch-id"}, "p_votes": []}
    assert store.save_batch(payload) == "batch-id"
    headers = post.call_args.kwargs["headers"]
    assert headers["apikey"] == "sb_secret_private"
    assert "Authorization" not in headers
    assert post.call_args.kwargs["allow_redirects"] is False
    post.return_value = response("different-id")
    with pytest.raises(StorageError):
        store.save_batch(payload)


def test_failure_does_not_silently_fallback_or_expose_credentials(monkeypatch):
    monkeypatch.setattr(requests, "post", Mock(side_effect=requests.Timeout("secret data")))
    with pytest.raises(StorageError) as error:
        SupabaseStore("https://study.supabase.co", "sb_secret_private").save_batch({})
    assert "secret data" not in str(error.value)


def test_preview_retries_are_idempotent():
    store = PreviewStore()
    payload = {"p_batch": {"batch_id": "id"}, "p_votes": ["vote"]}
    store.save_batch(payload)
    store.save_batch(copy.deepcopy(payload))
    assert len(store.submissions) == 1
    payload["p_votes"] = ["changed"]
    with pytest.raises(StorageError):
        store.save_batch(payload)


def test_paginated_export_works_even_with_small_server_page_caps(monkeypatch):
    get = Mock(side_effect=[response([{"participant_id": "one", "winner": "tie"}]),
                            response([{"participant_id": "two", "winner": "tie"}]), response([])])
    monkeypatch.setattr(requests, "get", get)
    content = export_csv("https://study.supabase.co", "sb_secret_private")
    assert "one,tie" in content and "two,tie" in content
    assert get.call_count == 3
    assert get.call_args_list[1].kwargs["params"]["offset"] == 1


@pytest.mark.parametrize("url", ["http://study.supabase.co", "https://study.supabase.co/path", "https://user@study.supabase.co"])
def test_invalid_project_urls_are_rejected(url):
    with pytest.raises(StorageError):
        SupabaseStore(url, "sb_secret_private")


DRIVE_URL = "https://script.google.com/macros/s/test-deployment/exec"
TEST_SECRET = "local-tests-only-" + "x" * 32


def drive_receipt(envelope):
    return {"ok": True, "batch_id": json.loads(envelope["payload"])["p_batch"]["batch_id"],
            "row_count": 10, "submission_sha256": hashlib.sha256(envelope["payload"].encode()).hexdigest()}


def http_response(data, status=200, headers=None):
    result = response(data)
    result.status_code = status
    result.headers = headers or {}
    return result


def test_drive_signs_canonical_utf8_request_and_checks_full_receipt(monkeypatch):
    def send(url, **kwargs):
        envelope = kwargs["json"]
        expected = hmac.new(TEST_SECRET.encode(), envelope["payload"].encode(), hashlib.sha256).hexdigest()
        assert envelope["signature"] == expected
        assert TEST_SECRET not in json.dumps(envelope)
        assert kwargs["allow_redirects"] is False
        return http_response(drive_receipt(envelope))

    monkeypatch.setattr(requests, "post", send)
    payload = {"p_batch": {"batch_id": "id"}, "p_votes": [{"prompt": 'étoile, "bleue"\n🌟'}] * 10}
    assert GoogleDriveCSVStore(DRIVE_URL, TEST_SECRET).save_batch(payload) == "id"


def test_drive_handles_google_content_redirect_without_forwarding_payload(monkeypatch):
    captured = {}

    def send(url, **kwargs):
        captured.update(kwargs["json"])
        return http_response(None, 302, {"Location": "https://script.googleusercontent.com/macros/echo?temporary=response"})

    get = Mock(side_effect=lambda *args, **kwargs: http_response(drive_receipt(captured)))
    monkeypatch.setattr(requests, "post", send)
    monkeypatch.setattr(requests, "get", get)
    assert GoogleDriveCSVStore(DRIVE_URL, TEST_SECRET).save_batch({"p_batch": {"batch_id": "id"}}) == "id"
    assert "json" not in get.call_args.kwargs and "data" not in get.call_args.kwargs
    assert get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("receipt", [{"ok": False}, {"ok": True, "batch_id": "id", "row_count": 9},
                                     {"ok": True, "batch_id": "other", "row_count": 10}, [], None])
def test_drive_does_not_accept_partial_or_invalid_receipts(monkeypatch, receipt):
    monkeypatch.setattr(requests, "post", Mock(return_value=http_response(receipt)))
    with pytest.raises(StorageError):
        GoogleDriveCSVStore(DRIVE_URL, TEST_SECRET).save_batch({"p_batch": {"batch_id": "id"}})


def test_drive_does_not_follow_unexpected_redirects(monkeypatch):
    monkeypatch.setattr(requests, "post", Mock(return_value=http_response(None, 302, {"Location": "https://example.org"})))
    get = Mock()
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(StorageError):
        GoogleDriveCSVStore(DRIVE_URL, TEST_SECRET).save_batch({"p_batch": {"batch_id": "id"}})
    get.assert_not_called()


@pytest.mark.parametrize("url", ["http://script.google.com/macros/s/id/exec", "https://example.org/exec",
                                 "https://script.google.com/macros/s/id/dev", None])
def test_drive_requires_deployed_google_https_endpoint(url):
    with pytest.raises(StorageError):
        GoogleDriveCSVStore(url, TEST_SECRET)


def test_storage_selection_is_explicit_and_legacy_compatible():
    assert isinstance(create_store({"storage": {"backend": "google_drive"},
                                   "google_drive": {"web_app_url": DRIVE_URL, "shared_secret": TEST_SECRET}}), GoogleDriveCSVStore)
    assert isinstance(create_store({"supabase": {"url": "https://test.supabase.co", "secret_key": "sb_secret_test"}}), SupabaseStore)
    with pytest.raises(StorageError):
        create_store({"storage": {"backend": "google_drive"}, "supabase": {"url": "https://test.supabase.co", "secret_key": "sb_secret_test"}})
