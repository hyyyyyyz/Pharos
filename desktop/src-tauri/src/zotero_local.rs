//! Read-only integration with Zotero Desktop's official Local API.
//!
//! The trust boundary in this module is intentional:
//! - the WebView can never choose a URL or a filesystem path;
//! - only the fixed loopback endpoint `127.0.0.1:23119` is contacted;
//! - absolute attachment paths live only in the native cache;
//! - the UI receives opaque attachment IDs and opens them through the
//!   `pharos-local://` protocol registered in `lib.rs`.
//!
//! Zotero remains authoritative. Pharos never reads `zotero.sqlite`, never
//! writes into Zotero's data directory, and a failed scan never replaces the
//! last successful snapshot.

use std::{
    collections::HashMap,
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        RwLock,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use futures_util::{stream, StreamExt};
use reqwest::{header, Client, StatusCode, Url};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::{http, AppHandle, Manager, State};

const LOCAL_API: &str = "http://127.0.0.1:23119/api";
const CACHE_SCHEMA: u32 = 1;
const PAGE_SIZE: usize = 100;
const MAX_ITEMS: usize = 100_000;
const ATTACHMENT_PROBES: usize = 12;
const USER_LIBRARY_ID: &str = "0";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct Cache {
    schema_version: u32,
    last_successful_sync_ms: Option<u64>,
    zotero_version: Option<u64>,
    libraries: Vec<CachedLibrary>,
    papers: Vec<CachedPaper>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CachedLibrary {
    id: String,
    kind: LibraryKind,
    name: String,
    item_version: Option<u64>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
enum LibraryKind {
    User,
    Group,
}

impl LibraryKind {
    fn route(self, id: &str) -> String {
        match self {
            Self::User => format!("users/{id}"),
            Self::Group => format!("groups/{id}"),
        }
    }

    fn public_label(self) -> &'static str {
        match self {
            Self::User => "personal",
            Self::Group => "group",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CachedPaper {
    id: String,
    library_id: String,
    library_kind: LibraryKind,
    library_name: String,
    item_key: String,
    item_version: u64,
    item_type: String,
    title: String,
    authors: Vec<String>,
    year: Option<i32>,
    venue: Option<String>,
    doi: Option<String>,
    abstract_text: Option<String>,
    url: Option<String>,
    date_added: Option<String>,
    attachments: Vec<CachedAttachment>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CachedAttachment {
    id: String,
    item_key: String,
    item_version: u64,
    filename: String,
    link_mode: Option<String>,
    /// Private native locator. This field is never copied into a command
    /// response, and therefore never crosses into JavaScript or FastAPI.
    local_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalZoteroLibrary {
    id: String,
    kind: String,
    name: String,
    paper_count: usize,
    pdf_available_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalZoteroStatus {
    available: bool,
    syncing: bool,
    cached_paper_count: usize,
    pdf_available_count: usize,
    last_successful_sync_ms: Option<u64>,
    zotero_version: Option<u64>,
    last_error: Option<String>,
    libraries: Vec<LocalZoteroLibrary>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalZoteroPaper {
    id: String,
    library_id: String,
    library_kind: String,
    library_name: String,
    item_key: String,
    item_version: u64,
    item_type: String,
    title: String,
    authors: Vec<String>,
    year: Option<i32>,
    venue: Option<String>,
    doi: Option<String>,
    abstract_text: Option<String>,
    url: Option<String>,
    date_added: Option<String>,
    pdf_available: bool,
    pdf_attachment_id: Option<String>,
    pdf_filename: Option<String>,
    pdf_attachment_count: usize,
}

impl From<&CachedPaper> for LocalZoteroPaper {
    fn from(paper: &CachedPaper) -> Self {
        let available: Vec<&CachedAttachment> = paper
            .attachments
            .iter()
            .filter(|attachment| attachment_available(attachment))
            .collect();
        let primary = available.first().copied();
        Self {
            id: paper.id.clone(),
            library_id: paper.library_id.clone(),
            library_kind: paper.library_kind.public_label().to_string(),
            library_name: paper.library_name.clone(),
            item_key: paper.item_key.clone(),
            item_version: paper.item_version,
            item_type: paper.item_type.clone(),
            title: paper.title.clone(),
            authors: paper.authors.clone(),
            year: paper.year,
            venue: paper.venue.clone(),
            doi: paper.doi.clone(),
            abstract_text: paper.abstract_text.clone(),
            url: paper.url.clone(),
            date_added: paper.date_added.clone(),
            pdf_available: primary.is_some(),
            pdf_attachment_id: primary.map(|attachment| attachment.id.clone()),
            pdf_filename: primary.map(|attachment| attachment.filename.clone()),
            pdf_attachment_count: available.len(),
        }
    }
}

pub struct LocalZoteroState {
    cache_path: PathBuf,
    cache: RwLock<Cache>,
    syncing: AtomicBool,
    last_error: RwLock<Option<String>>,
}

impl LocalZoteroState {
    pub fn load(app: &AppHandle) -> Self {
        let cache_path = app
            .path()
            .app_data_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("zotero-local-v1.json");
        let mut error = None;
        let cache = match fs::read(&cache_path) {
            Ok(bytes) => match serde_json::from_slice::<Cache>(&bytes) {
                Ok(cache) if cache.schema_version == CACHE_SCHEMA => cache,
                Ok(_) => {
                    error = Some("本地 Zotero 缓存版本不兼容，请重新同步。".to_string());
                    Cache::default()
                }
                Err(_) => {
                    error = Some("本地 Zotero 缓存损坏，已等待重新同步。".to_string());
                    Cache::default()
                }
            },
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Cache::default(),
            Err(_) => {
                error = Some("无法读取本地 Zotero 缓存。".to_string());
                Cache::default()
            }
        };
        Self {
            cache_path,
            cache: RwLock::new(cache),
            syncing: AtomicBool::new(false),
            last_error: RwLock::new(error),
        }
    }

    fn public_status(&self, available: bool) -> LocalZoteroStatus {
        let cache = self
            .cache
            .read()
            .unwrap_or_else(|poison| poison.into_inner());
        let mut per_library: HashMap<(&str, LibraryKind, &str), (usize, usize)> = HashMap::new();
        let mut pdf_available_count = 0;
        for paper in &cache.papers {
            let pdf = paper
                .attachments
                .iter()
                .filter(|attachment| attachment_available(attachment))
                .count();
            if pdf > 0 {
                pdf_available_count += 1;
            }
            let counts = per_library
                .entry((&paper.library_id, paper.library_kind, &paper.library_name))
                .or_default();
            counts.0 += 1;
            if pdf > 0 {
                counts.1 += 1;
            }
        }
        let mut libraries: Vec<LocalZoteroLibrary> = cache
            .libraries
            .iter()
            .map(|library| {
                let (paper_count, pdf_count) = per_library
                    .get(&(&*library.id, library.kind, &*library.name))
                    .copied()
                    .unwrap_or_default();
                LocalZoteroLibrary {
                    id: library.id.clone(),
                    kind: library.kind.public_label().to_string(),
                    name: library.name.clone(),
                    paper_count,
                    pdf_available_count: pdf_count,
                }
            })
            .collect();
        libraries.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
        LocalZoteroStatus {
            available,
            syncing: self.syncing.load(Ordering::Acquire),
            cached_paper_count: cache.papers.len(),
            pdf_available_count,
            last_successful_sync_ms: cache.last_successful_sync_ms,
            zotero_version: cache.zotero_version,
            last_error: self
                .last_error
                .read()
                .unwrap_or_else(|poison| poison.into_inner())
                .clone(),
            libraries,
        }
    }

    fn replace_cache(&self, cache: Cache) -> Result<(), String> {
        persist_cache(&self.cache_path, &cache)?;
        *self
            .cache
            .write()
            .unwrap_or_else(|poison| poison.into_inner()) = cache;
        *self
            .last_error
            .write()
            .unwrap_or_else(|poison| poison.into_inner()) = None;
        Ok(())
    }

    fn set_error(&self, message: String) {
        *self
            .last_error
            .write()
            .unwrap_or_else(|poison| poison.into_inner()) = Some(message);
    }

    fn attachment_path(&self, attachment_id: &str) -> Option<PathBuf> {
        let cache = self
            .cache
            .read()
            .unwrap_or_else(|poison| poison.into_inner());
        cache
            .papers
            .iter()
            .flat_map(|paper| paper.attachments.iter())
            .find(|attachment| attachment.id == attachment_id)
            .and_then(|attachment| attachment.local_path.clone())
    }
}

struct SyncFlag<'a>(&'a AtomicBool);

impl Drop for SyncFlag<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[derive(Debug, Deserialize)]
struct ApiItem {
    key: String,
    #[serde(default)]
    version: u64,
    #[serde(default)]
    data: Value,
}

#[derive(Debug, Deserialize)]
struct ApiGroup {
    id: u64,
    #[serde(default)]
    version: u64,
    #[serde(default)]
    data: Value,
}

#[derive(Debug, Clone)]
struct LibrarySpec {
    id: String,
    kind: LibraryKind,
    name: String,
    item_version: Option<u64>,
}

#[derive(Debug, Clone)]
struct AttachmentProbe {
    library: LibrarySpec,
    parent_item: String,
    item_key: String,
    item_version: u64,
    filename: String,
    link_mode: Option<String>,
}

fn client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(20))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "无法初始化本地 Zotero 连接。".to_string())
}

async fn request(client: &Client, path: &str) -> Result<reqwest::Response, String> {
    // `path` is always assembled internally from validated numeric group IDs
    // and Zotero item keys. The WebView never controls the host or scheme.
    let url = format!("{LOCAL_API}/{path}");
    client
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

async fn probe(client: &Client) -> bool {
    match request(client, "").await {
        Ok(response) => response.status().is_success(),
        Err(_) => false,
    }
}

async fn json_get<T: for<'de> Deserialize<'de>>(
    client: &Client,
    path: &str,
) -> Result<(T, Option<u64>), String> {
    let response = request(client, path).await?;
    if !response.status().is_success() {
        return Err(format!(
            "Zotero 本地接口返回 HTTP {}。",
            response.status().as_u16()
        ));
    }
    let version = response
        .headers()
        .get("Last-Modified-Version")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<u64>().ok());
    let value = response
        .json::<T>()
        .await
        .map_err(|_| "Zotero 返回了无法识别的数据。".to_string())?;
    Ok((value, version))
}

async fn fetch_paged_items(
    client: &Client,
    library: &LibrarySpec,
    suffix: &str,
) -> Result<(Vec<ApiItem>, Option<u64>), String> {
    let mut items = Vec::new();
    let mut start = 0;
    let mut library_version = None;
    loop {
        let route = library.kind.route(&library.id);
        let separator = if suffix.contains('?') { '&' } else { '?' };
        let path =
            format!("{route}/{suffix}{separator}format=json&limit={PAGE_SIZE}&start={start}");
        let (mut page, version): (Vec<ApiItem>, Option<u64>) = json_get(client, &path).await?;
        library_version = version.or(library_version);
        let count = page.len();
        items.append(&mut page);
        if count < PAGE_SIZE {
            break;
        }
        start += count;
        if start >= MAX_ITEMS {
            return Err("本地 Zotero 文库超过安全扫描上限（100000 条）。".to_string());
        }
    }
    Ok((items, library_version))
}

async fn libraries(client: &Client) -> Result<Vec<LibrarySpec>, String> {
    let mut result = vec![LibrarySpec {
        id: USER_LIBRARY_ID.to_string(),
        kind: LibraryKind::User,
        name: "我的 Zotero 文库".to_string(),
        item_version: None,
    }];
    let (groups, _): (Vec<ApiGroup>, Option<u64>) =
        json_get(client, "users/0/groups?format=json").await?;
    for group in groups {
        let name = value_string(&group.data, "name")
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| format!("Zotero 群组 {}", group.id));
        result.push(LibrarySpec {
            id: group.id.to_string(),
            kind: LibraryKind::Group,
            name,
            item_version: Some(group.version),
        });
    }
    Ok(result)
}

async fn resolve_attachment(client: &Client, probe: AttachmentProbe) -> (String, CachedAttachment) {
    let route = probe.library.kind.route(&probe.library.id);
    let path = format!("{route}/items/{}/file/view/url", probe.item_key);
    let local_path = match request(client, &path).await {
        Ok(response) if response.status().is_success() => response
            .text()
            .await
            .ok()
            .and_then(|body| file_url_path(body.trim()))
            .filter(|path| path.is_file()),
        _ => None,
    };
    (
        probe.parent_item,
        CachedAttachment {
            id: stable_id("attachment", &probe.library, &probe.item_key),
            item_key: probe.item_key,
            item_version: probe.item_version,
            filename: probe.filename,
            link_mode: probe.link_mode,
            local_path,
        },
    )
}

async fn scan(client: &Client) -> Result<Cache, String> {
    let specs = libraries(client).await?;
    let mut papers = Vec::new();
    let mut cache_libraries = Vec::new();
    let mut max_version = None;

    for mut library in specs {
        let (items, item_version) = fetch_paged_items(client, &library, "items/top").await?;
        let (attachments, attachment_version) =
            fetch_paged_items(client, &library, "items?itemType=attachment").await?;
        library.item_version = item_version.or(attachment_version).or(library.item_version);
        max_version = [max_version, library.item_version]
            .into_iter()
            .flatten()
            .max();

        let probes: Vec<AttachmentProbe> = attachments
            .into_iter()
            .filter_map(|attachment| attachment_probe(&library, attachment))
            .collect();
        let resolved: Vec<(String, CachedAttachment)> = stream::iter(probes)
            .map(|attachment| resolve_attachment(client, attachment))
            .buffer_unordered(ATTACHMENT_PROBES)
            .collect()
            .await;
        let mut by_parent: HashMap<String, Vec<CachedAttachment>> = HashMap::new();
        for (parent, attachment) in resolved {
            by_parent.entry(parent).or_default().push(attachment);
        }

        for item in items {
            if let Some(paper) = cached_paper(&library, item, &mut by_parent) {
                papers.push(paper);
            }
        }
        cache_libraries.push(CachedLibrary {
            id: library.id,
            kind: library.kind,
            name: library.name,
            item_version: library.item_version,
        });
    }

    papers.sort_by(|a, b| {
        b.year
            .cmp(&a.year)
            .then_with(|| a.title.to_lowercase().cmp(&b.title.to_lowercase()))
    });
    Ok(Cache {
        schema_version: CACHE_SCHEMA,
        last_successful_sync_ms: Some(now_ms()),
        zotero_version: max_version,
        libraries: cache_libraries,
        papers,
    })
}

fn attachment_probe(library: &LibrarySpec, item: ApiItem) -> Option<AttachmentProbe> {
    let data = &item.data;
    let content_type = value_string(data, "contentType").unwrap_or_default();
    let filename = value_string(data, "filename").unwrap_or_default();
    if content_type.to_ascii_lowercase() != "application/pdf"
        && !filename.to_ascii_lowercase().ends_with(".pdf")
    {
        return None;
    }
    // A standalone PDF has no parent item and appears as a top-level Zotero
    // item. Associate it with itself so it still becomes a readable row.
    let parent_item = value_string(data, "parentItem")
        .filter(|parent| !parent.is_empty())
        .unwrap_or_else(|| item.key.clone());
    Some(AttachmentProbe {
        library: library.clone(),
        parent_item,
        item_key: item.key,
        item_version: item.version,
        filename: if filename.is_empty() {
            "paper.pdf".to_string()
        } else {
            filename
        },
        link_mode: value_string(data, "linkMode"),
    })
}

fn cached_paper(
    library: &LibrarySpec,
    item: ApiItem,
    attachments: &mut HashMap<String, Vec<CachedAttachment>>,
) -> Option<CachedPaper> {
    let data = &item.data;
    let item_type = value_string(data, "itemType").unwrap_or_default();
    if matches!(item_type.as_str(), "note" | "annotation") {
        return None;
    }
    let own_attachments = attachments.remove(&item.key).unwrap_or_default();
    let title = value_string(data, "title")
        .filter(|value| !value.trim().is_empty())
        .map(|value| value.trim().to_string())
        .or_else(|| {
            own_attachments.first().map(|attachment| {
                attachment
                    .filename
                    .strip_suffix(".pdf")
                    .unwrap_or(&attachment.filename)
                    .to_string()
            })
        })?;
    // A top-level non-PDF attachment is not a paper. A standalone PDF is.
    if item_type == "attachment" && own_attachments.is_empty() {
        return None;
    }
    let venue = [
        "publicationTitle",
        "proceedingsTitle",
        "conferenceName",
        "repository",
        "university",
    ]
    .into_iter()
    .find_map(|key| value_string(data, key).filter(|value| !value.trim().is_empty()));
    let doi = value_string(data, "DOI")
        .map(|value| normalize_doi(&value))
        .filter(|value| !value.is_empty());
    let date = value_string(data, "date");
    Some(CachedPaper {
        id: stable_id("paper", library, &item.key),
        library_id: library.id.clone(),
        library_kind: library.kind,
        library_name: library.name.clone(),
        item_key: item.key.clone(),
        item_version: item.version,
        item_type,
        title,
        authors: creators(data),
        year: date.as_deref().and_then(parse_year),
        venue,
        doi,
        abstract_text: value_string(data, "abstractNote").filter(|value| !value.trim().is_empty()),
        url: value_string(data, "url").filter(|value| !value.trim().is_empty()),
        date_added: value_string(data, "dateAdded").filter(|value| !value.trim().is_empty()),
        attachments: own_attachments,
    })
}

fn creators(data: &Value) -> Vec<String> {
    data.get("creators")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|creator| {
            let name = value_string(creator, "name").unwrap_or_default();
            if !name.trim().is_empty() {
                return Some(name.trim().to_string());
            }
            let first = value_string(creator, "firstName").unwrap_or_default();
            let last = value_string(creator, "lastName").unwrap_or_default();
            let joined = format!("{first} {last}").trim().to_string();
            (!joined.is_empty()).then_some(joined)
        })
        .collect()
}

fn value_string(value: &Value, key: &str) -> Option<String> {
    value.get(key)?.as_str().map(ToString::to_string)
}

fn normalize_doi(raw: &str) -> String {
    let value = raw.trim();
    for prefix in ["https://doi.org/", "http://doi.org/", "doi:"] {
        if value.to_ascii_lowercase().starts_with(prefix) {
            return value[prefix.len()..].trim().to_string();
        }
    }
    value.to_string()
}

fn parse_year(raw: &str) -> Option<i32> {
    raw.as_bytes()
        .windows(4)
        .filter_map(|window| std::str::from_utf8(window).ok())
        .filter_map(|window| window.parse::<i32>().ok())
        .find(|year| (1000..=2999).contains(year))
}

fn stable_id(kind: &str, library: &LibrarySpec, key: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(kind.as_bytes());
    hash.update([0]);
    hash.update(library.kind.public_label().as_bytes());
    hash.update([0]);
    hash.update(library.id.as_bytes());
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

fn attachment_available(attachment: &CachedAttachment) -> bool {
    attachment.local_path.as_deref().is_some_and(Path::is_file)
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}

fn persist_cache(path: &Path, cache: &Cache) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "本地 Zotero 缓存路径无效。".to_string())?;
    fs::create_dir_all(parent).map_err(|_| "无法创建本地 Zotero 缓存目录。".to_string())?;
    let temp = path.with_extension("json.tmp");
    let bytes = serde_json::to_vec(cache).map_err(|_| "无法编码本地 Zotero 缓存。".to_string())?;
    fs::write(&temp, bytes).map_err(|_| "无法写入本地 Zotero 缓存。".to_string())?;
    fs::rename(&temp, path).map_err(|_| "无法保存本地 Zotero 缓存。".to_string())
}

#[tauri::command]
pub async fn zotero_local_status(
    state: State<'_, LocalZoteroState>,
) -> Result<LocalZoteroStatus, String> {
    let available = match client() {
        Ok(client) => probe(&client).await,
        Err(_) => false,
    };
    Ok(state.public_status(available))
}

#[tauri::command]
pub async fn zotero_local_sync(
    state: State<'_, LocalZoteroState>,
) -> Result<LocalZoteroStatus, String> {
    if state
        .syncing
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err("本地 Zotero 正在同步。".to_string());
    }
    let _flag = SyncFlag(&state.syncing);
    let client = client()?;
    match scan(&client).await {
        Ok(cache) => {
            state.replace_cache(cache)?;
            Ok(state.public_status(true))
        }
        Err(message) => {
            state.set_error(message.clone());
            Err(message)
        }
    }
}

#[tauri::command]
pub fn zotero_local_list(state: State<'_, LocalZoteroState>) -> Vec<LocalZoteroPaper> {
    state
        .cache
        .read()
        .unwrap_or_else(|poison| poison.into_inner())
        .papers
        .iter()
        .map(LocalZoteroPaper::from)
        .collect()
}

#[tauri::command]
pub fn zotero_local_get(
    paper_id: String,
    state: State<'_, LocalZoteroState>,
) -> Result<LocalZoteroPaper, String> {
    state
        .cache
        .read()
        .unwrap_or_else(|poison| poison.into_inner())
        .papers
        .iter()
        .find(|paper| paper.id == paper_id)
        .map(LocalZoteroPaper::from)
        .ok_or_else(|| "这篇本地 Zotero 文献已不在缓存中，请重新同步。".to_string())
}

/// Return an opaque URL that only this Tauri app can serve. The command checks
/// the attachment exists before handing it to pdf.js, but deliberately does not
/// reveal the backing path.
#[tauri::command]
pub fn zotero_local_pdf_url(
    attachment_id: String,
    state: State<'_, LocalZoteroState>,
) -> Result<String, String> {
    let path = state
        .attachment_path(&attachment_id)
        .ok_or_else(|| "这份 PDF 尚未下载到本机 Zotero。".to_string())?;
    validate_pdf(&path)?;
    Ok(format!("pharos-local://localhost/zotero/{}", attachment_id))
}

/// Read a selected local PDF for the explicit “import into Pharos” action.
/// Normal reading uses the range-capable custom protocol instead of copying the
/// entire document through JSON IPC.
#[tauri::command]
pub fn zotero_local_pdf_bytes(
    attachment_id: String,
    state: State<'_, LocalZoteroState>,
) -> Result<tauri::ipc::Response, String> {
    let path = state
        .attachment_path(&attachment_id)
        .ok_or_else(|| "这份 PDF 尚未下载到本机 Zotero。".to_string())?;
    validate_pdf(&path)?;
    let bytes = fs::read(path).map_err(|_| "无法读取本地 Zotero PDF。".to_string())?;
    Ok(tauri::ipc::Response::new(bytes))
}

fn validate_pdf(path: &Path) -> Result<u64, String> {
    let metadata =
        fs::metadata(path).map_err(|_| "本地 Zotero PDF 已被移动或删除。".to_string())?;
    if !metadata.is_file() {
        return Err("本地 Zotero PDF 路径不是文件。".to_string());
    }
    let mut file = File::open(path).map_err(|_| "无法打开本地 Zotero PDF。".to_string())?;
    let mut magic = [0_u8; 5];
    file.read_exact(&mut magic)
        .map_err(|_| "本地附件不是有效的 PDF。".to_string())?;
    if &magic != b"%PDF-" {
        return Err("本地附件不是有效的 PDF。".to_string());
    }
    Ok(metadata.len())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ByteRange {
    start: u64,
    end: u64,
}

fn parse_range(raw: Option<&str>, len: u64) -> Result<Option<ByteRange>, ()> {
    let Some(raw) = raw else { return Ok(None) };
    let value = raw.strip_prefix("bytes=").ok_or(())?;
    if value.contains(',') || len == 0 {
        return Err(());
    }
    let (start, end) = value.split_once('-').ok_or(())?;
    let range = if start.is_empty() {
        let suffix = end.parse::<u64>().map_err(|_| ())?;
        if suffix == 0 {
            return Err(());
        }
        let count = suffix.min(len);
        ByteRange {
            start: len - count,
            end: len - 1,
        }
    } else {
        let start = start.parse::<u64>().map_err(|_| ())?;
        if start >= len {
            return Err(());
        }
        let end = if end.is_empty() {
            len - 1
        } else {
            end.parse::<u64>().map_err(|_| ())?.min(len - 1)
        };
        if end < start {
            return Err(());
        }
        ByteRange { start, end }
    };
    Ok(Some(range))
}

fn response_builder(status: StatusCode) -> http::response::Builder {
    http::Response::builder()
        .status(status.as_u16())
        .header(header::ACCESS_CONTROL_ALLOW_ORIGIN.as_str(), "*")
        .header(header::CACHE_CONTROL.as_str(), "private, no-store")
        .header("X-Content-Type-Options", "nosniff")
}

/// Serve one cached attachment through a range-aware, path-free protocol.
pub fn protocol_response(
    app: &AppHandle,
    request: http::Request<Vec<u8>>,
) -> http::Response<Vec<u8>> {
    if request.method() == http::Method::OPTIONS {
        return response_builder(StatusCode::NO_CONTENT)
            .header(
                header::ACCESS_CONTROL_ALLOW_METHODS.as_str(),
                "GET, HEAD, OPTIONS",
            )
            .header(header::ACCESS_CONTROL_ALLOW_HEADERS.as_str(), "Range")
            .body(Vec::new())
            .unwrap();
    }
    if request.method() != http::Method::GET && request.method() != http::Method::HEAD {
        return response_builder(StatusCode::METHOD_NOT_ALLOWED)
            .body(Vec::new())
            .unwrap();
    }
    let Some(attachment_id) = request.uri().path().strip_prefix("/zotero/") else {
        return response_builder(StatusCode::NOT_FOUND)
            .body(Vec::new())
            .unwrap();
    };
    if attachment_id.is_empty()
        || !attachment_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return response_builder(StatusCode::BAD_REQUEST)
            .body(Vec::new())
            .unwrap();
    }
    let state = app.state::<LocalZoteroState>();
    let Some(path) = state.attachment_path(attachment_id) else {
        return response_builder(StatusCode::NOT_FOUND)
            .body(Vec::new())
            .unwrap();
    };
    let Ok(len) = validate_pdf(&path) else {
        return response_builder(StatusCode::NOT_FOUND)
            .body(Vec::new())
            .unwrap();
    };
    let raw_range = request
        .headers()
        .get(header::RANGE)
        .and_then(|value| value.to_str().ok());
    let range = match parse_range(raw_range, len) {
        Ok(range) => range,
        Err(()) => {
            return response_builder(StatusCode::RANGE_NOT_SATISFIABLE)
                .header(header::CONTENT_RANGE.as_str(), format!("bytes */{len}"))
                .body(Vec::new())
                .unwrap()
        }
    };
    let selected = range.unwrap_or(ByteRange {
        start: 0,
        end: len.saturating_sub(1),
    });
    let count = selected.end.saturating_sub(selected.start) + 1;
    let mut body = Vec::new();
    if request.method() == http::Method::GET {
        let result = File::open(path).and_then(|mut file| {
            file.seek(SeekFrom::Start(selected.start))?;
            let mut limited = file.take(count);
            limited.read_to_end(&mut body)?;
            Ok(())
        });
        if result.is_err() || body.len() as u64 != count {
            return response_builder(StatusCode::INTERNAL_SERVER_ERROR)
                .body(Vec::new())
                .unwrap();
        }
    }
    let status = if range.is_some() {
        StatusCode::PARTIAL_CONTENT
    } else {
        StatusCode::OK
    };
    let mut builder = response_builder(status)
        .header(header::CONTENT_TYPE.as_str(), "application/pdf")
        .header(header::ACCEPT_RANGES.as_str(), "bytes")
        .header(header::CONTENT_LENGTH.as_str(), count.to_string());
    if range.is_some() {
        builder = builder.header(
            header::CONTENT_RANGE.as_str(),
            format!("bytes {}-{}/{len}", selected.start, selected.end),
        );
    }
    builder.body(body).unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn library() -> LibrarySpec {
        LibrarySpec {
            id: "0".to_string(),
            kind: LibraryKind::User,
            name: "Personal".to_string(),
            item_version: None,
        }
    }

    #[test]
    fn stable_ids_do_not_contain_source_keys() {
        let id = stable_id("paper", &library(), "ABC12345");
        assert!(id.starts_with("zotero-local-"));
        assert!(!id.contains("ABC12345"));
    }

    #[test]
    fn years_are_conservative() {
        assert_eq!(parse_year("2024-07-03"), Some(2024));
        assert_eq!(parse_year("Spring 1998"), Some(1998));
        assert_eq!(parse_year("volume 42"), None);
        assert_eq!(parse_year("999"), None);
    }

    #[test]
    fn byte_ranges_cover_pdfjs_patterns() {
        assert_eq!(parse_range(None, 100), Ok(None));
        assert_eq!(
            parse_range(Some("bytes=0-9"), 100),
            Ok(Some(ByteRange { start: 0, end: 9 }))
        );
        assert_eq!(
            parse_range(Some("bytes=90-"), 100),
            Ok(Some(ByteRange { start: 90, end: 99 }))
        );
        assert_eq!(
            parse_range(Some("bytes=-10"), 100),
            Ok(Some(ByteRange { start: 90, end: 99 }))
        );
        assert!(parse_range(Some("bytes=100-101"), 100).is_err());
        assert!(parse_range(Some("bytes=0-1,4-5"), 100).is_err());
    }

    #[test]
    fn local_file_urls_only() {
        assert!(file_url_path("https://example.test/file.pdf").is_none());
        assert!(file_url_path("file:///tmp/paper.pdf").is_some());
    }

    #[test]
    fn paper_projection_never_exposes_paths() {
        let paper = CachedPaper {
            id: "p".to_string(),
            library_id: "0".to_string(),
            library_kind: LibraryKind::User,
            library_name: "Personal".to_string(),
            item_key: "KEY".to_string(),
            item_version: 1,
            item_type: "journalArticle".to_string(),
            title: "Paper".to_string(),
            authors: vec![],
            year: None,
            venue: None,
            doi: None,
            abstract_text: None,
            url: None,
            date_added: None,
            attachments: vec![CachedAttachment {
                id: "a".to_string(),
                item_key: "ATTACH".to_string(),
                item_version: 1,
                filename: "paper.pdf".to_string(),
                link_mode: Some("imported_file".to_string()),
                local_path: Some(PathBuf::from("/Users/private/Zotero/storage/paper.pdf")),
            }],
        };
        let json = serde_json::to_string(&LocalZoteroPaper::from(&paper)).unwrap();
        assert!(!json.contains("/Users/private"));
        assert!(!json.contains("localPath"));
    }

    #[test]
    #[ignore = "requires a running Zotero Desktop instance"]
    fn live_local_api_scan_reports_only_aggregate_counts() {
        let cache = tauri::async_runtime::block_on(async {
            let client = client().expect("client");
            scan(&client).await.expect("local Zotero scan")
        });
        let available = cache
            .papers
            .iter()
            .filter(|paper| paper.attachments.iter().any(attachment_available))
            .count();
        eprintln!(
            "local Zotero scan: libraries={} papers={} pdf_available={}",
            cache.libraries.len(),
            cache.papers.len(),
            available
        );
        assert_eq!(cache.schema_version, CACHE_SCHEMA);
    }
}
