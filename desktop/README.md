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
    icons/               # generated from site/assets/mark.png
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

A production build bakes `VITE_API_BASE` from `frontend/.env.desktop` (default
`http://127.0.0.1:8848/api` — the local backend). Change that file to point at a
deployed backend, or make it runtime-configurable in Settings later. The CSP in
`tauri.conf.json` (`connect-src`) must list every backend origin the app is
allowed to reach — add the production origin there when it exists.

## Build a distributable

```bash
npm run build:universal   # -> src-tauri/target/universal-apple-darwin/release/bundle/dmg/Pharos_*.dmg
```

CI (`.github/workflows/desktop-release.yml`) does this on a macOS runner and
attaches the universal `.dmg` to a **draft** GitHub Release. Trigger it by
pushing a tag like `desktop-v0.1.0`, or run it by hand from the Actions tab.

## Installing an unsigned build (Gatekeeper)

The `.dmg` is **unsigned** — there is no Apple Developer account ($99/yr). macOS
Gatekeeper will refuse it on first open ("Pharos is damaged / cannot be
verified"). To install anyway:

- **right-click** `Pharos.app` → **Open** (once), or
- `xattr -cr /Applications/Pharos.app`

Neither is a security bypass of the app itself — it just tells macOS you trust a
build that wasn't notarized by Apple. Proper code-signing + notarization (which
removes the warning entirely) is a later step and needs an Apple Developer
account; wire the certificate/notarization secrets into the CI action then.

## What the desktop shell can add later (without changing the UI)

The appearance stays identical; these are non-visual capabilities Tauri unlocks:
local Zotero access (`localhost:23119`, which the browser can't reach), token
storage in the macOS Keychain instead of `localStorage`, Finder drag-and-drop
and `.pdf` file associations, native menus/shortcuts, system notifications
(translation done, daily digest ready), and auto-update.
