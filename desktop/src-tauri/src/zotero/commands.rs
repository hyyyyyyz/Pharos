//! Provider-neutral Tauri commands and desktop sync orchestration.

use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        RwLock,
    },
    time::{SystemTime, UNIX_EPOCH},
};

use tauri::{AppHandle, Manager, State};

use super::{
    local_api::LocalApiProvider,
    mirror::ZoteroMirror,
    model::{
        ConnectionPhase, LibrarySnapshot, ProviderCapabilities, ProviderKind, ZoteroCollection,
        ZoteroConnectionStatus, ZoteroFulltext, ZoteroItemDetail, ZoteroItemQuery, ZoteroItemRef,
        ZoteroItemSummary, ZoteroLibrary, ZoteroLibraryRef, ZoteroPage, ZoteroProbe,
        ZoteroRefreshRequest, ZoteroSavedSearch, ZoteroSyncReport, ZoteroTag, LOCAL_SOURCE_ID,
    },
};

pub struct ZoteroState {
    provider: Option<LocalApiProvider>,
    mirror: Option<ZoteroMirror>,
    syncing: AtomicBool,
    last_probe: RwLock<ZoteroProbe>,
    last_error: RwLock<Option<String>>,
}

pub struct RefreshOutcome {
    pub report: ZoteroSyncReport,
    pub snapshots: Vec<LibrarySnapshot>,
}

struct SyncFlag<'a>(&'a AtomicBool);

impl Drop for SyncFlag<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

impl ZoteroState {
    pub fn load(app: &AppHandle) -> Self {
        let mut errors = Vec::new();
        let provider = match LocalApiProvider::new() {
            Ok(provider) => Some(provider),
            Err(error) => {
                errors.push(error);
                None
            }
        };
        let mirror_path = app
            .path()
            .app_data_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("zotero-mirror-v1.sqlite3");
        let mirror = match ZoteroMirror::open(mirror_path) {
            Ok(mirror) => Some(mirror),
            Err(error) => {
                errors.push(error);
                None
            }
        };
        Self {
            provider,
            mirror,
            syncing: AtomicBool::new(false),
            last_probe: RwLock::new(ZoteroProbe {
                available: false,
                zotero_version: None,
                api_version: None,
                schema_version: None,
            }),
            last_error: RwLock::new((!errors.is_empty()).then(|| errors.join(" "))),
        }
    }

    fn provider(&self) -> Result<&LocalApiProvider, String> {
        self.provider
            .as_ref()
            .ok_or_else(|| "本地 Zotero Provider 初始化失败。".to_string())
    }

    pub(crate) fn mirror(&self) -> Result<&ZoteroMirror, String> {
        self.mirror
            .as_ref()
            .ok_or_else(|| "Zotero 本地镜像初始化失败。".to_string())
    }

    pub(crate) fn attachment_path(&self, public_id: &str) -> Option<PathBuf> {
        self.mirror()
            .ok()
            .and_then(|mirror| mirror.attachment_path(public_id).ok())
            .flatten()
    }

    fn set_error(&self, message: Option<String>) {
        *self
            .last_error
            .write()
            .unwrap_or_else(|poison| poison.into_inner()) = message;
    }

    pub async fn status(&self) -> Result<ZoteroConnectionStatus, String> {
        let probe = match self.provider() {
            Ok(provider) => provider.probe_info().await,
            Err(_) => ZoteroProbe {
                available: false,
                zotero_version: None,
                api_version: None,
                schema_version: None,
            },
        };
        *self
            .last_probe
            .write()
            .unwrap_or_else(|poison| poison.into_inner()) = probe.clone();
        let metrics = self.mirror().ok().and_then(|mirror| mirror.metrics().ok());
        let library_count = metrics.map(|value| value.libraries).unwrap_or_default();
        let item_count = metrics.map(|value| value.items).unwrap_or_default();
        let syncing = self.syncing.load(Ordering::Acquire);
        let last_error = self
            .last_error
            .read()
            .unwrap_or_else(|poison| poison.into_inner())
            .clone();
        let phase = if syncing {
            ConnectionPhase::Indexing
        } else if probe.available && item_count > 0 {
            ConnectionPhase::Ready
        } else if probe.available {
            ConnectionPhase::Connecting
        } else if item_count > 0 {
            ConnectionPhase::Stale
        } else if last_error.is_some() {
            ConnectionPhase::Error
        } else {
            ConnectionPhase::Disconnected
        };
        let last_successful_sync_ms = self
            .mirror()
            .ok()
            .and_then(|mirror| mirror.get_meta("last_successful_sync_ms").ok())
            .flatten()
            .and_then(|value| value.parse().ok());
        Ok(ZoteroConnectionStatus {
            source_id: LOCAL_SOURCE_ID.to_string(),
            provider: ProviderKind::LocalApi,
            phase,
            capabilities: ProviderCapabilities::local_api(),
            available: probe.available,
            syncing,
            zotero_version: probe.zotero_version,
            api_version: probe.api_version,
            schema_version: probe.schema_version,
            last_successful_sync_ms,
            last_error,
            library_count,
            item_count,
        })
    }

    pub async fn refresh(&self, request: ZoteroRefreshRequest) -> Result<RefreshOutcome, String> {
        if self
            .syncing
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err("本地 Zotero 正在同步。".to_string());
        }
        let _flag = SyncFlag(&self.syncing);
        let provider = self.provider()?;
        let mirror = self.mirror()?;
        let probe = provider.probe_info().await;
        *self
            .last_probe
            .write()
            .unwrap_or_else(|poison| poison.into_inner()) = probe.clone();
        if !probe.available {
            let message =
                "无法连接本机 Zotero。请确认 Zotero 正在运行，并已开启本机 API。".to_string();
            self.set_error(Some(message.clone()));
            return Err(message);
        }

        let libraries = provider.fetch_libraries().await.map_err(|message| {
            self.set_error(Some(message.clone()));
            message
        })?;
        let mut snapshots = Vec::with_capacity(libraries.len());
        let mut used_full_snapshot = false;
        for library in &libraries {
            let cursor = mirror.library_version(&library.source_id, &library.library_id)?;
            let needs_full = request.force_full
                || cursor.is_none()
                || cursor.is_some_and(|version| version > library.version);
            if needs_full {
                let snapshot = provider.fetch_snapshot(library).await.map_err(|message| {
                    self.set_error(Some(message.clone()));
                    message
                })?;
                mirror.replace_library(&snapshot).map_err(|message| {
                    self.set_error(Some(message.clone()));
                    message
                })?;
                snapshots.push(snapshot);
                used_full_snapshot = true;
                continue;
            }
            let cursor = cursor.unwrap_or_default();
            if library.version <= cursor {
                continue;
            }
            let delta = provider
                .fetch_delta(library, cursor)
                .await
                .map_err(|message| {
                    self.set_error(Some(message.clone()));
                    message
                })?;
            mirror.apply_delta(&delta).map_err(|message| {
                self.set_error(Some(message.clone()));
                message
            })?;
        }
        mirror.reconcile_libraries(
            LOCAL_SOURCE_ID,
            &libraries
                .iter()
                .map(|library| library.library_id.clone())
                .collect::<Vec<_>>(),
        )?;
        let completed_at_ms = now_ms();
        mirror.set_meta("last_successful_sync_ms", &completed_at_ms.to_string())?;
        mirror.set_meta("mirror_ready", "1")?;
        if let Some(version) = probe.zotero_version.as_deref() {
            mirror.set_meta("zotero_version", version)?;
        }
        if let Some(version) = probe.schema_version {
            mirror.set_meta("zotero_schema_version", &version.to_string())?;
        }
        self.set_error(None);

        let metrics = mirror.metrics()?;
        Ok(RefreshOutcome {
            report: ZoteroSyncReport {
                source_id: LOCAL_SOURCE_ID.to_string(),
                provider: ProviderKind::LocalApi,
                full: used_full_snapshot,
                library_count: metrics.libraries,
                item_count: metrics.items,
                attachment_count: metrics.attachments,
                collection_count: metrics.collections,
                note_count: metrics.notes,
                annotation_count: metrics.annotations,
                available_attachment_count: metrics.available_attachments,
                completed_at_ms,
            },
            snapshots,
        })
    }
}

#[tauri::command]
pub async fn zotero_connection_status(
    state: State<'_, ZoteroState>,
) -> Result<ZoteroConnectionStatus, String> {
    state.status().await
}

#[tauri::command]
pub async fn zotero_refresh(
    request: Option<ZoteroRefreshRequest>,
    state: State<'_, ZoteroState>,
) -> Result<ZoteroSyncReport, String> {
    state
        .refresh(request.unwrap_or_default())
        .await
        .map(|value| value.report)
}

#[tauri::command]
pub fn zotero_list_libraries(state: State<'_, ZoteroState>) -> Result<Vec<ZoteroLibrary>, String> {
    state.mirror()?.list_libraries()
}

#[tauri::command]
pub fn zotero_list_collections(
    library: ZoteroLibraryRef,
    state: State<'_, ZoteroState>,
) -> Result<Vec<ZoteroCollection>, String> {
    state.mirror()?.list_collections(&library)
}

#[tauri::command]
pub fn zotero_query_items(
    query: Option<ZoteroItemQuery>,
    state: State<'_, ZoteroState>,
) -> Result<ZoteroPage<ZoteroItemSummary>, String> {
    state.mirror()?.query_items(&query.unwrap_or_default())
}

#[tauri::command]
pub fn zotero_get_item(
    item: ZoteroItemRef,
    state: State<'_, ZoteroState>,
) -> Result<ZoteroItemDetail, String> {
    state.mirror()?.item_detail(&item)
}

#[tauri::command]
pub fn zotero_list_item_children(
    item: ZoteroItemRef,
    state: State<'_, ZoteroState>,
) -> Result<Vec<ZoteroItemSummary>, String> {
    state.mirror()?.item_children(&item)
}

#[tauri::command]
pub fn zotero_list_tags(
    library: ZoteroLibraryRef,
    state: State<'_, ZoteroState>,
) -> Result<Vec<ZoteroTag>, String> {
    state.mirror()?.list_tags(&library)
}

#[tauri::command]
pub fn zotero_list_saved_searches(
    library: ZoteroLibraryRef,
    state: State<'_, ZoteroState>,
) -> Result<Vec<ZoteroSavedSearch>, String> {
    state.mirror()?.list_saved_searches(&library)
}

#[tauri::command]
pub async fn zotero_get_fulltext(
    item: ZoteroItemRef,
    state: State<'_, ZoteroState>,
) -> Result<Option<ZoteroFulltext>, String> {
    if let Some(fulltext) = state.mirror()?.fulltext(&item)? {
        return Ok(Some(fulltext));
    }
    let library_ref = ZoteroLibraryRef {
        source_id: item.source_id.clone(),
        library_id: item.library_id.clone(),
    };
    let library = state.mirror()?.library(&library_ref)?;
    let fulltext = state
        .provider()?
        .fetch_fulltext(&library, &item.item_key)
        .await?;
    if let Some(value) = &fulltext {
        state.mirror()?.store_fulltext(value)?;
    }
    Ok(fulltext)
}

#[tauri::command]
pub fn zotero_get_attachment_url(
    attachment_id: String,
    state: State<'_, ZoteroState>,
) -> Result<String, String> {
    let path = state
        .attachment_path(&attachment_id)
        .ok_or_else(|| "这份 Zotero 附件尚未下载到本机。".to_string())?;
    if !path.is_file() {
        return Err("这份 Zotero 附件已被移动或删除。".to_string());
    }
    Ok(format!("pharos-local://localhost/zotero/{attachment_id}"))
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}
