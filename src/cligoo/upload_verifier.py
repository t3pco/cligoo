"""Upload verification and retry logic for cligoo.

Handles post-upload verification to detect the GCS linkage issue,
automatic retry with exponential backoff, and user notification.
"""

import time

from .api import DegooAPIError, DegooClient


class UploadVerificationError(Exception):
    """Raised when upload verification fails after all retries."""

    def __init__(self, file_id: str, filename: str, attempts: int, reason: str):
        self.file_id = file_id
        self.filename = filename
        self.attempts = attempts
        self.reason = reason
        super().__init__(
            f"Upload verification failed for '{filename}' (File ID: {file_id}) after {attempts} attempt(s): {reason}"
        )


class UploadVerifier:
    """Verifies uploaded files and handles retry logic."""

    def __init__(self, max_retries: int = 3, initial_wait: float = 1.0):
        """Initialize the verifier.

        Args:
            max_retries: Maximum number of verification attempts
            initial_wait: Initial wait time in seconds (exponential backoff)
        """
        self.max_retries = max_retries
        self.initial_wait = initial_wait

    def verify_upload(
        self,
        client: DegooClient,
        item_id: str,
        filename: str,
        expected_size: int,
        verbose: bool = False,
    ) -> dict:
        """Verify that an uploaded file is accessible.

        Args:
            client: DegooClient instance
            item_id: ID of the uploaded file
            filename: Original filename (for error messages)
            expected_size: Expected file size in bytes
            verbose: Whether to print debug info

        Returns:
            The verified item dict with metadata

        Raises:
            UploadVerificationError: If verification fails after all retries
        """
        for attempt in range(self.max_retries):
            try:
                if attempt > 0:
                    wait_time = min(2 ** (attempt - 1), 30)
                    if verbose:
                        print(f"   [dim]Waiting {wait_time}s for server processing...[/dim]")
                    time.sleep(wait_time)

                # Get the item metadata
                item = client.get_item(item_id)

                # Check if upload is complete
                actual_size = item.get("DataSize") or 0
                has_url = bool(item.get("URL"))

                if verbose:
                    print(
                        f"   [dim]Attempt {attempt + 1}/{self.max_retries}: Size={actual_size}, HasURL={has_url}[/dim]"
                    )

                # Success: file is accessible
                if has_url and actual_size > 0:
                    if verbose and attempt > 0:
                        print(f"   [green]✓ Upload verified after {attempt + 1} attempt(s)[/green]")
                    return item

                # Size is 0 and no URL - GCS linkage issue
                if not has_url or actual_size == 0:
                    reason = "GCS linkage incomplete (Size=0 Bytes or no download URL)"
                    if attempt < self.max_retries - 1:
                        if verbose:
                            print(f"   [yellow]⚠ {reason} - retrying...[/yellow]")
                        continue

                    # Final attempt failed
                    raise UploadVerificationError(
                        file_id=item_id,
                        filename=filename,
                        attempts=self.max_retries,
                        reason=reason,
                    )

            except DegooAPIError as e:
                if attempt < self.max_retries - 1:
                    if verbose:
                        print(f"   [yellow]⚠ API error: {e} - retrying...[/yellow]")
                    continue

                raise UploadVerificationError(
                    file_id=item_id,
                    filename=filename,
                    attempts=self.max_retries,
                    reason=str(e),
                )

        # All retries exhausted
        raise UploadVerificationError(
            file_id=item_id,
            filename=filename,
            attempts=self.max_retries,
            reason="Maximum retry attempts exceeded",
        )


def verify_and_retry(
    client: DegooClient,
    item_id: str,
    filename: str,
    expected_size: int,
    max_retries: int = 3,
    verbose: bool = False,
) -> dict:
    """Verify an upload with retry logic.

    Convenience function that creates a verifier and checks the upload.

    Args:
        client: DegooClient instance
        item_id: ID of the uploaded file
        filename: Original filename
        expected_size: Expected file size
        max_retries: Number of retry attempts
        verbose: Whether to print status messages

    Returns:
        The verified item dict

    Raises:
        UploadVerificationError: If verification fails after all retries
    """
    verifier = UploadVerifier(max_retries=max_retries)
    return verifier.verify_upload(
        client=client,
        item_id=item_id,
        filename=filename,
        expected_size=expected_size,
        verbose=verbose,
    )
