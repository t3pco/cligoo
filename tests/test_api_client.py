"""Tests for cligoo.api.DegooClient construction behaviour.

Covers:
- DEFAULT_HEADERS isolation: each DegooClient gets an independent header dict
  so client normalisation on one instance never mutates the shared module-level
  dict or another client's headers.
- Token passthrough: when a token is supplied to the constructor the client
  returns it via `.token` without making a second call to get_token().
- GCS upload retry logic: transient network errors and 5xx responses are retried
  up to upload_retries times; 4xx responses are not retried.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from curl_cffi.requests.errors import RequestsError

from cligoo.api import DegooAPIError, DegooClient
from cligoo.constants import DEFAULT_HEADERS

# ── DEFAULT_HEADERS isolation ─────────────────────────────────────────────────


def test_client_headers_are_independent_of_module_dict():
    """Mutating a client's HTTP headers must not change DEFAULT_HEADERS."""
    client = DegooClient(token="test-token")
    try:
        # Simulate client header normalization.
        client._http.headers["x-injected"] = "yes"

        assert "x-injected" not in DEFAULT_HEADERS
    finally:
        client.close()


def test_two_clients_have_independent_headers():
    """Mutating one client's headers must not affect a second client's headers."""
    c1 = DegooClient(token="tok1")
    c2 = DegooClient(token="tok2")
    try:
        c1._http.headers["x-only-c1"] = "1"

        assert "x-only-c1" not in c2._http.headers
    finally:
        c1.close()
        c2.close()


def test_default_headers_unchanged_after_client_creation():
    """DEFAULT_HEADERS must be identical before and after constructing a client."""
    snapshot = dict(DEFAULT_HEADERS)
    client = DegooClient(token="tok")
    try:
        assert dict(DEFAULT_HEADERS) == snapshot
    finally:
        client.close()


def test_client_uses_chrome_impersonation():
    """GraphQL sessions must use Chromium TLS impersonation."""
    with patch("cligoo.api.curl_requests.Session") as session_factory:
        client = DegooClient(token="test-token")
        client.close()

    assert session_factory.call_args.kwargs["impersonate"] == "chrome124"


# ── Token passthrough ──────────────────────────────────────────────────────────


def test_token_passthrough_does_not_call_get_token():
    """When token= is supplied, .token must return it without fetching."""
    with patch("cligoo.api.get_token") as mock_get:
        client = DegooClient(token="supplied-token")
        try:
            assert client.token == "supplied-token"
            mock_get.assert_not_called()
        finally:
            client.close()


def test_token_fetched_lazily_when_not_supplied():
    """When no token is supplied, get_token() is called on first .token access."""
    with patch("cligoo.api.get_token", return_value="lazy-token") as mock_get:
        client = DegooClient()
        try:
            tok = client.token
            assert tok == "lazy-token"
            mock_get.assert_called_once()
        finally:
            client.close()


def test_token_fetched_on_every_access_for_auto_refresh():
    """Without an explicit token, .token must call get_token() each time so
    that short-lived access tokens are renewed transparently during long-running
    operations (shell sessions, bulk uploads, etc.)."""
    with patch("cligoo.api.get_token", return_value="fresh-token") as mock_get:
        client = DegooClient()
        try:
            assert client.token == "fresh-token"
            assert client.token == "fresh-token"
            assert mock_get.call_count == 2  # called on every access — intentional
        finally:
            client.close()


def test_gql_retries_rate_limit_using_retry_after_header():
    """A Degoo 429 is retried after the server-requested delay."""
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "7"})
    success = MagicMock(status_code=200)
    success.json.return_value = {"data": {"ok": True}}
    client = DegooClient(token="tok")

    with (
        patch.object(client._http, "post", side_effect=[rate_limited, success]) as post,
        patch("cligoo.api.time.sleep") as sleep,
    ):
        assert client._gql("query { ok }") == {"ok": True}

    assert post.call_count == 2
    sleep.assert_called_once_with(7.0)
    client.close()


def test_gql_stops_after_bounded_rate_limit_retries():
    """Repeated 429 responses fail clearly after bounded backoff attempts."""
    rate_limited = MagicMock(status_code=429, headers={})
    client = DegooClient(token="tok")

    with (
        patch.object(client._http, "post", return_value=rate_limited) as post,
        patch("cligoo.api.time.sleep") as sleep,
    ):
        try:
            client._gql("query { ok }")
        except DegooAPIError as exc:
            assert "rate limit exceeded (HTTP 429)" in str(exc)
        else:
            raise AssertionError("Expected DegooAPIError after repeated HTTP 429 responses")

    assert post.call_count == 6
    assert [entry.args[0] for entry in sleep.call_args_list] == [1, 2, 4, 8, 16]
    client.close()


# ── GCS upload retry logic ─────────────────────────────────────────────────────


def _make_gcs_response(status_code: int, text: str = "") -> MagicMock:
    """Return a mock upload response with the given status_code."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _upload_with_gcs_mock(tmp_path, gcs_side_effects, upload_retries=3):
    """Helper: run DegooClient.upload() with mocked Degoo GQL and GCS POST.

    *gcs_side_effects* is a list of return values / exceptions for successive
    calls to ``curl_requests.post`` (the GCS upload POST only — Degoo GQL calls go
    through a separate mock).

    Returns the (call_count, raised_exception_or_None) tuple.
    """
    # Write a tiny test file
    fp = tmp_path / "file.bin"
    fp.write_bytes(b"\x00" * 8)

    # Degoo GQL response stubs
    fake_auth_data = {
        "BaseURL": "https://storage.googleapis.com/bucket",
        "KeyPrefix": "prefix/",
        "PolicyBase64": "policy",
        "Signature": "sig",
        "AccessKey": {"Key": "GoogleAccessId", "Value": "svc@project.iam"},
        "ACL": None,
        "AdditionalBody": [],
    }
    gql_data_auth = {"getBucketWriteAuth4": [{"AuthData": fake_auth_data, "Error": None}]}
    gql_data_register = {"setUploadFile3": "file-id-123"}

    gcs_mock = MagicMock(side_effect=gcs_side_effects)

    with (
        patch("cligoo.api.get_token", return_value="tok"),
        patch.object(DegooClient, "_gql", side_effect=[gql_data_auth, gql_data_register]),
        patch("cligoo.api.curl_requests.post", gcs_mock),
        patch("cligoo.api.time.sleep"),  # skip backoff delays in tests
    ):
        client = DegooClient(token="tok")
        exc = None
        try:
            client.upload(fp, "42", upload_retries=upload_retries)
        except Exception as e:
            exc = e

    return gcs_mock.call_count, exc


def test_gcs_upload_succeeds_first_attempt(tmp_path):
    """Happy path: GCS returns 200 on the first attempt — no retry needed."""
    call_count, exc = _upload_with_gcs_mock(tmp_path, [_make_gcs_response(200)])
    assert exc is None
    assert call_count == 1


def test_gcs_upload_uses_unknown_key_for_unrecognized_extensions(tmp_path):
    fp = tmp_path / ".shards"
    fp.write_bytes(b"payload")
    auth_data = {
        "BaseURL": "https://storage.googleapis.com/bucket",
        "KeyPrefix": "prefix/",
        "PolicyBase64": "policy",
        "Signature": "sig",
        "AccessKey": {"Key": "GoogleAccessId", "Value": "svc"},
        "ACL": None,
        "AdditionalBody": [],
    }
    mime = MagicMock()

    with (
        patch("cligoo.api.get_token", return_value="tok"),
        patch.object(
            DegooClient,
            "_gql",
            side_effect=[
                {"getBucketWriteAuth4": [{"AuthData": auth_data, "Error": None}]},
                {"setUploadFile3": "file-id"},
            ],
        ),
        patch("cligoo.api.CurlMime", return_value=mime),
        patch("cligoo.api.curl_requests.post", return_value=_make_gcs_response(200)),
    ):
        client = DegooClient(token="tok")
        client.upload(fp, "42")
        client.close()

    key_call = next(call for call in mime.addpart.call_args_list if call.kwargs.get("name") == "key")
    assert key_call.kwargs["data"].startswith("prefix/unknown/")
    assert key_call.kwargs["data"].endswith(".unknown")


def test_gcs_upload_passes_file_path_to_curl_multipart(tmp_path):
    """Pass a path to libcurl, never a file object that could be stringified."""
    fp = tmp_path / "snapshot.f"
    fp.write_bytes(b"kopia snapshot contents")
    auth_data = {
        "BaseURL": "https://storage.googleapis.com/bucket",
        "KeyPrefix": "prefix/",
        "PolicyBase64": "policy",
        "Signature": "sig",
        "AccessKey": {"Key": "GoogleAccessId", "Value": "svc"},
        "ACL": None,
        "AdditionalBody": [],
    }
    mime = MagicMock()

    with (
        patch("cligoo.api.get_token", return_value="tok"),
        patch.object(
            DegooClient,
            "_gql",
            side_effect=[
                {"getBucketWriteAuth4": [{"AuthData": auth_data, "Error": None}]},
                {"setUploadFile3": "file-id"},
            ],
        ),
        patch("cligoo.api.CurlMime", return_value=mime),
        patch("cligoo.api.curl_requests.post", return_value=_make_gcs_response(200)),
    ):
        client = DegooClient(token="tok")
        client.upload(fp, "42")
        client.close()

    file_part = next(call for call in mime.addpart.call_args_list if call.kwargs.get("name") == "file")
    assert file_part.kwargs["local_path"] == fp
    assert file_part.kwargs["filename"] == fp.name
    assert "data" not in file_part.kwargs


def test_gcs_upload_retries_on_network_error_then_succeeds(tmp_path):
    """Network error on attempt 1, success on attempt 2."""
    effects = [
        RequestsError("connection reset"),
        _make_gcs_response(200),
    ]
    call_count, exc = _upload_with_gcs_mock(tmp_path, effects, upload_retries=3)
    assert exc is None
    assert call_count == 2


def test_gcs_upload_retries_on_5xx_then_succeeds(tmp_path):
    """GCS 503 on attempt 1, success on attempt 2."""
    effects = [
        _make_gcs_response(503, "Service Unavailable"),
        _make_gcs_response(200),
    ]
    call_count, exc = _upload_with_gcs_mock(tmp_path, effects, upload_retries=3)
    assert exc is None
    assert call_count == 2


def test_gcs_upload_exhausts_all_retries_and_raises(tmp_path):
    """All attempts fail with network errors → DegooAPIError after retries exhausted."""
    effects = [RequestsError("reset")] * 4  # more than upload_retries=3
    call_count, exc = _upload_with_gcs_mock(tmp_path, effects, upload_retries=3)
    assert isinstance(exc, DegooAPIError)
    assert "retries" in str(exc).lower()
    assert call_count == 4  # 1 initial + 3 retries


def test_gcs_upload_does_not_retry_on_4xx(tmp_path):
    """4xx (policy violation) is raised immediately without retrying."""
    effects = [_make_gcs_response(403, "AccessDenied")]
    call_count, exc = _upload_with_gcs_mock(tmp_path, effects, upload_retries=3)
    assert isinstance(exc, DegooAPIError)
    assert "403" in str(exc)
    assert call_count == 1  # not retried


def test_gcs_upload_zero_retries_tries_once(tmp_path):
    """upload_retries=0 means try once; failure raises immediately."""
    effects = [RequestsError("reset")]
    call_count, exc = _upload_with_gcs_mock(tmp_path, effects, upload_retries=0)
    assert isinstance(exc, DegooAPIError)
    assert call_count == 1


def test_gcs_upload_backoff_called_between_retries(tmp_path):
    """time.sleep is called once between each retry (not before the first attempt)."""
    effects = [
        RequestsError("reset"),
        RequestsError("reset"),
        _make_gcs_response(200),
    ]
    fp = tmp_path / "f.bin"
    fp.write_bytes(b"\x00" * 4)

    fake_auth_data = {
        "BaseURL": "https://storage.googleapis.com/bucket",
        "KeyPrefix": "prefix/",
        "PolicyBase64": "p",
        "Signature": "s",
        "AccessKey": {"Key": "GoogleAccessId", "Value": "svc"},
        "ACL": None,
        "AdditionalBody": [],
    }
    gql_data_auth = {"getBucketWriteAuth4": [{"AuthData": fake_auth_data, "Error": None}]}
    gql_data_register = {"setUploadFile3": "fid"}

    with (
        patch("cligoo.api.get_token", return_value="tok"),
        patch.object(DegooClient, "_gql", side_effect=[gql_data_auth, gql_data_register]),
        patch("cligoo.api.curl_requests.post", side_effect=effects),
        patch("cligoo.api.time.sleep") as mock_sleep,
    ):
        client = DegooClient(token="tok")
        client.upload(fp, "42", upload_retries=5)

    # sleep called for attempt 1 (backoff=1s) and attempt 2 (backoff=2s), not for attempt 0
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list[0] == call(1)
    assert mock_sleep.call_args_list[1] == call(2)
