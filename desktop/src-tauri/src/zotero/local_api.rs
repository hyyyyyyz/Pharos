//! Complete read-only provider for Zotero Desktop's official Local API.
//!
//! The endpoint is deliberately fixed to loopback. No command accepts a base
//! URL, host, scheme, or path from the WebView.

use std::{
    collections::{HashMap, HashSet},
    fs,
    path::PathBuf,
    time::Duration,
};

use futures_util::{stream, StreamExt};
use reqwest::{header, Client, Response, Url};
use serde::de::DeserializeOwned;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use super::{
    model::{
        LibraryKind, LibrarySnapshot, ProviderCapabilities, ZoteroAttachment, ZoteroCollection,
        ZoteroCreator, ZoteroFulltext, ZoteroFulltextIndex, ZoteroItem, ZoteroLibrary, ZoteroProbe,
        ZoteroSavedSearch, ZoteroSavedSearchMembership, ZoteroTag, ZoteroTagRef, LOCAL_SOURCE_ID,
    },
    provider::{ProviderFuture, ZoteroProvider},
};

const LOCAL_API: &str = "http://127.0.0.1:23119/api";
const PAGE_SIZE: usize = 100;
const MAX_OBJECTS: usize = 100_000;
const ATTACHMENT_PROBES: usize = 12;

#[derive(Debug, Clone)]
pub struct LocalApiProvider {
    client: Client,
}

#[derive(Debug, Clone)]
pub struct LibraryDelta {
    pub library: ZoteroLibrary,
    pub collections: Vec<ZoteroCollection>,
    pub items: Vec<ZoteroItem>,
    pub attachments: Vec<ZoteroAttachment>,
    pub searches: Vec<ZoteroSavedSearch>,
    pub search_memberships: Vec<ZoteroSavedSearchMembership>,
    pub tags: Vec<ZoteroTag>,
    pub fulltext: Vec<ZoteroFulltextIndex>,
    pub current_collection_keys: HashSet<String>,
    pub current_item_keys: HashSet<String>,
    pub current_search_keys: HashSet<String>,
}

#[derive(Debug, Clone, Default)]
struct ResponseMeta {
    library_version: Option<u64>,
    zotero_version: Option<String>,
    api_version: Option<u64>,
    schema_version: Option<u64>,
}

impl ResponseMeta {
    fn merge(&mut self, other: &Self) {
        self.library_version = [self.library_version, other.library_version]
            .into_iter()
            .flatten()
            .max();
        if other.zotero_version.is_some() {
            self.zotero_version.clone_from(&other.zotero_version);
        }
        self.api_version = other.api_version.or(self.api_version);
        self.schema_version = other.schema_version.or(self.schema_version);
    }
}

#[derive(Debug, Clone)]
struct AttachmentCandidate {
    library: ZoteroLibrary,
    key: String,
    version: u64,
    parent_key: Option<String>,
    link_mode: Option<String>,
    content_type: Option<String>,
    filename: Option<String>,
    enclosure_url: Option<String>,
    enclosure_size: Option<u64>,
    raw: Value,
}

impl LocalApiProvider {
    pub fn new() -> Result<Self, String> {
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(2))
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|error| format!("无法初始化本地 Zotero 连接：{error}"))?;
        Ok(Self { client })
    }

    pub async fn probe_info(&self) -> ZoteroProbe {
        match self.request("").await {
            Ok(response) if response.status().is_success() => {
                let meta = response_meta(&response);
                ZoteroProbe {
                    available: true,
                    zotero_version: meta.zotero_version,
                    api_version: meta.api_version,
                    schema_version: meta.schema_version,
                }
            }
            _ => ZoteroProbe {
                available: false,
                zotero_version: None,
                api_version: None,
                schema_version: None,
            },
        }
    }

    pub async fn fetch_libraries(&self) -> Result<Vec<ZoteroLibrary>, String> {
        let mut personal_raw = Value::Null;
        let mut personal_id = "0".to_string();
        let mut personal_name = "我的 Zotero 文库".to_string();
        let (sample, sample_meta): (Vec<Value>, ResponseMeta) =
            self.json_get("users/0/items?format=json&limit=1").await?;
        let personal_version = sample_meta.library_version.unwrap_or_default();
        if let Some(library) = sample.first().and_then(|item| item.get("library")) {
            personal_raw = library.clone();
            personal_id = value_id(library.get("id")).unwrap_or(personal_id);
            personal_name = value_string(library, "name")
                .filter(|value| !value.trim().is_empty())
                .unwrap_or(personal_name);
        }

        let mut libraries = vec![ZoteroLibrary {
            source_id: LOCAL_SOURCE_ID.to_string(),
            library_id: personal_id,
            kind: LibraryKind::User,
            name: personal_name,
            version: personal_version,
            editable: false,
            files_editable: false,
            raw: personal_raw,
        }];

        let (groups, groups_meta): (Vec<Value>, ResponseMeta) =
            self.json_get("users/0/groups?format=json").await?;
        for group in groups {
            let data = group.get("data").unwrap_or(&group);
            let Some(id) = value_id(group.get("id").or_else(|| data.get("id"))) else {
                continue;
            };
            let name = value_string(data, "name")
                .filter(|value| !value.trim().is_empty())
                .unwrap_or_else(|| format!("Zotero 群组 {id}"));
            let (_sample, library_meta): (Vec<Value>, ResponseMeta) = self
                .json_get(&format!("groups/{id}/items?format=json&limit=1"))
                .await?;
            libraries.push(ZoteroLibrary {
                source_id: LOCAL_SOURCE_ID.to_string(),
                library_id: id,
                kind: LibraryKind::Group,
                name,
                version: library_meta
                    .library_version
                    .or_else(|| value_u64(&group, "version"))
                    .or(groups_meta.library_version)
                    .unwrap_or_default(),
                editable: value_bool(data, "editable").unwrap_or(false),
                files_editable: value_bool(data, "filesEditable").unwrap_or(false),
                raw: group,
            });
        }
        Ok(libraries)
    }

    pub async fn fetch_snapshot(&self, library: &ZoteroLibrary) -> Result<LibrarySnapshot, String> {
        let route = library_route(library);
        let (collection_values, collection_meta) = self
            .fetch_paged(&format!("{route}/collections?includeTrashed=1"))
            .await?;
        let (item_values, item_meta) = self
            .fetch_paged(&format!("{route}/items?includeTrashed=1"))
            .await?;
        let (search_values, search_meta) = self
            .fetch_paged(&format!("{route}/searches?includeTrashed=1"))
            .await?;
        let (tag_values, tag_meta) = self.fetch_paged(&format!("{route}/tags")).await?;
        let (trash_values, trash_meta) = self.fetch_paged(&format!("{route}/items/trash")).await?;
        let (fulltext_values, fulltext_meta): (HashMap<String, u64>, ResponseMeta) =
            self.json_get(&format!("{route}/fulltext?since=0")).await?;

        let trash_keys: HashSet<String> = trash_values.iter().filter_map(object_key).collect();
        let collections = collection_values
            .into_iter()
            .filter_map(|value| collection_from_value(library, value))
            .collect::<Vec<_>>();
        let searches = search_values
            .into_iter()
            .filter_map(|value| search_from_value(library, value))
            .collect::<Vec<_>>();
        let search_memberships = self
            .fetch_saved_search_memberships(
                library,
                searches
                    .iter()
                    .filter(|search| !search.deleted)
                    .map(|search| search.key.clone())
                    .collect(),
            )
            .await?;
        let tags = tag_values
            .into_iter()
            .filter_map(|value| tag_from_value(library, value))
            .collect::<Vec<_>>();

        let mut items = Vec::with_capacity(item_values.len());
        let mut attachment_candidates = Vec::new();
        for value in item_values {
            let Some(item) = item_from_value(library, value.clone(), &trash_keys) else {
                continue;
            };
            if item.item_type == "attachment" {
                attachment_candidates.push(attachment_candidate(library, &item, &value));
            }
            items.push(item);
        }
        let attachments = stream::iter(attachment_candidates)
            .map(|candidate| self.resolve_attachment(candidate))
            .buffer_unordered(ATTACHMENT_PROBES)
            .collect::<Vec<_>>()
            .await;

        let fulltext = fulltext_values
            .into_iter()
            .map(|(item_key, version)| ZoteroFulltextIndex {
                source_id: library.source_id.clone(),
                library_id: library.library_id.clone(),
                item_key,
                version,
            })
            .collect::<Vec<_>>();

        let mut meta = collection_meta;
        for next in [item_meta, search_meta, tag_meta, trash_meta, fulltext_meta] {
            meta.merge(&next);
        }
        let mut current_library = library.clone();
        current_library.version = meta.library_version.unwrap_or_else(|| {
            items
                .iter()
                .map(|item| item.version)
                .chain(collections.iter().map(|collection| collection.version))
                .chain(searches.iter().map(|search| search.version))
                .max()
                .unwrap_or(library.version)
        });

        Ok(LibrarySnapshot {
            library: Some(current_library),
            collections,
            items,
            attachments,
            searches,
            search_memberships,
            tags,
            fulltext,
        })
    }

    pub async fn fetch_delta(
        &self,
        library: &ZoteroLibrary,
        since: u64,
    ) -> Result<LibraryDelta, String> {
        let route = library_route(library);
        let (collection_values, collection_meta) = self
            .fetch_paged(&format!(
                "{route}/collections?includeTrashed=1&since={since}"
            ))
            .await?;
        let (item_values, item_meta) = self
            .fetch_paged(&format!("{route}/items?includeTrashed=1&since={since}"))
            .await?;
        let (search_values, search_meta) = self
            .fetch_paged(&format!("{route}/searches?includeTrashed=1&since={since}"))
            .await?;
        let (tag_values, tag_meta) = self.fetch_paged(&format!("{route}/tags")).await?;
        let (trash_values, trash_meta) = self
            .fetch_paged(&format!("{route}/items/trash?since={since}"))
            .await?;
        let (fulltext_values, fulltext_meta): (HashMap<String, u64>, ResponseMeta) = self
            .json_get(&format!("{route}/fulltext?since={since}"))
            .await?;

        let (current_item_versions, current_item_meta) = self
            .fetch_paged_versions(&format!("{route}/items?includeTrashed=1"))
            .await?;
        let (current_collection_versions, current_collection_meta) = self
            .fetch_paged_versions(&format!("{route}/collections?includeTrashed=1"))
            .await?;
        let (current_search_versions, current_search_meta) = self
            .fetch_paged_versions(&format!("{route}/searches?includeTrashed=1"))
            .await?;
        let search_memberships = self
            .fetch_saved_search_memberships(
                library,
                current_search_versions.keys().cloned().collect(),
            )
            .await?;

        let trash_keys = trash_values
            .iter()
            .filter_map(object_key)
            .collect::<HashSet<_>>();
        let collections = collection_values
            .into_iter()
            .filter_map(|value| collection_from_value(library, value))
            .collect::<Vec<_>>();
        let searches = search_values
            .into_iter()
            .filter_map(|value| search_from_value(library, value))
            .collect::<Vec<_>>();
        let tags = tag_values
            .into_iter()
            .filter_map(|value| tag_from_value(library, value))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(item_values.len());
        let mut attachment_candidates = Vec::new();
        for value in item_values {
            let Some(item) = item_from_value(library, value.clone(), &trash_keys) else {
                continue;
            };
            if item.item_type == "attachment" {
                attachment_candidates.push(attachment_candidate(library, &item, &value));
            }
            items.push(item);
        }
        let attachments = stream::iter(attachment_candidates)
            .map(|candidate| self.resolve_attachment(candidate))
            .buffer_unordered(ATTACHMENT_PROBES)
            .collect::<Vec<_>>()
            .await;
        let fulltext = fulltext_values
            .into_iter()
            .map(|(item_key, version)| ZoteroFulltextIndex {
                source_id: library.source_id.clone(),
                library_id: library.library_id.clone(),
                item_key,
                version,
            })
            .collect();

        let mut meta = collection_meta;
        for next in [
            item_meta,
            search_meta,
            tag_meta,
            trash_meta,
            fulltext_meta,
            current_item_meta,
            current_collection_meta,
            current_search_meta,
        ] {
            meta.merge(&next);
        }
        let mut current_library = library.clone();
        current_library.version = meta.library_version.unwrap_or(library.version.max(since));
        Ok(LibraryDelta {
            library: current_library,
            collections,
            items,
            attachments,
            searches,
            search_memberships,
            tags,
            fulltext,
            current_collection_keys: current_collection_versions.into_keys().collect(),
            current_item_keys: current_item_versions.into_keys().collect(),
            current_search_keys: current_search_versions.into_keys().collect(),
        })
    }

    pub async fn fetch_fulltext(
        &self,
        library: &ZoteroLibrary,
        item_key: &str,
    ) -> Result<Option<ZoteroFulltext>, String> {
        validate_key(item_key)?;
        let route = library_route(library);
        let response = self
            .request(&format!("{route}/items/{item_key}/fulltext"))
            .await?;
        if response.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(http_error(&response));
        }
        let value = response
            .json::<Value>()
            .await
            .map_err(|error| format!("Zotero 返回了无法识别的全文数据：{error}"))?;
        Ok(Some(ZoteroFulltext {
            source_id: library.source_id.clone(),
            library_id: library.library_id.clone(),
            item_key: item_key.to_string(),
            version: value_u64(&value, "version").unwrap_or(library.version),
            content: value_string(&value, "content").unwrap_or_default(),
            indexed_pages: value_u64(&value, "indexedPages"),
            total_pages: value_u64(&value, "totalPages"),
        }))
    }

    async fn fetch_saved_search_memberships(
        &self,
        library: &ZoteroLibrary,
        search_keys: Vec<String>,
    ) -> Result<Vec<ZoteroSavedSearchMembership>, String> {
        let route = library_route(library);
        let mut memberships = Vec::new();
        for search_key in search_keys {
            validate_key(&search_key)?;
            let (versions, _meta) = self
                .fetch_paged_versions(&format!(
                    "{route}/searches/{search_key}/items?includeTrashed=1"
                ))
                .await?;
            memberships.push(ZoteroSavedSearchMembership {
                source_id: library.source_id.clone(),
                library_id: library.library_id.clone(),
                search_key,
                item_keys: versions.into_keys().collect(),
            });
        }
        Ok(memberships)
    }

    async fn resolve_attachment(&self, candidate: AttachmentCandidate) -> ZoteroAttachment {
        let mut local_path = candidate
            .enclosure_url
            .as_deref()
            .and_then(file_url_path)
            .filter(|path| path.is_file());
        if local_path.is_none() {
            let route = library_route(&candidate.library);
            let path = format!("{route}/items/{}/file/view/url", candidate.key);
            if let Ok(response) = self.request(&path).await {
                if response.status().is_success() {
                    local_path = response
                        .text()
                        .await
                        .ok()
                        .and_then(|body| file_url_path(body.trim()))
                        .filter(|path| path.is_file());
                }
            }
        }
        let metadata = local_path
            .as_deref()
            .and_then(|path| fs::metadata(path).ok())
            .filter(|metadata| metadata.is_file());
        let size_bytes = metadata
            .as_ref()
            .map(fs::Metadata::len)
            .or(candidate.enclosure_size);
        ZoteroAttachment {
            source_id: candidate.library.source_id.clone(),
            library_id: candidate.library.library_id.clone(),
            key: candidate.key.clone(),
            version: candidate.version,
            parent_key: candidate.parent_key,
            public_id: stable_public_id(&candidate.library, &candidate.key),
            link_mode: candidate.link_mode,
            content_type: candidate.content_type,
            filename: candidate.filename,
            available: local_path.is_some(),
            size_bytes,
            local_path,
            raw: candidate.raw,
        }
    }

    async fn fetch_paged(&self, suffix: &str) -> Result<(Vec<Value>, ResponseMeta), String> {
        let mut objects = Vec::new();
        let mut start = 0;
        let mut combined_meta = ResponseMeta::default();
        loop {
            let separator = if suffix.contains('?') { '&' } else { '?' };
            let path = format!("{suffix}{separator}format=json&limit={PAGE_SIZE}&start={start}");
            let (mut page, meta): (Vec<Value>, ResponseMeta) = self.json_get(&path).await?;
            combined_meta.merge(&meta);
            let count = page.len();
            objects.append(&mut page);
            if count < PAGE_SIZE {
                break;
            }
            start += count;
            if start >= MAX_OBJECTS {
                return Err("本地 Zotero 文库超过安全扫描上限（100000 个对象）。".to_string());
            }
        }
        Ok((objects, combined_meta))
    }

    async fn fetch_paged_versions(
        &self,
        suffix: &str,
    ) -> Result<(HashMap<String, u64>, ResponseMeta), String> {
        let mut versions = HashMap::new();
        let mut start = 0;
        let mut combined_meta = ResponseMeta::default();
        loop {
            let separator = if suffix.contains('?') { '&' } else { '?' };
            let path =
                format!("{suffix}{separator}format=versions&limit={PAGE_SIZE}&start={start}");
            let (page, meta): (HashMap<String, u64>, ResponseMeta) = self.json_get(&path).await?;
            combined_meta.merge(&meta);
            let count = page.len();
            versions.extend(page);
            if count < PAGE_SIZE {
                break;
            }
            start += count;
            if start >= MAX_OBJECTS {
                return Err("本地 Zotero 文库超过安全扫描上限（100000 个对象）。".to_string());
            }
        }
        Ok((versions, combined_meta))
    }

    async fn json_get<T: DeserializeOwned>(&self, path: &str) -> Result<(T, ResponseMeta), String> {
        let response = self.request(path).await?;
        if !response.status().is_success() {
            return Err(http_error(&response));
        }
        let meta = response_meta(&response);
        let value = response
            .json::<T>()
            .await
            .map_err(|error| format!("Zotero 返回了无法识别的数据：{error}"))?;
        Ok((value, meta))
    }

    async fn request(&self, path: &str) -> Result<Response, String> {
        let url = if path.is_empty() {
            format!("{LOCAL_API}/")
        } else {
            format!("{LOCAL_API}/{path}")
        };
        self.client
            .get(url)
            .header("Zotero-API-Version", "3")
            .header(header::ACCEPT, "application/json")
            .send()
            .await
            .map_err(|_| {
                "无法连接本机 Zotero。请确认 Zotero 正在运行，并已开启“允许其他应用与 Zotero 通信”。"
                    .to_string()
            })
    }
}

impl ZoteroProvider for LocalApiProvider {
    fn id(&self) -> &'static str {
        LOCAL_SOURCE_ID
    }

    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities::local_api()
    }

    fn libraries(&self) -> ProviderFuture<'_, Vec<ZoteroLibrary>> {
        Box::pin(self.fetch_libraries())
    }

    fn snapshot<'a>(&'a self, library: &'a ZoteroLibrary) -> ProviderFuture<'a, LibrarySnapshot> {
        Box::pin(self.fetch_snapshot(library))
    }
}

fn collection_from_value(library: &ZoteroLibrary, raw: Value) -> Option<ZoteroCollection> {
    let data = raw.get("data")?;
    Some(ZoteroCollection {
        source_id: library.source_id.clone(),
        library_id: library.library_id.clone(),
        key: object_key(&raw)?,
        version: object_version(&raw),
        name: value_string(data, "name").unwrap_or_else(|| "未命名分类".to_string()),
        parent_key: string_value(data.get("parentCollection")),
        item_count: raw
            .get("meta")
            .and_then(|meta| value_u64(meta, "numItems"))
            .unwrap_or_default(),
        deleted: value_bool(data, "deleted").unwrap_or(false),
        raw,
    })
}

fn search_from_value(library: &ZoteroLibrary, raw: Value) -> Option<ZoteroSavedSearch> {
    let data = raw.get("data")?;
    Some(ZoteroSavedSearch {
        source_id: library.source_id.clone(),
        library_id: library.library_id.clone(),
        key: object_key(&raw)?,
        version: object_version(&raw),
        name: value_string(data, "name").unwrap_or_else(|| "未命名搜索".to_string()),
        deleted: value_bool(data, "deleted").unwrap_or(false),
        conditions: data
            .get("conditions")
            .cloned()
            .unwrap_or(Value::Array(vec![])),
        raw,
    })
}

fn tag_from_value(library: &ZoteroLibrary, raw: Value) -> Option<ZoteroTag> {
    Some(ZoteroTag {
        source_id: library.source_id.clone(),
        library_id: library.library_id.clone(),
        tag: value_string(&raw, "tag")?,
        kind: raw.get("meta").and_then(|meta| value_i64(meta, "type")),
        item_count: raw.get("meta").and_then(|meta| value_u64(meta, "numItems")),
    })
}

fn item_from_value(
    library: &ZoteroLibrary,
    raw: Value,
    trash_keys: &HashSet<String>,
) -> Option<ZoteroItem> {
    let data = raw.get("data")?;
    let key = object_key(&raw)?;
    let item_type = value_string(data, "itemType").unwrap_or_else(|| "unknown".to_string());
    let title = display_title(data, &item_type);
    let creators = data
        .get("creators")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|creator| ZoteroCreator {
            creator_type: value_string(creator, "creatorType"),
            first_name: value_string(creator, "firstName"),
            last_name: value_string(creator, "lastName"),
            name: value_string(creator, "name"),
        })
        .collect();
    let tags = data
        .get("tags")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|tag| {
            Some(ZoteroTagRef {
                tag: value_string(tag, "tag")?,
                kind: value_i64(tag, "type"),
            })
        })
        .collect();
    let collection_keys = data
        .get("collections")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(ToString::to_string)
        .collect();
    Some(ZoteroItem {
        source_id: library.source_id.clone(),
        library_id: library.library_id.clone(),
        key: key.clone(),
        version: object_version(&raw),
        item_type,
        parent_key: value_string(data, "parentItem").filter(|value| !value.is_empty()),
        title,
        abstract_note: value_string(data, "abstractNote").filter(|value| !value.trim().is_empty()),
        date_added: value_string(data, "dateAdded"),
        date_modified: value_string(data, "dateModified"),
        creators,
        tags,
        collection_keys,
        relations: data
            .get("relations")
            .cloned()
            .unwrap_or_else(|| Value::Object(Map::new())),
        deleted: value_bool(data, "deleted").unwrap_or(false) || trash_keys.contains(&key),
        raw,
    })
}

fn attachment_candidate(
    library: &ZoteroLibrary,
    item: &ZoteroItem,
    raw: &Value,
) -> AttachmentCandidate {
    let data = raw.get("data").unwrap_or(&Value::Null);
    let enclosure = raw.get("links").and_then(|links| links.get("enclosure"));
    AttachmentCandidate {
        library: library.clone(),
        key: item.key.clone(),
        version: item.version,
        parent_key: item.parent_key.clone(),
        link_mode: value_string(data, "linkMode"),
        content_type: value_string(data, "contentType"),
        filename: value_string(data, "filename").filter(|value| !value.is_empty()),
        enclosure_url: enclosure.and_then(|value| value_string(value, "href")),
        enclosure_size: enclosure.and_then(|value| value_u64(value, "length")),
        raw: raw.clone(),
    }
}

fn display_title(data: &Value, item_type: &str) -> Option<String> {
    let direct = value_string(data, "title").filter(|value| !value.trim().is_empty());
    if direct.is_some() {
        return direct;
    }
    let candidate = match item_type {
        "note" => value_string(data, "note"),
        "annotation" => {
            value_string(data, "annotationText").or_else(|| value_string(data, "annotationComment"))
        }
        "attachment" => value_string(data, "filename"),
        _ => None,
    }?;
    let plain = strip_html(&candidate);
    if plain.is_empty() {
        None
    } else if plain.chars().count() > 160 {
        Some(format!("{}…", plain.chars().take(160).collect::<String>()))
    } else {
        Some(plain)
    }
}

fn strip_html(value: &str) -> String {
    let mut plain = String::with_capacity(value.len());
    let mut in_tag = false;
    for character in value.chars() {
        match character {
            '<' => in_tag = true,
            '>' => {
                in_tag = false;
                plain.push(' ');
            }
            _ if !in_tag => plain.push(character),
            _ => {}
        }
    }
    plain.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn response_meta(response: &Response) -> ResponseMeta {
    ResponseMeta {
        library_version: header_u64(response, "Last-Modified-Version"),
        zotero_version: response
            .headers()
            .get("X-Zotero-Version")
            .and_then(|value| value.to_str().ok())
            .map(ToString::to_string),
        api_version: header_u64(response, "Zotero-API-Version")
            .or_else(|| header_u64(response, "X-Zotero-Connector-API-Version")),
        schema_version: header_u64(response, "Zotero-Schema-Version"),
    }
}

fn header_u64(response: &Response, name: &str) -> Option<u64> {
    response
        .headers()
        .get(name)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse().ok())
}

fn http_error(response: &Response) -> String {
    match response.status().as_u16() {
        403 => {
            "Zotero 已禁止本机应用访问。请在 Zotero 设置 → 高级中开启“允许其他应用与 Zotero 通信”。"
                .to_string()
        }
        status => format!("Zotero 本地接口返回 HTTP {status}。"),
    }
}

fn library_route(library: &ZoteroLibrary) -> String {
    match library.kind {
        LibraryKind::User => format!("users/{}", library.library_id),
        LibraryKind::Group => format!("groups/{}", library.library_id),
    }
}

fn object_key(value: &Value) -> Option<String> {
    value_string(value, "key")
        .or_else(|| value.get("data").and_then(|data| value_string(data, "key")))
}

fn object_version(value: &Value) -> u64 {
    value_u64(value, "version")
        .or_else(|| {
            value
                .get("data")
                .and_then(|data| value_u64(data, "version"))
        })
        .unwrap_or_default()
}

fn value_string(value: &Value, key: &str) -> Option<String> {
    value.get(key)?.as_str().map(ToString::to_string)
}

fn value_u64(value: &Value, key: &str) -> Option<u64> {
    value.get(key)?.as_u64()
}

fn value_i64(value: &Value, key: &str) -> Option<i64> {
    value.get(key)?.as_i64()
}

fn value_bool(value: &Value, key: &str) -> Option<bool> {
    value.get(key)?.as_bool()
}

fn value_id(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
}

fn string_value(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(value) if !value.is_empty() => Some(value.clone()),
        _ => None,
    }
}

fn validate_key(key: &str) -> Result<(), String> {
    if !key.is_empty()
        && key.len() <= 64
        && key
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        Ok(())
    } else {
        Err("Zotero 对象标识无效。".to_string())
    }
}

fn stable_public_id(library: &ZoteroLibrary, key: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(b"attachment\0");
    hash.update(library.source_id.as_bytes());
    hash.update([0]);
    hash.update(library.library_id.as_bytes());
    hash.update([0]);
    hash.update(key.as_bytes());
    format!("zotero-local-{}", &hex::encode(hash.finalize())[..32])
}

fn file_url_path(raw: &str) -> Option<PathBuf> {
    let url = Url::parse(raw).ok()?;
    if url.scheme() != "file" {
        return None;
    }
    url.to_file_path().ok()
}

pub fn is_pdf_attachment(attachment: &ZoteroAttachment) -> bool {
    attachment
        .content_type
        .as_deref()
        .is_some_and(|value| value.eq_ignore_ascii_case("application/pdf"))
        || attachment
            .filename
            .as_deref()
            .is_some_and(|value| value.to_ascii_lowercase().ends_with(".pdf"))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn notes_and_annotations_receive_useful_titles() {
        assert_eq!(
            display_title(&json!({"note": "<p>Hello <b>world</b></p>"}), "note"),
            Some("Hello world".to_string())
        );
        assert_eq!(
            display_title(&json!({"annotationText": "Core trick"}), "annotation"),
            Some("Core trick".to_string())
        );
    }

    #[test]
    fn public_attachment_ids_hide_zotero_keys() {
        let library = ZoteroLibrary {
            source_id: LOCAL_SOURCE_ID.to_string(),
            library_id: "123".to_string(),
            kind: LibraryKind::User,
            name: "Library".to_string(),
            version: 1,
            editable: false,
            files_editable: false,
            raw: Value::Null,
        };
        let id = stable_public_id(&library, "SECRET12");
        assert!(id.starts_with("zotero-local-"));
        assert!(!id.contains("SECRET12"));
    }

    #[test]
    fn only_local_file_urls_are_accepted() {
        assert!(file_url_path("https://example.test/paper.pdf").is_none());
        assert!(file_url_path("file:///tmp/paper.pdf").is_some());
    }

    #[test]
    #[ignore = "requires a running Zotero Desktop instance"]
    fn live_provider_reads_the_complete_object_graph() {
        tauri::async_runtime::block_on(async {
            use crate::zotero::{
                mirror::ZoteroMirror,
                model::{ZoteroItemQuery, ZoteroLibraryRef},
            };

            let provider = LocalApiProvider::new().expect("provider");
            let probe = provider.probe_info().await;
            assert!(probe.available);
            let libraries = provider.fetch_libraries().await.expect("libraries");
            assert!(!libraries.is_empty());
            let snapshot = provider
                .fetch_snapshot(&libraries[0])
                .await
                .expect("snapshot");
            eprintln!(
                "complete local graph: libraries={} collections={} items={} attachments={} searches={} tags={} fulltext={}",
                libraries.len(),
                snapshot.collections.len(),
                snapshot.items.len(),
                snapshot.attachments.len(),
                snapshot.searches.len(),
                snapshot.tags.len(),
                snapshot.fulltext.len(),
            );
            assert!(snapshot
                .items
                .iter()
                .any(|item| item.item_type == "attachment"));

            let path = std::env::temp_dir().join(format!(
                "pharos-zotero-live-{}-{}.sqlite3",
                std::process::id(),
                snapshot.library.as_ref().unwrap().version
            ));
            let mirror = ZoteroMirror::open(&path).expect("mirror");
            mirror.replace_library(&snapshot).expect("write mirror");
            let metrics = mirror.metrics().expect("metrics");
            assert_eq!(
                metrics.items as usize,
                snapshot.items.iter().filter(|item| !item.deleted).count()
            );
            let library = snapshot.library.as_ref().unwrap();
            let page = mirror
                .query_items(&ZoteroItemQuery {
                    library: Some(ZoteroLibraryRef {
                        source_id: library.source_id.clone(),
                        library_id: library.library_id.clone(),
                    }),
                    limit: 25,
                    ..ZoteroItemQuery::default()
                })
                .expect("query mirror");
            assert!(!page.items.is_empty());
            if let Some(summary) = page.items.iter().find(|item| item.attachment_count > 0) {
                let detail = mirror
                    .item_detail(&crate::zotero::model::ZoteroItemRef {
                        source_id: summary.source_id.clone(),
                        library_id: summary.library_id.clone(),
                        item_key: summary.key.clone(),
                    })
                    .expect("item detail");
                assert!(!detail.attachments.is_empty());
                if let Some(attachment) = detail
                    .attachments
                    .iter()
                    .find(|attachment| attachment.available && is_pdf_attachment(attachment))
                {
                    let path = mirror
                        .attachment_path(&attachment.public_id)
                        .expect("attachment lookup")
                        .expect("available attachment path");
                    let prefix = std::fs::read(path).expect("read attachment");
                    assert!(prefix.starts_with(b"%PDF-"));
                }
            }
            let delta = provider
                .fetch_delta(library, library.version)
                .await
                .expect("no-op delta");
            assert!(delta.items.is_empty());
            mirror.apply_delta(&delta).expect("apply no-op delta");
            let after_delta = mirror.metrics().expect("post-delta metrics");
            assert_eq!(after_delta.items, metrics.items);
            assert_eq!(after_delta.attachments, metrics.attachments);
            drop(mirror);
            for candidate in [
                path.clone(),
                path.with_extension("sqlite3-wal"),
                path.with_extension("sqlite3-shm"),
            ] {
                let _ = std::fs::remove_file(candidate);
            }
        });
    }
}
