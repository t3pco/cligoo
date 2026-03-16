# Degoo API — Internal Reference for Developers & AI Agents

> **Audience**: contributors extending the CLI, AI agents picking up the project,
> or anyone who needs to re-examine the API after a Degoo update.
>
> **How this was produced**: by intercepting network traffic from the Degoo web
> app (`app.degoo.com`) using Chrome DevTools, then cross-referencing with
> community work at [bernd-wechner/Degoo](https://github.com/bernd-wechner/Degoo).
> Nothing here required special access — all requests were made by the normal
> browser session after a standard Google OAuth login.

---

## Table of Contents

- [1. Discovery Methodology](#1-discovery-methodology)
- [2. Endpoints](#2-endpoints)
- [3. Authentication Architecture](#3-authentication-architecture)
  - [3a. REST Login (email/password)](#3a-rest-login-emailpassword)
  - [3b. Token Refresh](#3b-token-refresh)
  - [3c. Access Token (JWT)](#3c-access-token-jwt)
  - [3d. AWS AppSync API Key](#3d-aws-appsync-api-key)
  - [3e. Google OAuth / Refresh Token via Browser](#3e-google-oauth--refresh-token-via-browser)
  - [3f. Chrome Profile Seeding (zero-interaction login)](#3f-chrome-profile-seeding-zero-interaction-login)
- [4. GraphQL API](#4-graphql-api)
  - [4a. Request envelope](#4a-request-envelope)
  - [4b. Error envelope](#4b-error-envelope)
  - [4c. Operation versioning](#4c-operation-versioning)
- [5. Known Queries (full signatures)](#5-known-queries-full-signatures)
- [6. Known Mutations (full signatures)](#6-known-mutations-full-signatures)
- [7. Item Data Model](#7-item-data-model)
- [8. Item Categories](#8-item-categories)
- [9. Upload Flow](#9-upload-flow)
  - [9a. Degoo Checksum Algorithm](#9a-degoo-checksum-algorithm)
  - [9b. GCS Object Key Format](#9b-gcs-object-key-format)
  - [9c. CreationTime — milliseconds, not seconds](#9c-creationtime--milliseconds-not-seconds)
  - [9d. Download Flow](#9d-download-flow)
- [10. Pagination](#10-pagination)
- [11. Known Unknowns / Gaps](#11-known-unknowns--gaps)
- [12. How to Re-examine After a Degoo Update](#12-how-to-re-examine-after-a-degoo-update)
- [13. GraphQL Schema Introspection](#13-graphql-schema-introspection)
- [14. Source Code Map](#14-source-code-map)
- [15. Discovered Field Constraints (2026-03 Introspection)](#15-discovered-field-constraints-2026-03-introspection)
- [16. Change Log](#16-change-log)
- [17. Two-Phase Folder Creation (Cat 6 → Cat 2 Promotion)](#17-two-phase-folder-creation-cat-6--cat-2-promotion)
- [18. Implementation Pitfalls (lessons learnt)](#18-implementation-pitfalls-lessons-learnt)
  - [18a. Token fetch — pass the token to DegooClient](#18a-token-fetch--pass-the-token-to-degooclient)
  - [18b. DEFAULT_HEADERS is a shared module-level dict — always copy it](#18b-default_headers-is-a-shared-module-level-dict--always-copy-it)
  - [18c. save_config() must be atomic](#18c-save_config-must-be-atomic)
  - [18d. Always use encoding="utf-8" for token/credential files](#18d-always-use-encodingutf-8-for-tokencredential-files)
  - [18e. Tab-completion glob patterns must escape user input](#18e-tab-completion-glob-patterns-must-escape-user-input)
  - [18f. Shell subcommands — always check the return code](#18f-shell-subcommands--always-check-the-return-code)

---

## 1. Discovery Methodology

### 1a. How to re-examine the API manually

1. Open `https://app.degoo.com` in Chrome and sign in (Google OAuth or email)
2. Open **DevTools** → **Network** tab → filter by **Fetch/XHR**
3. Reload the page and browse around (upload a file, open a folder, etc.)
4. Look for requests to `production-appsync.degoo.com` — these are the GraphQL calls
5. For each request, inspect:
   - **Headers** → `x-api-key`, `Content-Type`, any auth headers
   - **Payload** → `query`, `variables` (includes the `Token` JWT)
   - **Response** → the data shape returned

### 1b. Automated re-examination

Run the probe script to test all known operations against live credentials:

```bash
python scripts/probe_api.py              # uses stored token
python scripts/probe_api.py --json       # machine-readable output
python scripts/probe_api.py --introspect # attempt GraphQL schema introspection
```

See [`scripts/probe_api.py`](../scripts/probe_api.py) for full documentation.

---

## 2. Endpoints

| Name | URL | Protocol | Purpose |
| --- | --- | --- | --- |
| GraphQL API | `https://production-appsync.degoo.com/graphql` | GraphQL over HTTPS POST | All file/folder operations |
| Login | `https://rest-api.degoo.com/login` | REST POST JSON | Email+password authentication |
| Token refresh | `https://rest-api.degoo.com/access-token/v2` | REST POST JSON | Exchange refresh token for access token |
| Register | `https://rest-api.degoo.com/register` | REST POST JSON | New account creation (not implemented) |
| Storage upload | Dynamic URL returned by `getBucketWriteAuth4` | HTTPS multipart POST | Upload file bytes to Google Cloud Storage |

---

## 3. Authentication Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AUTHENTICATION FLOWS                                     │
│                                                                             │
│  Email / password                                                           │
│  ─────────────────                                                          │
│  POST /login  →  {RefreshToken}  →  POST /access-token/v2  →  {Token}     │
│                                                                             │
│  Google OAuth (browser)                                                     │
│  ──────────────────────                                                     │
│  OAuth dance inside app.degoo.com  →  HttpOnly cookie (refresh JWT)        │
│                                   →  variables.Token in GraphQL requests   │
│                                                                             │
│  Both flows result in two tokens:                                           │
│    • Access token  (short-lived ~1 h, JWT HS256, passed per-request)       │
│    • Refresh token (long-lived,  JWT,  stored HttpOnly or in keyring)      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3a. REST Login (email/password)

```http
POST https://rest-api.degoo.com/login
Content-Type: application/json

{
  "GenerateToken": true,
  "Username": "user@example.com",
  "Password": "hunter2"
}
```

**Success response (HTTP 200):**

```json
{
  "RefreshToken": "<long-lived JWT or opaque token>",
  "Token": "<may also be present>"
}
```

The response contains either `RefreshToken` or `Token` (or both). The value is
exchanged immediately for a short-lived access token via `/access-token/v2`.

**Known failure modes:**

- HTTP 401 → wrong credentials
- HTTP 504 → endpoint temporarily down (common; retry)
- HTTP 200 with empty `{}` → account uses Google OAuth, no password set

### 3b. Token Refresh

```http
POST https://rest-api.degoo.com/access-token/v2
Content-Type: application/json

{"RefreshToken": "<value-from-login>"}
```

**Success response:**

```json
{
  "Token": "<short-lived access JWT>",
  "AccessToken": "<same field, alt name>"
}
```

### 3c. Access Token (JWT)

- Algorithm: **HS256** (symmetric — secret is Degoo-side)
- Payload fields observed: `exp`, `iat`, `sub` (user ID), possibly `email`
- Typical lifetime: ~1 hour (`exp - iat ≈ 3600`)
- Passed as `variables.Token` in **every** GraphQL request body
- **Not** passed as an HTTP `Authorization` header

### 3d. AWS AppSync API Key

The GraphQL endpoint is an **AWS AppSync** service. All requests also carry:

```text
x-api-key: da2-vs6twz5vnjdavpqndtbzg3prra
```

This key is **public** — embedded in the JS bundle at `app.degoo.com` and
does not change per-user. Its role is endpoint-level throttling / routing, not
per-user authentication (that is handled by the JWT).

**Override mechanism** (if Degoo rotates the key without a new CLI release):

1. `DEGOO_API_KEY` environment variable — highest priority
2. `"api_key"` field in `~/.config/cligoo/config.toml` (`[api]` section)
3. Bundled default in `constants.py` — used when neither override is present

The key is resolved once at module import time by `_resolve_api_key()` in
`constants.py`. `DEFAULT_HEADERS` is built from the resolved value and then
**copied** (not referenced) by each `DegooClient` instance so httpx header
normalisation cannot mutate the shared module-level dict.

### 3e. Google OAuth / Refresh Token via Browser

The Google OAuth flow is entirely inside `app.degoo.com`. After Google redirects
back, Degoo issues:

- An **access token** visible in the `variables.Token` field of the first
  GraphQL request
- A **refresh token** stored in an **HttpOnly cookie** (not accessible to JS)

The Playwright-based `cligoo login --browser` flow captures both by:

1. Intercepting the first authenticated GraphQL POST body → access token
2. Calling `page.context.cookies()` via CDP → reads HttpOnly cookies → refresh token

### 3f. Chrome Profile Seeding (zero-interaction login)

When a Chrome profile is configured via `cligoo config`, `fetch_token_via_browser()`
seeds a fresh temporary `user_data_dir` with that profile's session cookies before
launching Chrome.  This allows the token to be captured without any user
interaction when the existing Degoo session is still valid.

**Mechanism:**

```bash
cligoo config selects "Profile 3"
  → saves chrome_profile = "Profile 3" to config.toml ([session])

cligoo login --browser:
  1. reads config.toml → profile_dir = "Profile 3"
  2. creates tmp_dir = mkdtemp("degoo-login-")
  3. copies ~/Library/Application Support/Google/Chrome/Profile 3/Cookies
            → tmp_dir/Default/Cookies
  4. copies .../Profile 3/Login Data → tmp_dir/Default/Login Data  (if exists)
  5. launches Chrome with user_data_dir=tmp_dir  (no conflict — different path)
  6. navigates to app.degoo.com
  7. Degoo session cookie is present → page loads authenticated
  8. first GraphQL POST fires → token intercepted → browser closes
  9. tmp_dir deleted
```

**Why the copy works (cookie encryption):**

Chrome encrypts cookie values using a key stored in the platform credential
store — macOS Keychain (`Chrome Safe Storage`), Linux GNOME Keyring, Windows
DPAPI. The key is scoped to the **OS user account**, not to any specific profile
directory. A Playwright-launched Chrome with the copied `Cookies` file therefore
decrypts them successfully on the same machine.

**Critical: Playwright's `--use-mock-keychain` flag must be suppressed**

Playwright injects `--use-mock-keychain` and `--password-store=basic` into
every Chrome launch by default (via `chromiumSwitches.js`). These flags tell
Chrome to use a fake Keychain that has no real keys, so **all encrypted cookie
values silently decrypt to garbage**. The session cookies are present in the
database but are effectively invisible to Chrome — Degoo redirects to the login
page even with a perfectly valid copied `Cookies` file.

The fix is to include both flags in `ignore_default_args`:

```python
ignore_default_args=[
    "--enable-automation",
    "--use-mock-keychain",    # ← prevents real Keychain access
    "--password-store=basic", # ← same effect via password store path
]
```

This applies to all launch paths. For fresh profiles it has no visible effect
(no pre-existing cookies to decrypt), but keeping the flag list consistent
ensures cookies acquired during login are also written with real Keychain
encryption rather than a mock key that is lost when the process exits.

**Conflict avoidance:**

The copy uses a completely different directory from the real Chrome user data
directory. Chrome's `SingletonLock` (which prevents two processes from owning
the same `user_data_dir`) does not apply. Whether Chrome is running or not with
the original profile is irrelevant — there is no conflict.

**Graceful degradation:**

| Scenario | Result |
| --- | --- |
| Both Google + Degoo sessions active | Token captured in < 5 s, zero user interaction |
| Google session valid, Degoo expired | Degoo redirects to Google OAuth → Google auto-approves → minimal interaction |
| Both sessions expired | Normal Google login screen — same as unconfigured behaviour |
| Chrome not installed | Playwright Chromium fallback (fresh temp profile, no cookie seeding) |

---

## 4. GraphQL API

### 4a. Request envelope

Every request is a standard GraphQL POST:

```http
POST https://production-appsync.degoo.com/graphql
Content-Type: application/json
x-api-key: da2-vs6twz5vnjdavpqndtbzg3prra

{
  "operationName": "GetUserInfo3",
  "query": "query GetUserInfo3($Token: String!) { getUserInfo3(Token: $Token) { ... } }",
  "variables": {
    "Token": "<access JWT>",
    ...other variables...
  }
}
```

### 4b. Error envelope

Errors are returned as HTTP 200 with a GraphQL error body:

```json
{
  "errors": [
    {
      "message": "UnauthorizedException",
      "locations": [...],
      "path": [...],
      "extensions": {"errorType": "Unauthorized"}
    }
  ],
  "data": null
}
```

Common error strings: `"UnauthorizedException"`, `"Token expired"`,
`"Not found"`.

### 4c. Operation versioning

Degoo versions their operations with numeric suffixes (`3`, `4`, `5`…).
When they update an operation, they typically add a new numbered version
alongside the old one. The CLI targets the **highest known version** of each
operation. If an operation stops working, check DevTools for a newer version.

| Operation | Current version | Notes |
| --- | --- | --- |
| `getUserInfo` | `getUserInfo3` | |
| `getFileChildren` | `getFileChildren5` | Paginated with `NextToken` |
| `getOverlay` | `getOverlay4` | Single item by ID |
| `getSearchContent` | `getSearchContent3` | Full-text search |
| `getBucketWriteAuth` | `getBucketWriteAuth4` | Upload credentials |
| `setUploadFile` | `setUploadFile3` | Register file + create folder |
| `setDeleteFile` | `setDeleteFile5` | Trash + permanent delete |
| `getCollections` | `getCollections5` | Albums |
| `setCollection` | `setCollection2` | Create album |
| `getFilesFromPaths` | `getFilesFromPaths` | No version suffix |
| `getShared` | `getShared` | No version suffix |
| `getDeletedFiles` | `getDeletedFiles` | No version suffix |
| `getFeed` | `getFeed` | No version suffix |
| `getPermissions` | `getPermissions3` | |
| `setMoveFile` | `setMoveFile` | Copy + move |
| `setRenameFile` | `setRenameFile` | |
| `setShareFile` | `setShareFile` | |
| `setDeleteShareFile` | `setDeleteShareFile` | |
| `setDescription` | `setDescription` | |
| `setExperience` | `setExperience2` | Mark as viewed ("Moments") |

---

## 5. Known Queries (full signatures)

### getUserInfo3

Returns the authenticated user's profile and storage quota.

```graphql
query GetUserInfo3($Token: String!) {
    getUserInfo3(Token: $Token) {
        ID
        FirstName
        LastName
        Email
        AvatarURL
        CountryCode
        LanguageCode
        Phone
        AccountType        # int: 0=free, 1=pro?, 2=premium?
        UsedQuota          # bytes as string
        TotalQuota         # bytes as string
        OAuth2Provider     # "google" for OAuth accounts
        GPMigrationStatus
        FeatureNoAds
        FeatureTopSecret
        FeatureDownsampling
        FeatureAutomaticVideoUploads
        FileSizeLimit      # bytes as string
    }
}
```

### getFileChildren5

List children of a folder. Paginated via `NextToken`.

```graphql
query GetFileChildren5(
    $Token: String!
    $ParentID: String
    $AllParentIDs: [String]
    $Limit: Int!
    $Order: Int!
    $NextToken: String
) {
    getFileChildren5(
        Token: $Token
        ParentID: $ParentID
        AllParentIDs: $AllParentIDs
        Limit: $Limit
        Order: $Order
        NextToken: $NextToken
    ) {
        Items { ...ItemProperties }
        NextToken   # opaque cursor; null when no more pages
    }
}
```

**`$Order` values observed:**

- `3` = by date descending (default in web app)
- `1` = alphabetical ascending (likely)

**`$ParentID`**: `"0"` = root. Device folders live directly under root.

### getOverlay4

Fetch metadata for a single item by its Degoo ID.

```graphql
query GetOverlay4($Token: String!, $ID: IDType!) {
    getOverlay4(Token: $Token, ID: $ID) {
        ...ItemProperties
    }
}
```

`IDType` is an input object: `{ FileID: "123456789" }` (string ID).

### getSearchContent3

Full-text search across all files.

```graphql
query GetSearchContent3(
    $Token: String!
    $SearchTerm: String!
    $Limit: Int!
    $NextToken: String
) {
    getSearchContent3(Token: $Token, SearchTerm: $SearchTerm, Limit: $Limit, NextToken: $NextToken) {
        Items { ...ItemProperties }
        NextToken
    }
}
```

### getDeletedFiles

List items in the recycle bin.

```graphql
query GetDeletedFiles($Token: String!, $Limit: Int!, $NextToken: String) {
    getDeletedFiles(Token: $Token, Limit: $Limit, NextToken: $NextToken) {
        Items { ...ItemProperties }
        NextToken
    }
}
```

### getShared

List items shared with the authenticated user.

```graphql
query GetShared($Token: String!, $Limit: Int!, $NextToken: String) {
    getShared(Token: $Token, Limit: $Limit, NextToken: $NextToken) {
        Items { ...ItemProperties }
        NextToken
    }
}
```

### getCollections5

List albums / collections.

```graphql
query GetCollections5(
    $Token: String!
    $Limit: Int!
    $Order: Int!
    $Type: Int
    $NextToken: String
) {
    getCollections5(Token: $Token, Limit: $Limit, Order: $Order, Type: $Type, NextToken: $NextToken) {
        Items {
            ContentView { ...ItemProperties }
        }
        NextToken
    }
}
```

### getPermissions3

Get sharing permissions for an item.

```graphql
query GetPermissions3($Token: String!, $ID: String!) {
    getPermissions3(Token: $Token, ID: $ID) {
        CurrentUserPermissions   # int bitmask (observed values: 1, 3, 7, 15)
        Users {
            ID
            Name
            Email
        }
    }
}
```

### getFeed

Moments / feed timeline (recent photos/videos, randomly ordered).

```graphql
query GetFeed($Token: String!, $Limit: Int!, $Random: Float) {
    getFeed(Token: $Token, Limit: $Limit, Random: $Random) {
        ...ItemProperties
    }
}
```

`$Random` is a float seed for random ordering (pass `null` for deterministic).

### getBucketWriteAuth4

Get Google Cloud Storage upload credentials. Part of the upload flow.

```graphql
query GetBucketWriteAuth4(
    $Token: String!
    $ParentID: String!
    $StorageUploadInfos: [StorageUploadInfo2]
) {
    getBucketWriteAuth4(Token: $Token, ParentID: $ParentID, StorageUploadInfos: $StorageUploadInfos) {
        AuthData {
            PolicyBase64   # GCS policy document, base64
            Signature      # HMAC-SHA1 signature of the policy
            BaseURL        # GCS bucket URL
            KeyPrefix      # path prefix for the object key
            AccessKey {
                Key        # "AWSAccessKeyId" (yes, GCS uses AWS-compat API)
                Value
            }
            ACL            # "public-read" or "private"
            AdditionalBody {
                Key
                Value
            }
        }
        Error   # non-null string if authorization failed
    }
}
```

`StorageUploadInfo2` input: `{ FileName: "file.txt", FileLength: 1234 }`

### getFilesFromPaths

Batch-resolve items by path strings.

```graphql
query GetFilesFromPaths($Token: String!, $FileIDPaths: [FileIDPath]) {
    getFilesFromPaths(Token: $Token, FileIDPaths: $FileIDPaths) {
        ...ItemProperties
    }
}
```

`FileIDPath` input shape: unknown (not yet exercised in the CLI).

---

## 6. Known Mutations (full signatures)

### setUploadFile3

Used for **both** registering uploaded files AND creating folders.

```graphql
mutation SetUploadFile3($Token: String!, $FileInfos: [FileInfoUpload3]!) {
    setUploadFile3(Token: $Token, FileInfos: $FileInfos)
}
```

`FileInfoUpload3` fields (confirmed via introspection):

```json
{
    Name: String!          # file or folder name
    ParentID: String!      # ID of the parent folder
    Size: String!          # file size in bytes; "0" for folders
    Checksum: String!      # must be "" — see §9a for why
    CreationTime: String!  # Unix timestamp in SECONDS, as a string
    Data: String           # optional; purpose unknown
}
```

> **⚠️ `Category` is NOT a valid field.** The API returns an error if you
> include it. Degoo infers the category server-side from `Size` and `Checksum`.
> Folders created with `Size="0"` start as **Category 6** (Document placeholder)
> — see §17 for the full two-phase folder creation lifecycle.

Returns: a string (observed: `"OK"` or a file ID).

### setDeleteFile5

Move items to the recycle bin, or permanently delete.

```graphql
mutation SetDeleteFile5($Token: String!, $IsInRecycleBin: Boolean!, $IDs: [IDType]!) {
    setDeleteFile5(Token: $Token, IsInRecycleBin: $IsInRecycleBin, IDs: $IDs)
}
```

- `IsInRecycleBin: false` → moves to trash
- `IsInRecycleBin: true` → permanent delete (use when item is already in bin)

`IDType`: `{ FileID: "123456789" }`

### setMoveFile

Move or copy items to a new parent folder.

```graphql
mutation SetMoveFile($Token: String!, $Copy: Boolean, $NewParentID: String!, $FileIDs: [String]!) {
    setMoveFile(Token: $Token, Copy: $Copy, NewParentID: $NewParentID, FileIDs: $FileIDs)
}
```

### setRenameFile

```graphql
mutation SetRenameFile($Token: String!, $FileRenames: [FileRenameInfo]!) {
    setRenameFile(Token: $Token, FileRenames: $FileRenames)
}
```

`FileRenameInfo`: `{ ID: "123456789", NewName: "new_name.txt" }`

### setShareFile

Share an item (optionally with specific users; omit `Usernames` for a public link).

```graphql
mutation SetShareFile($Token: String!, $ID: String!, $Usernames: [String]) {
    setShareFile(Token: $Token, ID: $ID, Usernames: $Usernames)
}
```

### setDeleteShareFile

Remove all sharing from an item.

```graphql
mutation SetDeleteShareFile($Token: String!, $ID: String!) {
    setDeleteShareFile(Token: $Token, ID: $ID)
}
```

### setCollection2

Create a collection (album).

```graphql
mutation SetCollection2(
    $Token: String!
    $FileIDs: [String]
    $Title: String
    $Description: String
    $Usernames: [String]
    $ReadOnly: Boolean
) {
    setCollection2(Token: $Token, FileIDs: $FileIDs, Title: $Title,
                   Description: $Description, Usernames: $Usernames, ReadOnly: $ReadOnly)
}
```

### setDescription

```graphql
mutation SetDescription($Token: String!, $ID: String!, $Description: String!) {
    setDescription(Token: $Token, ID: $ID, Description: $Description)
}
```

### setExperience2

Mark items as "viewed" in the Moments feed (prevents re-appearing in random feed).

```graphql
mutation SetExperience2($Token: String!, $MetadataIDs: [String]!) {
    setExperience2(Token: $Token, MetadataIDs: $MetadataIDs)
}
```

---

## 7. Item Data Model

The `ItemProperties` fragment (used by most queries):

| Field | Type | Notes |
| --- | --- | --- |
| `ID` | String | Degoo numeric ID as a string |
| `MetadataID` | String | Internal metadata identifier |
| `UserID` | String | Owner's user ID |
| `DeviceID` | String | Device the file belongs to |
| `MetadataKey` | String | Internal key |
| `Name` | String | File/folder name |
| `FilePath` | String | Full path string, e.g. `/Device/Folder/file.txt` |
| `LocalPath` | String | Original local path (from upload client) |
| `LastUploadTime` | Int | Unix ms timestamp |
| `LastModificationTime` | Int | Unix ms timestamp |
| `ParentID` | String | ID of the parent folder (`"0"` = root) |
| `Category` | Int | See §8 |
| `Size` | String | File size in bytes |
| `Platform` | Int | Upload client platform code |
| `URL` | String | Direct download URL (signed GCS URL, time-limited) |
| `ThumbnailURL` | String | Thumbnail image URL |
| `CreationTime` | Int | Unix ms timestamp |
| `IsSelfLiked` | Boolean | Whether current user liked it |
| `Likes` | Int | Total likes |
| `IsHidden` | Boolean | |
| `IsInRecycleBin` | Boolean | |
| `Description` | String | User-set description |
| `Country` / `Province` / `Place` | String | Geo metadata from EXIF |
| `Location2` | Object | `{ Country, Province, Place, GeoLocation { Latitude, Longitude } }` |
| `GeoLocation` | Object | `{ Latitude, Longitude }` |
| `Data` | String | Unknown (base64-encoded metadata?) |
| `DataBlock` | String | Unknown |
| `CompressionParameters` | String | Unknown |
| `Shareinfo` / `ShareInfo` | Object | `{ Status, ShareTime }` (both capitalizations appear) |

**Timestamp note**: timestamps > 1×10¹² are in **milliseconds**; divide by 1000
for Unix seconds. The Degoo app mixes ms and s in different fields.

---

## 8. Item Categories

| Value | Name | Folder-like? | Notes |
| --- | --- | --- | --- |
| `0` | File | No | Generic file (rare in practice) |
| `1` | Device | Yes | Top-level container; lives under root |
| `2` | Folder | Yes | A fully-promoted real folder |
| `3` | Image | No | |
| `4` | Video | No | |
| `5` | Music | No | |
| `6` | Document | No | Also used as **temporary folder placeholder** — see §17 |
| `10` | Recycle Bin | Yes | One per device |

Folders and Devices both appear as children of the root (`ParentID = "0"`).
Items cannot be moved directly to root — they must go into a Device folder.

> **Important**: when you create a folder via `setUploadFile3`, the item first
> appears as **Category 6** (Document), not Category 2. Degoo asynchronously
> promotes it to Category 2 only after content is added inside it. The promoted
> item gets a **different ID** from the original placeholder. See §17 for the
> full lifecycle and required workaround.

---

## 9. Upload Flow

File upload is a **3-step** process:

```text
Step 1: Get GCS credentials
  → GraphQL: getBucketWriteAuth4(ParentID, StorageUploadInfos: [{FileName, Checksum, Size}])
  ← {PolicyBase64, Signature, BaseURL, KeyPrefix, AccessKey, ACL, AdditionalBody}

Step 2: Upload bytes to Google Cloud Storage
  → HTTPS multipart POST to BaseURL
     key = KeyPrefix + "{ext}/{checksum}.{ext}"   ← checksum-based GCS key (see §9a)
     with fields: {AccessKey kv pairs, policy, signature, key, acl, Content-Type, ...AdditionalBody, file}
  ← HTTP 200/201/204 on success

Step 3: Register in Degoo
  → GraphQL: setUploadFile3(FileInfos: [{Name, ParentID, Size, Checksum, CreationTime}])
  ← true (boolean) on success
```

**Critical**: `StorageUploadInfos` **must** be provided in Step 1 with the correct
checksum and file size. Omitting it causes the server to return a generic GCS
key that does not match the file, resulting in `Size=0` and no download URL
after the upload completes.

### 9a. Degoo Checksum Algorithm

Degoo uses a **custom SHA1-based checksum** wrapped in a minimal protobuf envelope.

**Step 1 — compute inner SHA1:**

```python
import base64, hashlib

SEED = bytes([13, 7, 2, 2, 15, 40, 75, 117, 13, 10, 19, 16, 29, 23, 3, 36])

def degoo_checksum(filepath):
    h = hashlib.sha1()
    h.update(SEED)                        # 1. seed bytes (do NOT include file size)
    with open(filepath, "rb") as f:
        h.update(f.read(1024 * 1024))     # 2. first 1 MiB of content
    proto = b'\x0a\x14' + h.digest() + b'\x10\x00'   # 3. protobuf wrap
    return base64.urlsafe_b64encode(proto).decode().rstrip('=')
```

The output format is **base64url without padding**, e.g.:
`ChQVgjMd4f9UAnRnJNB8-dCTOpsPLBAA`

Decoded, the 24 bytes are: `\x0a\x14` (protobuf field 1, wire type 2, length 20),
20-byte SHA1 digest, and `\x10\x00` (protobuf field 2, varint 0).

**Key observations confirmed by Playwright network capture (2026-03-14):**

- The **file size is NOT included** in the hash input — only seed + first 1 MiB
- The result is **protobuf-wrapped** before base64url encoding (not a raw base64 digest)
- The same checksum value is used in **both** `getBucketWriteAuth4` (StorageUploadInfos)
  and `setUploadFile3` (FileInfos[].Checksum)
- The checksum becomes part of the **GCS object key**: `{KeyPrefix}{ext}/{checksum}.{ext}`

### 9b. GCS Object Key Format

When `StorageUploadInfos` is provided, the server's returned `KeyPrefix` is a
short opaque path (e.g. `ADfzPh/6tnxDg/`). The actual GCS object key to use is:

```text
{KeyPrefix}{lowercase_extension}/{checksum}.{lowercase_extension}
```

For example, uploading `report.pdf` with checksum `ChQVgjMd4f9...`:

```text
ADfzPh/6tnxDg/pdf/ChQVgjMd4f9UAnRnJNB8-dCTOpsPLBAA.pdf
```

The GCS policy condition enforces `starts-with` on this exact key, so the
`key` field in the multipart POST must match this pattern exactly.

### 9c. CreationTime — milliseconds, not seconds

The `CreationTime` field in both `setUploadFile3` and folder creation via
`setUploadFile3` must be a **millisecond** Unix timestamp as a string
(matching JavaScript's `Date.now()` convention):

```python
"CreationTime": str(int(time.time() * 1000))   # ✅ correct
"CreationTime": str(int(time.time()))           # ❌ wrong — seconds
```

### 9d. Download Flow

File download requires **two steps** (unlike upload which is three):

```text
Step 1: Resolve item metadata
  → GraphQL: getOverlay4(Token, IDs: [item_id])
  ← item dict including URL (presigned CDN URL) and Name, Size, etc.

Step 2: Stream bytes from the CDN
  → HTTPS GET to item["URL"]  (follow_redirects=True)
  ← chunked response; Content-Length header present on most items
```

Key observations:

- The **CDN URL is presigned and short-lived** — do not cache it across sessions
  or long-running processes.
- **`getFileChildren5` does NOT return URLs** — `URL` is always empty in that
  response. Always call `getOverlay4` to get a usable download URL.
- **`Content-Length` is usually present** in the GCS/CDN response, enabling
  accurate progress bars.  For some older or very small files the header may be
  absent; progress tracking must handle `total = 0` gracefully.
- **Folders have no URL** — `item["URL"]` is empty or absent.  Calling
  `download()` on a folder raises `DegooAPIError`.

### 9b. Download Flow

File download requires **two steps** (unlike upload which is three):

```text
Step 1: Resolve item metadata
  → GraphQL: getOverlay4(Token, IDs: [item_id])
  ← item dict including URL (presigned CDN URL) and Name, Size, etc.

Step 2: Stream bytes from the CDN
  → HTTPS GET to item["URL"]  (follow_redirects=True)
  ← chunked response; Content-Length header present on most items
```

Key observations:

- The **CDN URL is presigned and short-lived** — do not cache it across sessions
  or long-running processes.
- **`Content-Length` is usually present** in the GCS/CDN response, enabling
  accurate progress bars.  For some older or very small files the header may be
  absent; progress tracking must handle `total = 0` gracefully.
- **Folders have no URL** — `item["URL"]` is empty or absent.  Calling
  `download()` on a folder raises `DegooAPIError`.  The CLI checks `is_folder()`
  before attempting a download and routes to `_collect_download_tasks()` instead.
- The `progress_callback(downloaded, total)` signature is called on every
  `chunk_size=65536` chunk, giving real-time streaming progress.

### 9c. Upload Progress Limitation

The `progress_callback` parameter is accepted by `DegooClient.upload()` but
**is not called during the GCS POST** in the current implementation.  The
upload to Google Cloud Storage is performed as an atomic multipart `httpx.post`
call — httpx's synchronous API does not emit per-chunk callbacks for outgoing
data.

Consequence for the CLI: upload tasks show a **spinner** (indeterminate) while
the transfer is in flight; the bar jumps to 100 % only after `httpx.post`
returns.  This is accurate but not incremental.  Download tasks show real
incremental progress.

A future improvement could wrap the file handle in a `_ProgressFile` shim that
intercepts `read()` calls and forwards byte counts to the callback — this would
work without changes to the httpx call site.

---

## 10. Pagination

Queries that return lists use **cursor-based pagination** via `NextToken`:

```python
next_token = None
while True:
    variables = {"Limit": 100, "ParentID": parent_id}
    if next_token:
        variables["NextToken"] = next_token
    result = gql(GET_FILE_CHILDREN, variables)
    items.extend(result["Items"] or [])
    next_token = result.get("NextToken")
    if not next_token:
        break
```

`NextToken` is an **opaque string** — do not parse it. Pass it back verbatim.
It is `null` on the last page.

---

## 11. Known Unknowns / Gaps

These areas were not fully explored and may be worth investigating:

| Area | Status | Notes |
| --- | --- | --- |
| `getFilesFromPaths` | Untested | Input type `FileIDPath` shape unknown |
| `$AllParentIDs` in `getFileChildren5` | Unknown | May be used for multi-parent queries |
| `Platform` field codes | Unknown | Observed values: 3 (web), others unknown |
| `$Order` values | Partially known | `3` = date desc; other values unclear |
| `CurrentUserPermissions` bitmask | Unknown | Values 1,3,7,15 seen; bit meanings unclear |
| Restore from trash | Not implemented | No known mutation for this yet |
| Account registration | Not implemented | `POST /register` endpoint exists |
| Direct download URL expiry | Unknown | Signed GCS URLs; TTL not confirmed |
| `setExperience2` behavior | Untested | Presumably marks feed items as seen |
| Batch upload (multiple files) | Untested | `FileInfos` is an array; may work |
| `Data` / `DataBlock` fields | Unknown | Appear base64; content unknown |
| `getFeed $Random` semantics | Unclear | Float seed for random feed; exact behavior unknown |
| GraphQL schema introspection | Unknown | AppSync may block `__schema` queries |
| Rate limits | Unknown | No 429s observed in normal use |
| Token scopes | Unknown | Whether tokens are scoped by operation |

---

## 12. How to Re-examine After a Degoo Update

If the API breaks (operations return errors, new versions appear, etc.):

### Quick manual check

```bash
python scripts/probe_api.py --verbose
```

This runs all known operations and shows which ones fail and what they return.

### Find new operation versions

In Chrome DevTools, filter Network requests to `production-appsync.degoo.com`
and look at the `operationName` field in the Payload tab. If you see
`GetFileChildren6` where we use `GetFileChildren5`, a new version is deployed.

### Update the codebase

1. Update the query string in `src/cligoo/queries.py`
2. Update the operation name in `src/cligoo/api.py` method calls
3. Update the version table in this document (§4c)
4. Run `python scripts/probe_api.py` to verify
5. Run `make test`

### Capture the JS bundle for deep analysis

```bash
# Fetch the main JS bundle from the Degoo app
curl -s https://app.degoo.com | grep -o 'src="[^"]*\.js"' | head -5
# Then fetch and search for operation names:
curl -s <bundle-url> | grep -o 'get[A-Z][A-Za-z]*[0-9]*\|set[A-Z][A-Za-z]*[0-9]*' | sort -u
```

This lists all GraphQL operation names hardcoded in the JS bundle, which may
reveal operations not yet discovered or documented here.

---

## 13. GraphQL Schema Introspection

AppSync endpoints sometimes allow introspection (useful for discovering all
types and operations automatically):

```bash
python scripts/probe_api.py --introspect
```

Or manually with curl:

```bash
curl -s -X POST https://production-appsync.degoo.com/graphql \
  -H "Content-Type: application/json" \
  -H "x-api-key: da2-vs6twz5vnjdavpqndtbzg3prra" \
  -d '{"query":"{ __schema { queryType { name } mutationType { name } } }"}' | python -m json.tool
```

If introspection is enabled, the full schema reveals all types, fields, and
operations without needing to reverse-engineer the JS bundle.

---

## 14. Source Code Map

```text
src/cligoo/
├── constants.py   Endpoints, API key, category codes, checksum seed,
│                  ITEM_PROPERTIES fragment, SEARCH_ITEM_PROPERTIES (slim, for ContentView2 queries)
├── queries.py     All GraphQL query/mutation strings (verbatim from network capture)
├── auth.py        Token storage, login, refresh, browser capture (with profile seeding), get_token()
├── chrome.py      Chrome installation detection; profile enumeration from Local State JSON
├── config.py      Read/write ~/.config/cligoo/config.toml (user preferences:
│                  login_method, chrome_profile, api_key, transfer_workers,
│                  upload_retries, etc.); atomic save_config() via mkstemp+os.replace;
│                  get_api_key() resolves env→file→None; legacy config.json fallback
├── api.py         DegooClient class — all API methods with retry/pagination logic
└── cli.py         Click commands: all file/auth/config commands
```

The layering is intentional: `queries.py` is pure strings (easy to update when
Degoo ships new operation versions), `api.py` adds Python logic on top, and
`cli.py` only handles user I/O.

### Runtime state files vs. config

```text
~/.config/cligoo/
├── config.toml        User preferences — primary config (login_method, chrome_profile,
│                      api_key, transfer_workers, upload_retries, output format, etc.)
├── config.json        Legacy flat-JSON config — read as fallback if config.toml absent
├── cwd.json           Session state — current working directory (changes on every `cd`)
├── tokens.json        Auth token fallback when keyring is unavailable
└── credentials.json   Email/password fallback when keyring is unavailable
```

`config.toml` is the only file the user intentionally sets via `cligoo config`.
The other files are runtime state written automatically during normal use.
`config.json` is a legacy fallback maintained for backwards compatibility.

---

## 15. Discovered Field Constraints (2026-03 Introspection)

These were discovered by running `__type` introspection queries against the live API:

### GraphQL Type → Item field differences

| Type | Returned by | Notes |
| --- | --- | --- |
| `ContentView` | `getFileChildren5`, `getDeletedFiles`, `getShared`, `getFeed` | Accepts `Country`, `Province`, `Place`, `GeoLocation` in queries even though introspection doesn't list them (AppSync is lenient here) |
| `ContentView2` | `getOverlay4`, `getSearchContent3` (via `ContentViewConnection2`) | Strict validation — `Country`, `Province`, `Place`, `GeoLocation` at top level and `Province`, `Place`, `GeoLocation` inside `Location2` are **undefined**. Use `SEARCH_ITEM_PROPERTIES` |

### `FileInfoUpload3` input fields (for `setUploadFile3`)

All `String!` (NonNull): `ParentID`, `Checksum`, `Name`, `Size`, `CreationTime`.
Optional `String`: `Data`.
**`Category` is NOT a field** — Degoo infers category server-side.

### `StorageUploadInfo2` input fields (for `getBucketWriteAuth4`)

All `String!` (NonNull): `FileName`, `Checksum`, `Size`.

> **⚠️ Correction (2026-03-14)**: Earlier notes stated that providing
> `StorageUploadInfos` caused `"Invalid input!"` — this was wrong. The `"Invalid
> input!"` error was caused by passing an incorrectly structured value. When
> provided with the correct checksum (see §9a), `StorageUploadInfos` is
> **required** for uploads to work correctly. Omitting it causes the server to
> return a generic GCS key, and the uploaded file ends up with `Size=0` and no
> download URL.

### `getBucketWriteAuth4` return type

Returns `[BucketWriteAuthInfo]` (a **list**), NOT a single object.
Always index `result[0]` to get the `AuthData` dict.

### `AuthData` field shapes

- `AccessKey`: single `{Key, Value}` object (NOT a list)
- `AdditionalBody`: list of `{Key, Value}` objects

### `setUploadFile3` `Checksum` behavior

> **⚠️ Correction (2026-03-14)**: Earlier notes stated that passing the
> locally-computed checksum returns `"Request failed!"` and `""` should be
> used instead. This was wrong — the old algorithm was incorrect (it included
> the file size in the hash). The correct algorithm (seed + first 1 MiB,
> protobuf-wrapped, base64url) **must** be passed. Passing `""` causes the
> server to store the file without linking it to the GCS object, resulting
> in `Size=0` and no download URL. See §9a for the correct algorithm.

### `getDeletedFiles` — required argument

`Order: Int!` is required (not optional). Pass `DEFAULT_ORDER = 3`.

### `getShared` — required arguments

`IncludeSelfContent: Boolean!` and `OrderDescending: Boolean!` are required.

### Folder creation via `setUploadFile3`

No dedicated folder creation mutation exists. Folders are created by calling
`setUploadFile3` with `Size="0"` and `Checksum=""`. The resulting item always
starts as **Category 6** (Document). The server asynchronously creates a new
**Category 2** folder with a **different ID** once at least one child item is
created inside the placeholder. Empty Cat 6 placeholders are never promoted.
See §17 for the confirmed two-phase lifecycle and the sentinel-trigger pattern.

---

## 17. Two-Phase Folder Creation (Cat 6 → Cat 2 Promotion)

This section documents the complete lifecycle of folder creation in Degoo, as
confirmed through live integration testing (2026-03).

### 17a. Phase 1 — Immediate: Category 6 placeholder

When `setUploadFile3` is called with `Size="0"` and `Checksum=""`, the Degoo
backend immediately creates a visible item under the target parent, but it is
typed as **Category 6** (Document), not Category 2 (Folder):

```bash
client.mkdir("my-folder", parent_id)
# → Degoo creates item with:
#     Name: "my-folder"
#     Category: 6          ← NOT 2
#     ID: "1234567890"     ← the Cat 6 placeholder ID
```

This item appears in `list_dir` responses within a few seconds.

### 17b. Phase 2 — Asynchronous: Category 2 promotion

After a **child item is created inside the Cat 6 placeholder**, the Degoo
backend asynchronously generates a new, properly-typed **Category 2** folder:

```bash
client.mkdir(".sentinel", cat6_placeholder_id)
# Triggers backend to create:
#     Name: "my-folder"
#     Category: 2          ← real folder
#     ID: "9876543210"     ← COMPLETELY DIFFERENT from the Cat 6 ID
```

Key properties of the promotion:

- The **Cat 2 folder has a different ID** than the Cat 6 placeholder.
  Any code that cached the Cat 6 ID to later `list_dir()` into the folder
  will get empty results or errors — it must discover the new Cat 2 ID.
- **Timing**: promotion typically takes **1–3 minutes** after the first child
  is added. The Cat 6 placeholder remains visible alongside the Cat 2 item
  during the transition.
- **Empty Cat 6 folders are never promoted.** If no child items are ever
  created inside, the Cat 6 placeholder remains indefinitely.
- The sentinel child item is kept by the backend; it does not disappear
  after the promotion completes.

### 17c. Detection: How to find the real Cat 2 folder

After triggering promotion, poll `list_dir` on the parent until an item
with the target name and `Category == 2` appears:

```python
# Poll up to 3 minutes for the Cat 2 version to appear
real_folder_id = cat6_id  # fallback if promotion stalls
for _ in range(60):       # 60 × 3 s = 3 min max
    time.sleep(3)
    children = client.list_dir(parent_id, limit=50)
    real = next(
        (c for c in children if c["Name"] == folder_name and c.get("Category") == 2),
        None,
    )
    if real is not None:
        real_folder_id = str(real["ID"])
        break
```

### 17d. Practical workaround — the sentinel trigger

The integration test fixture (`tests/integration/test_live_api.py:test_folder`)
implements this pattern:

1. Call `client.mkdir(folder_name, device_id)` — creates Cat 6 placeholder
2. Wait for the Cat 6 item to appear in `list_dir` (up to 20 s)
3. Immediately create a sentinel child inside the Cat 6 placeholder:
   `client.mkdir(".test-sentinel", cat6_id)`
4. Poll `list_dir` on the parent until a Cat 2 item with the same name appears
   (up to 3 min)
5. Use the Cat 2 item's ID for all subsequent operations

> **Why this matters for user-facing code**: The `cligoo mkdir` CLI command
> currently returns immediately after the `setUploadFile3` call, reporting
> success with the Cat 6 placeholder's ID. If a user runs `cligoo ls` inside
> the new folder immediately after, they see an empty folder (correct) — but
> if they try to use the folder ID as a parent for `upload`, the file may not
> become visible until the backend has processed the promotion. This is a
> known eventual-consistency quirk of the Degoo API, not a bug in the CLI.

### 17e. Confirmed API behavior summary

| Observation | Confirmed? |
| --- | --- |
| New folders start as Category 6 | ✅ Yes |
| Empty Cat 6 folders are never promoted | ✅ Yes (observed 4+ min wait) |
| Any child item triggers promotion | ✅ Yes (sentinel mkdir works) |
| Cat 2 folder gets a different ID | ✅ Yes |
| Promotion time: 1–3 minutes | ✅ Yes |
| `Category` field accepted by `setUploadFile3` | ❌ No — API returns error |
| `setUploadFile3` returns the new Cat 2 ID | ❌ No — returns `"OK"` |

---

## 18. Implementation Pitfalls (lessons learnt)

These are non-obvious implementation decisions that bit us at least once. Future
contributors should read this before changing the affected code.

### 18a. Token fetch — pass the token to DegooClient

`_client()` in `cli.py` calls `get_token()` for early auth validation, then
constructs `DegooClient(token=token)` passing the result directly. If you omit
the `token=` argument, `DegooClient` starts with `_token = None` and calls
`get_token()` again on the first GraphQL request — a second network round-trip
per command and a potential staleness race if the token was silently refreshed
between the two calls.

### 18b. `DEFAULT_HEADERS` is a shared module-level dict — always copy it

`httpx.Client` normalises header keys to lowercase internally. If you pass the
shared `DEFAULT_HEADERS` dict directly, httpx may mutate it and corrupt the
module-level dict for all future `DegooClient` instances in the same process.
Always pass `dict(DEFAULT_HEADERS)` (a shallow copy) to `httpx.Client`.

### 18c. `save_config()` must be atomic

`Path.write_text()` truncates the file before writing. A `SIGKILL` between
truncation and write completion leaves `config.toml` empty. Parsing an empty
TOML file raises an error which `load_config()`'s bare `except` silently
swallows, returning `{}` and erasing all user settings. The correct pattern is
`tempfile.mkstemp()` in the same directory followed by `os.replace()` — both
steps are in `save_config()` and must stay that way.

### 18d. Always use `encoding="utf-8"` for token/credential files

`_write_private()` writes files via `os.fdopen(fd, "w")`, which uses the
platform default encoding. On non-UTF-8 locales (Windows with `cp1252`, some
Linux with `ISO-8859-1`) this can produce bytes that `read_text()` without an
explicit encoding cannot decode. Both write and read paths specify
`encoding="utf-8"` explicitly.

### 18e. Tab-completion glob patterns must escape user input

`_complete_local()` calls `glob.escape(text)` before appending `*`. Without
this, a user who types `**` or `[` as a partial path name would expand into an
unintended recursive glob or raise a `glob.glob` exception. Any future
completion helper that builds a glob from user input must do the same.

### 18f. Shell subcommands — always check the return code

`shell.py` subcommands (`do_ls`, `do_tree`, `do_upload`, `do_download`,
`do_lls`) spawn `degoo` or `ls` via `subprocess.run`. Without inspecting
`result.returncode`, a failed upload produces an empty prompt with no feedback.
All new shell commands that spawn subprocesses must print a `✗` message when
the return code is non-zero.

### 18g. Parallel transfers — `SystemExit` does not propagate from threads

`concurrent.futures.ThreadPoolExecutor` wraps all exceptions raised inside
worker callables into the `Future` object.  A `SystemExit` raised in a worker
thread is **not** re-raised in the main thread — it is caught silently.

All internal transfer helpers (`_upload_one`, `_download_one`) therefore raise
`RuntimeError` on failure rather than `SystemExit`.  The command body iterates
`as_completed(futures)` and catches `Exception`, incrementing a `failed`
counter and collecting error messages.  `SystemExit(1)` is only raised **once**
from the main thread after the executor has fully shut down.

### 18h. Directory creation must precede parallel file uploads

`_collect_upload_tasks()` walks the local tree **sequentially**, calling
`client.mkdir()` + `client.resolve_path_under()` for each sub-folder before
collecting its files.  Only after the full flat task list is built does the
`ThreadPoolExecutor` start uploading files in parallel.

This ordering is required because:

1. `mkdir` returns no ID — the ID is recovered by `resolve_path_under()`, which
   scans the parent's children.
2. Concurrent `mkdir` calls for sibling directories at the same level are safe,
   but concurrent calls with their own `resolve_path_under` could return wrong
   IDs if Degoo's listing has propagation delay.

Keep the collection phase serial; only the file-transfer phase is parallel.

### 18i. Upload checksum — wrong algorithm causes Size=0 and no download URL

The upload checksum (passed to both `getBucketWriteAuth4` and `setUploadFile3`)
must be computed exactly as described in §9a. Three specific mistakes that each
cause silent upload corruption (file appears in the listing but is inaccessible):

1. **Including file size in the hash input**: the algorithm is
   `SHA1(seed + first_1MiB)` — NOT `SHA1(seed + size + first_1MiB)`.
2. **Returning a raw base64 digest**: the result must be protobuf-wrapped
   (`\x0a\x14` + sha1_bytes + `\x10\x00`) before base64url encoding.
3. **Passing `""` as the checksum**: the server accepts the empty string
   syntactically but does not link the GCS object to the metadata entry,
   resulting in `Size=0` and `URL=""` in all subsequent API responses.

Root-cause confirmed via Playwright network capture comparing CLI vs browser
uploads side-by-side on 2026-03-14.

### 18j. StorageUploadInfos must be provided and must use the correct checksum

`getBucketWriteAuth4` accepts an optional `StorageUploadInfos` argument.  When
omitted (or passed with a wrong checksum), the server returns a **generic** GCS
key (`KeyPrefix + original_filename`) whose policy does not match the actual GCS
object Degoo expects.  The file uploads successfully to GCS but the server-side
linking step never fires, so `Size` stays 0 and `URL` stays empty.

Always pass `StorageUploadInfos: [{ FileName, Checksum, Size }]` with the
protobuf-wrapped checksum from §9a.

### 18k. GCS object key must follow the checksum-based pattern

The `key` field in the GCS multipart POST must be:

```text
{KeyPrefix}{lowercase_extension}/{checksum}.{lowercase_extension}
```

Using `KeyPrefix + filename` (the original filename) violates the GCS policy
condition (`starts-with` on the checksum-based path) and the upload is rejected
with HTTP 403.

---

## 16. Change Log

| Date | Change |
| --- | --- |
| 2026-03 | Initial implementation; all operations above confirmed working |
| 2026-03 | Added `cligoo login --browser` (Playwright, system Chrome, refresh token capture) |
| 2026-03 | Fixed `getBucketWriteAuth4` list response parsing; fixed upload `Content-Type` and `AccessKey` form fields; changed `setUploadFile3` to use empty `Checksum`; fixed `getOverlay4` / `getSearchContent3` to use `SEARCH_ITEM_PROPERTIES`; fixed `getDeletedFiles` `Order` argument; fixed `getShared` `IncludeSelfContent` / `OrderDescending` arguments; added `cd`, `pwd`, persistent CWD, `ls --depth`, auto-help on missing args |
| 2026-03 | **Confirmed two-phase folder creation lifecycle** via live integration testing: `setUploadFile3` always creates Cat 6 placeholder; Cat 2 promotion requires a child item to be added first; promoted folder has a different ID; empty Cat 6 folders are never promoted; promotion takes 1–3 min. Corrected §6 (`Category` is NOT a valid `FileInfoUpload3` field), §8 (Cat 6 note), §15 (folder creation behaviour), and added §17 with full lifecycle documentation and sentinel-trigger workaround. |
| 2026-03 | **Fixed `--use-mock-keychain` blocking cookie decryption.** Playwright injects `--use-mock-keychain` and `--password-store=basic` by default, preventing Chrome from accessing the real macOS Keychain and rendering all copied session cookies unreadable. Added both to `ignore_default_args` in `fetch_token_via_browser()`. Documented in §3f. |
| 2026-03 | **Added Chrome profile seeding for zero-interaction browser login.** New `cligoo config` command enumerates installed Chrome profiles from `Local State` JSON and persists the selection to `config.json`. `fetch_token_via_browser()` now copies `Cookies` + `Login Data` from the selected profile into a fresh temporary `user_data_dir` before launch — no `SingletonLock` conflict regardless of whether Chrome is running. When the existing Degoo session is still active, the token is captured in < 5 s with no user interaction. New modules: `chrome.py` (profile detection), `config.py` (config read/write). Documented in §3f. |
| 2026-03 | **Parallel transfers + multi-source upload/download.** Added §9b (download flow), §9c (upload progress limitation), §18g (`SystemExit` thread caveat), §18h (serial dir creation + parallel file transfers). `upload` and `download` commands now accept multiple sources via `FILES...`/`ITEMS...`, run file transfers in a `ThreadPoolExecutor` (default 20 workers, configurable via `transfer_workers` in `config.json` or `--workers`), and use a single shared Rich `Progress` display. New options: `--dest/-t` (replaces positional dest — **breaking change**), `--exclude PATTERN` (recursive upload), `--skip-existing` (download). |
| 2026-03 | **Security / correctness hardening round 2.** (1) `_client()` now passes the fetched token to `DegooClient(token=...)` — eliminates double auth round-trip per command. (2) `DEFAULT_HEADERS` is copied (`dict(DEFAULT_HEADERS)`) at `DegooClient` construction — prevents httpx header normalisation from mutating the shared module-level dict. (3) `save_config()` replaced `Path.write_text()` with `tempfile.mkstemp` + `os.replace` for crash-safe atomic writes. (4) `TOKEN_FILE` and `CRED_FILE` now read with `encoding="utf-8"` explicitly. (5) `_complete_local()` calls `glob.escape(text)` before building glob patterns. (6) All shell subcommands (`ls`, `tree`, `upload`, `download`, `lls`) inspect `returncode` and print `✗` on failure. (7) `api_key` config entry added: key can now be overridden via `DEGOO_API_KEY` env var or `config.json` without reinstalling. Documented in §3d and §18. |
| 2026-03-14 | **Fixed upload root cause — files were Size=0 with no download URL.** Root cause found by Playwright network capture comparing browser vs CLI API calls. Three bugs fixed in `api.py`: (1) `_degoo_checksum()` was including file size in the SHA1 input — correct algorithm is `SHA1(seed + first_1MiB)` with no size. (2) Checksum output must be protobuf-wrapped (`\x0a\x14` + sha1 + `\x10\x00`) and base64url-encoded, not raw base64. (3) `_get_upload_auth()` was omitting `StorageUploadInfos` — it must be passed with the correct checksum so the server generates a checksum-based GCS key. (4) GCS `key` field in the multipart POST must be `{KeyPrefix}{ext}/{checksum}.{ext}`, not `{KeyPrefix}{filename}`. (5) `CreationTime` in `setUploadFile3` must be milliseconds (`time.time() * 1000`), not seconds. Confirmed working via live integration test: 3 files uploaded with correct size and working download URLs. Corrected §9, §15 (StorageUploadInfos and Checksum notes), added §18i–18k. |
