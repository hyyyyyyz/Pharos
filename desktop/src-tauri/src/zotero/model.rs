//! Complete, provider-neutral Zotero entities.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const LOCAL_SOURCE_ID: &str = "zotero-local";

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ProviderKind {
    Connector,
    LocalApi,
    Cloud,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ConnectionPhase {
    Disconnected,
    Detecting,
    Connecting,
    Indexing,
    Ready,
    Stale,
    Error,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "camelCase")]
pub struct ProviderCapabilities {
    pub metadata_read: bool,
    pub file_read: bool,
    pub fulltext_read: bool,
    pub metadata_write: bool,
    pub notes_write: bool,
    pub annotations_write: bool,
    pub realtime_events: bool,
}

impl ProviderCapabilities {
    pub const fn local_api() -> Self {
        Self {
            metadata_read: true,
            file_read: true,
            fulltext_read: true,
            metadata_write: false,
            notes_write: false,
            annotations_write: false,
            realtime_events: false,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LibraryKind {
    User,
    Group,
}

impl LibraryKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Group => "group",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroLibrary {
    pub source_id: String,
    pub library_id: String,
    pub kind: LibraryKind,
    pub name: String,
    pub version: u64,
    pub editable: bool,
    pub files_editable: bool,
    #[serde(default)]
    pub raw: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroCollection {
    pub source_id: String,
    pub library_id: String,
    pub key: String,
    pub version: u64,
    pub name: String,
    pub parent_key: Option<String>,
    #[serde(default)]
    pub item_count: u64,
    #[serde(default)]
    pub deleted: bool,
    #[serde(default)]
    pub raw: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroSavedSearch {
    pub source_id: String,
    pub library_id: String,
    pub key: String,
    pub version: u64,
    pub name: String,
    #[serde(default)]
    pub deleted: bool,
    #[serde(default)]
    pub conditions: Value,
    #[serde(default)]
    pub raw: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroCreator {
    pub creator_type: Option<String>,
    pub first_name: Option<String>,
    pub last_name: Option<String>,
    pub name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroTagRef {
    pub tag: String,
    pub kind: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroItem {
    pub source_id: String,
    pub library_id: String,
    pub key: String,
    pub version: u64,
    pub item_type: String,
    pub parent_key: Option<String>,
    pub title: Option<String>,
    pub abstract_note: Option<String>,
    pub date_added: Option<String>,
    pub date_modified: Option<String>,
    #[serde(default)]
    pub creators: Vec<ZoteroCreator>,
    #[serde(default)]
    pub tags: Vec<ZoteroTagRef>,
    #[serde(default)]
    pub collection_keys: Vec<String>,
    #[serde(default)]
    pub relations: Value,
    #[serde(default)]
    pub raw: Value,
    pub deleted: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroAttachment {
    pub source_id: String,
    pub library_id: String,
    pub key: String,
    pub version: u64,
    pub parent_key: Option<String>,
    pub public_id: String,
    pub link_mode: Option<String>,
    pub content_type: Option<String>,
    pub filename: Option<String>,
    pub available: bool,
    pub size_bytes: Option<u64>,
    #[serde(skip_serializing, skip_deserializing, default)]
    pub local_path: Option<PathBuf>,
    #[serde(default)]
    pub raw: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroTag {
    pub source_id: String,
    pub library_id: String,
    pub tag: String,
    pub kind: Option<i64>,
    pub item_count: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroFulltextIndex {
    pub source_id: String,
    pub library_id: String,
    pub item_key: String,
    pub version: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroItemRef {
    pub source_id: String,
    pub library_id: String,
    pub item_key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroLibraryRef {
    pub source_id: String,
    pub library_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroItemQuery {
    pub library: Option<ZoteroLibraryRef>,
    pub collection_key: Option<String>,
    pub parent_key: Option<String>,
    #[serde(default)]
    pub item_types: Vec<String>,
    pub tag: Option<String>,
    pub search: Option<String>,
    #[serde(default)]
    pub include_deleted: bool,
    #[serde(default = "default_query_limit")]
    pub limit: u32,
    #[serde(default)]
    pub offset: u32,
}

fn default_query_limit() -> u32 {
    100
}

impl Default for ZoteroItemQuery {
    fn default() -> Self {
        Self {
            library: None,
            collection_key: None,
            parent_key: None,
            item_types: Vec::new(),
            tag: None,
            search: None,
            include_deleted: false,
            limit: default_query_limit(),
            offset: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroItemSummary {
    pub source_id: String,
    pub library_id: String,
    pub key: String,
    pub version: u64,
    pub item_type: String,
    pub parent_key: Option<String>,
    pub title: Option<String>,
    pub abstract_note: Option<String>,
    pub date_added: Option<String>,
    pub date_modified: Option<String>,
    pub creators: Vec<ZoteroCreator>,
    pub tags: Vec<ZoteroTagRef>,
    pub collection_keys: Vec<String>,
    pub deleted: bool,
    pub child_count: u64,
    pub attachment_count: u64,
    pub available_attachment_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroItemDetail {
    pub item: ZoteroItem,
    pub attachments: Vec<ZoteroAttachment>,
    pub children: Vec<ZoteroItemSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroPage<T> {
    pub items: Vec<T>,
    pub total: u64,
    pub limit: u32,
    pub offset: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroFulltext {
    pub source_id: String,
    pub library_id: String,
    pub item_key: String,
    pub version: u64,
    pub content: String,
    pub indexed_pages: Option<u64>,
    pub total_pages: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroProbe {
    pub available: bool,
    pub zotero_version: Option<String>,
    pub api_version: Option<u64>,
    pub schema_version: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroConnectionStatus {
    pub source_id: String,
    pub provider: ProviderKind,
    pub phase: ConnectionPhase,
    pub capabilities: ProviderCapabilities,
    pub available: bool,
    pub syncing: bool,
    pub zotero_version: Option<String>,
    pub api_version: Option<u64>,
    pub schema_version: Option<u64>,
    pub last_successful_sync_ms: Option<u64>,
    pub last_error: Option<String>,
    pub library_count: u64,
    pub item_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroRefreshRequest {
    #[serde(default)]
    pub force_full: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ZoteroSyncReport {
    pub source_id: String,
    pub provider: ProviderKind,
    pub full: bool,
    pub library_count: u64,
    pub item_count: u64,
    pub attachment_count: u64,
    pub collection_count: u64,
    pub note_count: u64,
    pub annotation_count: u64,
    pub available_attachment_count: u64,
    pub completed_at_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
#[serde(rename_all = "camelCase")]
pub struct LibrarySnapshot {
    pub library: Option<ZoteroLibrary>,
    pub collections: Vec<ZoteroCollection>,
    pub items: Vec<ZoteroItem>,
    pub attachments: Vec<ZoteroAttachment>,
    pub searches: Vec<ZoteroSavedSearch>,
    pub tags: Vec<ZoteroTag>,
    pub fulltext: Vec<ZoteroFulltextIndex>,
}
