"""GraphQL query and mutation strings for the Degoo API.

Extracted from the Degoo web application JS bundles at app.degoo.com.
"""

from .constants import ITEM_PROPERTIES, SEARCH_ITEM_PROPERTIES

# ═══════════════════════════════════════════════════════════════════════════════
# QUERIES
# ═══════════════════════════════════════════════════════════════════════════════

GET_USER_INFO = """
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
        AccountType
        UsedQuota
        TotalQuota
        OAuth2Provider
        GPMigrationStatus
        FeatureNoAds
        FeatureTopSecret
        FeatureDownsampling
        FeatureAutomaticVideoUploads
        FileSizeLimit
    }
}
"""

GET_FILE_CHILDREN = f"""
query GetFileChildren5(
    $Token: String!
    $ParentID: String
    $AllParentIDs: [String]
    $Limit: Int!
    $Order: Int!
    $NextToken: String
) {{
    getFileChildren5(
        Token: $Token
        ParentID: $ParentID
        AllParentIDs: $AllParentIDs
        Limit: $Limit
        Order: $Order
        NextToken: $NextToken
    ) {{
        Items {{
            {ITEM_PROPERTIES}
        }}
        NextToken
    }}
}}
"""

GET_OVERLAY = f"""
query GetOverlay4($Token: String!, $ID: IDType!) {{
    getOverlay4(Token: $Token, ID: $ID) {{
        {SEARCH_ITEM_PROPERTIES}
    }}
}}
"""

GET_FEED = f"""
query GetFeed($Token: String!, $Limit: Int!, $Random: Float) {{
    getFeed(Token: $Token, Limit: $Limit, Random: $Random) {{
        {ITEM_PROPERTIES}
    }}
}}
"""

GET_SEARCH = f"""
query GetSearchContent3(
    $Token: String!
    $SearchTerm: String!
    $Limit: Int!
    $NextToken: String
) {{
    getSearchContent3(
        Token: $Token
        SearchTerm: $SearchTerm
        Limit: $Limit
        NextToken: $NextToken
    ) {{
        Items {{
            {SEARCH_ITEM_PROPERTIES}
        }}
        NextToken
    }}
}}
"""

GET_DELETED_FILES = f"""
query GetDeletedFiles(
    $Token: String!
    $Limit: Int!
    $Order: Int!
    $NextToken: String
) {{
    getDeletedFiles(
        Token: $Token
        Limit: $Limit
        Order: $Order
        NextToken: $NextToken
    ) {{
        Items {{
            {ITEM_PROPERTIES}
        }}
        NextToken
    }}
}}
"""

GET_BUCKET_WRITE_AUTH = """
query GetBucketWriteAuth4(
    $Token: String!
    $ParentID: String!
    $StorageUploadInfos: [StorageUploadInfo2]
) {
    getBucketWriteAuth4(
        Token: $Token
        ParentID: $ParentID
        StorageUploadInfos: $StorageUploadInfos
    ) {
        AuthData {
            PolicyBase64
            Signature
            BaseURL
            KeyPrefix
            AccessKey {
                Key
                Value
            }
            ACL
            AdditionalBody {
                Key
                Value
            }
        }
        Error
    }
}
"""

GET_FILES_FROM_PATHS = f"""
query GetFilesFromPaths($Token: String!, $FileIDPaths: [FileIDPath]) {{
    getFilesFromPaths(Token: $Token, FileIDPaths: $FileIDPaths) {{
        {ITEM_PROPERTIES}
    }}
}}
"""

GET_COLLECTIONS = f"""
query GetCollections5(
    $Token: String!
    $Limit: Int!
    $Order: Int!
    $Type: Int
    $NextToken: String
) {{
    getCollections5(
        Token: $Token
        Limit: $Limit
        Order: $Order
        Type: $Type
        NextToken: $NextToken
    ) {{
        Items {{
            ContentView {{
                {ITEM_PROPERTIES}
            }}
        }}
        NextToken
    }}
}}
"""

GET_SHARED = f"""
query GetShared(
    $Token: String!
    $Limit: Int!
    $IncludeSelfContent: Boolean!
    $OrderDescending: Boolean!
    $NextToken: String
) {{
    getShared(
        Token: $Token
        Limit: $Limit
        IncludeSelfContent: $IncludeSelfContent
        OrderDescending: $OrderDescending
        NextToken: $NextToken
    ) {{
        Items {{
            {ITEM_PROPERTIES}
        }}
        NextToken
    }}
}}
"""

GET_PERMISSIONS = """
query GetPermissions3($Token: String!, $ID: String!) {
    getPermissions3(Token: $Token, ID: $ID) {
        CurrentUserPermissions
        Users {
            ID
            Name
            Email
        }
    }
}
"""

# ═══════════════════════════════════════════════════════════════════════════════
# MUTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

SET_UPLOAD_FILE = """
mutation SetUploadFile3($Token: String!, $FileInfos: [FileInfoUpload3]!) {
    setUploadFile3(Token: $Token, FileInfos: $FileInfos)
}
"""

SET_DELETE_FILE = """
mutation SetDeleteFile5(
    $Token: String!
    $IsInRecycleBin: Boolean!
    $IDs: [IDType]!
) {
    setDeleteFile5(Token: $Token, IsInRecycleBin: $IsInRecycleBin, IDs: $IDs)
}
"""

SET_MOVE_FILE = """
mutation SetMoveFile(
    $Token: String!
    $Copy: Boolean
    $NewParentID: String!
    $FileIDs: [String]!
) {
    setMoveFile(
        Token: $Token
        Copy: $Copy
        NewParentID: $NewParentID
        FileIDs: $FileIDs
    )
}
"""

SET_RENAME_FILE = """
mutation SetRenameFile($Token: String!, $FileRenames: [FileRenameInfo]!) {
    setRenameFile(Token: $Token, FileRenames: $FileRenames)
}
"""

SET_SHARE_FILE = """
mutation SetShareFile($Token: String!, $ID: String!, $Usernames: [String]) {
    setShareFile(Token: $Token, ID: $ID, Usernames: $Usernames)
}
"""

SET_DELETE_SHARE_FILE = """
mutation SetDeleteShareFile($Token: String!, $ID: String!) {
    setDeleteShareFile(Token: $Token, ID: $ID)
}
"""

SET_COLLECTION = """
mutation SetCollection2(
    $Token: String!
    $FileIDs: [String]
    $Title: String
    $Description: String
    $Usernames: [String]
    $ReadOnly: Boolean
) {
    setCollection2(
        Token: $Token
        FileIDs: $FileIDs
        Title: $Title
        Description: $Description
        Usernames: $Usernames
        ReadOnly: $ReadOnly
    )
}
"""

SET_DESCRIPTION = """
mutation SetDescription($Token: String!, $ID: String!, $Description: String!) {
    setDescription(Token: $Token, ID: $ID, Description: $Description)
}
"""

SET_EXPERIENCE = """
mutation SetExperience2($Token: String!, $MetadataIDs: [String]!) {
    setExperience2(Token: $Token, MetadataIDs: $MetadataIDs)
}
"""
