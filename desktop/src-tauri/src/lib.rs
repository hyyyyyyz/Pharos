//! The Pharos desktop shell.
//!
//! The interface remains the same React app as the browser build. Native-only
//! capabilities live behind narrow commands: today that includes user-approved
//! Daily Vault folders and the loopback-only Zotero Local API.

mod zotero;
mod zotero_local;

use tauri::Manager;

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
            app.manage(zotero_local::LocalZoteroState::load(app.handle()));
            app.manage(zotero::commands::ZoteroState::load(app.handle()));
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
            zotero_local::zotero_local_status,
            zotero_local::zotero_local_sync,
            zotero_local::zotero_local_list,
            zotero_local::zotero_local_get,
            zotero_local::zotero_local_pdf_url,
            zotero_local::zotero_local_pdf_bytes,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Pharos desktop app");
}
