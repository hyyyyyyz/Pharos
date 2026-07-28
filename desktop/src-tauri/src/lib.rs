//! The Pharos desktop shell.
//!
//! The interface remains the same React app as the browser build. Native-only
//! capabilities live behind narrow commands: today that includes user-approved
//! Daily Vault folders and the loopback-only Zotero Local API.

mod ai;
mod codex_bridge;
mod workspace;
mod zotero;
mod zotero_local;

use std::{fs, path::Path};

use tauri::Manager;

/// Copy caches created by pre-Workspace desktop builds into the new portable
/// hierarchy. The old files are intentionally retained as a rollback anchor.
fn migrate_legacy_file(source: &Path, destination: &Path) -> Result<(), String> {
    if destination.exists() || !source.is_file() {
        return Ok(());
    }
    let parent = destination
        .parent()
        .ok_or_else(|| "Invalid Workspace migration destination".to_string())?;
    fs::create_dir_all(parent).map_err(|error| {
        format!(
            "Unable to create Workspace migration directory {}: {error}",
            parent.display()
        )
    })?;
    let temporary = destination.with_extension("migrating");
    fs::copy(source, &temporary).map_err(|error| {
        format!(
            "Unable to copy legacy desktop data {}: {error}",
            source.display()
        )
    })?;
    fs::rename(&temporary, destination).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!(
            "Unable to install migrated desktop data {}: {error}",
            destination.display()
        )
    })
}

/// Builds and runs the application. Shared by the desktop launcher (`main.rs`)
/// and the mobile entry point, so both platforms start the same app.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));
    }

    builder
        .plugin(tauri_plugin_deep_link::init())
        // Register fs before persisted-scope: the latter serialises and restores
        // the former's user-approved runtime scope across app launches.
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_persisted_scope::init())
        .plugin(tauri_plugin_dialog::init())
        // Open external links (GitHub, DOI, arXiv) in the system browser.
        .plugin(tauri_plugin_opener::init())
        // Local PDFs are addressed by opaque attachment IDs. The protocol
        // resolves those IDs inside Rust and supports byte ranges for pdf.js;
        // no filesystem path crosses into the WebView.
        .register_uri_scheme_protocol("pharos-local", |ctx, request| {
            zotero_local::protocol_response(ctx.app_handle(), request)
        })
        .setup(|app| {
            let workspace = workspace::WorkspaceState::initialize(app.handle())
                .map_err(std::io::Error::other)?;
            let app_data = app.path().app_data_dir()?;
            migrate_legacy_file(
                &app_data.join("zotero-mirror-v1.sqlite3"),
                &workspace.zotero_mirror_path(),
            )
            .map_err(std::io::Error::other)?;
            migrate_legacy_file(
                &app_data.join("zotero-local-v1.json"),
                &workspace.legacy_zotero_cache_path(),
            )
            .map_err(std::io::Error::other)?;

            let zotero_mirror_path = workspace.zotero_mirror_path();
            let zotero_cache_path = workspace.legacy_zotero_cache_path();
            app.manage(workspace);
            app.manage(ai::AiState::default());
            app.manage(zotero_local::LocalZoteroState::load(zotero_cache_path));
            app.manage(zotero::commands::ZoteroState::load(zotero_mirror_path));
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = handle.state::<zotero::commands::ZoteroState>();
                // A failed background refresh keeps the last successful mirror
                // intact. Users can still browse offline and retry manually.
                let _ = state
                    .refresh(zotero::model::ZoteroRefreshRequest::default())
                    .await;
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            zotero::commands::zotero_connection_status,
            zotero::commands::zotero_refresh,
            zotero::commands::zotero_list_libraries,
            zotero::commands::zotero_list_collections,
            zotero::commands::zotero_query_items,
            zotero::commands::zotero_get_item,
            zotero::commands::zotero_list_item_children,
            zotero::commands::zotero_list_tags,
            zotero::commands::zotero_list_saved_searches,
            zotero::commands::zotero_get_fulltext,
            zotero::commands::zotero_get_attachment_url,
            zotero::commands::zotero_attachment_bytes,
            zotero_local::zotero_local_status,
            zotero_local::zotero_local_sync,
            zotero_local::zotero_local_list,
            zotero_local::zotero_local_get,
            zotero_local::zotero_local_pdf_url,
            zotero_local::zotero_local_pdf_bytes,
            workspace::workspace_status,
            workspace::workspace_relocate,
            workspace::workspace_health,
            ai::provider_status,
            ai::provider_save,
            ai::provider_clear,
            ai::conversation_list,
            ai::conversation_create,
            ai::conversation_load,
            ai::conversation_delete,
            ai::document_context_status,
            ai::document_prepare_context,
            ai::conversation_cancel,
            ai::conversation_send_stream,
            codex_bridge::codex_capabilities,
            codex_bridge::codex_discover_sessions,
            codex_bridge::codex_import_session,
            codex_bridge::codex_create_handoff,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Pharos desktop app");
}
