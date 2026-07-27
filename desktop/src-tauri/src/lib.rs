//! The Pharos desktop shell.
//!
//! This is deliberately thin. The entire UI is the same React app that ships
//! to the browser (`frontend/`), loaded from the bundle; the Rust side only
//! opens the window and wires the few native niceties a desktop app should
//! have. Keeping logic out of here is what guarantees the desktop and web
//! clients stay identical — there is only one UI codebase.

/// Builds and runs the application. Shared by the desktop launcher (`main.rs`)
/// and the mobile entry point, so both platforms start the same app.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Register fs before persisted-scope: the latter serialises and restores
        // the former's user-approved runtime scope across app launches.
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_persisted_scope::init())
        .plugin(tauri_plugin_dialog::init())
        // Open external links (GitHub, DOI, arXiv) in the system browser.
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running the Pharos desktop app");
}
