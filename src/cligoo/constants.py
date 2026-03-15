"""Degoo API constants — endpoints, categories, and GraphQL operations."""

# ── Endpoints ──────────────────────────────────────────────────────────────────
GRAPHQL_URL = "https://production-appsync.degoo.com/graphql"
LOGIN_URL = "https://rest-api.degoo.com/login"
REGISTER_URL = "https://rest-api.degoo.com/register"
TOKEN_REFRESH_URL = "https://rest-api.degoo.com/access-token/v2"

# ── API Key (AWS AppSync) ─────────────────────────────────────────────────────
# This is the public AppSync client key embedded in the Degoo web application.
# It is not a secret — it is visible in any browser's network inspector when
# visiting app.degoo.com.  If Degoo rotates the key you can override it without
# reinstalling via two mechanisms (highest priority first):
#   1. Environment variable:  DEGOO_API_KEY=<new-key>
#   2. Config file entry:     "api_key": "<new-key>"  in
#                             ~/.config/cligoo/config.json
_BUNDLED_API_KEY = "da2-vs6twz5vnjdavpqndtbzg3prra"


def _resolve_api_key() -> str:
    try:
        from .config import get_api_key

        return get_api_key() or _BUNDLED_API_KEY
    except Exception:
        return _BUNDLED_API_KEY


API_KEY: str = _resolve_api_key()
del _resolve_api_key

# ── HTTP headers template ─────────────────────────────────────────────────────
DEFAULT_HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json",
}

# ── Item categories ───────────────────────────────────────────────────────────
CATEGORY_FILE = 0
CATEGORY_DEVICE = 1
CATEGORY_FOLDER = 2
CATEGORY_IMAGE = 3
CATEGORY_VIDEO = 4
CATEGORY_MUSIC = 5
CATEGORY_DOCUMENT = 6
CATEGORY_RECYCLE_BIN = 10

CATEGORY_NAMES = {
    0: "File",
    1: "Device",
    2: "Folder",
    3: "Image",
    4: "Video",
    5: "Music",
    6: "Document",
    10: "Recycle Bin",
}

FOLDER_CATEGORIES = {CATEGORY_DEVICE, CATEGORY_FOLDER, CATEGORY_RECYCLE_BIN}

# ── Default item properties returned by queries ───────────────────────────────
# Full set — used by GetFileChildren5, GetOverlay4, GetFeed, GetDeletedFiles, GetShared
ITEM_PROPERTIES = """
    ID
    MetadataID
    UserID
    DeviceID
    MetadataKey
    Name
    FilePath
    LocalPath
    LastUploadTime
    LastModificationTime
    ParentID
    Category
    Size
    Platform
    URL
    ThumbnailURL
    CreationTime
    IsSelfLiked
    Likes
    IsHidden
    IsInRecycleBin
    Description
    Country
    Province
    Place
    Location2 {
        Country
        Province
        Place
        GeoLocation {
            Latitude
            Longitude
        }
    }
    GeoLocation {
        Latitude
        Longitude
    }
    Data
    DataBlock
    CompressionParameters
    Shareinfo {
        Status
        ShareTime
    }
    ShareInfo {
        Status
        ShareTime
    }
"""

# Slim set for GetSearchContent3 — ContentView2 does not define Country, Province,
# Place, GeoLocation at the top level, and Location2 only carries Country there.
SEARCH_ITEM_PROPERTIES = """
    ID
    MetadataID
    UserID
    DeviceID
    MetadataKey
    Name
    FilePath
    LocalPath
    LastUploadTime
    LastModificationTime
    ParentID
    Category
    Size
    Platform
    URL
    ThumbnailURL
    CreationTime
    IsSelfLiked
    Likes
    IsHidden
    IsInRecycleBin
    Description
    Location2 {
        Country
    }
    Data
    DataBlock
    CompressionParameters
    Shareinfo {
        Status
        ShareTime
    }
    ShareInfo {
        Status
        ShareTime
    }
"""

# ── Pagination ────────────────────────────────────────────────────────────────
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
DEFAULT_ORDER = 3  # by date descending

# ── Upload checksum seed ──────────────────────────────────────────────────────
CHECKSUM_SEED = [13, 7, 2, 2, 15, 40, 75, 117, 13, 10, 19, 16, 29, 23, 3, 36]
