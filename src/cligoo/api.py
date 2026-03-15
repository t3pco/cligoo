"""Degoo GraphQL API client.

Thin wrapper around httpx that sends authenticated GraphQL requests
and returns parsed results.
"""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .auth import get_token
from .constants import (
    CATEGORY_FOLDER,
    CATEGORY_NAMES,
    CHECKSUM_SEED,
    DEFAULT_HEADERS,
    DEFAULT_LIMIT,
    DEFAULT_ORDER,
    FOLDER_CATEGORIES,
    GRAPHQL_URL,
    MAX_LIMIT,
)
from .queries import (
    GET_BUCKET_WRITE_AUTH,
    GET_COLLECTIONS,
    GET_DELETED_FILES,
    GET_FEED,
    GET_FILE_CHILDREN,
    GET_OVERLAY,
    GET_PERMISSIONS,
    GET_SEARCH,
    GET_SHARED,
    SET_COLLECTION,
    SET_DELETE_FILE,
    SET_DELETE_SHARE_FILE,
    SET_DESCRIPTION,
    SET_MOVE_FILE,
    SET_RENAME_FILE,
    SET_SHARE_FILE,
    SET_UPLOAD_FILE,
)


class DegooAPIError(Exception):
    """Raised when the Degoo API returns an error."""


class DegooAlreadyExistsError(DegooAPIError):
    """Raised when Degoo reports the file already exists (deduplication)."""


class _ProgressFile:
    """Read-only file wrapper that calls *callback(bytes_read, total)* on each chunk.

    Passed to ``httpx.post`` as the file body so upload progress is reported
    incrementally as httpx reads from it for the multipart form POST.
    """

    def __init__(self, path: Path, total: int, callback: Callable[[int, int], None]) -> None:
        self._f = open(path, "rb")  # noqa: WPS515
        self._total = total
        self._read = 0
        self._cb = callback

    def read(self, n: int = -1) -> bytes:
        chunk = self._f.read(n)
        self._read += len(chunk)
        self._cb(self._read, self._total)
        return chunk

    def close(self) -> None:
        self._f.close()

    # httpx inspects __len__ to set Content-Length — expose the total size
    def __len__(self) -> int:
        return self._total


class DegooClient:
    """Stateless GraphQL client for the Degoo cloud storage API."""

    def __init__(
        self,
        token: str | None = None,
        timeout: float | None = None,
        graphql_url: str | None = None,
        debug: bool | None = None,
    ) -> None:
        from .config import get_api_debug, get_api_timeout, get_graphql_url

        self._explicit_token = token   # None → use get_token() dynamically
        self._token = token
        self._timeout = timeout if timeout is not None else get_api_timeout()
        self._graphql_url = graphql_url or get_graphql_url() or GRAPHQL_URL
        self._debug = debug if debug is not None else get_api_debug()

        # Copy DEFAULT_HEADERS so httpx normalisation (lower-casing keys etc.)
        # never mutates the shared module-level dict.
        if self._debug:
            import sys

            def _log_req(req: httpx.Request) -> None:
                print(f"[DEBUG] --> {req.method} {req.url}", file=sys.stderr)

            def _log_resp(resp: httpx.Response) -> None:
                print(f"[DEBUG] <-- {resp.status_code} {resp.url}", file=sys.stderr)

            self._http = httpx.Client(
                headers=dict(DEFAULT_HEADERS),
                timeout=self._timeout,
                event_hooks={"request": [_log_req], "response": [_log_resp]},
            )
        else:
            self._http = httpx.Client(headers=dict(DEFAULT_HEADERS), timeout=self._timeout)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> "DegooClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass

    # ── helpers ───────────────────────────────────────────────────────────
    @property
    def token(self) -> str:
        if self._explicit_token is not None:
            # Caller supplied an explicit token (e.g. tests) — honour it as-is.
            return self._explicit_token
        # No explicit token: always go through get_token() so short-lived
        # access tokens are refreshed transparently during long-running shell
        # sessions or bulk uploads/downloads.  get_token() is cheap (keyring
        # read + JWT decode) when the token is still valid.
        self._token = get_token()
        return self._token

    def _gql(self, query: str, variables: dict[str, Any] | None = None, operation: str | None = None) -> Any:
        """Send a GraphQL request and return the ``data`` payload."""
        variables = variables or {}
        variables["Token"] = self.token

        body: dict[str, Any] = {"query": query, "variables": variables}
        if operation:
            body["operationName"] = operation

        resp = self._http.post(self._graphql_url, json=body)
        resp.raise_for_status()
        payload = resp.json()

        if "errors" in payload:
            msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
            raise DegooAPIError(msgs)

        data = payload.get("data")
        if data is None:
            raise DegooAPIError(
                "API returned null data — your token may be expired or invalid. Run `degoo login` to re-authenticate."
            )
        return data

    def _paginate(
        self,
        query: str,
        base_vars: dict[str, Any],
        result_key: str,
        operation: str,
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict]:
        """Shared pagination loop for GraphQL list queries.

        Repeatedly calls *query* with *base_vars*, adding ``NextToken`` on each
        subsequent page, and collects Items from ``data[result_key]``.
        Returns at most *limit* items total.
        """
        items: list[dict] = []
        next_token: Optional[str] = None

        while True:
            variables = dict(base_vars)
            if next_token:
                variables["NextToken"] = next_token

            data = self._gql(query, variables, operation=operation)
            result = data[result_key]
            batch = result.get("Items") or []
            items.extend(batch)

            next_token = result.get("NextToken")
            if not next_token or len(items) >= limit:
                break

        return items[:limit]

    # ═══════════════════════════════════════════════════════════════════════
    # User
    # ═══════════════════════════════════════════════════════════════════════
    def get_user_info(self) -> dict:
        """Return the authenticated user's profile and quota."""
        from .queries import GET_USER_INFO

        data = self._gql(GET_USER_INFO, operation="GetUserInfo3")
        return data["getUserInfo3"]

    # ═══════════════════════════════════════════════════════════════════════
    # File / folder listing
    # ═══════════════════════════════════════════════════════════════════════
    def list_dir(
        self,
        parent_id: str = "0",
        *,
        limit: Optional[int] = DEFAULT_LIMIT,
        order: int = DEFAULT_ORDER,
    ) -> list[dict]:
        """List children of a folder (default: root = ``"0"``).

        Pass ``limit=None`` to fetch **all** pages without a total-count cap.
        Otherwise at most *limit* items are returned across all pages.
        """
        items: list[dict] = []
        next_token: Optional[str] = None

        while True:
            variables: dict[str, Any] = {
                "ParentID": str(parent_id),
                "Limit": MAX_LIMIT,
                "Order": order,
            }
            if next_token:
                variables["NextToken"] = next_token

            data = self._gql(GET_FILE_CHILDREN, variables, operation="GetFileChildren5")
            result = data["getFileChildren5"]
            batch = result.get("Items") or []
            items.extend(batch)

            next_token = result.get("NextToken")
            if not next_token:
                break
            if limit is not None and len(items) >= limit:
                break

        return items if limit is None else items[:limit]

    def get_item(self, item_id: str) -> dict:
        """Get metadata for a single item by ID."""
        data = self._gql(
            GET_OVERLAY,
            {"ID": {"FileID": str(item_id)}},
            operation="GetOverlay4",
        )
        return data["getOverlay4"]

    def get_feed(self, limit: int = 30) -> list[dict]:
        """Get the moments/feed timeline."""
        data = self._gql(GET_FEED, {"Limit": limit}, operation="GetFeed")
        return data["getFeed"] or []

    # ═══════════════════════════════════════════════════════════════════════
    # Search
    # ═══════════════════════════════════════════════════════════════════════
    def search(self, term: str, *, limit: int = DEFAULT_LIMIT) -> list[dict]:
        """Full-text search across all files."""
        return self._paginate(
            GET_SEARCH,
            {"SearchTerm": term, "Limit": min(limit, MAX_LIMIT)},
            "getSearchContent3",
            "GetSearchContent3",
            limit=limit,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Trash / recycle bin
    # ═══════════════════════════════════════════════════════════════════════
    def list_trash(self, *, limit: int = DEFAULT_LIMIT, order: int = DEFAULT_ORDER) -> list[dict]:
        """List items in the recycle bin."""
        return self._paginate(
            GET_DELETED_FILES,
            {"Limit": min(limit, MAX_LIMIT), "Order": order},
            "getDeletedFiles",
            "GetDeletedFiles",
            limit=limit,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Shared / collections
    # ═══════════════════════════════════════════════════════════════════════
    def list_shared(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        include_self: bool = True,
        order_descending: bool = True,
    ) -> list[dict]:
        """List shared items."""
        return self._paginate(
            GET_SHARED,
            {"Limit": min(limit, MAX_LIMIT), "IncludeSelfContent": include_self, "OrderDescending": order_descending},
            "getShared",
            "GetShared",
            limit=limit,
        )

    def list_collections(self, *, limit: int = DEFAULT_LIMIT, order: int = DEFAULT_ORDER) -> list[dict]:
        """List collections (albums)."""
        data = self._gql(
            GET_COLLECTIONS,
            {"Limit": limit, "Order": order},
            operation="GetCollections5",
        )
        result = data["getCollections5"]
        return result.get("Items") or []

    def get_permissions(self, item_id: str) -> dict:
        """Get sharing permissions for an item."""
        data = self._gql(GET_PERMISSIONS, {"ID": str(item_id)}, operation="GetPermissions3")
        return data["getPermissions3"]

    # ═══════════════════════════════════════════════════════════════════════
    # Mutations — create / delete / move / rename
    # ═══════════════════════════════════════════════════════════════════════
    def mkdir(self, name: str, parent_id: str = "0") -> str:
        """Create a folder and return the API response."""
        # Creating a folder: setUploadFile3 with Size=0 and empty Checksum.
        # FileInfoUpload3 fields: Name, ParentID, Size (String), Checksum, CreationTime (String), Data
        # 'Category' is NOT a valid field — Degoo infers folder type from Size=0 + empty Checksum.
        file_info = {
            "Name": name,
            "ParentID": str(parent_id),
            "Size": "0",
            "Checksum": "",
            "CreationTime": str(int(time.time() * 1000)),
        }
        data = self._gql(
            SET_UPLOAD_FILE,
            {"FileInfos": [file_info]},
            operation="SetUploadFile3",
        )
        return data.get("setUploadFile3", "OK")

    def delete(self, item_ids: list[str], *, permanent: bool = False) -> str:
        """Move items to recycle bin, or permanently delete them.

        ``permanent=False`` (default): moves items to the recycle bin.
        ``permanent=True``: deletes items immediately, bypassing the recycle bin.

        The GraphQL field ``IsInRecycleBin`` tells the Degoo API whether to
        treat the items as *already in the recycle bin* and therefore delete
        them permanently.  Passing ``permanent=True`` sets this flag, which is
        the correct signal for a hard-delete.  This is distinct from whether
        the items are actually in the recycle bin at call time.
        """
        ids = [{"FileID": str(i)} for i in item_ids]
        data = self._gql(
            SET_DELETE_FILE,
            {"IDs": ids, "IsInRecycleBin": permanent},
            operation="SetDeleteFile5",
        )
        return data.get("setDeleteFile5", "OK")

    def move(self, file_ids: list[str], new_parent_id: str, *, copy: bool = False) -> str:
        """Move (or copy) items to a new parent folder."""
        data = self._gql(
            SET_MOVE_FILE,
            {
                "FileIDs": [str(i) for i in file_ids],
                "NewParentID": str(new_parent_id),
                "Copy": copy,
            },
            operation="SetMoveFile",
        )
        return data.get("setMoveFile", "OK")

    def rename(self, item_id: str, new_name: str) -> str:
        """Rename an item."""
        data = self._gql(
            SET_RENAME_FILE,
            {"FileRenames": [{"ID": str(item_id), "NewName": new_name}]},
            operation="SetRenameFile",
        )
        return data.get("setRenameFile", "OK")

    def set_description(self, item_id: str, description: str) -> str:
        """Set the description of an item."""
        data = self._gql(
            SET_DESCRIPTION,
            {"ID": str(item_id), "Description": description},
            operation="SetDescription",
        )
        return data.get("setDescription", "OK")

    def share(self, item_id: str, usernames: list[str] | None = None) -> str:
        """Share an item (optionally with specific users)."""
        variables: dict[str, Any] = {"ID": str(item_id)}
        if usernames:
            variables["Usernames"] = usernames
        data = self._gql(SET_SHARE_FILE, variables, operation="SetShareFile")
        return data.get("setShareFile", "OK")

    def unshare(self, item_id: str) -> str:
        """Remove sharing from an item."""
        data = self._gql(SET_DELETE_SHARE_FILE, {"ID": str(item_id)}, operation="SetDeleteShareFile")
        return data.get("setDeleteShareFile", "OK")

    def create_collection(
        self,
        title: str,
        file_ids: list[str] | None = None,
        description: str = "",
    ) -> str:
        """Create a collection (album)."""
        variables: dict[str, Any] = {"Title": title, "Description": description}
        if file_ids:
            variables["FileIDs"] = [str(i) for i in file_ids]
        data = self._gql(SET_COLLECTION, variables, operation="SetCollection2")
        return data.get("setCollection2", "OK")

    # ═══════════════════════════════════════════════════════════════════════
    # Upload
    # ═══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _degoo_checksum(filepath: Path) -> str:
        """Compute the Degoo-specific checksum for a file.

        Algorithm (reverse-engineered from the web app):
          1. SHA1( seed_bytes + first_1MB_of_file )
          2. Wrap digest in a minimal protobuf: \\x0a\\x14 + sha1(20 bytes) + \\x10\\x00
          3. Base64url-encode without padding
        """
        h = hashlib.sha1()
        h.update(bytes(CHECKSUM_SEED))
        with open(filepath, "rb") as f:
            h.update(f.read(1024 * 1024))
        proto = b'\x0a\x14' + h.digest() + b'\x10\x00'
        return base64.urlsafe_b64encode(proto).decode().rstrip('=')

    def _get_upload_auth(self, parent_id: str, filename: str, size: int, checksum: str) -> dict:
        """Get upload authorization (Google Cloud Storage credentials).

        getBucketWriteAuth4 returns a list of BucketWriteAuthInfo objects.
        StorageUploadInfos is required for the server to pre-compute the GCS
        key path using the checksum; omitting it causes the file to be stored
        with a generic key and Size=0 / no download URL in the metadata.
        """
        data = self._gql(
            GET_BUCKET_WRITE_AUTH,
            {
                "ParentID": str(parent_id),
                "StorageUploadInfos": [
                    {
                        "FileName": filename,
                        "Checksum": checksum,
                        "Size": str(size),
                    }
                ],
            },
            operation="GetBucketWriteAuth4",
        )
        result = data["getBucketWriteAuth4"]
        # The API always returns a list; take the first (and only) entry.
        if not result:
            raise DegooAPIError("getBucketWriteAuth4 returned an empty list")
        auth_item = result[0]
        if auth_item.get("Error"):
            err = auth_item["Error"]
            if "already exist" in err.lower():
                raise DegooAlreadyExistsError(f"Upload auth failed: {err}")
            raise DegooAPIError(f"Upload auth failed: {err}")
        return auth_item["AuthData"]

    def upload(
        self,
        filepath: str | Path,
        parent_id: str = "0",
        *,
        name: str | None = None,
        progress_callback: Any = None,
        verify: bool = False,
        max_retries: int = 3,
    ) -> str:
        """Upload a local file to Degoo.

        Returns the file ID from setUploadFile3.

        Args:
            filepath: Path to the file to upload
            parent_id: ID of the destination folder (default: root)
            name: Optional custom name for the uploaded file
            progress_callback: Optional callback for upload progress
            verify: Whether to verify the upload succeeded (detects GCS linkage issues)
            max_retries: Number of verification retry attempts (if verify=True)
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        size = filepath.stat().st_size
        filename = name or filepath.name
        checksum = self._degoo_checksum(filepath)

        # 1. Get upload credentials (checksum is required by StorageUploadInfo2)
        try:
            auth_data = self._get_upload_auth(parent_id, filename, size, checksum)
        except DegooAlreadyExistsError:
            # Content already exists in GCS (global deduplication).
            # If the file is already linked in this folder, skip entirely to
            # avoid creating a duplicate entry.
            existing = self.resolve_path_under(str(parent_id), filename)
            if existing is not None:
                raise  # already in target folder — treat as skip
            # Not yet linked in this folder — call setUploadFile3 to create
            # the metadata entry without re-uploading bytes to GCS.
            file_info = {
                "Checksum": checksum,
                "Name": filename,
                "CreationTime": str(int(time.time() * 1000)),
                "ParentID": str(parent_id),
                "Size": str(size),
            }
            data = self._gql(
                SET_UPLOAD_FILE,
                {"FileInfos": [file_info]},
                operation="SetUploadFile3",
            )
            return data.get("setUploadFile3", "OK")

        # 2. Upload to Google Cloud Storage
        base_url = auth_data["BaseURL"]
        key_prefix = auth_data["KeyPrefix"]

        # Determine MIME type so it satisfies the policy's Content-Type condition
        import mimetypes

        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        form_data: dict[str, Any] = {}
        # AccessKey is a single {Key, Value} object (Google service account ID)
        access_key = auth_data.get("AccessKey")
        if access_key:
            form_data[access_key["Key"]] = access_key["Value"]
        form_data["policy"] = auth_data["PolicyBase64"]
        form_data["signature"] = auth_data["Signature"]
        # GCS key format (required by the policy): {KeyPrefix}{ext}/{checksum}.{ext}
        # e.g. "ADfzPh/6tnxDg/pdf/ChQVgjMd4f9UAnRnJNB8-dCTOpsPLBAA.pdf"
        ext = Path(filename).suffix.lstrip('.').lower() or "bin"
        form_data["key"] = f"{key_prefix}{ext}/{checksum}.{ext}"
        form_data["Content-Type"] = content_type
        if auth_data.get("ACL"):
            form_data["acl"] = auth_data["ACL"]
        # AdditionalBody is a list of {Key, Value} objects (e.g. Cache-Control)
        for kv in auth_data.get("AdditionalBody", []) or []:
            form_data[kv["Key"]] = kv["Value"]

        if progress_callback:
            pf: Any = _ProgressFile(filepath, size, progress_callback)
        else:
            pf = open(filepath, "rb")  # noqa: WPS515
        try:
            files = {"file": (filename, pf, content_type)}
            upload_resp = httpx.post(base_url, data=form_data, files=files, timeout=600)
        finally:
            pf.close()

        if upload_resp.status_code not in (200, 201, 204):
            raise DegooAPIError(f"Upload to storage failed (HTTP {upload_resp.status_code}): {upload_resp.text}")

        # 3. Register the file in Degoo
        # CreationTime must be in milliseconds (JavaScript Date.now() convention).
        # Checksum must be the protobuf-wrapped base64url value computed above.
        file_info = {
            "Checksum": checksum,
            "Name": filename,
            "CreationTime": str(int(time.time() * 1000)),
            "ParentID": str(parent_id),
            "Size": str(size),
        }
        data = self._gql(
            SET_UPLOAD_FILE,
            {"FileInfos": [file_info]},
            operation="SetUploadFile3",
        )
        upload_result = data.get("setUploadFile3", "OK")

        # Try to get the file ID from the response
        file_id: Optional[str] = None
        if upload_result != "OK" and upload_result:
            file_id = str(upload_result)

        # If we don't have the ID yet, find the file by listing the parent
        if not file_id:
            try:
                # Wait a moment for the file to be registered
                time.sleep(0.5)
                items = self.list_dir(parent_id, limit=1)
                # Find the file we just uploaded by name
                for item in items:
                    if item.get("Name") == filename:
                        file_id = item.get("ID")
                        break
            except Exception:
                # If we can't find it, that's OK - verification will fail with more info
                pass

        # Optional: verify the upload succeeded
        if verify and file_id:
            from .upload_verifier import verify_and_retry

            verify_and_retry(
                self, file_id, filename, size, max_retries=max_retries, verbose=False
            )

        return file_id or upload_result

    # ═══════════════════════════════════════════════════════════════════════
    # Download
    # ═══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _unique_local_path(path: "Path") -> "Path":
        """Return *path* if it does not exist, otherwise ``stem (N).suffix``.

        Mimics browser "Save As" behaviour: ``report.pdf`` → ``report (1).pdf``
        → ``report (2).pdf`` …
        """
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        n = 1
        while True:
            candidate = parent / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def download(
        self,
        item_id: str,
        dest: str | Path = ".",
        *,
        name: str | None = None,
        progress_callback: Any = None,
        overwrite: bool = False,
    ) -> Path:
        """Download a file by its Degoo ID.

        Returns the path to the downloaded file.

        If *overwrite* is False (default) and the destination file already
        exists, the download is saved with a numbered suffix instead of
        overwriting the existing file (browser-style: ``report (1).pdf``).
        Pass ``overwrite=True`` to replace the existing file.
        """
        item = self.get_item(item_id)
        url = item.get("URL")
        if not url:
            raise DegooAPIError(f"Item {item_id} has no download URL (may be a folder)")

        filename = name or item.get("Name", f"degoo_{item_id}")
        dest = Path(dest)
        if dest.is_dir():
            dest = dest / filename

        # Validate before opening the network stream to avoid wasting bandwidth
        # when the destination directory does not exist.
        if not dest.parent.exists():
            raise FileNotFoundError(f"Destination directory does not exist: {dest.parent}")

        if not overwrite:
            dest = self._unique_local_path(dest)

        with httpx.stream("GET", url, follow_redirects=True, timeout=600) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)

        return dest

    # ═══════════════════════════════════════════════════════════════════════
    # Utilities
    # ═══════════════════════════════════════════════════════════════════════
    def resolve_path(self, path: str) -> dict | None:
        """Walk a ``/Device/Folder/File`` path and return the item, or None.

        When multiple items share the same name (e.g. a Folder and a Document
        ghost created by Degoo's mkdir), prefer the real Folder (Category=2)
        so that child listings and further path resolution work correctly.
        """
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts:
            return {"ID": "0", "Name": "/", "Category": CATEGORY_FOLDER}

        current_id = "0"
        current_item: dict | None = None

        for part in parts:
            children = self.list_dir(current_id, limit=MAX_LIMIT)
            candidates = [c for c in children if c.get("Name") == part]
            if not candidates:
                return None
            # Prefer real folders over Document ghosts with the same name
            folders = [c for c in candidates if self.is_folder(c)]
            current_item = folders[0] if folders else candidates[0]
            current_id = str(current_item["ID"])

        return current_item

    def resolve_path_under(self, parent_id: str, name: str) -> dict | None:
        """Return the best direct child of *parent_id* whose Name matches *name*.

        Used after ``mkdir`` to recover the ID of the newly created folder.
        When Degoo creates both a proper Folder (Category=2) and a Document
        ghost (Category=6, Size=0) with the same name, prefer the Folder so
        that subsequent child listings work correctly.

        Uses ``list_dir(limit=None)`` to paginate ALL children so items beyond
        position MAX_LIMIT in a very large parent are never missed.
        """
        best: dict | None = None
        for item in self.list_dir(parent_id, limit=None):
            if item.get("Name") != name:
                continue
            if self.is_folder(item):
                return item          # real folder — best possible match
            if best is None:
                best = item          # keep first non-folder match as fallback
        return best

    @staticmethod
    def is_folder(item: dict) -> bool:
        """Return True if the item is a folder-like category."""
        return item.get("Category", 0) in FOLDER_CATEGORIES

    @staticmethod
    def category_name(item: dict) -> str:
        """Return a human-readable category name."""
        return CATEGORY_NAMES.get(item.get("Category", 0), "Unknown")
