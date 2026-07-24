// Prevents a second console window from opening alongside the app on Windows
// release builds. Harmless on macOS/Linux.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    pharos_desktop_lib::run()
}
