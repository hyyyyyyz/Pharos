# Pharos Desktop (Tauri)

The Pharos desktop app is a thin native shell around **the exact same React
frontend** that ships to the browser (`../frontend`). There is only one UI
codebase — the desktop client loads `frontend/dist` in a native WebView — so the
desktop and web clients are identical by construction, not by matching styles.

It is a **thin client**: like the web app, it talks to the Pharos backend over
the network. It does not bundle the backend or the translation engine.

## Why Tauri (not Electron / native)

- **Native rewrite (SwiftUI) was ruled out** — it could only ever *resemble* the
  web app, never be identical, and would double the UI maintenance.
- **Tauri over Electron** — the app is ~8 MB and idles around 30–50 MB of RAM,
  versus ~150 MB / 200–300 MB for Electron. Tauri's one caveat is that macOS
  uses WebKit (WKWebView) rather than Chromium, so a design leaning on webfonts
  or Chrome-only CSS could render slightly differently. Pharos's design uses
  system fonts and standard CSS (flex/grid/oklch/color-mix), all fully supported
  by WebKit, so the difference is negligible. Even Zotero (Gecko) and Obsidian
  (Electron) are web-tech-in-a-shell — native is only chosen by apps with no web
  twin.

## Layout

```
desktop/
  package.json          # Tauri scripts + @tauri-apps/cli
  src-tauri/
    Cargo.toml          # Rust deps (tauri 2)
    tauri.conf.json      # v2 config; frontendDist -> ../../frontend/dist
    build.rs
    src/{main,lib}.rs    # the shell: opens the window, loads the web UI
    capabilities/default.json  # v2 permissions (window + open-external)
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
Trigger it by pushing a tag like `desktop-v0.2.0`, or run it by hand from the
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

## What the desktop shell can add later (without changing the UI)

The appearance stays identical; these are non-visual capabilities Tauri unlocks:
local Zotero access (`localhost:23119`, which the browser can't reach), token
storage in the macOS Keychain instead of `localStorage`, Finder drag-and-drop
and `.pdf` file associations, native menus/shortcuts, system notifications
(translation done, daily digest ready), and auto-update.
