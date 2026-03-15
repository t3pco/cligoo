"""Tests for cligoo.queries — ensure all GraphQL operations are well-formed."""

from cligoo import queries


def _assert_graphql(op: str, kind: str, name: str):
    """Check that an operation string contains the expected kind and name."""
    assert kind in op, f"Expected '{kind}' in operation"
    assert name in op, f"Expected '{name}' in operation"
    assert "Token" in op, f"Expected 'Token' variable in {name}"


# ── Queries ───────────────────────────────────────────────────────────────────
def test_get_user_info():
    _assert_graphql(queries.GET_USER_INFO, "query", "GetUserInfo3")


def test_get_file_children():
    _assert_graphql(queries.GET_FILE_CHILDREN, "query", "GetFileChildren5")
    assert "ParentID" in queries.GET_FILE_CHILDREN
    assert "Limit" in queries.GET_FILE_CHILDREN
    assert "NextToken" in queries.GET_FILE_CHILDREN


def test_get_overlay():
    _assert_graphql(queries.GET_OVERLAY, "query", "GetOverlay4")


def test_get_feed():
    _assert_graphql(queries.GET_FEED, "query", "GetFeed")


def test_get_search():
    _assert_graphql(queries.GET_SEARCH, "query", "GetSearchContent3")
    assert "SearchTerm" in queries.GET_SEARCH


def test_get_deleted_files():
    _assert_graphql(queries.GET_DELETED_FILES, "query", "GetDeletedFiles")


def test_get_bucket_write_auth():
    _assert_graphql(queries.GET_BUCKET_WRITE_AUTH, "query", "GetBucketWriteAuth4")
    assert "StorageUploadInfos" in queries.GET_BUCKET_WRITE_AUTH


def test_get_collections():
    _assert_graphql(queries.GET_COLLECTIONS, "query", "GetCollections5")


def test_get_shared():
    _assert_graphql(queries.GET_SHARED, "query", "GetShared")


def test_get_permissions():
    _assert_graphql(queries.GET_PERMISSIONS, "query", "GetPermissions3")


# ── Mutations ─────────────────────────────────────────────────────────────────
def test_set_upload_file():
    _assert_graphql(queries.SET_UPLOAD_FILE, "mutation", "SetUploadFile3")


def test_set_delete_file():
    _assert_graphql(queries.SET_DELETE_FILE, "mutation", "SetDeleteFile5")
    assert "IsInRecycleBin" in queries.SET_DELETE_FILE


def test_set_move_file():
    _assert_graphql(queries.SET_MOVE_FILE, "mutation", "SetMoveFile")
    assert "NewParentID" in queries.SET_MOVE_FILE


def test_set_rename_file():
    _assert_graphql(queries.SET_RENAME_FILE, "mutation", "SetRenameFile")


def test_set_share_file():
    _assert_graphql(queries.SET_SHARE_FILE, "mutation", "SetShareFile")


def test_set_delete_share_file():
    _assert_graphql(queries.SET_DELETE_SHARE_FILE, "mutation", "SetDeleteShareFile")


def test_set_collection():
    _assert_graphql(queries.SET_COLLECTION, "mutation", "SetCollection2")


def test_set_description():
    _assert_graphql(queries.SET_DESCRIPTION, "mutation", "SetDescription")
