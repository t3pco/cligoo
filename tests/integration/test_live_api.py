"""Live integration tests for cligoo — uses the real Degoo API.

These tests require valid credentials stored via `degoo login`.
They create all test data inside a dedicated test folder and always
clean up after themselves — no existing data is touched.

Run with:
    pytest tests/integration/ -v -m integration

Or together with unit tests:
    pytest -v -m "not integration" tests/   # unit only (default CI)
    pytest -v -m integration tests/integration/  # live only
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

# ── Skip marker ───────────────────────────────────────────────────────────────
pytestmark = pytest.mark.integration


# ── Fixture: isolated test folder ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """Return an authenticated DegooClient."""
    from cligoo.api import DegooClient

    return DegooClient()


@pytest.fixture(scope="module")
def web_device_id(client):
    """Return the ID of the 'Web' device (root-level device folder)."""
    items = client.list_dir("0", limit=20)
    for item in items:
        if item.get("Name") == "Web":
            return str(item["ID"])
    # Fallback: use first device
    if items:
        return str(items[0]["ID"])
    pytest.skip("No devices found in root")


@pytest.fixture(scope="module")
def test_folder(client, web_device_id):
    """Create a throwaway test folder in Web; delete it after all tests.

    Degoo API behaviour discovered through testing:
    - setUploadFile3 initially creates a Category 6 (Document) placeholder.
    - Only after items are created *inside* the placeholder does Degoo's backend
      asynchronously promote it to a proper Category 2 (Folder) — with a NEW ID.
    - Empty Cat 6 placeholders are never promoted.
    Strategy: create the Cat 6 folder, immediately create a sentinel item inside
    it to trigger the promotion, then poll until the Cat 2 version appears.
    """
    folder_name = f"cligoo-regression-{int(time.time())}"
    client.mkdir(folder_name, web_device_id)

    # Find the Cat 6 placeholder (visible immediately at device level)
    cat6_id: str | None = None
    for _ in range(10):
        time.sleep(2)
        children = client.list_dir(web_device_id, limit=50)
        folder = next((c for c in children if c["Name"] == folder_name), None)
        if folder is not None:
            cat6_id = str(folder["ID"])
            break
    if cat6_id is None:
        pytest.skip(f"Could not find newly created folder '{folder_name}'")

    # Create a sentinel item inside the Cat 6 folder; this triggers the
    # backend to generate the real Category 2 folder asynchronously.
    sentinel_name = ".test-sentinel"
    client.mkdir(sentinel_name, cat6_id)

    # Poll until the Category 2 version appears (typically 1-3 min).
    # Degoo creates it with a DIFFERENT ID from the Cat 6 placeholder.
    folder_id = cat6_id  # fallback if promotion never happens
    for _ in range(60):  # up to ~3 min
        time.sleep(3)
        children = client.list_dir(web_device_id, limit=50)
        real_folder = next(
            (c for c in children if c["Name"] == folder_name and c.get("Category") == 2),
            None,
        )
        if real_folder is not None:
            folder_id = str(real_folder["ID"])
            break

    yield {"id": folder_id, "name": folder_name, "parent_id": web_device_id}

    # Teardown: move to trash (includes sentinel + any test-created children)
    try:
        client.delete([folder_id], permanent=False)
    except Exception as e:
        print(f"[warn] cleanup failed for {folder_id}: {e}")


# ── Read-only queries (no state changes) ─────────────────────────────────────


class TestReadOnly:
    def test_whoami(self, client):
        info = client.get_user_info()
        assert "Name" in info or "Email" in info
        assert "TotalQuota" in info

    def test_quota_values_sensible(self, client):
        info = client.get_user_info()
        used = int(info.get("UsedQuota") or 0)
        total = int(info.get("TotalQuota") or 1)
        assert 0 <= used <= total

    def test_list_root(self, client):
        items = client.list_dir("0", limit=10)
        assert isinstance(items, list)
        # Root should have at least one device
        assert len(items) >= 1
        # All items should have IDs and Names
        for item in items:
            assert "ID" in item
            assert "Name" in item

    def test_list_web_device(self, client, web_device_id):
        items = client.list_dir(web_device_id, limit=10)
        assert isinstance(items, list)

    def test_get_feed(self, client):
        items = client.get_feed(limit=5)
        assert isinstance(items, list)

    def test_list_trash(self, client):
        items = client.list_trash(limit=5)
        assert isinstance(items, list)

    def test_list_shared(self, client):
        items = client.list_shared(limit=5)
        assert isinstance(items, list)

    def test_search_returns_list(self, client):
        results = client.search("2014", limit=3)
        assert isinstance(results, list)

    def test_search_result_fields(self, client):
        results = client.search("2014", limit=3)
        for r in results:
            assert "ID" in r
            assert "Name" in r
            assert "Category" in r


# ── Write operations (all inside test_folder) ─────────────────────────────────


class TestWrite:
    """Sequential write tests — order matters because they share state."""

    # Shared state across methods in this class
    uploaded_file_id: str = ""
    subfolder_id: str = ""

    def test_01_mkdir(self, client, test_folder):
        """Create a subfolder inside the test folder."""
        subfolder_name = f"sub-{int(time.time())}"
        client.mkdir(subfolder_name, test_folder["id"])

        # Degoo's API has eventual consistency — retry for up to 30 s
        # (parent is already Category 2 by the time tests reach here)
        sub = None
        for _ in range(6):
            time.sleep(5)
            children = client.list_dir(test_folder["id"], limit=20)
            sub = next((c for c in children if c["Name"] == subfolder_name), None)
            if sub is not None:
                break

        assert sub is not None, f"Subfolder '{subfolder_name}' not found after mkdir (even after retries)"
        TestWrite.subfolder_id = str(sub["ID"])

    def test_02_upload(self, client, test_folder):
        """Upload a small text file to the test folder."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"cligoo regression test content\n")
            fpath = Path(f.name)

        try:
            client.upload(fpath, test_folder["id"])
        finally:
            fpath.unlink(missing_ok=True)

        # Degoo's API has eventual consistency — retry for up to 30 s
        # (parent is already Category 2 by the time tests reach here)
        uploaded = None
        for _ in range(6):
            time.sleep(5)
            children = client.list_dir(test_folder["id"], limit=20)
            uploaded = next((c for c in children if c["Name"] == fpath.name), None)
            if uploaded is not None:
                break

        assert uploaded is not None, f"Uploaded file '{fpath.name}' not found (even after retries)"
        TestWrite.uploaded_file_id = str(uploaded["ID"])

    def test_03_get_item(self, client):
        """get_item returns the uploaded file's metadata."""
        if not TestWrite.uploaded_file_id:
            pytest.skip("test_02_upload did not run")
        item = client.get_item(TestWrite.uploaded_file_id)
        assert item["ID"] == TestWrite.uploaded_file_id
        assert "Name" in item
        assert "Category" in item

    def test_04_rename(self, client):
        """Rename the uploaded file."""
        if not TestWrite.uploaded_file_id:
            pytest.skip("test_02_upload did not run")
        new_name = "renamed_regression.txt"
        result = client.rename(TestWrite.uploaded_file_id, new_name)
        assert result is not None  # True or a response string

    def test_05_move_to_subfolder(self, client, test_folder):
        """Move the renamed file into the subfolder."""
        if not TestWrite.uploaded_file_id or not TestWrite.subfolder_id:
            pytest.skip("previous tests did not run")
        client.move([TestWrite.uploaded_file_id], TestWrite.subfolder_id)
        # File should no longer be in parent
        parent_children = client.list_dir(test_folder["id"], limit=20)
        ids_in_parent = {str(c["ID"]) for c in parent_children}
        assert TestWrite.uploaded_file_id not in ids_in_parent

    def test_06_copy_back_to_test_folder(self, client, test_folder):
        """Copy the file back to the test folder root."""
        if not TestWrite.uploaded_file_id or not TestWrite.subfolder_id:
            pytest.skip("previous tests did not run")
        client.move([TestWrite.uploaded_file_id], test_folder["id"], copy=True)
        # A copy should now exist in test_folder
        children = client.list_dir(test_folder["id"], limit=20)
        assert len(children) >= 1

    def test_07_download(self, client, test_folder):
        """Download one of the files back to a temp location."""
        if not TestWrite.subfolder_id:
            pytest.skip("subfolder not created")
        sub_children = client.list_dir(TestWrite.subfolder_id, limit=5)
        if not sub_children:
            pytest.skip("nothing in subfolder to download")
        item = sub_children[0]
        url = item.get("URL", "")
        if not url:
            pytest.skip("item has no download URL yet (Degoo may still be processing)")
        with tempfile.TemporaryDirectory() as td:
            dest = client.download(item["ID"], td)
            assert dest.exists()
            assert dest.stat().st_size > 0

    def test_08_rm_to_trash(self, client, test_folder):
        """Delete (to trash) all items we created inside the test folder."""
        children = client.list_dir(test_folder["id"], limit=50)
        if children:
            ids = [str(c["ID"]) for c in children]
            result = client.delete(ids, permanent=False)
            assert result is not None


# ── CLI round-trip via Click runner ──────────────────────────────────────────


class TestCLI:
    """Run CLI commands end-to-end via click.testing.CliRunner."""

    def test_cli_whoami(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["whoami"])
        assert result.exit_code == 0, result.output
        assert "@" in result.output or "Name" in result.output

    def test_cli_quota(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["quota"])
        assert result.exit_code == 0, result.output
        assert "Used" in result.output

    def test_cli_ls_root(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["ls", "/"])
        assert result.exit_code == 0, result.output

    def test_cli_ls_depth(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["ls", "/", "-d", "1"])
        assert result.exit_code == 0, result.output

    def test_cli_tree(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["tree", "/", "--depth", "1"])
        assert result.exit_code == 0, result.output

    def test_cli_search(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["search", "2014", "--limit", "3"])
        assert result.exit_code == 0, result.output

    def test_cli_search_no_args_shows_help(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        # mix_stderr was removed in Click 8.3.1
        result = CliRunner().invoke(main, ["search"])
        assert "Usage:" in result.output

    def test_cli_trash(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["trash", "--limit", "5"])
        assert result.exit_code == 0, result.output

    def test_cli_shared(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["shared", "--limit", "5"])
        assert result.exit_code == 0, result.output

    def test_cli_feed(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        result = CliRunner().invoke(main, ["feed", "--limit", "5"])
        assert result.exit_code == 0, result.output

    def test_cli_info(self):
        """info on a known real item (first item in root listing)."""
        from click.testing import CliRunner

        from cligoo.api import DegooClient
        from cligoo.cli import main

        client = DegooClient()
        items = client.list_dir("0", limit=1)
        if not items:
            pytest.skip("No items in root")
        item_id = str(items[0]["ID"])
        result = CliRunner().invoke(main, ["info", item_id])
        assert result.exit_code == 0, result.output
        assert items[0]["Name"] in result.output

    def test_cli_cd_and_pwd(self, tmp_path):
        from click.testing import CliRunner

        import cligoo.cli as cli_module
        from cligoo.cli import main

        cwd_file = tmp_path / "cwd.json"
        runner = CliRunner()
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(cli_module, "_CWD_FILE", cwd_file)
            res1 = runner.invoke(main, ["cd", "/"])
            assert res1.exit_code == 0, res1.output
            res2 = runner.invoke(main, ["pwd"])
            assert res2.exit_code == 0, res2.output
            assert "/" in res2.output

    def test_cli_missing_args_show_help(self):
        from click.testing import CliRunner

        from cligoo.cli import main

        # mix_stderr was removed in Click 8.3.1
        for cmd in ["search", "info", "rename", "mv", "cp"]:
            result = CliRunner().invoke(main, [cmd])
            assert "Usage:" in result.output, f"{cmd}: expected help in output, got: {result.output[:200]}"

    def test_cli_upload_and_rm(self, test_folder):
        """Upload a file to the test folder then move it to trash."""
        from click.testing import CliRunner

        from cligoo.cli import main

        runner = CliRunner()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"cli upload test\n")
            fpath = Path(f.name)

        try:
            # Path must include the device prefix, e.g. /Web/<folder>
            res = runner.invoke(main, ["upload", str(fpath), "--dest", f"/Web/{test_folder['name']}"])
            assert res.exit_code == 0, res.output
        finally:
            fpath.unlink(missing_ok=True)

        # Find the newly uploaded file
        from cligoo.api import DegooClient

        client = DegooClient()
        children = client.list_dir(test_folder["id"], limit=20)
        uploaded = next((c for c in children if c["Name"] == fpath.name), None)
        if uploaded:
            res2 = runner.invoke(main, ["rm", str(uploaded["ID"])])
            assert res2.exit_code == 0, res2.output
