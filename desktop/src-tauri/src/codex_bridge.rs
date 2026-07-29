//! Safe interoperability with local Codex terminal/desktop conversation logs.
//!
//! Pharos only imports dialogue that was visible to the user. It never reads
//! Codex authentication/configuration databases and never mutates Codex JSONL.

use std::{
    env,
    ffi::OsStr,
    fs::{self, File},
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex, OnceLock},
    time::UNIX_EPOCH,
};

use regex::Regex;
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::State;

use crate::{
    ai::{
        export_visible_conversation, import_external_conversation, AiState, ConversationSummary,
        DocumentRef,
    },
    workspace::WorkspaceState,
};

const MAX_SESSION_BYTES: u64 = 512 * 1024 * 1024;
const MAX_LINE_BYTES: usize = 8 * 1024 * 1024;
const MAX_PREVIEW_BYTES: u64 = 2 * 1024 * 1024;
const MAX_IMPORTED_MESSAGE_CHARS: usize = 300_000;
const MAX_IMPORTED_TOTAL_CHARS: usize = 2_000_000;
const MAX_HANDOFF_CHARS: usize = 120_000;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CodexCapabilities {
    available: bool,
    version: Option<String>,
    codex_home: Option<String>,
    readable_roots: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CodexSessionSummary {
    path: String,
    session_id: Option<String>,
    title: String,
    cwd: Option<String>,
    updated_at_ms: i64,
    message_count: usize,
    truncated: bool,
    archived: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CodexHandoffResult {
    thread_id: String,
    cwd: String,
}

#[derive(Default)]
struct ParsedSession {
    session_id: Option<String>,
    cwd: Option<String>,
    title: String,
    messages: Vec<(String, String, i64)>,
}

fn codex_home() -> Option<PathBuf> {
    env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".codex")))
        .or_else(|| env::var_os("USERPROFILE").map(|home| PathBuf::from(home).join(".codex")))
}

fn codex_binary() -> Option<PathBuf> {
    if let Some(path) = env::var_os("CODEX_BINARY").map(PathBuf::from) {
        if path.is_file() {
            return Some(path);
        }
    }
    let names: &[&str] = if cfg!(windows) {
        &["codex.exe", "codex.cmd", "codex"]
    } else {
        &["codex"]
    };
    if let Some(path_value) = env::var_os("PATH") {
        for directory in env::split_paths(&path_value) {
            for name in names {
                let candidate = directory.join(name);
                if candidate.is_file() {
                    return Some(candidate);
                }
            }
        }
    }
    let mut candidates = vec![
        PathBuf::from("/Applications/Codex.app/Contents/Resources/codex"),
        PathBuf::from("/Applications/ChatGPT.app/Contents/Resources/codex"),
        PathBuf::from("/opt/homebrew/bin/codex"),
        PathBuf::from("/usr/local/bin/codex"),
    ];
    if let Some(home) = env::var_os("HOME").map(PathBuf::from) {
        candidates.extend([
            home.join(".local/bin/codex"),
            home.join(".npm-global/bin/codex"),
        ]);
    }
    candidates.into_iter().find(|path| path.is_file())
}

fn readable_roots() -> Vec<(PathBuf, bool)> {
    let Some(home) = codex_home() else {
        return Vec::new();
    };
    [
        (home.join("sessions"), false),
        (home.join("archived_sessions"), true),
    ]
    .into_iter()
    .filter_map(|(path, archived)| {
        let metadata = fs::symlink_metadata(&path).ok()?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return None;
        }
        fs::canonicalize(path).ok().map(|path| (path, archived))
    })
    .collect()
}

fn modified_ms(metadata: &fs::Metadata) -> i64 {
    metadata
        .modified()
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_millis().min(i64::MAX as u128) as i64)
        .unwrap_or(0)
}

fn collect_jsonl(root: &Path, archived: bool, out: &mut Vec<(PathBuf, bool, i64)>, depth: u8) {
    if depth > 8 {
        return;
    }
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = fs::symlink_metadata(&path) else {
            continue;
        };
        if metadata.file_type().is_symlink() {
            continue;
        }
        if metadata.is_dir() {
            collect_jsonl(&path, archived, out, depth + 1);
        } else if metadata.is_file()
            && metadata.len() <= MAX_SESSION_BYTES
            && path.extension() == Some(OsStr::new("jsonl"))
        {
            out.push((path, archived, modified_ms(&metadata)));
        }
    }
}

fn safe_session_path(raw: &str) -> Result<(PathBuf, bool, i64), String> {
    let path = PathBuf::from(raw);
    let metadata =
        fs::symlink_metadata(&path).map_err(|_| "Codex 对话文件不存在或不可读取。".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("Codex 对话路径不是普通文件。".to_string());
    }
    if metadata.len() > MAX_SESSION_BYTES {
        return Err("Codex 对话文件超过 512 MiB 安全上限。".to_string());
    }
    let canonical =
        fs::canonicalize(&path).map_err(|_| "无法确认 Codex 对话文件的真实路径。".to_string())?;
    for (root, archived) in readable_roots() {
        if canonical.starts_with(&root) {
            return Ok((canonical, archived, modified_ms(&metadata)));
        }
    }
    Err("只能导入 CODEX_HOME/sessions 或 archived_sessions 中的对话。".to_string())
}

fn secret_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| {
        Regex::new(
            r"(?i)(sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[^\s,;]{8,})",
        )
        .expect("static secret redaction regex")
    })
}

fn remove_tagged_block(mut text: String, tag: &str) -> String {
    let open = format!("<{tag}");
    let close = format!("</{tag}>");
    loop {
        let Some(start) = text.find(&open) else {
            break;
        };
        let Some(relative_end) = text[start..].find(&close) else {
            text.truncate(start);
            break;
        };
        let end = start + relative_end + close.len();
        text.replace_range(start..end, "");
    }
    text
}

fn sanitise_visible_text(value: &str, role: &str) -> Option<String> {
    let mut text = value.replace('\0', "");
    for tag in [
        "environment_context",
        "in-app-browser-context",
        "app-context",
        "permissions",
        "collaboration_mode",
        "plugins_instructions",
        "skills_instructions",
    ] {
        text = remove_tagged_block(text, tag);
    }
    if role == "user" {
        if let Some((_, request)) = text.rsplit_once("## My request for Codex:") {
            text = request.to_string();
        }
    }
    let trimmed = text.trim();
    if role == "user"
        && (trimmed.starts_with("# AGENTS.md instructions")
            || trimmed.starts_with("<developer")
            || trimmed.starts_with("<system"))
    {
        return None;
    }
    let redacted = secret_regex().replace_all(trimmed, "[已隐藏敏感凭据]");
    let cleaned = redacted
        .chars()
        .take(MAX_IMPORTED_MESSAGE_CHARS)
        .collect::<String>();
    (!cleaned.trim().is_empty()).then_some(cleaned)
}

fn content_text(payload: &Value, role: &str) -> Option<String> {
    let blocks = payload.get("content")?.as_array()?;
    let mut output = String::new();
    for block in blocks {
        let kind = block
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !matches!(kind, "input_text" | "output_text" | "text") {
            continue;
        }
        if let Some(text) = block.get("text").and_then(Value::as_str) {
            if let Some(text) = sanitise_visible_text(text, role) {
                if !output.is_empty() {
                    output.push_str("\n\n");
                }
                output.push_str(&text);
            }
        }
    }
    (!output.is_empty()).then_some(output)
}

fn first_line_title(text: &str) -> String {
    let candidate = text
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("Codex 对话");
    let title = candidate.chars().take(64).collect::<String>();
    if candidate.chars().count() > 64 {
        format!("{title}…")
    } else {
        title
    }
}

fn parse_session_limited(
    path: &Path,
    fallback_timestamp: i64,
    max_bytes: Option<u64>,
) -> Result<(ParsedSession, bool), String> {
    let file = File::open(path).map_err(|error| format!("无法读取 Codex 对话：{error}"))?;
    let mut reader = BufReader::new(file);
    let mut event_messages = Vec::new();
    let mut response_messages = Vec::new();
    let mut session_id = None;
    let mut cwd = None;
    let mut line = Vec::new();
    let mut sequence = 0i64;
    let mut event_chars = 0usize;
    let mut response_chars = 0usize;
    let mut consumed_bytes = 0u64;
    let mut truncated = false;

    loop {
        line.clear();
        let read = reader
            .read_until(b'\n', &mut line)
            .map_err(|error| format!("无法读取 Codex JSONL：{error}"))?;
        if read == 0 {
            break;
        }
        consumed_bytes = consumed_bytes.saturating_add(read as u64);
        if max_bytes.is_some_and(|limit| consumed_bytes > limit) {
            truncated = true;
            break;
        }
        if line.len() > MAX_LINE_BYTES {
            continue;
        }
        let Ok(value) = serde_json::from_slice::<Value>(&line) else {
            continue;
        };
        let kind = value
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let payload = value.get("payload").unwrap_or(&Value::Null);
        if kind == "session_meta" {
            session_id = payload
                .get("id")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or(session_id);
            cwd = payload
                .get("cwd")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or(cwd);
            continue;
        }

        let timestamp = fallback_timestamp.saturating_add(sequence);
        sequence = sequence.saturating_add(1);
        if kind == "event_msg" {
            let event_kind = payload
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let role = match event_kind {
                "user_message" => "user",
                "agent_message" => "assistant",
                _ => continue,
            };
            let Some(raw) = payload.get("message").and_then(Value::as_str) else {
                continue;
            };
            let Some(text) = sanitise_visible_text(raw, role) else {
                continue;
            };
            if event_chars + text.chars().count() > MAX_IMPORTED_TOTAL_CHARS {
                continue;
            }
            event_chars += text.chars().count();
            event_messages.push((role.to_string(), text, timestamp));
        } else if kind == "response_item"
            && payload.get("type").and_then(Value::as_str) == Some("message")
        {
            let role = match payload.get("role").and_then(Value::as_str) {
                Some("user") => "user",
                Some("assistant") => "assistant",
                _ => continue,
            };
            let Some(text) = content_text(payload, role) else {
                continue;
            };
            if response_chars + text.chars().count() > MAX_IMPORTED_TOTAL_CHARS {
                continue;
            }
            response_chars += text.chars().count();
            response_messages.push((role.to_string(), text, timestamp));
        }
    }

    let messages = if event_messages.iter().any(|(role, _, _)| role == "user") {
        event_messages
    } else {
        response_messages
    };
    let title = messages
        .iter()
        .find(|(role, _, _)| role == "user")
        .map(|(_, text, _)| first_line_title(text))
        .unwrap_or_else(|| "Codex 对话".to_string());
    Ok((
        ParsedSession {
            session_id,
            cwd,
            title,
            messages,
        },
        truncated,
    ))
}

fn parse_session(path: &Path, fallback_timestamp: i64) -> Result<ParsedSession, String> {
    parse_session_limited(path, fallback_timestamp, None).map(|(parsed, _)| parsed)
}

fn fallback_session_id(path: &Path) -> String {
    let digest = hex::encode(Sha256::digest(path.to_string_lossy().as_bytes()));
    format!("file-{}", &digest[..32])
}

#[tauri::command]
pub fn codex_capabilities() -> CodexCapabilities {
    let output =
        codex_binary().and_then(|binary| Command::new(binary).arg("--version").output().ok());
    let available = output.as_ref().is_some_and(|value| value.status.success());
    let version = output.and_then(|value| {
        let text = String::from_utf8_lossy(&value.stdout).trim().to_string();
        (!text.is_empty()).then_some(text)
    });
    CodexCapabilities {
        available,
        version,
        codex_home: codex_home().map(|path| path.to_string_lossy().into_owned()),
        readable_roots: readable_roots()
            .into_iter()
            .map(|(path, _)| path.to_string_lossy().into_owned())
            .collect(),
    }
}

#[tauri::command]
pub fn codex_discover_sessions(limit: Option<usize>) -> Result<Vec<CodexSessionSummary>, String> {
    let limit = limit.unwrap_or(40).clamp(1, 500);
    let mut files = Vec::new();
    for (root, archived) in readable_roots() {
        collect_jsonl(&root, archived, &mut files, 0);
    }
    files.sort_by(|left, right| right.2.cmp(&left.2));
    files.truncate(limit);

    Ok(files
        .into_iter()
        .filter_map(|(path, archived, updated_at_ms)| {
            let (parsed, truncated) =
                parse_session_limited(&path, updated_at_ms, Some(MAX_PREVIEW_BYTES)).ok()?;
            Some(CodexSessionSummary {
                path: path.to_string_lossy().into_owned(),
                session_id: parsed.session_id,
                title: parsed.title,
                cwd: parsed.cwd,
                updated_at_ms,
                message_count: parsed.messages.len(),
                truncated,
                archived,
            })
        })
        .collect())
}

#[tauri::command]
pub fn codex_import_session(
    path: String,
    document_ref: Option<DocumentRef>,
    workspace: State<'_, WorkspaceState>,
    ai_state: State<'_, AiState>,
) -> Result<ConversationSummary, String> {
    let (path, _archived, updated_at_ms) = safe_session_path(&path)?;
    let parsed = parse_session(&path, updated_at_ms)?;
    let session_id = parsed
        .session_id
        .unwrap_or_else(|| fallback_session_id(&path));
    import_external_conversation(
        &workspace,
        &ai_state,
        document_ref,
        parsed.title,
        "codex",
        session_id,
        parsed.messages,
    )
}

fn handoff_prompt(title: &str, messages: &[(String, String)]) -> String {
    let mut transcript = String::new();
    for (role, content) in messages {
        let label = if role == "user" {
            "用户"
        } else {
            "Pharos AI"
        };
        let content = secret_regex().replace_all(content, "[已隐藏敏感凭据]");
        let section = format!("\n\n## {label}\n{content}");
        let remaining = MAX_HANDOFF_CHARS.saturating_sub(transcript.chars().count());
        if remaining == 0 {
            break;
        }
        transcript.extend(section.chars().take(remaining));
    }
    format!(
        "请接管我在 Pharos 中围绕论文《{title}》进行的研究对话。下面只包含可见的用户/助手消息，\
         不包含隐藏推理、工具输出或认证信息。请先概括当前共识、未解决问题和建议的下一步，然后继续协作。\n{transcript}"
    )
}

fn create_codex_task(prompt: String, cwd: PathBuf) -> Result<CodexHandoffResult, String> {
    let binary = codex_binary().ok_or_else(|| "没有找到 Codex CLI。".to_string())?;
    let mut child = Command::new(binary)
        .args(["exec", "--json", "-C"])
        .arg(&cwd)
        .arg("-")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("无法启动 Codex CLI：{error}"))?;

    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(prompt.as_bytes())
            .map_err(|error| format!("无法把对话交给 Codex：{error}"))?;
    }
    let stderr_text = Arc::new(Mutex::new(String::new()));
    let stderr_handle = child.stderr.take().map(|stderr| {
        let output = Arc::clone(&stderr_text);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stderr).take(16 * 1024);
            let mut text = String::new();
            let _ = reader.read_to_string(&mut text);
            *output.lock().unwrap_or_else(|poison| poison.into_inner()) = text;
        })
    });
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Codex CLI 没有提供 JSON 输出。".to_string())?;
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    loop {
        line.clear();
        let read = reader
            .read_line(&mut line)
            .map_err(|error| format!("无法读取 Codex CLI 输出：{error}"))?;
        if read == 0 {
            let status = child
                .wait()
                .map_err(|error| format!("无法等待 Codex CLI：{error}"))?;
            if let Some(handle) = stderr_handle {
                let _ = handle.join();
            }
            let detail = stderr_text
                .lock()
                .unwrap_or_else(|poison| poison.into_inner())
                .trim()
                .chars()
                .take(600)
                .collect::<String>();
            return Err(if detail.is_empty() {
                format!("Codex CLI 未创建任务（退出状态 {status}）。")
            } else {
                format!("Codex CLI 未创建任务：{detail}")
            });
        }
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let event_type = value.get("type").and_then(Value::as_str);
        if event_type != Some("thread.started") {
            continue;
        }
        let Some(thread_id) = value
            .get("thread_id")
            .or_else(|| value.get("threadId"))
            .and_then(Value::as_str)
            .map(str::to_string)
        else {
            continue;
        };
        std::thread::spawn(move || {
            let mut remaining = String::new();
            let _ = reader.read_to_string(&mut remaining);
            let _ = child.wait();
            if let Some(handle) = stderr_handle {
                let _ = handle.join();
            }
        });
        return Ok(CodexHandoffResult {
            thread_id,
            cwd: cwd.to_string_lossy().into_owned(),
        });
    }
}

#[tauri::command]
pub async fn codex_create_handoff(
    conversation_id: String,
    cwd: Option<String>,
    workspace: State<'_, WorkspaceState>,
) -> Result<CodexHandoffResult, String> {
    let detail = export_visible_conversation(&workspace, &conversation_id)?;
    if detail.messages.is_empty() {
        return Err("当前 AI 对话还是空的。".to_string());
    }
    let prompt = handoff_prompt(
        &detail.summary.document_title,
        &detail
            .messages
            .into_iter()
            .map(|message| (message.role, message.content))
            .collect::<Vec<_>>(),
    );
    let cwd = cwd
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(PathBuf::from))
        .unwrap_or_else(|| workspace.root().to_path_buf());
    let cwd =
        fs::canonicalize(&cwd).map_err(|_| format!("Codex 工作目录不存在：{}", cwd.display()))?;
    if !cwd.is_dir() {
        return Err("Codex 工作目录不是文件夹。".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || create_codex_task(prompt, cwd))
        .await
        .map_err(|error| format!("Codex 任务线程失败：{error}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removes_injected_context_and_credentials() {
        let text = "真正的问题\n<environment_context>private path</environment_context>\nOPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz";
        let clean = sanitise_visible_text(text, "user").unwrap();
        assert!(clean.contains("真正的问题"));
        assert!(!clean.contains("private path"));
        assert!(!clean.contains("abcdefghijklmnopqrstuvwxyz"));
    }

    #[test]
    fn creates_a_short_title_from_the_first_visible_line() {
        assert_eq!(first_line_title("\n\n  研究这个方法\n更多"), "研究这个方法");
    }

    #[test]
    fn removes_desktop_attachment_headers_from_user_requests() {
        let raw = "# Files mentioned by the user:\n\n## screenshot.png: /tmp/private.png\n\n## My request for Codex:\n真正的问题";
        assert_eq!(
            sanitise_visible_text(raw, "user").as_deref(),
            Some("真正的问题")
        );
    }

    #[test]
    fn imports_only_visible_codex_dialogue_without_duplicates() {
        let path = std::env::temp_dir().join(format!(
            "pharos-codex-parse-{}-{}.jsonl",
            std::process::id(),
            modified_ms(&fs::metadata(std::env::temp_dir()).unwrap())
        ));
        let fixture = [
            serde_json::json!({
                "type": "session_meta",
                "payload": { "id": "thread-test", "cwd": "/tmp" }
            }),
            serde_json::json!({
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "分析这篇论文<environment_context>隐藏路径</environment_context>"
                }
            }),
            serde_json::json!({
                "type": "event_msg",
                "payload": { "type": "agent_message", "message": "先检查核心方法。" }
            }),
            serde_json::json!({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{ "type": "input_text", "text": "重复副本" }]
                }
            }),
            serde_json::json!({
                "type": "response_item",
                "payload": { "type": "reasoning", "content": "不可导入" }
            }),
        ]
        .into_iter()
        .map(|value| value.to_string())
        .collect::<Vec<_>>()
        .join("\n");
        fs::write(&path, fixture).unwrap();

        let parsed = parse_session(&path, 100).unwrap();
        let _ = fs::remove_file(&path);
        assert_eq!(parsed.session_id.as_deref(), Some("thread-test"));
        assert_eq!(parsed.messages.len(), 2);
        assert_eq!(parsed.messages[0].1, "分析这篇论文");
        assert_eq!(parsed.messages[1].1, "先检查核心方法。");
    }
}
