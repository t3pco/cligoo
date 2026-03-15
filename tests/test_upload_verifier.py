"""Tests for upload verification and retry logic."""

from unittest import mock

import pytest

from cligoo.upload_verifier import (
    UploadVerificationError,
    UploadVerifier,
    verify_and_retry,
)


class TestUploadVerifier:
    """Tests for UploadVerifier class."""

    def test_verify_success_on_first_attempt(self):
        """Successful verification on first try."""
        mock_client = mock.Mock()
        mock_client.get_item.return_value = {
            "ID": "12345",
            "Name": "test.txt",
            "DataSize": 1024,
            "URL": "https://cdn.degoo.com/...",
        }

        verifier = UploadVerifier(max_retries=3)
        result = verifier.verify_upload(
            mock_client, "12345", "test.txt", 1024, verbose=False
        )

        assert result["ID"] == "12345"
        assert result["DataSize"] == 1024
        assert mock_client.get_item.call_count == 1

    def test_verify_success_after_retries(self):
        """Upload succeeds after retrying when initially incomplete."""
        mock_client = mock.Mock()
        # First two attempts return incomplete, third succeeds
        mock_client.get_item.side_effect = [
            {"ID": "12345", "Name": "test.txt", "DataSize": 0, "URL": ""},  # Fail
            {"ID": "12345", "Name": "test.txt", "DataSize": 0, "URL": ""},  # Fail
            {  # Success
                "ID": "12345",
                "Name": "test.txt",
                "DataSize": 1024,
                "URL": "https://cdn.degoo.com/...",
            },
        ]

        verifier = UploadVerifier(max_retries=3, initial_wait=0.01)
        result = verifier.verify_upload(
            mock_client, "12345", "test.txt", 1024, verbose=False
        )

        assert result["DataSize"] == 1024
        assert mock_client.get_item.call_count == 3

    def test_verify_failure_no_url(self):
        """Verification fails when file has no download URL."""
        mock_client = mock.Mock()
        mock_client.get_item.return_value = {
            "ID": "12345",
            "Name": "test.txt",
            "DataSize": 0,
            "URL": "",  # GCS linkage problem
        }

        verifier = UploadVerifier(max_retries=1, initial_wait=0.01)

        with pytest.raises(UploadVerificationError) as exc_info:
            verifier.verify_upload(
                mock_client, "12345", "test.txt", 1024, verbose=False
            )

        assert exc_info.value.file_id == "12345"
        assert exc_info.value.filename == "test.txt"
        assert exc_info.value.attempts == 1
        assert "GCS linkage" in exc_info.value.reason

    def test_verify_failure_zero_size(self):
        """Verification fails when file size is 0 bytes."""
        mock_client = mock.Mock()
        mock_client.get_item.return_value = {
            "ID": "12345",
            "Name": "test.txt",
            "DataSize": 0,
            "URL": "https://cdn.degoo.com/...",  # Has URL but size is 0
        }

        verifier = UploadVerifier(max_retries=1)

        with pytest.raises(UploadVerificationError) as exc_info:
            verifier.verify_upload(
                mock_client, "12345", "test.txt", 1024, verbose=False
            )

        assert "GCS linkage" in exc_info.value.reason

    def test_verify_failure_api_error(self):
        """Verification handles API errors gracefully."""
        from cligoo.api import DegooAPIError

        mock_client = mock.Mock()
        mock_client.get_item.side_effect = DegooAPIError("API is down")

        verifier = UploadVerifier(max_retries=1)

        with pytest.raises(UploadVerificationError) as exc_info:
            verifier.verify_upload(
                mock_client, "12345", "test.txt", 1024, verbose=False
            )

        assert "API is down" in exc_info.value.reason

    def test_verify_retry_with_exponential_backoff(self):
        """Retries use exponential backoff (2^n seconds)."""
        mock_client = mock.Mock()
        mock_client.get_item.side_effect = [
            {"ID": "12345", "Name": "test.txt", "DataSize": 0, "URL": ""},
            {"ID": "12345", "Name": "test.txt", "DataSize": 0, "URL": ""},
            {
                "ID": "12345",
                "Name": "test.txt",
                "DataSize": 1024,
                "URL": "https://cdn.degoo.com/...",
            },
        ]

        verifier = UploadVerifier(max_retries=3, initial_wait=0.01)

        with mock.patch("time.sleep") as mock_sleep:
            verifier.verify_upload(
                mock_client, "12345", "test.txt", 1024, verbose=False
            )

        # Should sleep between attempts: 1s (2^0), 2s (2^1)
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0][0][0] == 1  # First wait
        assert mock_sleep.call_args_list[1][0][0] == 2  # Second wait

    def test_verify_and_retry_helper(self):
        """Test the convenience function."""
        mock_client = mock.Mock()
        mock_client.get_item.return_value = {
            "ID": "12345",
            "Name": "test.txt",
            "DataSize": 1024,
            "URL": "https://cdn.degoo.com/...",
        }

        result = verify_and_retry(
            mock_client, "12345", "test.txt", 1024, max_retries=3, verbose=False
        )

        assert result["ID"] == "12345"


class TestUploadVerificationError:
    """Tests for UploadVerificationError exception."""

    def test_error_message_formatting(self):
        """Error message includes relevant details."""
        err = UploadVerificationError(
            file_id="12345",
            filename="myfile.txt",
            attempts=3,
            reason="GCS linkage incomplete",
        )

        assert "myfile.txt" in str(err)
        assert "12345" in str(err)
        assert "3" in str(err)
        assert "GCS linkage" in str(err)

    def test_error_attributes(self):
        """Error stores attributes for programmatic access."""
        err = UploadVerificationError(
            file_id="abc123",
            filename="data.bin",
            attempts=2,
            reason="Network timeout",
        )

        assert err.file_id == "abc123"
        assert err.filename == "data.bin"
        assert err.attempts == 2
        assert err.reason == "Network timeout"
