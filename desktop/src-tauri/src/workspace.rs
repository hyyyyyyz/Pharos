//! Portable, versioned local storage for the Pharos desktop application.
//!
//! Durable research data lives below a single workspace root. The only state
//! outside that root is a small per-device bootstrap file pointing to the
//! active workspace. Credentials intentionally remain in the OS keychain.

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager, State};

pub const WORKSPACE_FORMAT: &str = "io.github.hyyyyyyz.pharos.workspace";
pub const WORKSPACE_FORMAT_VERSION: u32 = 1;
pub const DATABASE_SCHEMA_VERSION: i64 = 1;

const BOOTSTRAP_SCHEMA_VERSION: u32 = 1;
const BOOTSTRAP_FILE: &str = "workspace-bootstrap.json";
const MANIFEST_FILE: &str = "pharos.workspace.json";
const DATABASE_RELATIVE_PATH: &str = "database/pharos.sqlite3";
const ZOTERO_MIRROR_RELATIVE_PATH: &str = "database/zotero-mirror-v1.sqlite3";
const LEGACY_ZOTERO_CACHE_RELATIVE_PATH: &str = "cache/zotero-local-v1.json";

const REQUIRED_DIRECTORIES: &[&str] = &[
    "config",
    "database",
    "library/objects/sha256",
    "library/metadata",
    "library/thumbnails",
    "daily/issues",
    "conversations",
    "interchange/codex/imports",
    "interchange/codex/exports",
    "annotations",
    "backups",
    "migrations",
    "cache",
    "logs",
    "tmp",
];

const REQUIRED_TABLES: &[&str] = &[
    "schema_migrations",
    "workspace_meta",
    "conversations",
    "paper_contexts",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceBootstrap {
    schema_version: u32,
    active_root: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceManifest {
    format: String,
    format_version: u32,
    workspace_id: String,
    created_at_ms: i64,
    created_by: ManifestCreator,
    database: ManifestDatabase,
    conversations: ManifestConversations,
    content_store: ManifestContentStore,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ManifestCreator {
    app: String,
    version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ManifestDatabase {
    path: String,
    schema_version: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ManifestConversations {
    path: String,
    event_schema_version: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ManifestContentStore {
    path: String,
    algorithm: String,
}

/// Workspace selected when the current process started.
///
/// Relocation deliberately does not mutate `root`. It updates the bootstrap
/// and returns `requiresRestart = true`, preventing repositories from seeing
/// different roots in the middle of a write.
pub struct WorkspaceState {
    root: PathBuf,
    bootstrap_path: PathBuf,
    operation_lock: Mutex<()>,
}

impl std::fmt::Debug for WorkspaceState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("WorkspaceState")
            .field("root", &self.root)
            .field("bootstrap_path", &self.bootstrap_path)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceStatus {
    pub root: String,
    pub configured_root: String,
    pub daily_path: String,
    pub workspace_id: String,
    pub format_version: u32,
    pub database_schema_version: i64,
    pub requires_restart: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceRelocateResult {
    pub root: String,
    pub workspace_id: String,
    pub copied: bool,
    pub requires_restart: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceHealth {
    pub healthy: bool,
    pub root: String,
    pub workspace_id: Option<String>,
    pub manifest_valid: bool,
    pub database_valid: bool,
    pub database_schema_version: Option<i64>,
    pub sqlite_quick_check: Option<String>,
    pub writable: bool,
    pub issues: Vec<String>,
}

impl WorkspaceState {
    /// Resolve the bootstrap and initialize the process workspace.
    pub fn initialize(app: &AppHandle) -> Result<Self, String> {
        let config_dir = app
            .path()
            .app_config_dir()
            .map_err(|error| format!("Unable to resolve the Pharos config directory: {error}"))?;
        let data_dir = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("Unable to resolve the Pharos data directory: {error}"))?;
        Self::initialize_from_paths(&config_dir, &data_dir)
    }

    fn initialize_from_paths(config_dir: &Path, data_dir: &Path) -> Result<Self, String> {
        fs::create_dir_all(config_dir)
            .map_err(|error| format!("Unable to create {}: {error}", config_dir.display()))?;
        fs::create_dir_all(data_dir)
            .map_err(|error| format!("Unable to create {}: {error}", data_dir.display()))?;

        let bootstrap_path = config_dir.join(BOOTSTRAP_FILE);
        let (root, first_run) = if bootstrap_path.exists() {
            let bootstrap = read_bootstrap(&bootstrap_path)?;
            if !bootstrap.active_root.is_absolute() {
                return Err("The configured Pharos workspace path is not absolute".to_owned());
            }
            if !bootstrap.active_root.exists() {
                return Err(format!(
                    "The configured Pharos workspace is unavailable: {}",
                    bootstrap.active_root.display()
                ));
            }
            (canonicalize_directory(&bootstrap.active_root)?, false)
        } else {
            (data_dir.join("workspace"), true)
        };

        if !first_run && !root.join(MANIFEST_FILE).is_file() {
            return Err(format!(
                "The configured directory is not a Pharos workspace: {}",
                root.display()
            ));
        }

        let manifest = ensure_workspace(&root)?;
        let root = canonicalize_directory(&root)?;
        if first_run {
            write_bootstrap(&bootstrap_path, &root)?;
        }

        let current_manifest = read_manifest(&root)?;
        if current_manifest.workspace_id != manifest.workspace_id {
            return Err("The workspace changed while Pharos was starting".to_owned());
        }

        Ok(Self {
            root,
            bootstrap_path,
            operation_lock: Mutex::new(()),
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn db_path(&self) -> PathBuf {
        self.root.join(DATABASE_RELATIVE_PATH)
    }

    pub fn zotero_mirror_path(&self) -> PathBuf {
        self.root.join(ZOTERO_MIRROR_RELATIVE_PATH)
    }

    pub fn legacy_zotero_cache_path(&self) -> PathBuf {
        self.root.join(LEGACY_ZOTERO_CACHE_RELATIVE_PATH)
    }

    pub fn daily_path(&self) -> PathBuf {
        self.root.join("daily")
    }

    pub fn workspace_id(&self) -> Result<String, String> {
        Ok(read_manifest(&self.root)?.workspace_id)
    }

    fn status(&self) -> Result<WorkspaceStatus, String> {
        let manifest = read_manifest(&self.root)?;
        let database_schema_version = database_schema_version(&self.db_path())?;
        let configured_root = read_bootstrap(&self.bootstrap_path)?.active_root;
        let configured_root = canonicalize_directory(&configured_root)?;

        Ok(WorkspaceStatus {
            root: path_for_display(&self.root),
            configured_root: path_for_display(&configured_root),
            daily_path: path_for_display(&self.daily_path()),
            workspace_id: manifest.workspace_id,
            format_version: manifest.format_version,
            database_schema_version,
            requires_restart: configured_root != self.root,
        })
    }

    fn health(&self) -> WorkspaceHealth {
        let mut health = WorkspaceHealth {
            healthy: false,
            root: path_for_display(&self.root),
            workspace_id: None,
            manifest_valid: false,
            database_valid: false,
            database_schema_version: None,
            sqlite_quick_check: None,
            writable: false,
            issues: Vec::new(),
        };

        match read_manifest(&self.root) {
            Ok(manifest) => {
                health.workspace_id = Some(manifest.workspace_id);
                health.manifest_valid = true;
            }
            Err(error) => health.issues.push(error),
        }

        for relative in REQUIRED_DIRECTORIES {
            if !self.root.join(relative).is_dir() {
                health.issues.push(format!(
                    "Required workspace directory is missing: {relative}"
                ));
            }
        }

        match inspect_database(&self.db_path()) {
            Ok((schema_version, quick_check)) => {
                health.database_schema_version = Some(schema_version);
                health.sqlite_quick_check = Some(quick_check.clone());
                health.database_valid = schema_version == DATABASE_SCHEMA_VERSION
                    && quick_check.eq_ignore_ascii_case("ok");
                if schema_version != DATABASE_SCHEMA_VERSION {
                    health.issues.push(format!(
                        "Expected database schema {DATABASE_SCHEMA_VERSION}, found {schema_version}"
                    ));
                }
                if !quick_check.eq_ignore_ascii_case("ok") {
                    health
                        .issues
                        .push(format!("SQLite quick_check returned: {quick_check}"));
                }
            }
            Err(error) => health.issues.push(error),
        }

        if let Err(error) = verify_required_tables(&self.db_path()) {
            health.database_valid = false;
            health.issues.push(error);
        }

        match workspace_is_writable(&self.root) {
            Ok(()) => health.writable = true,
            Err(error) => health.issues.push(error),
        }

        health.healthy = health.manifest_valid
            && health.database_valid
            && health.writable
            && health.issues.is_empty();
        health
    }

    fn relocate(&self, destination: &Path) -> Result<WorkspaceRelocateResult, String> {
        let _operation = self
            .operation_lock
            .lock()
            .map_err(|_| "The workspace operation lock is poisoned".to_owned())?;

        if !destination.is_absolute() {
            return Err("The new workspace path must be absolute".to_owned());
        }

        let destination_existed = destination.exists();
        if destination_existed && !destination.is_dir() {
            return Err(format!(
                "The workspace destination is not a directory: {}",
                destination.display()
            ));
        }
        if !destination_existed {
            fs::create_dir_all(destination).map_err(|error| {
                format!(
                    "Unable to create workspace destination {}: {error}",
                    destination.display()
                )
            })?;
        }

        let destination = canonicalize_directory(destination)?;
        let source = canonicalize_directory(&self.root)?;
        if destination == source {
            return Err("The selected directory is already the active workspace".to_owned());
        }
        if destination.starts_with(&source) || source.starts_with(&destination) {
            return Err(
                "A workspace cannot be relocated inside itself or into one of its ancestors"
                    .to_owned(),
            );
        }

        let destination_manifest = destination.join(MANIFEST_FILE);
        let (manifest, copied) = if destination_manifest.is_file() {
            // Existing workspaces are switched to, never overwritten or merged.
            let manifest = read_manifest(&destination)?;
            ensure_required_directories(&destination)?;
            initialize_database(&destination.join(DATABASE_RELATIVE_PATH))?;
            (manifest, false)
        } else {
            if !directory_is_effectively_empty(&destination)? {
                if !destination_existed {
                    let _ = fs::remove_dir_all(&destination);
                }
                return Err(format!(
                    "The destination is not empty and is not a valid Pharos workspace: {}",
                    destination.display()
                ));
            }

            let copied_workspace = (|| {
                copy_workspace(&source, &destination)?;
                let manifest = read_manifest(&destination)?;
                initialize_database(&destination.join(DATABASE_RELATIVE_PATH))?;
                Ok::<_, String>(manifest)
            })();
            match copied_workspace {
                Ok(manifest) => (manifest, true),
                Err(error) => {
                    let _ = clear_directory(&destination);
                    if !destination_existed {
                        let _ = fs::remove_dir(&destination);
                    }
                    return Err(error);
                }
            }
        };

        write_bootstrap(&self.bootstrap_path, &destination)?;
        Ok(WorkspaceRelocateResult {
            root: path_for_display(&destination),
            workspace_id: manifest.workspace_id,
            copied,
            requires_restart: true,
        })
    }
}

#[tauri::command]
pub fn workspace_status(state: State<'_, WorkspaceState>) -> Result<WorkspaceStatus, String> {
    state.status()
}

#[tauri::command]
pub fn workspace_relocate(
    state: State<'_, WorkspaceState>,
    destination: String,
) -> Result<WorkspaceRelocateResult, String> {
    state.relocate(Path::new(&destination))
}

#[tauri::command]
pub fn workspace_health(state: State<'_, WorkspaceState>) -> WorkspaceHealth {
    state.health()
}

fn ensure_workspace(root: &Path) -> Result<WorkspaceManifest, String> {
    if root.exists() && !root.is_dir() {
        return Err(format!(
            "The workspace path is not a directory: {}",
            root.display()
        ));
    }
    fs::create_dir_all(root)
        .map_err(|error| format!("Unable to create {}: {error}", root.display()))?;

    let manifest_path = root.join(MANIFEST_FILE);
    let manifest = if manifest_path.exists() {
        read_manifest(root)?
    } else {
        if !directory_is_effectively_empty(root)? {
            return Err(format!(
                "Refusing to initialize a workspace in non-empty directory {}",
                root.display()
            ));
        }
        let manifest = WorkspaceManifest {
            format: WORKSPACE_FORMAT.to_owned(),
            format_version: WORKSPACE_FORMAT_VERSION,
            workspace_id: create_workspace_id(root),
            created_at_ms: unix_time_millis(),
            created_by: ManifestCreator {
                app: "Pharos".to_owned(),
                version: env!("CARGO_PKG_VERSION").to_owned(),
            },
            database: ManifestDatabase {
                path: DATABASE_RELATIVE_PATH.to_owned(),
                schema_version: DATABASE_SCHEMA_VERSION,
            },
            conversations: ManifestConversations {
                path: "conversations".to_owned(),
                event_schema_version: 1,
            },
            content_store: ManifestContentStore {
                path: "library/objects".to_owned(),
                algorithm: "sha256".to_owned(),
            },
        };
        write_json_atomic(&manifest_path, &manifest)?;
        manifest
    };

    ensure_required_directories(root)?;
    let database_path = root.join(DATABASE_RELATIVE_PATH);
    initialize_database(&database_path)?;
    persist_workspace_meta(&database_path, &manifest)?;
    Ok(manifest)
}

fn read_manifest(root: &Path) -> Result<WorkspaceManifest, String> {
    let manifest: WorkspaceManifest = read_json(&root.join(MANIFEST_FILE))?;
    if manifest.format != WORKSPACE_FORMAT {
        return Err(format!(
            "Unsupported workspace format {:?}",
            manifest.format
        ));
    }
    if manifest.format_version != WORKSPACE_FORMAT_VERSION {
        return Err(format!(
            "Unsupported workspace format version {}",
            manifest.format_version
        ));
    }
    if manifest.workspace_id.trim().is_empty() {
        return Err("The workspace manifest has no workspace ID".to_owned());
    }
    if manifest.database.path != DATABASE_RELATIVE_PATH {
        return Err("The workspace manifest contains an unsupported database path".to_owned());
    }
    if manifest.database.schema_version > DATABASE_SCHEMA_VERSION {
        return Err(format!(
            "Workspace database schema {} is newer than this Pharos build supports",
            manifest.database.schema_version
        ));
    }
    if manifest.content_store.algorithm != "sha256" {
        return Err("The workspace uses an unsupported content hashing algorithm".to_owned());
    }
    Ok(manifest)
}

fn ensure_required_directories(root: &Path) -> Result<(), String> {
    for relative in REQUIRED_DIRECTORIES {
        let path = root.join(relative);
        fs::create_dir_all(&path)
            .map_err(|error| format!("Unable to create {}: {error}", path.display()))?;
    }
    Ok(())
}

fn initialize_database(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Unable to create {}: {error}", parent.display()))?;
    }

    let mut connection = Connection::open(path)
        .map_err(|error| format!("Unable to open workspace database: {error}"))?;
    configure_connection(&connection)?;
    let current_version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| format!("Unable to read workspace database schema: {error}"))?;
    if current_version > DATABASE_SCHEMA_VERSION {
        return Err(format!(
            "Workspace database schema {current_version} is newer than this Pharos build supports"
        ));
    }

    if current_version < 1 {
        let applied_at_ms = unix_time_millis();
        let transaction = connection
            .transaction()
            .map_err(|error| format!("Unable to begin workspace migration: {error}"))?;
        transaction
            .execute_batch(
                r#"
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version         INTEGER PRIMARY KEY,
                    name            TEXT NOT NULL,
                    applied_at_ms   INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspace_meta (
                    key             TEXT PRIMARY KEY,
                    value           TEXT NOT NULL,
                    updated_at_ms   INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id                  TEXT PRIMARY KEY,
                    document_key        TEXT NOT NULL,
                    document_kind       TEXT NOT NULL,
                    document_title      TEXT NOT NULL,
                    title               TEXT NOT NULL,
                    source              TEXT NOT NULL DEFAULT 'pharos',
                    source_session_id   TEXT,
                    created_at_ms       INTEGER NOT NULL,
                    updated_at_ms       INTEGER NOT NULL,
                    event_path          TEXT NOT NULL,
                    deleted_at_ms       INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_document
                    ON conversations(document_key, updated_at_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_conversations_source_session
                    ON conversations(source, source_session_id);

                CREATE TABLE IF NOT EXISTS paper_contexts (
                    document_key        TEXT PRIMARY KEY,
                    document_kind       TEXT NOT NULL,
                    document_title      TEXT NOT NULL,
                    content_hash        TEXT NOT NULL,
                    text_path           TEXT NOT NULL,
                    summary             TEXT,
                    page_count          INTEGER,
                    char_count          INTEGER NOT NULL,
                    status              TEXT NOT NULL,
                    error               TEXT,
                    created_at_ms       INTEGER NOT NULL,
                    updated_at_ms       INTEGER NOT NULL
                );
                "#,
            )
            .map_err(|error| format!("Unable to apply workspace schema v1: {error}"))?;
        transaction
            .execute(
                "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at_ms) VALUES (1, 'workspace-v1', ?1)",
                params![applied_at_ms],
            )
            .map_err(|error| format!("Unable to record workspace migration: {error}"))?;
        transaction
            .execute_batch("PRAGMA user_version = 1;")
            .map_err(|error| format!("Unable to set workspace schema version: {error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("Unable to commit workspace migration: {error}"))?;
    }

    verify_required_tables(path)
}

fn configure_connection(connection: &Connection) -> Result<(), String> {
    connection
        .busy_timeout(Duration::from_secs(5))
        .map_err(|error| format!("Unable to configure SQLite busy timeout: {error}"))?;
    connection
        .execute_batch("PRAGMA foreign_keys = ON; PRAGMA synchronous = NORMAL;")
        .map_err(|error| format!("Unable to configure workspace database: {error}"))?;
    let _: String = connection
        .query_row("PRAGMA journal_mode = WAL", [], |row| row.get(0))
        .map_err(|error| format!("Unable to enable SQLite WAL mode: {error}"))?;
    Ok(())
}

fn persist_workspace_meta(path: &Path, manifest: &WorkspaceManifest) -> Result<(), String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("Unable to open workspace database: {error}"))?;
    configure_connection(&connection)?;
    let updated_at_ms = unix_time_millis();
    for (key, value) in [
        ("workspace_id", manifest.workspace_id.clone()),
        ("workspace_format", manifest.format.clone()),
        (
            "workspace_format_version",
            manifest.format_version.to_string(),
        ),
    ] {
        connection
            .execute(
                "INSERT INTO workspace_meta(key, value, updated_at_ms) VALUES (?1, ?2, ?3)\n\
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at_ms = excluded.updated_at_ms",
                params![key, value, updated_at_ms],
            )
            .map_err(|error| format!("Unable to update workspace metadata: {error}"))?;
    }
    Ok(())
}

fn inspect_database(path: &Path) -> Result<(i64, String), String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("Unable to open workspace database: {error}"))?;
    let schema_version = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| format!("Unable to read workspace database schema: {error}"))?;
    let quick_check = connection
        .query_row("PRAGMA quick_check(1)", [], |row| row.get(0))
        .map_err(|error| format!("Unable to run SQLite quick_check: {error}"))?;
    Ok((schema_version, quick_check))
}

fn database_schema_version(path: &Path) -> Result<i64, String> {
    inspect_database(path).map(|(schema_version, _)| schema_version)
}

fn verify_required_tables(path: &Path) -> Result<(), String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("Unable to open workspace database: {error}"))?;
    for table in REQUIRED_TABLES {
        let present: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
                params![table],
                |row| row.get(0),
            )
            .map_err(|error| format!("Unable to inspect workspace database: {error}"))?;
        if present != 1 {
            return Err(format!("Required workspace table is missing: {table}"));
        }
    }
    Ok(())
}

fn copy_workspace(source: &Path, destination: &Path) -> Result<(), String> {
    copy_directory_contents(source, destination, source)?;
    ensure_required_directories(destination)
}

fn copy_directory_contents(
    source: &Path,
    destination: &Path,
    workspace_root: &Path,
) -> Result<(), String> {
    fs::create_dir_all(destination)
        .map_err(|error| format!("Unable to create {}: {error}", destination.display()))?;

    let entries = fs::read_dir(source)
        .map_err(|error| format!("Unable to read {}: {error}", source.display()))?;
    for entry in entries {
        let entry =
            entry.map_err(|error| format!("Unable to enumerate {}: {error}", source.display()))?;
        let source_path = entry.path();
        let relative = source_path.strip_prefix(workspace_root).map_err(|error| {
            format!(
                "Unable to resolve workspace-relative path {}: {error}",
                source_path.display()
            )
        })?;

        if relative.starts_with("tmp") || is_sqlite_sidecar(relative) {
            continue;
        }

        let metadata = fs::symlink_metadata(&source_path)
            .map_err(|error| format!("Unable to inspect {}: {error}", source_path.display()))?;
        if metadata.file_type().is_symlink() {
            return Err(format!(
                "Refusing to relocate a workspace containing a symbolic link: {}",
                source_path.display()
            ));
        }

        let destination_path = destination.join(entry.file_name());
        if metadata.is_dir() {
            copy_directory_contents(&source_path, &destination_path, workspace_root)?;
        } else if metadata.is_file() {
            if is_workspace_database(relative) {
                snapshot_sqlite_database(&source_path, &destination_path)?;
            } else {
                fs::copy(&source_path, &destination_path).map_err(|error| {
                    format!(
                        "Unable to copy {} to {}: {error}",
                        source_path.display(),
                        destination_path.display()
                    )
                })?;
            }
        } else {
            return Err(format!(
                "Unsupported file type in workspace: {}",
                source_path.display()
            ));
        }
    }
    Ok(())
}

fn is_workspace_database(relative: &Path) -> bool {
    relative.starts_with("database")
        && matches!(
            relative
                .extension()
                .and_then(|extension| extension.to_str()),
            Some("sqlite") | Some("sqlite3") | Some("db")
        )
}

fn is_sqlite_sidecar(relative: &Path) -> bool {
    let file_name = relative
        .file_name()
        .and_then(|file_name| file_name.to_str())
        .unwrap_or_default();
    file_name.ends_with("-wal") || file_name.ends_with("-shm") || file_name.ends_with("-journal")
}

fn snapshot_sqlite_database(source: &Path, destination: &Path) -> Result<(), String> {
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Unable to create {}: {error}", parent.display()))?;
    }
    if destination.exists() {
        fs::remove_file(destination)
            .map_err(|error| format!("Unable to replace {}: {error}", destination.display()))?;
    }

    let connection = Connection::open(source)
        .map_err(|error| format!("Unable to open SQLite source {}: {error}", source.display()))?;
    connection
        .busy_timeout(Duration::from_secs(10))
        .map_err(|error| format!("Unable to configure SQLite snapshot: {error}"))?;
    let escaped_destination = destination.to_string_lossy().replace('\'', "''");
    connection
        .execute_batch(&format!("VACUUM INTO '{escaped_destination}';"))
        .map_err(|error| {
            format!(
                "Unable to create a consistent SQLite snapshot of {}: {error}",
                source.display()
            )
        })?;

    let (_, quick_check) = inspect_database(destination)?;
    if !quick_check.eq_ignore_ascii_case("ok") {
        return Err(format!(
            "Relocated SQLite database {} failed quick_check: {quick_check}",
            destination.display()
        ));
    }
    Ok(())
}

fn workspace_is_writable(root: &Path) -> Result<(), String> {
    let probe = root
        .join("tmp")
        .join(format!(".workspace-health-{}", unique_suffix()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe)
            .map_err(|error| format!("The workspace is not writable: {error}"))?;
        file.write_all(b"pharos-workspace-health")
            .map_err(|error| format!("The workspace is not writable: {error}"))?;
        file.sync_all()
            .map_err(|error| format!("The workspace is not writable: {error}"))
    })();
    let _ = fs::remove_file(&probe);
    result
}

fn write_bootstrap(path: &Path, root: &Path) -> Result<(), String> {
    write_json_atomic(
        path,
        &WorkspaceBootstrap {
            schema_version: BOOTSTRAP_SCHEMA_VERSION,
            active_root: root.to_path_buf(),
        },
    )
}

fn read_bootstrap(path: &Path) -> Result<WorkspaceBootstrap, String> {
    let bootstrap: WorkspaceBootstrap = read_json(path)?;
    if bootstrap.schema_version != BOOTSTRAP_SCHEMA_VERSION {
        return Err(format!(
            "Unsupported workspace bootstrap version {}",
            bootstrap.schema_version
        ));
    }
    Ok(bootstrap)
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let bytes =
        fs::read(path).map_err(|error| format!("Unable to read {}: {error}", path.display()))?;
    serde_json::from_slice(&bytes)
        .map_err(|error| format!("Unable to decode {}: {error}", path.display()))
}

fn write_json_atomic<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Unable to create {}: {error}", parent.display()))?;
    }
    let mut bytes = serde_json::to_vec_pretty(value)
        .map_err(|error| format!("Unable to encode {}: {error}", path.display()))?;
    bytes.push(b'\n');
    let temporary = path.with_extension(format!("tmp-{}", unique_suffix()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("Unable to create {}: {error}", temporary.display()))?;
    file.write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("Unable to persist {}: {error}", temporary.display()))?;
    drop(file);

    match fs::rename(&temporary, path) {
        Ok(()) => {}
        Err(_first_error) if path.exists() => {
            // Unix replaces atomically above. Windows requires a small
            // backup-and-swap fallback when the destination already exists.
            let backup = path.with_extension("bak");
            let _ = fs::remove_file(&backup);
            fs::rename(path, &backup).map_err(|error| {
                let _ = fs::remove_file(&temporary);
                format!(
                    "Unable to prepare {} for replacement: {error}",
                    path.display()
                )
            })?;
            if let Err(error) = fs::rename(&temporary, path) {
                let _ = fs::rename(&backup, path);
                let _ = fs::remove_file(&temporary);
                return Err(format!("Unable to replace {}: {error}", path.display()));
            }
            let _ = fs::remove_file(&backup);
        }
        Err(error) => {
            let _ = fs::remove_file(&temporary);
            return Err(format!("Unable to install {}: {error}", path.display()));
        }
    }

    #[cfg(unix)]
    if let Some(parent) = path.parent() {
        let _ = File::open(parent).and_then(|directory| directory.sync_all());
    }
    Ok(())
}

fn canonicalize_directory(path: &Path) -> Result<PathBuf, String> {
    let canonical = fs::canonicalize(path)
        .map_err(|error| format!("Unable to resolve {}: {error}", path.display()))?;
    if !canonical.is_dir() {
        return Err(format!("Path is not a directory: {}", canonical.display()));
    }
    Ok(canonical)
}

fn directory_is_effectively_empty(path: &Path) -> Result<bool, String> {
    let entries = fs::read_dir(path)
        .map_err(|error| format!("Unable to read {}: {error}", path.display()))?;
    for entry in entries {
        let entry =
            entry.map_err(|error| format!("Unable to enumerate {}: {error}", path.display()))?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !matches!(name.as_ref(), ".DS_Store" | "Thumbs.db" | "desktop.ini") {
            return Ok(false);
        }
    }
    Ok(true)
}

fn clear_directory(path: &Path) -> Result<(), String> {
    for entry in
        fs::read_dir(path).map_err(|error| format!("Unable to read {}: {error}", path.display()))?
    {
        let entry =
            entry.map_err(|error| format!("Unable to enumerate {}: {error}", path.display()))?;
        let entry_path = entry.path();
        let metadata = fs::symlink_metadata(&entry_path)
            .map_err(|error| format!("Unable to inspect {}: {error}", entry_path.display()))?;
        if metadata.is_dir() && !metadata.file_type().is_symlink() {
            fs::remove_dir_all(&entry_path)
                .map_err(|error| format!("Unable to remove {}: {error}", entry_path.display()))?;
        } else {
            fs::remove_file(&entry_path)
                .map_err(|error| format!("Unable to remove {}: {error}", entry_path.display()))?;
        }
    }
    Ok(())
}

fn create_workspace_id(root: &Path) -> String {
    let seed = format!(
        "{}:{}:{}",
        root.display(),
        unique_suffix(),
        std::process::id()
    );
    let digest = Sha256::digest(seed.as_bytes());
    hex::encode(digest)[..32].to_owned()
}

fn unix_time_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as i64
}

fn unique_suffix() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{}-{nanos}", std::process::id())
}

fn path_for_display(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!("pharos-workspace-{label}-{}", unique_suffix()))
    }

    #[test]
    fn creates_a_versioned_workspace_and_required_schema() {
        let base = test_root("create");
        let config = base.join("config");
        let data = base.join("data");
        let state = WorkspaceState::initialize_from_paths(&config, &data).unwrap();

        assert!(state.root().join(MANIFEST_FILE).is_file());
        assert!(state.db_path().is_file());
        assert!(state.daily_path().join("issues").is_dir());
        assert!(state.legacy_zotero_cache_path().starts_with(state.root()));
        assert!(state.zotero_mirror_path().starts_with(state.root()));
        assert_eq!(database_schema_version(&state.db_path()).unwrap(), 1);
        verify_required_tables(&state.db_path()).unwrap();
        assert!(state.health().healthy);

        let reopened = WorkspaceState::initialize_from_paths(&config, &data).unwrap();
        assert_eq!(reopened.root(), state.root());
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn relocation_copies_to_empty_directory_and_defers_switch_until_restart() {
        let base = test_root("relocate");
        let config = base.join("config");
        let data = base.join("data");
        let destination = base.join("portable-workspace");
        let state = WorkspaceState::initialize_from_paths(&config, &data).unwrap();

        let result = state.relocate(&destination).unwrap();
        assert!(result.copied);
        assert!(result.requires_restart);
        assert!(destination.join(MANIFEST_FILE).is_file());
        assert!(destination.join(DATABASE_RELATIVE_PATH).is_file());
        assert!(state.status().unwrap().requires_restart);

        drop(state);
        let reopened = WorkspaceState::initialize_from_paths(&config, &data).unwrap();
        assert_eq!(reopened.root(), fs::canonicalize(&destination).unwrap());
        assert!(reopened.health().healthy);
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn relocation_refuses_a_non_workspace_non_empty_directory() {
        let base = test_root("refuse");
        let config = base.join("config");
        let data = base.join("data");
        let destination = base.join("occupied");
        fs::create_dir_all(&destination).unwrap();
        fs::write(destination.join("user-file.txt"), b"do not overwrite").unwrap();
        let state = WorkspaceState::initialize_from_paths(&config, &data).unwrap();

        let error = state.relocate(&destination).unwrap_err();
        assert!(error.contains("not empty"));
        assert_eq!(
            fs::read(destination.join("user-file.txt")).unwrap(),
            b"do not overwrite"
        );
        let _ = fs::remove_dir_all(base);
    }
}
