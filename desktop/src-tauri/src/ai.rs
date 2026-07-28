//! Local-first paper conversations and OpenAI-compatible model access.
//!
//! The WebView never receives a saved API key. It sends a paper reference and
//! locally extracted text to this module; Rust stores the durable conversation
//! log inside the active Pharos Workspace and talks to the configured model.

use std::{
    collections::{HashMap, HashSet},
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use futures_util::StreamExt;
use keyring::Entry;
use reqwest::StatusCode;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{ipc::Channel, State};

use crate::workspace::WorkspaceState;

const PROVIDER_FILE: &str = "config/model-provider.json";
const KEYRING_SERVICE: &str = "io.github.hyyyyyyz.pharos.model-provider";
const MAX_PAPER_CHARS: usize = 160_000;
const MAX_REQUEST_CONTEXT_CHARS: usize = 92_000;
const MAX_HISTORY_CHARS: usize = 48_000;
const MAX_MESSAGE_CHARS: usize = 32_000;
static ID_COUNTER: AtomicU64 = AtomicU64::new(1);

#[derive(Default)]
pub struct AiState {
    cancellations: Mutex<HashMap<String, Arc<AtomicBool>>>,
    preparing: Mutex<HashSet<String>>,
    io_lock: Mutex<()>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DocumentRef {
    pub key: String,
    pub kind: String,
    pub title: String,
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default)]
    pub library_id: Option<String>,
    #[serde(default)]
    pub item_key: Option<String>,
    #[serde(default)]
    pub attachment_id: Option<String>,
    #[serde(default)]
    pub paper_id: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DocumentContext {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub authors: String,
    #[serde(default)]
    pub abstract_text: String,
    #[serde(default)]
    pub full_text: String,
    #[serde(default)]
    pub current_page: Option<u32>,
    #[serde(default)]
    pub page_count: Option<u32>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderConfig {
    pub base_url: String,
    pub model: String,
    #[serde(default = "default_temperature")]
    pub temperature: f64,
    #[serde(default)]
    pub max_output_tokens: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderSaveRequest {
    pub base_url: String,
    pub model: String,
    #[serde(default = "default_temperature")]
    pub temperature: f64,
    #[serde(default)]
    pub max_output_tokens: Option<u32>,
    #[serde(default)]
    pub api_key: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderStatus {
    pub configured: bool,
    pub has_credential: bool,
    pub base_url: String,
    pub model: String,
    pub temperature: f64,
    pub max_output_tokens: Option<u32>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationSummary {
    pub id: String,
    pub document_key: String,
    pub document_kind: String,
    pub document_title: String,
    pub title: String,
    pub source: String,
    pub source_session_id: Option<String>,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatMessage {
    pub id: String,
    pub role: String,
    pub content: String,
    pub timestamp_ms: i64,
    #[serde(default)]
    pub model: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationDetail {
    #[serde(flatten)]
    pub summary: ConversationSummary,
    pub messages: Vec<ChatMessage>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationCreateRequest {
    pub document_ref: DocumentRef,
    #[serde(default)]
    pub title: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatSendRequest {
    pub run_id: String,
    pub conversation_id: String,
    pub document_ref: DocumentRef,
    pub message: String,
    #[serde(default)]
    pub current_context: Option<DocumentContext>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PrepareDocumentRequest {
    pub document_ref: DocumentRef,
    pub context: DocumentContext,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PaperContextStatus {
    pub document_key: String,
    pub status: String,
    pub char_count: u64,
    pub page_count: Option<u32>,
    pub has_summary: bool,
    pub summary: Option<String>,
    pub error: Option<String>,
    pub updated_at_ms: i64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChatEvent {
    Started { run_id: String },
    Delta { text: String },
    Done { message: ChatMessage },
    Error { message: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ConversationEvent {
    schema_version: u32,
    event_id: String,
    seq: u64,
    timestamp_ms: i64,
    #[serde(rename = "type")]
    kind: String,
    payload: Value,
}

fn default_temperature() -> f64 {
    0.25
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as i64
}

fn new_id(prefix: &str) -> String {
    let counter = ID_COUNTER.fetch_add(1, Ordering::Relaxed);
    let seed = format!("{prefix}:{}:{}:{}", now_ms(), std::process::id(), counter);
    let digest = hex::encode(Sha256::digest(seed.as_bytes()));
    format!("{prefix}-{}", &digest[..24])
}

fn content_hash(text: &str) -> String {
    hex::encode(Sha256::digest(text.as_bytes()))
}

fn clean_document(reference: &DocumentRef) -> Result<(), String> {
    if reference.key.trim().is_empty() || reference.key.len() > 1024 {
        return Err("论文标识无效。".to_string());
    }
    if reference.kind.trim().is_empty() || reference.kind.len() > 80 {
        return Err("论文来源无效。".to_string());
    }
    Ok(())
}

fn clean_message(value: &str) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty() {
        return Err("请输入问题。".to_string());
    }
    if value.chars().count() > MAX_MESSAGE_CHARS {
        return Err("单条消息过长，请拆成几个问题。".to_string());
    }
    Ok(value.to_string())
}

fn db(workspace: &WorkspaceState) -> Result<Connection, String> {
    let connection = Connection::open(workspace.db_path())
        .map_err(|error| format!("无法打开 Pharos Workspace 数据库：{error}"))?;
    connection
        .execute_batch(
            "PRAGMA foreign_keys = ON;\n\
             PRAGMA journal_mode = WAL;\n\
             CREATE TABLE IF NOT EXISTS conversations (\n\
               id TEXT PRIMARY KEY, document_key TEXT NOT NULL, document_kind TEXT NOT NULL,\n\
               document_title TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'pharos',\n\
               source_session_id TEXT, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,\n\
               event_path TEXT NOT NULL, deleted_at_ms INTEGER\n\
             );\n\
             CREATE INDEX IF NOT EXISTS idx_conversations_document\n\
               ON conversations(document_key, updated_at_ms DESC);\n\
             CREATE TABLE IF NOT EXISTS paper_contexts (\n\
               document_key TEXT PRIMARY KEY, document_kind TEXT NOT NULL, document_title TEXT NOT NULL,\n\
               content_hash TEXT NOT NULL, text_path TEXT NOT NULL, summary TEXT, page_count INTEGER,\n\
               char_count INTEGER NOT NULL, status TEXT NOT NULL, error TEXT,\n\
               created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL\n\
             );",
        )
        .map_err(|error| format!("无法初始化 AI 对话数据库：{error}"))?;
    Ok(connection)
}

fn provider_path(workspace: &WorkspaceState) -> PathBuf {
    workspace.root().join(PROVIDER_FILE)
}

fn load_provider(workspace: &WorkspaceState) -> Result<Option<ProviderConfig>, String> {
    let path = provider_path(workspace);
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("无法读取模型配置：{error}")),
    };
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|_| "模型配置文件已损坏，请重新保存。".to_string())
}

fn write_provider(workspace: &WorkspaceState, config: &ProviderConfig) -> Result<(), String> {
    let path = provider_path(workspace);
    let parent = path
        .parent()
        .ok_or_else(|| "模型配置路径无效。".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("无法创建模型配置目录：{error}"))?;
    let temp = path.with_extension("json.tmp");
    let bytes = serde_json::to_vec_pretty(config).map_err(|error| error.to_string())?;
    fs::write(&temp, bytes).map_err(|error| format!("无法写入模型配置：{error}"))?;
    match fs::rename(&temp, &path) {
        Ok(()) => {}
        Err(_) if path.exists() => {
            let backup = path.with_extension("json.bak");
            let _ = fs::remove_file(&backup);
            fs::rename(&path, &backup).map_err(|error| format!("无法替换模型配置：{error}"))?;
            if let Err(error) = fs::rename(&temp, &path) {
                let _ = fs::rename(&backup, &path);
                let _ = fs::remove_file(&temp);
                return Err(format!("无法提交模型配置：{error}"));
            }
            let _ = fs::remove_file(backup);
        }
        Err(error) => {
            let _ = fs::remove_file(&temp);
            return Err(format!("无法提交模型配置：{error}"));
        }
    }
    Ok(())
}

fn credential_entry(workspace: &WorkspaceState) -> Result<Entry, String> {
    let workspace_id = workspace.workspace_id()?;
    Entry::new(KEYRING_SERVICE, &format!("workspace-{workspace_id}"))
        .map_err(|_| "无法访问系统凭据库。".to_string())
}

fn load_api_key(workspace: &WorkspaceState) -> Result<Option<String>, String> {
    let entry = credential_entry(workspace)?;
    match entry.get_password() {
        Ok(value) if !value.trim().is_empty() => Ok(Some(value)),
        Ok(_) => Ok(None),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(_) => Err("无法从系统凭据库读取模型密钥。".to_string()),
    }
}

fn validate_provider(config: &ProviderConfig) -> Result<(), String> {
    let url = reqwest::Url::parse(config.base_url.trim())
        .map_err(|_| "API Base URL 不是有效网址。".to_string())?;
    let local_http = url.scheme() == "http"
        && url.host_str().is_some_and(|host| {
            host == "localhost"
                || host
                    .parse::<std::net::IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback())
        });
    if url.scheme() != "https" && !local_http {
        return Err("API Base URL 必须使用 HTTPS；本机 localhost 可使用 HTTP。".to_string());
    }
    if config.model.trim().is_empty() || config.model.len() > 200 {
        return Err("模型名称无效。".to_string());
    }
    if !(0.0..=2.0).contains(&config.temperature) {
        return Err("Temperature 必须在 0 到 2 之间。".to_string());
    }
    if config
        .max_output_tokens
        .is_some_and(|value| !(256..=128_000).contains(&value))
    {
        return Err("最大输出 Token 必须在 256 到 128000 之间。".to_string());
    }
    Ok(())
}

fn chat_endpoint(base: &str) -> String {
    let mut value = base.trim().trim_end_matches('/').to_string();
    if value.ends_with("/chat/completions") {
        return value;
    }
    let last = value.rsplit('/').next().unwrap_or_default();
    if last.len() >= 2
        && last.starts_with('v')
        && last[1..]
            .chars()
            .all(|character| character.is_ascii_digit())
    {
        value.push_str("/chat/completions");
    } else {
        value.push_str("/v1/chat/completions");
    }
    value
}

#[tauri::command]
pub fn provider_status(workspace: State<'_, WorkspaceState>) -> Result<ProviderStatus, String> {
    let config = load_provider(&workspace)?;
    let has_credential = load_api_key(&workspace)?.is_some();
    let value = config.unwrap_or(ProviderConfig {
        base_url: String::new(),
        model: String::new(),
        temperature: default_temperature(),
        max_output_tokens: Some(4096),
    });
    Ok(ProviderStatus {
        configured: !value.base_url.is_empty() && !value.model.is_empty() && has_credential,
        has_credential,
        base_url: value.base_url,
        model: value.model,
        temperature: value.temperature,
        max_output_tokens: value.max_output_tokens,
    })
}

#[tauri::command]
pub fn provider_save(
    request: ProviderSaveRequest,
    workspace: State<'_, WorkspaceState>,
) -> Result<ProviderStatus, String> {
    let config = ProviderConfig {
        base_url: request.base_url.trim().trim_end_matches('/').to_string(),
        model: request.model.trim().to_string(),
        temperature: request.temperature,
        max_output_tokens: request.max_output_tokens,
    };
    validate_provider(&config)?;
    if let Some(secret) = request.api_key.map(|value| value.trim().to_string()) {
        if !secret.is_empty() {
            credential_entry(&workspace)?
                .set_password(&secret)
                .map_err(|_| "无法把 API Key 保存到系统凭据库。".to_string())?;
        }
    }
    if load_api_key(&workspace)?.is_none() {
        return Err("请输入 API Key。密钥只会保存在系统凭据库中。".to_string());
    }
    write_provider(&workspace, &config)?;
    provider_status(workspace)
}

#[tauri::command]
pub fn provider_clear(workspace: State<'_, WorkspaceState>) -> Result<(), String> {
    if let Ok(entry) = credential_entry(&workspace) {
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => {}
            Err(_) => return Err("无法从系统凭据库删除模型密钥。".to_string()),
        }
    }
    let path = provider_path(&workspace);
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("无法删除模型配置：{error}")),
    }
}

fn conversation_path(workspace: &WorkspaceState, id: &str) -> PathBuf {
    let shard = id
        .strip_prefix("conv-")
        .and_then(|value| value.get(..2))
        .unwrap_or("00");
    workspace
        .root()
        .join("conversations")
        .join(shard)
        .join(id)
        .join("events-000001.jsonl")
}

fn relative_path(workspace: &WorkspaceState, path: &Path) -> Result<String, String> {
    path.strip_prefix(workspace.root())
        .map(|value| value.to_string_lossy().replace('\\', "/"))
        .map_err(|_| "会话文件不在 Pharos Workspace 内。".to_string())
}

fn absolute_event_path(workspace: &WorkspaceState, relative: &str) -> Result<PathBuf, String> {
    if relative.starts_with('/')
        || relative.contains('\\')
        || relative
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err("会话文件路径无效。".to_string());
    }
    Ok(workspace.root().join(relative))
}

fn append_event(path: &Path, kind: &str, payload: Value) -> Result<(), String> {
    let parent = path.parent().ok_or_else(|| "会话目录无效。".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("无法创建会话目录：{error}"))?;
    let seq = if path.exists() {
        let reader = BufReader::new(File::open(path).map_err(|error| error.to_string())?);
        reader.lines().count() as u64 + 1
    } else {
        1
    };
    let event = ConversationEvent {
        schema_version: 1,
        event_id: new_id("evt"),
        seq,
        timestamp_ms: now_ms(),
        kind: kind.to_string(),
        payload,
    };
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| format!("无法打开会话日志：{error}"))?;
    serde_json::to_writer(&mut file, &event).map_err(|error| error.to_string())?;
    file.write_all(b"\n")
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_data())
        .map_err(|error| format!("无法提交会话记录：{error}"))
}

fn row_summary(row: &rusqlite::Row<'_>) -> rusqlite::Result<ConversationSummary> {
    Ok(ConversationSummary {
        id: row.get(0)?,
        document_key: row.get(1)?,
        document_kind: row.get(2)?,
        document_title: row.get(3)?,
        title: row.get(4)?,
        source: row.get(5)?,
        source_session_id: row.get(6)?,
        created_at_ms: row.get(7)?,
        updated_at_ms: row.get(8)?,
    })
}

#[tauri::command]
pub fn conversation_list(
    document_ref: DocumentRef,
    workspace: State<'_, WorkspaceState>,
) -> Result<Vec<ConversationSummary>, String> {
    clean_document(&document_ref)?;
    let connection = db(&workspace)?;
    let mut statement = connection
        .prepare(
            "SELECT id, document_key, document_kind, document_title, title, source,\n\
                    source_session_id, created_at_ms, updated_at_ms\n\
             FROM conversations WHERE document_key = ?1 AND deleted_at_ms IS NULL\n\
             ORDER BY updated_at_ms DESC",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([document_ref.key], row_summary)
        .map_err(|error| error.to_string())?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn conversation_create(
    request: ConversationCreateRequest,
    workspace: State<'_, WorkspaceState>,
    state: State<'_, AiState>,
) -> Result<ConversationSummary, String> {
    clean_document(&request.document_ref)?;
    let id = new_id("conv");
    let title = request
        .title
        .map(|value| value.trim().chars().take(120).collect::<String>())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "论文对话".to_string());
    let path = conversation_path(&workspace, &id);
    let relative = relative_path(&workspace, &path)?;
    let timestamp = now_ms();
    let summary = ConversationSummary {
        id: id.clone(),
        document_key: request.document_ref.key.clone(),
        document_kind: request.document_ref.kind.clone(),
        document_title: request.document_ref.title.clone(),
        title,
        source: "pharos".to_string(),
        source_session_id: None,
        created_at_ms: timestamp,
        updated_at_ms: timestamp,
    };
    let _guard = state
        .io_lock
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    append_event(
        &path,
        "session_meta",
        json!({
            "conversationId": id,
            "document": request.document_ref,
            "title": summary.title,
            "source": "pharos"
        }),
    )?;
    db(&workspace)?
        .execute(
            "INSERT INTO conversations(id, document_key, document_kind, document_title, title,\n\
              source, source_session_id, created_at_ms, updated_at_ms, event_path, deleted_at_ms)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, 'pharos', NULL, ?6, ?6, ?7, NULL)",
            params![
                summary.id,
                summary.document_key,
                summary.document_kind,
                summary.document_title,
                summary.title,
                timestamp,
                relative
            ],
        )
        .map_err(|error| format!("无法建立论文对话：{error}"))?;
    Ok(summary)
}

fn conversation_row(
    workspace: &WorkspaceState,
    conversation_id: &str,
) -> Result<(ConversationSummary, String), String> {
    db(workspace)?
        .query_row(
            "SELECT id, document_key, document_kind, document_title, title, source,\n\
                    source_session_id, created_at_ms, updated_at_ms, event_path\n\
             FROM conversations WHERE id = ?1 AND deleted_at_ms IS NULL",
            [conversation_id],
            |row| Ok((row_summary(row)?, row.get(9)?)),
        )
        .optional()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "这段对话不存在或已删除。".to_string())
}

fn read_messages(path: &Path) -> Result<Vec<ChatMessage>, String> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("无法读取会话日志：{error}")),
    };
    let mut messages = Vec::new();
    for line in BufReader::new(file).lines() {
        let line = match line {
            Ok(line) if line.len() <= 2 * 1024 * 1024 => line,
            _ => continue,
        };
        let event: ConversationEvent = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if event.kind != "message" {
            continue;
        }
        let message: ChatMessage = match serde_json::from_value(event.payload) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if message.role == "user" || message.role == "assistant" {
            messages.push(message);
        }
    }
    Ok(messages)
}

/// Import only already-visible dialogue from another local client. The caller
/// is responsible for filtering its source format; this function gives the
/// imported transcript the same append-only storage and SQLite index as native
/// Pharos conversations.
pub(crate) fn import_external_conversation(
    workspace: &WorkspaceState,
    state: &AiState,
    document_ref: Option<DocumentRef>,
    title: String,
    source: &str,
    source_session_id: String,
    messages: Vec<(String, String, i64)>,
) -> Result<ConversationSummary, String> {
    if source.trim().is_empty() || source.len() > 80 || source_session_id.trim().is_empty() {
        return Err("外部对话来源无效。".to_string());
    }
    if messages.is_empty() {
        return Err("这段外部对话没有可导入的用户或助手消息。".to_string());
    }
    let title = title.trim().chars().take(120).collect::<String>();
    let document_ref = document_ref.unwrap_or_else(|| DocumentRef {
        key: format!(
            "{source}:{}",
            content_hash(&source_session_id)[..24].to_string()
        ),
        kind: source.to_string(),
        title: if title.is_empty() {
            "导入的对话".to_string()
        } else {
            title.clone()
        },
        source_id: None,
        library_id: None,
        item_key: None,
        attachment_id: None,
        paper_id: None,
    });
    clean_document(&document_ref)?;

    if let Some(existing) = db(workspace)?
        .query_row(
            "SELECT id, document_key, document_kind, document_title, title, source,\n\
                    source_session_id, created_at_ms, updated_at_ms\n\
             FROM conversations WHERE source = ?1 AND source_session_id = ?2\n\
               AND document_key = ?3 AND deleted_at_ms IS NULL\n\
             ORDER BY updated_at_ms DESC LIMIT 1",
            params![source, source_session_id, document_ref.key],
            row_summary,
        )
        .optional()
        .map_err(|error| error.to_string())?
    {
        return Ok(existing);
    }

    let id = new_id("conv");
    let path = conversation_path(workspace, &id);
    let relative = relative_path(workspace, &path)?;
    let created_at = messages
        .iter()
        .map(|(_, _, timestamp)| *timestamp)
        .filter(|timestamp| *timestamp > 0)
        .min()
        .unwrap_or_else(now_ms);
    let updated_at = messages
        .iter()
        .map(|(_, _, timestamp)| *timestamp)
        .max()
        .unwrap_or(created_at)
        .max(created_at);
    let summary = ConversationSummary {
        id: id.clone(),
        document_key: document_ref.key.clone(),
        document_kind: document_ref.kind.clone(),
        document_title: document_ref.title.clone(),
        title: if title.is_empty() {
            "导入的对话".to_string()
        } else {
            title
        },
        source: source.to_string(),
        source_session_id: Some(source_session_id.clone()),
        created_at_ms: created_at,
        updated_at_ms: updated_at,
    };

    let _guard = state
        .io_lock
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    append_event(
        &path,
        "session_meta",
        json!({
            "conversationId": id,
            "document": document_ref,
            "title": summary.title,
            "source": source,
            "sourceSessionId": source_session_id,
        }),
    )?;
    for (role, content, timestamp_ms) in messages {
        if role != "user" && role != "assistant" {
            continue;
        }
        let content = content.trim().chars().take(300_000).collect::<String>();
        if content.is_empty() {
            continue;
        }
        append_event(
            &path,
            "message",
            serde_json::to_value(ChatMessage {
                id: new_id("msg"),
                role,
                content,
                timestamp_ms: timestamp_ms.max(0),
                model: None,
            })
            .map_err(|error| error.to_string())?,
        )?;
    }
    db(workspace)?
        .execute(
            "INSERT INTO conversations(id, document_key, document_kind, document_title, title,\n\
              source, source_session_id, created_at_ms, updated_at_ms, event_path, deleted_at_ms)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, NULL)",
            params![
                summary.id,
                summary.document_key,
                summary.document_kind,
                summary.document_title,
                summary.title,
                summary.source,
                summary.source_session_id,
                summary.created_at_ms,
                summary.updated_at_ms,
                relative,
            ],
        )
        .map_err(|error| format!("无法登记导入的对话：{error}"))?;
    Ok(summary)
}

pub(crate) fn export_visible_conversation(
    workspace: &WorkspaceState,
    conversation_id: &str,
) -> Result<ConversationDetail, String> {
    let (summary, relative) = conversation_row(workspace, conversation_id)?;
    let messages = read_messages(&absolute_event_path(workspace, &relative)?)?;
    Ok(ConversationDetail { summary, messages })
}

#[tauri::command]
pub fn conversation_load(
    conversation_id: String,
    workspace: State<'_, WorkspaceState>,
) -> Result<ConversationDetail, String> {
    let (summary, relative) = conversation_row(&workspace, &conversation_id)?;
    let messages = read_messages(&absolute_event_path(&workspace, &relative)?)?;
    Ok(ConversationDetail { summary, messages })
}

#[tauri::command]
pub fn conversation_delete(
    conversation_id: String,
    workspace: State<'_, WorkspaceState>,
    state: State<'_, AiState>,
) -> Result<(), String> {
    let (_summary, relative) = conversation_row(&workspace, &conversation_id)?;
    let _guard = state
        .io_lock
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    append_event(
        &absolute_event_path(&workspace, &relative)?,
        "session_closed",
        json!({ "reason": "user_deleted" }),
    )?;
    db(&workspace)?
        .execute(
            "UPDATE conversations SET deleted_at_ms = ?2, updated_at_ms = ?2 WHERE id = ?1",
            params![conversation_id, now_ms()],
        )
        .map_err(|error| error.to_string())?;
    Ok(())
}

fn context_text_path(workspace: &WorkspaceState, hash: &str) -> PathBuf {
    workspace
        .root()
        .join("library")
        .join("metadata")
        .join("paper-contexts")
        .join(&hash[..2])
        .join(format!("{hash}.txt"))
}

fn save_context_text(path: &Path, text: &str) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "论文索引路径无效。".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("无法创建论文索引目录：{error}"))?;
    if path.exists() {
        return Ok(());
    }
    let temp = path.with_extension("txt.tmp");
    fs::write(&temp, text.as_bytes()).map_err(|error| format!("无法写入论文索引：{error}"))?;
    fs::rename(temp, path).map_err(|error| format!("无法提交论文索引：{error}"))
}

fn paper_context_row(
    workspace: &WorkspaceState,
    document_key: &str,
) -> Result<Option<(PaperContextStatus, String)>, String> {
    db(workspace)?
        .query_row(
            "SELECT document_key, status, char_count, page_count, summary, error, updated_at_ms, text_path\n\
             FROM paper_contexts WHERE document_key = ?1",
            [document_key],
            |row| {
                let summary: Option<String> = row.get(4)?;
                Ok((
                    PaperContextStatus {
                        document_key: row.get(0)?,
                        status: row.get(1)?,
                        char_count: row.get(2)?,
                        page_count: row.get(3)?,
                        has_summary: summary.as_deref().is_some_and(|value| !value.trim().is_empty()),
                        summary,
                        error: row.get(5)?,
                        updated_at_ms: row.get(6)?,
                    },
                    row.get(7)?,
                ))
            },
        )
        .optional()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn document_context_status(
    document_ref: DocumentRef,
    workspace: State<'_, WorkspaceState>,
) -> Result<Option<PaperContextStatus>, String> {
    clean_document(&document_ref)?;
    Ok(paper_context_row(&workspace, &document_ref.key)?.map(|value| value.0))
}

struct PrepareGuard<'a> {
    key: String,
    state: &'a AiState,
}

struct CancellationGuard<'a> {
    run_id: String,
    state: &'a AiState,
}

impl Drop for CancellationGuard<'_> {
    fn drop(&mut self) {
        self.state
            .cancellations
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .remove(&self.run_id);
    }
}

impl Drop for PrepareGuard<'_> {
    fn drop(&mut self) {
        self.state
            .preparing
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .remove(&self.key);
    }
}

#[tauri::command]
pub async fn document_prepare_context(
    request: PrepareDocumentRequest,
    workspace: State<'_, WorkspaceState>,
    state: State<'_, AiState>,
) -> Result<PaperContextStatus, String> {
    clean_document(&request.document_ref)?;
    {
        let mut preparing = state
            .preparing
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        if !preparing.insert(request.document_ref.key.clone()) {
            return Ok(paper_context_row(&workspace, &request.document_ref.key)?
                .map(|value| value.0)
                .unwrap_or(PaperContextStatus {
                    document_key: request.document_ref.key,
                    status: "preparing".to_string(),
                    char_count: 0,
                    page_count: request.context.page_count,
                    has_summary: false,
                    summary: None,
                    error: None,
                    updated_at_ms: now_ms(),
                }));
        }
    }
    let _preparing = PrepareGuard {
        key: request.document_ref.key.clone(),
        state: &state,
    };

    let mut full_text = request.context.full_text.trim().to_string();
    if full_text.chars().count() > MAX_PAPER_CHARS {
        full_text = full_text.chars().take(MAX_PAPER_CHARS).collect();
    }
    let composite = format!(
        "标题：{}\n作者：{}\n摘要：{}\n\n{}",
        request.context.title.trim(),
        request.context.authors.trim(),
        request.context.abstract_text.trim(),
        full_text
    );
    if composite.trim().chars().count() < 80 {
        return Err("当前 PDF 没有足够的可提取文字，暂时无法建立论文上下文。".to_string());
    }
    let hash = content_hash(&composite);
    if let Some((existing, _)) = paper_context_row(&workspace, &request.document_ref.key)? {
        let same_hash: Option<String> = db(&workspace)?
            .query_row(
                "SELECT content_hash FROM paper_contexts WHERE document_key = ?1",
                [&request.document_ref.key],
                |row| row.get(0),
            )
            .optional()
            .map_err(|error| error.to_string())?;
        if same_hash.as_deref() == Some(hash.as_str()) && existing.has_summary {
            return Ok(existing);
        }
    }
    let text_path = context_text_path(&workspace, &hash);
    save_context_text(&text_path, &composite)?;
    let relative = relative_path(&workspace, &text_path)?;
    let timestamp = now_ms();
    db(&workspace)?
        .execute(
            "INSERT INTO paper_contexts(document_key, document_kind, document_title, content_hash,\n\
              text_path, summary, page_count, char_count, status, error, created_at_ms, updated_at_ms)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, NULL, ?6, ?7, 'indexed', NULL, ?8, ?8)\n\
             ON CONFLICT(document_key) DO UPDATE SET document_kind = excluded.document_kind,\n\
              document_title = excluded.document_title, content_hash = excluded.content_hash,\n\
              text_path = excluded.text_path, page_count = excluded.page_count,\n\
              char_count = excluded.char_count, status = 'indexed', error = NULL,\n\
              updated_at_ms = excluded.updated_at_ms",
            params![
                request.document_ref.key,
                request.document_ref.kind,
                request.document_ref.title,
                hash,
                relative,
                request.context.page_count,
                composite.chars().count() as u64,
                timestamp
            ],
        )
        .map_err(|error| format!("无法登记论文上下文：{error}"))?;

    let Some(config) = load_provider(&workspace)? else {
        return Ok(paper_context_row(&workspace, &request.document_ref.key)?
            .expect("context inserted")
            .0);
    };
    let Some(secret) = load_api_key(&workspace)? else {
        return Ok(paper_context_row(&workspace, &request.document_ref.key)?
            .expect("context inserted")
            .0);
    };
    db(&workspace)?
        .execute(
            "UPDATE paper_contexts SET status = 'understanding', error = NULL, updated_at_ms = ?2\n\
             WHERE document_key = ?1",
            params![request.document_ref.key, now_ms()],
        )
        .map_err(|error| error.to_string())?;

    let prompt = format!(
        "请先完整理解下面这篇论文，并建立一份以后问答可复用的中文研究档案。必须忠于原文，输出 Markdown，包含：\n\
         1. 一句话结论；2. 研究问题；3. 核心 trick（最关键、最独特的机制）；\n\
         4. 方法流程；5. 实验与证据；6. 局限；7. 关键术语与符号；8. 可继续追问的问题。\n\
         论文文字层可能有排版噪声，不要据此编造。\n\n{}",
        composite.chars().take(MAX_REQUEST_CONTEXT_CHARS).collect::<String>()
    );
    let messages = vec![
        json!({"role":"system","content":"你是 Pharos 的论文阅读助手。先建立可靠的论文理解档案，后续用于精确问答。"}),
        json!({"role":"user","content":prompt}),
    ];
    match completion(&config, &secret, messages).await {
        Ok(summary) => {
            db(&workspace)?
                .execute(
                    "UPDATE paper_contexts SET summary = ?2, status = 'ready', error = NULL,\n\
                     updated_at_ms = ?3 WHERE document_key = ?1",
                    params![request.document_ref.key, summary, now_ms()],
                )
                .map_err(|error| error.to_string())?;
        }
        Err(error) => {
            db(&workspace)?
                .execute(
                    "UPDATE paper_contexts SET status = 'indexed', error = ?2, updated_at_ms = ?3\n\
                     WHERE document_key = ?1",
                    params![request.document_ref.key, error, now_ms()],
                )
                .map_err(|db_error| db_error.to_string())?;
        }
    }
    Ok(paper_context_row(&workspace, &request.document_ref.key)?
        .expect("context inserted")
        .0)
}

async fn completion(
    config: &ProviderConfig,
    secret: &str,
    messages: Vec<Value>,
) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(150))
        .build()
        .map_err(|error| error.to_string())?;
    let response = client
        .post(chat_endpoint(&config.base_url))
        .bearer_auth(secret)
        .json(&json!({
            "model": config.model,
            "messages": messages,
            "stream": false,
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens.unwrap_or(4096)
        }))
        .send()
        .await
        .map_err(|error| friendly_network_error(&error))?;
    let status = response.status();
    let value: Value = response
        .json()
        .await
        .map_err(|_| format!("模型接口返回了无法解析的响应（HTTP {status}）。"))?;
    if !status.is_success() {
        return Err(provider_error(status, &value));
    }
    extract_message_content(&value).ok_or_else(|| "模型没有返回文字内容。".to_string())
}

fn provider_error(status: StatusCode, value: &Value) -> String {
    let detail = value
        .pointer("/error/message")
        .or_else(|| value.get("message"))
        .or_else(|| value.get("detail"))
        .and_then(Value::as_str)
        .unwrap_or("模型服务拒绝了请求");
    let detail: String = detail.chars().take(500).collect();
    format!("模型接口 HTTP {}：{}", status.as_u16(), detail)
}

fn friendly_network_error(error: &reqwest::Error) -> String {
    if error.is_timeout() {
        "模型响应超时，请检查中转站或稍后重试。".to_string()
    } else if error.is_connect() {
        "无法连接模型接口，请检查 Base URL 和网络。".to_string()
    } else {
        format!("模型请求失败：{error}")
    }
}

fn extract_message_content(value: &Value) -> Option<String> {
    let content = value.pointer("/choices/0/message/content")?;
    if let Some(text) = content.as_str() {
        return Some(text.to_string());
    }
    let blocks = content.as_array()?;
    let mut output = String::new();
    for block in blocks {
        if let Some(text) = block.get("text").and_then(Value::as_str) {
            output.push_str(text);
        }
    }
    (!output.is_empty()).then_some(output)
}

fn extract_delta(value: &Value) -> Result<Option<String>, String> {
    if value.get("error").is_some() {
        return Err(provider_error(StatusCode::BAD_GATEWAY, value));
    }
    let Some(content) = value.pointer("/choices/0/delta/content") else {
        return Ok(None);
    };
    if let Some(text) = content.as_str() {
        return Ok((!text.is_empty()).then(|| text.to_string()));
    }
    let Some(blocks) = content.as_array() else {
        return Ok(None);
    };
    let mut output = String::new();
    for block in blocks {
        if let Some(text) = block.get("text").and_then(Value::as_str) {
            output.push_str(text);
        }
    }
    Ok((!output.is_empty()).then_some(output))
}

fn history_for_request(messages: &[ChatMessage]) -> Vec<Value> {
    let mut selected = Vec::new();
    let mut chars = 0usize;
    for message in messages.iter().rev() {
        let count = message.content.chars().count();
        if !selected.is_empty() && chars + count > MAX_HISTORY_CHARS {
            break;
        }
        chars += count;
        selected.push(json!({"role": message.role, "content": message.content}));
    }
    selected.reverse();
    selected
}

fn query_terms(query: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut ascii = String::new();
    let mut cjk = Vec::new();
    for character in query.to_lowercase().chars() {
        if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
            ascii.push(character);
            if cjk.len() >= 2 {
                for width in 2..=3.min(cjk.len()) {
                    out.extend(cjk.windows(width).map(|window| window.iter().collect()));
                }
            }
            cjk.clear();
        } else if ('\u{3400}'..='\u{9fff}').contains(&character) {
            if ascii.len() >= 3 {
                out.push(std::mem::take(&mut ascii));
            } else {
                ascii.clear();
            }
            cjk.push(character);
        } else if ascii.len() >= 3 {
            out.push(std::mem::take(&mut ascii));
        } else {
            ascii.clear();
            if cjk.len() >= 2 {
                for width in 2..=3.min(cjk.len()) {
                    out.extend(cjk.windows(width).map(|window| window.iter().collect()));
                }
            }
            cjk.clear();
        }
    }
    if ascii.len() >= 3 {
        out.push(ascii);
    }
    if cjk.len() >= 2 {
        for width in 2..=3.min(cjk.len()) {
            out.extend(cjk.windows(width).map(|window| window.iter().collect()));
        }
    }
    out.sort();
    out.dedup();
    out
}

fn relevant_context(text: &str, query: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let chars: Vec<char> = text.chars().collect();
    let edge_size = (limit / 7).clamp(600, 2_000);
    let intro: String = chars.iter().take(edge_size).collect();
    let conclusion_start = chars.len().saturating_sub(edge_size);
    let conclusion: String = chars[conclusion_start..].iter().collect();
    let intro_header = "[论文开头]\n";
    let conclusion_header = "\n\n[论文结尾]\n";
    let fixed_chars = intro_header.chars().count()
        + intro.chars().count()
        + conclusion_header.chars().count()
        + conclusion.chars().count();
    let middle_budget = limit.saturating_sub(fixed_chars);
    let chunk_size = 4_000usize;
    let terms = query_terms(query);
    let mut chunks: Vec<(usize, usize)> = chars
        .chunks(chunk_size)
        .enumerate()
        .filter(|(index, _)| {
            let start = index * chunk_size;
            start >= edge_size && start < conclusion_start
        })
        .map(|(index, chunk)| {
            let lower: String = chunk.iter().collect::<String>().to_lowercase();
            let score = terms
                .iter()
                .map(|term| lower.matches(term).count())
                .sum::<usize>();
            (score, index)
        })
        .collect();
    chunks.sort_by(|left, right| right.0.cmp(&left.0).then_with(|| left.1.cmp(&right.1)));
    let wanted = (middle_budget / chunk_size).saturating_add(1);
    let mut indexes: Vec<usize> = chunks
        .into_iter()
        .take(wanted)
        .map(|(_, index)| index)
        .collect();
    indexes.sort_unstable();
    indexes.dedup();
    let mut middle = String::new();
    for index in indexes {
        let used = middle.chars().count();
        if used >= middle_budget {
            break;
        }
        let start = index * chunk_size;
        if start >= chars.len() {
            continue;
        }
        let end = (start + chunk_size).min(chars.len());
        let header = format!("\n\n[论文相关摘录 {}]\n", index + 1);
        let remaining = middle_budget.saturating_sub(used + header.chars().count());
        if remaining == 0 {
            break;
        }
        middle.push_str(&header);
        middle.extend(chars[start..end].iter().take(remaining));
    }
    format!("{intro_header}{intro}{middle}{conclusion_header}{conclusion}")
}

fn stored_context(
    workspace: &WorkspaceState,
    document_key: &str,
    query: &str,
) -> Result<(Option<String>, Option<String>), String> {
    let Some((status, relative)) = paper_context_row(workspace, document_key)? else {
        return Ok((None, None));
    };
    let path = absolute_event_path(workspace, &relative)?;
    let mut text = String::new();
    File::open(path)
        .and_then(|mut file| file.read_to_string(&mut text))
        .map_err(|error| format!("无法读取论文上下文：{error}"))?;
    Ok((
        status.summary,
        Some(relevant_context(&text, query, MAX_REQUEST_CONTEXT_CHARS)),
    ))
}

fn paper_system_prompt(
    reference: &DocumentRef,
    summary: Option<&str>,
    excerpts: Option<&str>,
) -> String {
    format!(
        "你是 Pharos 的 AI 对话助手，正在和用户精读论文《{}》。\n\
         必须以论文内容为依据，用中文直接回答；论文标题、方法名和术语可保留英文。\n\
         若依据不足要明确说明，不得编造。尽量指出对应页码或摘录标记。\n\
         使用清晰 Markdown；数学公式使用 $...$ 或 $$...$$。\n\n\
         [预先建立的论文理解档案]\n{}\n\n[本轮相关论文正文]\n{}",
        reference.title,
        summary.unwrap_or("尚未生成；请以正文为准。"),
        excerpts.unwrap_or("当前没有可提取的论文正文，只能依据书目信息回答。")
    )
}

fn emit(channel: &Channel<ChatEvent>, event: ChatEvent) {
    let _ = channel.send(event);
}

#[tauri::command]
pub fn conversation_cancel(run_id: String, state: State<'_, AiState>) -> bool {
    state
        .cancellations
        .lock()
        .unwrap_or_else(|poison| poison.into_inner())
        .get(&run_id)
        .map(|flag| {
            flag.store(true, Ordering::Release);
            true
        })
        .unwrap_or(false)
}

#[tauri::command]
pub async fn conversation_send_stream(
    request: ChatSendRequest,
    on_event: Channel<ChatEvent>,
    workspace: State<'_, WorkspaceState>,
    state: State<'_, AiState>,
) -> Result<String, String> {
    clean_document(&request.document_ref)?;
    let question = clean_message(&request.message)?;
    let config = load_provider(&workspace)?
        .ok_or_else(|| "请先在设置中配置 OpenAI 兼容模型。".to_string())?;
    validate_provider(&config)?;
    let secret = load_api_key(&workspace)?
        .ok_or_else(|| "模型 API Key 不在系统凭据库中，请重新保存。".to_string())?;
    let (conversation, relative) = conversation_row(&workspace, &request.conversation_id)?;
    if conversation.document_key != request.document_ref.key {
        return Err("这段对话不属于当前论文。".to_string());
    }
    let event_path = absolute_event_path(&workspace, &relative)?;
    let cancel = Arc::new(AtomicBool::new(false));
    state
        .cancellations
        .lock()
        .unwrap_or_else(|poison| poison.into_inner())
        .insert(request.run_id.clone(), cancel.clone());
    let _cancellation = CancellationGuard {
        run_id: request.run_id.clone(),
        state: &state,
    };
    emit(
        &on_event,
        ChatEvent::Started {
            run_id: request.run_id.clone(),
        },
    );

    let user_message = ChatMessage {
        id: new_id("msg"),
        role: "user".to_string(),
        content: question.clone(),
        timestamp_ms: now_ms(),
        model: None,
    };
    {
        let _guard = state
            .io_lock
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        append_event(
            &event_path,
            "message",
            serde_json::to_value(&user_message).map_err(|error| error.to_string())?,
        )?;
        let generated_title = question.chars().take(42).collect::<String>();
        db(&workspace)?
            .execute(
                "UPDATE conversations SET updated_at_ms = ?2,\n\
                   title = CASE WHEN title = '论文对话' THEN ?3 ELSE title END WHERE id = ?1",
                params![
                    request.conversation_id,
                    user_message.timestamp_ms,
                    generated_title
                ],
            )
            .map_err(|error| error.to_string())?;
    }

    if paper_context_row(&workspace, &request.document_ref.key)?.is_none() {
        if let Some(context) = request.current_context {
            let _ = document_prepare_context(
                PrepareDocumentRequest {
                    document_ref: request.document_ref.clone(),
                    context,
                },
                workspace.clone(),
                state.clone(),
            )
            .await;
        }
    }
    let prior = read_messages(&event_path)?;
    let (summary, excerpts) = stored_context(&workspace, &request.document_ref.key, &question)?;
    let mut messages = vec![json!({
        "role": "system",
        "content": paper_system_prompt(
            &request.document_ref,
            summary.as_deref(),
            excerpts.as_deref()
        )
    })];
    messages.extend(history_for_request(&prior));

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(180))
        .build()
        .map_err(|error| error.to_string())?;
    let response = client
        .post(chat_endpoint(&config.base_url))
        .bearer_auth(secret)
        .json(&json!({
            "model": config.model,
            "messages": messages,
            "stream": true,
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens.unwrap_or(4096)
        }))
        .send()
        .await
        .map_err(|error| friendly_network_error(&error))?;
    let status = response.status();
    if !status.is_success() {
        let value: Value = response.json().await.unwrap_or_else(|_| json!({}));
        let error = provider_error(status, &value);
        emit(
            &on_event,
            ChatEvent::Error {
                message: error.clone(),
            },
        );
        return Err(error);
    }

    let mut stream = response.bytes_stream();
    let mut pending = String::new();
    let mut answer = String::new();
    let mut stream_error: Option<String> = None;
    while let Some(chunk) = stream.next().await {
        if cancel.load(Ordering::Acquire) {
            break;
        }
        let bytes = match chunk {
            Ok(value) => value,
            Err(error) => {
                stream_error = Some(friendly_network_error(&error));
                break;
            }
        };
        pending.push_str(&String::from_utf8_lossy(&bytes));
        while let Some(position) = pending.find('\n') {
            let mut line = pending.drain(..=position).collect::<String>();
            line = line.trim().to_string();
            if !line.starts_with("data:") {
                continue;
            }
            let data = line.trim_start_matches("data:").trim();
            if data.is_empty() || data == "[DONE]" {
                continue;
            }
            let value: Value = match serde_json::from_str(data) {
                Ok(value) => value,
                Err(_) => continue,
            };
            match extract_delta(&value) {
                Ok(Some(delta)) => {
                    answer.push_str(&delta);
                    emit(&on_event, ChatEvent::Delta { text: delta });
                }
                Ok(None) => {}
                Err(error) => {
                    stream_error = Some(error);
                    break;
                }
            }
        }
        if stream_error.is_some() {
            break;
        }
    }
    if let Some(error) = stream_error {
        emit(
            &on_event,
            ChatEvent::Error {
                message: error.clone(),
            },
        );
        return Err(error);
    }
    if cancel.load(Ordering::Acquire) {
        let error = "已停止生成。".to_string();
        emit(
            &on_event,
            ChatEvent::Error {
                message: error.clone(),
            },
        );
        return Err(error);
    }
    if answer.trim().is_empty() {
        let error = "模型没有返回文字内容。".to_string();
        emit(
            &on_event,
            ChatEvent::Error {
                message: error.clone(),
            },
        );
        return Err(error);
    }
    let assistant_message = ChatMessage {
        id: new_id("msg"),
        role: "assistant".to_string(),
        content: answer,
        timestamp_ms: now_ms(),
        model: Some(config.model),
    };
    {
        let _guard = state
            .io_lock
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        append_event(
            &event_path,
            "message",
            serde_json::to_value(&assistant_message).map_err(|error| error.to_string())?,
        )?;
        db(&workspace)?
            .execute(
                "UPDATE conversations SET updated_at_ms = ?2 WHERE id = ?1",
                params![request.conversation_id, assistant_message.timestamp_ms],
            )
            .map_err(|error| error.to_string())?;
    }
    emit(
        &on_event,
        ChatEvent::Done {
            message: assistant_message,
        },
    );
    Ok(request.run_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_openai_compatible_endpoints() {
        assert_eq!(
            chat_endpoint("https://example.test"),
            "https://example.test/v1/chat/completions"
        );
        assert_eq!(
            chat_endpoint("https://example.test/v1/"),
            "https://example.test/v1/chat/completions"
        );
        assert_eq!(
            chat_endpoint("https://example.test/v4/chat/completions"),
            "https://example.test/v4/chat/completions"
        );
    }

    #[test]
    fn only_allows_plain_http_for_real_loopback_hosts() {
        let config = |base_url: &str| ProviderConfig {
            base_url: base_url.to_string(),
            model: "model".to_string(),
            temperature: 0.2,
            max_output_tokens: Some(4096),
        };
        assert!(validate_provider(&config("http://127.0.0.1:8317/v1")).is_ok());
        assert!(validate_provider(&config("http://localhost:8317/v1")).is_ok());
        assert!(validate_provider(&config("http://localhost.evil.test/v1")).is_err());
        assert!(validate_provider(&config("https://relay.example/v1")).is_ok());
    }

    #[test]
    fn long_context_keeps_relevant_chunks_and_edges() {
        let text = format!(
            "introduction {} UNIQUE_METHOD_TOKEN {} conclusion",
            "a".repeat(20_000),
            "b".repeat(20_000)
        );
        let selected = relevant_context(&text, "Explain UNIQUE_METHOD_TOKEN", 12_000);
        assert!(selected.contains("introduction"));
        assert!(selected.contains("UNIQUE_METHOD_TOKEN"));
        assert!(selected.contains("conclusion"));
        assert!(selected.chars().count() <= 12_000);
    }

    #[test]
    fn event_log_ignores_unknown_records() {
        let root = std::env::temp_dir().join(new_id("pharos-ai-test"));
        let path = root.join("events.jsonl");
        append_event(&path, "unknown", json!({"secret": "not-a-message"})).unwrap();
        append_event(
            &path,
            "message",
            serde_json::to_value(ChatMessage {
                id: "m1".to_string(),
                role: "user".to_string(),
                content: "hello".to_string(),
                timestamp_ms: 1,
                model: None,
            })
            .unwrap(),
        )
        .unwrap();
        let messages = read_messages(&path).unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].content, "hello");
        let _ = fs::remove_dir_all(root);
    }
}
