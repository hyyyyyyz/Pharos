# Pharos Desktop (Tauri)

The Pharos desktop app wraps **the exact same React frontend** that ships to the
browser (`../frontend`) in a Tauri native runtime. There is one UI codebase — the
desktop client loads `frontend/dist` in a native WebView — while Rust adds the
local capabilities that a browser cannot safely provide.

It still talks to the Pharos backend for accounts, cloud records, discovery,
translation jobs, and research projects. The desktop runtime additionally owns
a versioned, offline-capable mirror of the user's local Zotero libraries and a
path-hiding PDF stream protocol. It does not bundle the Pharos backend or the
translation engine.

## Why Tauri (not Electron / native)

- **Native rewrite (SwiftUI) was ruled out** — it could only ever *resemble* the
  web app, never be identical, and would double the UI maintenance.
- **Tauri over Electron** — version 0.3.0's universal macOS application bundle
  is about 15 MB and uses the operating system WebView instead of shipping a
  second browser engine. Tauri's main caveat is that macOS uses WebKit
  (WKWebView) rather than Chromium, so Chrome-only CSS can render differently.
  Pharos uses system fonts and standards-based CSS supported by current WebKit.

## Layout

```
desktop/
  package.json          # Tauri scripts + @tauri-apps/cli
  src-tauri/
    Cargo.toml          # Rust deps (tauri 2)
    tauri.conf.json      # v2 config; frontendDist -> ../../frontend/dist
    build.rs
    src/{main,lib}.rs    # app bootstrap, commands, and local PDF protocol
    src/zotero/          # Local API provider, SQLite mirror, and repository
    capabilities/default.json  # narrowly scoped Tauri permissions
    icons/               # generated from assets/brand/app-icon.png
```

## Run it

Prerequisites: [Rust](https://rustup.rs), Node 18+, Xcode Command Line Tools,
and the Pharos **backend running** (the app talks to it):

```bash
# from repo root — start the backend first
python -m uvicorn pharos.main:app --host 127.0.0.1 --port 8848

# then, in another shell
cd desktop
npm install
npm run dev        # compiles the Rust shell + starts Vite + opens the window
```

`npm run dev` runs the frontend dev server (Vite, mode `dev`), so in development
the app hits the backend through Vite's `/api` proxy — exactly like the browser.

## Which backend does it talk to?

A production build bakes `VITE_API_BASE` from `frontend/.env.desktop`, which is
tracked as `https://pharos.selab.top/api`. Plain `npm run dev` still uses Vite's
local `/api` proxy for backend development. The CSP in `tauri.conf.json`
(`connect-src`) lists both the production service and local development origins.

## Build a distributable

```bash
# macOS — universal .dmg (Apple Silicon + Intel in one download)
npm run build:universal   # -> src-tauri/target/universal-apple-darwin/release/bundle/dmg/Pharos_*.dmg

# Windows — build on a Windows machine (produces .msi + .exe/NSIS)
npm run build             # -> src-tauri/target/release/bundle/{msi,nsis}/Pharos_*
```

CI (`.github/workflows/desktop-release.yml`) builds **both macOS and Windows** in
one matrix run and attaches all installers to a single **draft** GitHub Release.
Trigger it by pushing a tag like `desktop-v0.3.0`, or run it by hand from the
Actions tab. On Windows, Tauri uses the system WebView2 (Chromium), so the app
renders identically to the web version there. (Linux/AppImage is one more matrix
entry plus an apt step for `libwebkit2gtk-4.1-dev` — add it when you want it.)

## Installing an unsigned build

The installers are **unsigned** — there is no Apple ($99/yr) or Windows
code-signing certificate yet. Each OS warns on first open; neither warning is
about the app being unsafe, only about it not being signed by a paid identity.

- **macOS** (Gatekeeper: "Pharos is damaged / cannot be verified"):
  right-click `Pharos.app` → **Open** once, or `xattr -cr /Applications/Pharos.app`.
- **Windows** (SmartScreen: "Windows protected your PC"):
  click **More info** → **Run anyway**.

Proper code-signing + notarization removes both warnings and is a later step:
add an Apple Developer cert + notarization secrets, and a Windows signing
certificate, to the CI action.

## Local Zotero integration

Version 0.3.0 can read the complete local Zotero graph without requiring Zotero
cloud storage: personal and group libraries, nested collections, executable
saved searches, every item type, notes, PDF annotations, tags, relations,
full-text indexes, and local attachments. The first sync creates a versioned
SQLite mirror; later syncs use Zotero library versions and deletion cursors.
The mirror remains browsable when Zotero is closed.

Pharos never reads or writes `zotero.sqlite` directly. It uses Zotero's official
loopback Local API and treats Zotero as the source of truth. Real attachment
paths stay inside the Rust process. The UI receives opaque identifiers and reads
validated PDF byte ranges through `pharos-local://`. A PDF is copied to Pharos
only after an explicit **Import to Pharos** action.

Local API write-back is intentionally disabled. The bundled Zotero Connector is
a hardened transport preview whose read, realtime, and write capabilities stay
off until pairing, Notifier integration, conflict handling, and transactional
write tests are complete.

## What can be added later without changing the UI

The shared interface can progressively gain Keychain-backed tokens, Finder
drag-and-drop and `.pdf` file associations, native menus and shortcuts, system
notifications, auto-update, and a tested opt-in Zotero write-back provider.
