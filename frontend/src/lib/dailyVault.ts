import type { DailyVaultArchive, DailyVaultDay, DailyVaultProfile } from "../api/types";

const MANIFEST_NAME = "pharos-vault.json";
const MANIFEST_SCHEMA =
  "https://raw.githubusercontent.com/hyyyyyyz/Pharos/main/schemas/daily-vault/v1/manifest.schema.json";
const CONNECTION_KEY = "pharos.daily.vault.connection.v1";
const HANDLE_DB = "pharos-daily-vault";
const HANDLE_STORE = "handles";
const HANDLE_KEY = "active";
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const SHA_RE = /^[a-f0-9]{64}$/;

interface BrowserWritable {
  write(data: string): Promise<void>;
  close(): Promise<void>;
}

interface BrowserFileHandle {
  getFile(): Promise<File>;
  createWritable(): Promise<BrowserWritable>;
}

interface BrowserDirectoryHandle {
  readonly name: string;
  getDirectoryHandle(
    name: string,
    options?: { create?: boolean },
  ): Promise<BrowserDirectoryHandle>;
  getFileHandle(name: string, options?: { create?: boolean }): Promise<BrowserFileHandle>;
  queryPermission?(options: { mode: "readwrite" }): Promise<PermissionState>;
  requestPermission?(options: { mode: "readwrite" }): Promise<PermissionState>;
}

interface BrowserPickerWindow extends Window {
  showDirectoryPicker?: (options: {
    id: string;
    mode: "readwrite";
    startIn: "documents";
  }) => Promise<BrowserDirectoryHandle>;
  __TAURI_INTERNALS__?: unknown;
}

export type DailyVaultLocation =
  | {
      kind: "browser";
      name: string;
      handle: BrowserDirectoryHandle;
      trustedVaultId: string | null;
    }
  | {
      kind: "tauri";
      name: string;
      path: string;
      trustedVaultId: string | null;
      /** The standard `daily/` directory inside the active Pharos Workspace. */
      managed?: boolean;
    };

export interface DailyVaultManifestEntry {
  path: string;
  sha256: string;
}

export interface DailyVaultManifestDay extends DailyVaultManifestEntry {
  date: string;
  paper_count: number;
}

export interface DailyVaultManifest {
  $schema: typeof MANIFEST_SCHEMA;
  kind: "pharos.daily.vault";
  format_version: 1;
  vault_id: string;
  created_at: string;
  updated_at: string;
  generator: string;
  profile: DailyVaultManifestEntry;
  days: DailyVaultManifestDay[];
}

interface RememberedConnection {
  kind: "browser" | "tauri";
  vaultId: string;
  path?: string;
  managed?: boolean;
}

const pickerWindow = (): BrowserPickerWindow => window as BrowserPickerWindow;
const isTauri = (): boolean => pickerWindow().__TAURI_INTERNALS__ !== undefined;

function safeRelativePath(path: string): string[] {
  const parts = path.split("/");
  if (
    path.startsWith("/") ||
    path.includes("\\") ||
    parts.length === 0 ||
    parts.some((part) => !part || part === "." || part === "..")
  ) {
    throw new Error(`Daily Vault 包含不安全路径：${path}`);
  }
  return parts;
}

function parseConnection(): RememberedConnection | null {
  try {
    const raw = localStorage.getItem(CONNECTION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<RememberedConnection>;
    if (
      (value.kind !== "browser" && value.kind !== "tauri") ||
      typeof value.vaultId !== "string"
    ) {
      return null;
    }
    return value as RememberedConnection;
  } catch {
    return null;
  }
}

function rememberConnection(location: DailyVaultLocation, vaultId: string): void {
  const value: RememberedConnection =
    location.kind === "tauri"
      ? { kind: "tauri", path: location.path, vaultId, managed: location.managed === true }
      : { kind: "browser", vaultId };
  localStorage.setItem(CONNECTION_KEY, JSON.stringify(value));
}

function openHandleDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(HANDLE_DB, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(HANDLE_STORE)) {
        request.result.createObjectStore(HANDLE_STORE);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("无法打开目录授权存储"));
  });
}

async function saveBrowserHandle(handle: BrowserDirectoryHandle): Promise<void> {
  const db = await openHandleDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(HANDLE_STORE, "readwrite");
    tx.objectStore(HANDLE_STORE).put(handle, HANDLE_KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("无法保存目录授权"));
  });
  db.close();
}

async function loadBrowserHandle(): Promise<BrowserDirectoryHandle | null> {
  if (!("indexedDB" in window)) return null;
  try {
    const db = await openHandleDb();
    const handle = await new Promise<BrowserDirectoryHandle | null>((resolve, reject) => {
      const tx = db.transaction(HANDLE_STORE, "readonly");
      const request = tx.objectStore(HANDLE_STORE).get(HANDLE_KEY);
      request.onsuccess = () => resolve((request.result as BrowserDirectoryHandle) ?? null);
      request.onerror = () => reject(request.error ?? new Error("无法读取目录授权"));
    });
    db.close();
    return handle;
  } catch {
    return null;
  }
}

async function clearBrowserHandle(): Promise<void> {
  if (!("indexedDB" in window)) return;
  try {
    const db = await openHandleDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(HANDLE_STORE, "readwrite");
      tx.objectStore(HANDLE_STORE).delete(HANDLE_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error("无法清除目录授权"));
    });
    db.close();
  } catch {
    // A stale IndexedDB entry is harmless after the connection metadata is gone.
  }
}

async function browserDirectory(
  root: BrowserDirectoryHandle,
  parts: string[],
  create: boolean,
): Promise<BrowserDirectoryHandle> {
  let current = root;
  for (const part of parts) {
    current = await current.getDirectoryHandle(part, { create });
  }
  return current;
}

async function browserRead(root: BrowserDirectoryHandle, path: string): Promise<string> {
  const parts = safeRelativePath(path);
  const filename = parts.pop();
  if (!filename) throw new Error("文件路径缺少文件名");
  const directory = await browserDirectory(root, parts, false);
  const file = await (await directory.getFileHandle(filename)).getFile();
  return file.text();
}

async function browserWrite(
  root: BrowserDirectoryHandle,
  path: string,
  contents: string,
): Promise<void> {
  const parts = safeRelativePath(path);
  const filename = parts.pop();
  if (!filename) throw new Error("文件路径缺少文件名");
  const directory = await browserDirectory(root, parts, true);
  const stream = await (await directory.getFileHandle(filename, { create: true })).createWritable();
  await stream.write(contents);
  await stream.close();
}

async function browserExists(root: BrowserDirectoryHandle, path: string): Promise<boolean> {
  try {
    await browserRead(root, path);
    return true;
  } catch (error) {
    if (error instanceof DOMException && error.name === "NotFoundError") return false;
    throw error;
  }
}

async function tauriPath(root: string, relative: string): Promise<string> {
  const { join } = await import("@tauri-apps/api/path");
  return join(root, ...safeRelativePath(relative));
}

async function tauriRead(root: string, path: string): Promise<string> {
  const { readTextFile } = await import("@tauri-apps/plugin-fs");
  return readTextFile(await tauriPath(root, path));
}

async function tauriWrite(root: string, path: string, contents: string): Promise<void> {
  const { mkdir, writeTextFile } = await import("@tauri-apps/plugin-fs");
  const parts = safeRelativePath(path);
  parts.pop();
  if (parts.length > 0) await mkdir(await tauriPath(root, parts.join("/")), { recursive: true });
  await writeTextFile(await tauriPath(root, path), contents);
}

async function tauriExists(root: string, path: string): Promise<boolean> {
  const { exists } = await import("@tauri-apps/plugin-fs");
  return exists(await tauriPath(root, path));
}

async function readText(location: DailyVaultLocation, path: string): Promise<string> {
  return location.kind === "browser"
    ? browserRead(location.handle, path)
    : tauriRead(location.path, path);
}

async function writeText(
  location: DailyVaultLocation,
  path: string,
  contents: string,
): Promise<void> {
  if (location.kind === "browser") await browserWrite(location.handle, path, contents);
  else await tauriWrite(location.path, path, contents);
}

async function exists(location: DailyVaultLocation, path: string): Promise<boolean> {
  return location.kind === "browser"
    ? browserExists(location.handle, path)
    : tauriExists(location.path, path);
}

function encodeJson(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}

function assertEntry(value: unknown, label: string): asserts value is DailyVaultManifestEntry {
  if (!value || typeof value !== "object") throw new Error(`${label} 缺失`);
  const entry = value as Partial<DailyVaultManifestEntry>;
  if (typeof entry.path !== "string" || typeof entry.sha256 !== "string") {
    throw new Error(`${label} 格式错误`);
  }
  safeRelativePath(entry.path);
  if (!SHA_RE.test(entry.sha256)) throw new Error(`${label} 校验值错误`);
}

function parseManifest(text: string): DailyVaultManifest {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error("pharos-vault.json 不是有效 JSON");
  }
  if (!raw || typeof raw !== "object") throw new Error("Daily Vault 清单格式错误");
  const value = raw as Partial<DailyVaultManifest>;
  if (value.kind !== "pharos.daily.vault" || value.format_version !== 1) {
    throw new Error("该目录不是受支持的 Pharos Daily Vault v1");
  }
  if (
    typeof value.vault_id !== "string" ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string" ||
    typeof value.generator !== "string"
  ) {
    throw new Error("Daily Vault 清单缺少必要字段");
  }
  assertEntry(value.profile, "profile");
  if (!Array.isArray(value.days) || value.days.length > 3_660) {
    throw new Error("Daily Vault 日期索引无效");
  }
  const dates = new Set<string>();
  for (const item of value.days) {
    assertEntry(item, "day");
    if (
      !DATE_RE.test(item.date) ||
      !Number.isInteger(item.paper_count) ||
      item.paper_count < 0 ||
      item.paper_count > 500 ||
      dates.has(item.date)
    ) {
      throw new Error("Daily Vault 日期记录无效或重复");
    }
    dates.add(item.date);
  }
  return value as DailyVaultManifest;
}

async function verifiedJson<T>(
  location: DailyVaultLocation,
  entry: DailyVaultManifestEntry,
  label: string,
): Promise<T> {
  const text = await readText(location, entry.path);
  if ((await sha256(text)) !== entry.sha256) throw new Error(`${label} 校验失败，文件可能已损坏`);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`${label} 不是有效 JSON`);
  }
}

function assertProfile(value: DailyVaultProfile): void {
  if (value.kind !== "pharos.daily.profile" || value.schema_version !== 1) {
    throw new Error("Daily Vault 用户配置版本不受支持");
  }
}

function assertDay(value: DailyVaultDay, expectedDate: string): void {
  if (
    value.kind !== "pharos.daily.issue" ||
    value.schema_version !== 1 ||
    value.date !== expectedDate ||
    !Array.isArray(value.papers)
  ) {
    throw new Error(`${expectedDate} 的每日论文文件格式错误`);
  }
}

export function writableDailyDirectorySupported(): boolean {
  return isTauri() || typeof pickerWindow().showDirectoryPicker === "function";
}

export async function chooseDailyVaultDirectory(): Promise<DailyVaultLocation> {
  if (isTauri()) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const selected = await open({ directory: true, multiple: false, title: "选择 Daily Vault 目录" });
    if (typeof selected !== "string") throw new Error("未选择目录");
    const segments = selected.split(/[\\/]/).filter(Boolean);
    const name = segments[segments.length - 1] ?? selected;
    return { kind: "tauri", name, path: selected, trustedVaultId: null };
  }

  const picker = pickerWindow().showDirectoryPicker;
  if (!picker) throw new Error("当前浏览器不支持持续写入本地目录，请使用 JSON 备份");
  const handle = await picker({ id: "pharos-daily-vault", mode: "readwrite", startIn: "documents" });
  await saveBrowserHandle(handle);
  return { kind: "browser", name: handle.name, handle, trustedVaultId: null };
}

export async function loadRememberedDailyVault(): Promise<DailyVaultLocation | null> {
  const saved = parseConnection();
  if (!saved && isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    const workspace = await invoke<{ dailyPath: string }>("workspace_status");
    return {
      kind: "tauri",
      name: "Pharos Workspace",
      path: workspace.dailyPath,
      trustedVaultId: null,
      managed: true,
    };
  }
  if (!saved) return null;
  if (saved.kind === "tauri" && isTauri() && saved.path) {
    let path = saved.path;
    if (saved.managed) {
      const { invoke } = await import("@tauri-apps/api/core");
      path = (await invoke<{ dailyPath: string }>("workspace_status")).dailyPath;
    }
    const segments = path.split(/[\\/]/).filter(Boolean);
    const name = saved.managed ? "Pharos Workspace" : (segments[segments.length - 1] ?? path);
    return {
      kind: "tauri",
      name,
      path,
      trustedVaultId: saved.vaultId,
      managed: saved.managed === true,
    };
  }
  if (saved.kind === "browser" && !isTauri()) {
    const handle = await loadBrowserHandle();
    if (handle) {
      return { kind: "browser", name: handle.name, handle, trustedVaultId: saved.vaultId };
    }
  }
  return null;
}

export async function ensureDailyVaultPermission(
  location: DailyVaultLocation,
  request: boolean,
): Promise<boolean> {
  if (location.kind === "tauri") return true;
  const query = location.handle.queryPermission?.bind(location.handle);
  const current = query ? await query({ mode: "readwrite" }) : "prompt";
  if (current === "granted") return true;
  if (!request || !location.handle.requestPermission) return false;
  return (await location.handle.requestPermission({ mode: "readwrite" })) === "granted";
}

export async function readDailyVaultManifest(
  location: DailyVaultLocation,
): Promise<DailyVaultManifest | null> {
  if (!(await exists(location, MANIFEST_NAME))) return null;
  return parseManifest(await readText(location, MANIFEST_NAME));
}

export async function writeDailyVault(
  location: DailyVaultLocation,
  source: DailyVaultArchive,
  previous?: DailyVaultManifest | null,
): Promise<DailyVaultManifest> {
  if (!(await ensureDailyVaultPermission(location, true))) throw new Error("没有目录写入权限");
  const now = new Date().toISOString();
  const vaultId = previous?.vault_id ?? source.vault_id ?? crypto.randomUUID();
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  const profile: DailyVaultProfile = { ...source.profile, timezone };
  const profileText = encodeJson(profile);
  const profileSha = await sha256(profileText);
  const profilePath = `profiles/${profileSha}.json`;
  await writeText(location, profilePath, profileText);

  const days: DailyVaultManifestDay[] = [];
  for (const day of [...source.days].sort((a, b) => b.date.localeCompare(a.date))) {
    if (!DATE_RE.test(day.date)) throw new Error(`每日论文日期无效：${day.date}`);
    const dayText = encodeJson(day);
    const daySha = await sha256(dayText);
    const [year, month, date] = day.date.split("-");
    const path = `days/${year}/${month}/${date}/${daySha}.json`;
    await writeText(location, path, dayText);
    days.push({ date: day.date, path, sha256: daySha, paper_count: day.papers.length });
  }

  const manifest: DailyVaultManifest = {
    $schema: MANIFEST_SCHEMA,
    kind: "pharos.daily.vault",
    format_version: 1,
    vault_id: vaultId,
    created_at: previous?.created_at ?? now,
    updated_at: now,
    generator: source.generator,
    profile: { path: profilePath, sha256: profileSha },
    days,
  };
  // Manifest last: it is the commit marker. Every path it references already
  // exists and has a content hash, so a failed write leaves the previous
  // manifest fully usable instead of pointing at a half-written snapshot.
  await writeText(location, MANIFEST_NAME, encodeJson(manifest));
  rememberConnection(location, vaultId);
  return manifest;
}

export async function readDailyVault(location: DailyVaultLocation): Promise<DailyVaultArchive> {
  if (!(await ensureDailyVaultPermission(location, true))) throw new Error("没有目录读取权限");
  const manifest = await readDailyVaultManifest(location);
  if (!manifest) throw new Error("所选目录中没有 pharos-vault.json");

  const profile = await verifiedJson<DailyVaultProfile>(location, manifest.profile, "用户配置");
  assertProfile(profile);
  const days: DailyVaultDay[] = [];
  for (const entry of manifest.days) {
    const day = await verifiedJson<DailyVaultDay>(location, entry, entry.date);
    assertDay(day, entry.date);
    if (day.papers.length !== entry.paper_count) throw new Error(`${entry.date} 的论文数量校验失败`);
    days.push(day);
  }
  return {
    kind: "pharos.daily.archive",
    schema_version: 1,
    vault_id: manifest.vault_id,
    exported_at: manifest.updated_at,
    generator: manifest.generator,
    profile,
    days,
  };
}

export function downloadDailyVaultJson(source: DailyVaultArchive): void {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  const archive: DailyVaultArchive = {
    ...source,
    vault_id: source.vault_id ?? crypto.randomUUID(),
    profile: { ...source.profile, timezone },
  };
  const blob = new Blob([encodeJson(archive)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `pharos-daily-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function chooseDailyVaultJson(): Promise<DailyVaultArchive> {
  return new Promise((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json,application/json";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) {
        reject(new Error("未选择备份文件"));
        return;
      }
      try {
        const value = JSON.parse(await file.text()) as DailyVaultArchive;
        if (value.kind !== "pharos.daily.archive" || value.schema_version !== 1) {
          throw new Error("该文件不是受支持的 Pharos Daily Vault v1 备份");
        }
        resolve(value);
      } catch (error) {
        reject(error instanceof Error ? error : new Error("无法读取备份文件"));
      }
    };
    input.click();
  });
}

export function isTrustedDailyVault(
  location: DailyVaultLocation,
  manifest: DailyVaultManifest,
): boolean {
  return location.trustedVaultId === manifest.vault_id;
}

export function trustDailyVault(
  location: DailyVaultLocation,
  manifest: DailyVaultManifest,
): DailyVaultLocation {
  rememberConnection(location, manifest.vault_id);
  return { ...location, trustedVaultId: manifest.vault_id };
}

export async function forgetDailyVaultConnection(): Promise<void> {
  localStorage.removeItem(CONNECTION_KEY);
  await clearBrowserHandle();
}
