/**
 * A Daily Vault is a directory the user picked, written by one build and read
 * back by another — possibly after being edited, synced or partially restored
 * by something else entirely. That makes the manifest a trust boundary, and the
 * two things it is trusted to do are exactly what these tests pin: refuse a path
 * that walks out of the chosen directory, and refuse content whose hash no
 * longer matches. Neither failure is visible in a working app; both are only
 * visible in a corrupted vault or a compromised one.
 *
 * The File System Access API has no Node implementation, so the directory is a
 * fake — but the hashing, the manifest parsing and the write ordering are all
 * the real code.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DailyVaultArchive, DailyVaultDay, DailyVaultPaper } from "../api/types";
import type { DailyVaultLocation } from "./dailyVault";
import {
  isTrustedDailyVault,
  readDailyVault,
  readDailyVaultManifest,
  writeDailyVault,
} from "./dailyVault";

/* ------------------------------------------------------------- fake disk */

interface FakeDisk {
  files: Map<string, string>;
  /** Every path closed for writing, in order. */
  writes: string[];
}

type VaultHandle = DailyVaultLocation["handle"];

function directory(disk: FakeDisk, prefix: string): VaultHandle {
  const missing = (path: string): DOMException =>
    new DOMException(`${path} not found`, "NotFoundError");
  return {
    name: prefix === "" ? "vault" : prefix,
    async getDirectoryHandle(name, options) {
      const path = `${prefix}${name}/`;
      const known = [...disk.files.keys()].some((file) => file.startsWith(path));
      if (!options?.create && !known) throw missing(path);
      return directory(disk, path);
    },
    async getFileHandle(name, options) {
      const path = `${prefix}${name}`;
      if (!options?.create && !disk.files.has(path)) throw missing(path);
      return {
        async getFile() {
          const contents = disk.files.get(path);
          if (contents === undefined) throw missing(path);
          return new File([contents], name);
        },
        async createWritable() {
          let pending = "";
          return {
            async write(data: string) {
              pending += data;
            },
            async close() {
              disk.files.set(path, pending);
              disk.writes.push(path);
            },
          };
        },
      };
    },
    async queryPermission() {
      return "granted";
    },
  };
}

function mount(files: Record<string, string> = {}): {
  disk: FakeDisk;
  location: DailyVaultLocation;
} {
  const disk: FakeDisk = { files: new Map(Object.entries(files)), writes: [] };
  return {
    disk,
    location: { name: "vault", handle: directory(disk, ""), trustedVaultId: null },
  };
}

/* -------------------------------------------------------------- fixtures */

const SHA_PROFILE = "a".repeat(64);
const SHA_DAY = "b".repeat(64);

const manifestJson = (over: Record<string, unknown> = {}): string =>
  JSON.stringify({
    $schema:
      "https://raw.githubusercontent.com/hyyyyyyz/Pharos/main/schemas/daily-vault/v1/manifest.schema.json",
    kind: "pharos.daily.vault",
    format_version: 1,
    vault_id: "vault-1",
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-02T00:00:00.000Z",
    generator: "pharos-test",
    profile: { path: `profiles/${SHA_PROFILE}.json`, sha256: SHA_PROFILE },
    days: [
      {
        date: "2026-01-01",
        path: `days/2026/01/01/${SHA_DAY}.json`,
        sha256: SHA_DAY,
        paper_count: 1,
      },
    ],
    ...over,
  });

const paperFixture = (date: string, rank: number): DailyVaultPaper => ({
  kind: "pharos.daily.paper",
  schema_version: 1,
  arxiv_id: `2601.0000${rank}`,
  date,
  rank,
  title: "A paper",
  authors: ["A. Vaswani"],
  abstract: null,
  categories: ["cs.CL"],
  matched_direction: null,
  matched_keywords: [],
  arxiv_url: null,
  pdf_url: null,
  published_at: null,
  venue: null,
  read_status: "pending",
  summary_zh: null,
  highlights: null,
  scores: null,
  read_model: null,
  read_at: null,
  read_error: null,
  created_at: `${date}T00:00:00Z`,
});

const dayFixture = (date: string, papers: number): DailyVaultDay => ({
  kind: "pharos.daily.issue",
  schema_version: 1,
  date,
  run: null,
  papers: Array.from({ length: papers }, (_, i) => paperFixture(date, i)),
});

const archive = (days: DailyVaultDay[]): DailyVaultArchive => ({
  kind: "pharos.daily.archive",
  schema_version: 1,
  vault_id: null,
  exported_at: "2026-01-03T00:00:00.000Z",
  generator: "pharos-test",
  profile: {
    kind: "pharos.daily.profile",
    schema_version: 1,
    timezone: null,
    settings: {
      kind: "pharos.daily.settings",
      schema_version: 1,
      categories: ["cs.CL"],
      max_per_day: 20,
      enabled: true,
    },
    directions: [{ name: "VLA", keywords: ["vla"], enabled: true, position: 0 }],
    updated_at: null,
  },
  days,
});

beforeEach(() => {
  // `rememberConnection` writes the vault id straight to localStorage without a
  // guard, so the write path needs one to exist.
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string): string | null => store.get(key) ?? null,
    setItem: (key: string, value: string): void => void store.set(key, value),
    removeItem: (key: string): void => void store.delete(key),
  });
});

afterEach(() => vi.unstubAllGlobals());

/* ----------------------------------------------------------------- tests */

describe("readDailyVaultManifest", () => {
  it("treats a directory with no manifest as empty, not broken", async () => {
    // Picking a fresh directory to write the first snapshot into is the normal
    // first-run path; it must not surface as an error.
    const { location } = mount();
    expect(await readDailyVaultManifest(location)).toBeNull();
  });

  it("reads a well-formed manifest", async () => {
    const { location } = mount({ "pharos-vault.json": manifestJson() });
    const manifest = await readDailyVaultManifest(location);
    expect(manifest?.vault_id).toBe("vault-1");
    expect(manifest?.days).toHaveLength(1);
  });

  it("rejects a directory that is not a v1 vault", async () => {
    const wrongKind = mount({ "pharos-vault.json": manifestJson({ kind: "something.else" }) });
    const wrongVersion = mount({ "pharos-vault.json": manifestJson({ format_version: 2 }) });

    await expect(readDailyVaultManifest(wrongKind.location)).rejects.toThrow(/受支持/);
    await expect(readDailyVaultManifest(wrongVersion.location)).rejects.toThrow(/受支持/);
  });

  it("rejects a manifest path that would escape the chosen directory", async () => {
    // The manifest names every file that gets read. Without this check a vault
    // handed to the user could point at anything the directory handle can
    // reach, and the reader would dutifully open it.
    const traversal = mount({
      "pharos-vault.json": manifestJson({
        days: [
          {
            date: "2026-01-01",
            path: "days/../../../secrets.json",
            sha256: SHA_DAY,
            paper_count: 1,
          },
        ],
      }),
    });
    const absolute = mount({
      "pharos-vault.json": manifestJson({
        profile: { path: "/etc/passwd", sha256: SHA_PROFILE },
      }),
    });

    await expect(readDailyVaultManifest(traversal.location)).rejects.toThrow(/不安全路径/);
    await expect(readDailyVaultManifest(absolute.location)).rejects.toThrow(/不安全路径/);
  });

  it("rejects a checksum that is not a SHA-256 digest", async () => {
    const { location } = mount({
      "pharos-vault.json": manifestJson({ profile: { path: "profiles/p.json", sha256: "nope" } }),
    });
    await expect(readDailyVaultManifest(location)).rejects.toThrow(/校验值错误/);
  });

  it("rejects a day index that lists the same date twice", async () => {
    // Two entries for one date means two different files claim to be that day;
    // there is no honest way to pick one.
    const { location } = mount({
      "pharos-vault.json": manifestJson({
        days: [
          { date: "2026-01-01", path: `days/a/${SHA_DAY}.json`, sha256: SHA_DAY, paper_count: 1 },
          { date: "2026-01-01", path: `days/b/${SHA_DAY}.json`, sha256: SHA_DAY, paper_count: 1 },
        ],
      }),
    });
    await expect(readDailyVaultManifest(location)).rejects.toThrow(/重复/);
  });
});

describe("writeDailyVault", () => {
  it("writes the manifest last, so a failed write leaves the old one usable", async () => {
    const { disk, location } = mount();

    await writeDailyVault(location, archive([dayFixture("2026-01-01", 1)]));

    expect(disk.writes[disk.writes.length - 1]).toBe("pharos-vault.json");
    expect(disk.writes).toHaveLength(3); // profile, one day, manifest
  });

  it("addresses every file by the hash of its contents", async () => {
    const { disk, location } = mount();

    const manifest = await writeDailyVault(location, archive([dayFixture("2026-01-01", 2)]));

    expect(manifest.days[0]!.path).toBe(`days/2026/01/01/${manifest.days[0]!.sha256}.json`);
    expect(manifest.profile.path).toBe(`profiles/${manifest.profile.sha256}.json`);
    expect(disk.files.has(manifest.days[0]!.path)).toBe(true);
  });

  it("indexes days newest first", async () => {
    const { location } = mount();

    const manifest = await writeDailyVault(
      location,
      archive([dayFixture("2026-01-01", 1), dayFixture("2026-01-03", 1), dayFixture("2026-01-02", 1)]),
    );

    expect(manifest.days.map((d) => d.date)).toEqual(["2026-01-03", "2026-01-02", "2026-01-01"]);
  });

  it("keeps the vault's identity across snapshots", async () => {
    // A new vault id on every save would make each snapshot look like a
    // different vault to the trust check, and re-prompt the user forever.
    const { location } = mount();
    const first = await writeDailyVault(location, archive([dayFixture("2026-01-01", 1)]));
    const second = await writeDailyVault(location, archive([dayFixture("2026-01-02", 1)]), first);

    expect(second.vault_id).toBe(first.vault_id);
    expect(second.created_at).toBe(first.created_at);
    // The manifest is a whole snapshot, not an append: it indexes what this
    // write produced, and the previous day's file survives only as an
    // unreferenced blob.
    expect(second.days.map((d) => d.date)).toEqual(["2026-01-02"]);
  });

  it("rejects a day whose date is not a calendar date", async () => {
    const { location } = mount();
    await expect(
      writeDailyVault(location, archive([dayFixture("2026-1-1", 1)])),
    ).rejects.toThrow(/日期无效/);
  });
});

describe("readDailyVault", () => {
  it("round-trips a written vault", async () => {
    const { location } = mount();
    const source = archive([dayFixture("2026-01-01", 2), dayFixture("2026-01-02", 1)]);

    await writeDailyVault(location, source);
    const restored = await readDailyVault(location);

    expect(restored.days.map((d) => d.date)).toEqual(["2026-01-02", "2026-01-01"]);
    expect(restored.days[1]!.papers).toHaveLength(2);
    expect(restored.profile.directions[0]!.name).toBe("VLA");
    // Filled in on the way out, so the archive records where it was written.
    expect(restored.profile.timezone).not.toBeUndefined();
  });

  it("refuses a day file that no longer matches its checksum", async () => {
    // The reason every entry carries a hash: a half-synced or hand-edited file
    // must fail loudly rather than import as a silently truncated day.
    const { disk, location } = mount();
    const manifest = await writeDailyVault(location, archive([dayFixture("2026-01-01", 2)]));
    const path = manifest.days[0]!.path;
    disk.files.set(path, disk.files.get(path)!.replace("A paper", "A tampered paper"));

    await expect(readDailyVault(location)).rejects.toThrow(/校验失败/);
  });

  it("refuses a directory with no manifest at all", async () => {
    const { location } = mount();
    await expect(readDailyVault(location)).rejects.toThrow(/pharos-vault\.json/);
  });
});

describe("isTrustedDailyVault", () => {
  it("trusts only the vault this browser was connected to", async () => {
    // The remembered id is what separates "the directory I have been writing to"
    // from "a directory that merely contains a vault", which is the difference
    // between saving and overwriting someone else's.
    const { location } = mount();
    const manifest = await writeDailyVault(location, archive([dayFixture("2026-01-01", 1)]));

    expect(isTrustedDailyVault(location, manifest)).toBe(false);
    expect(isTrustedDailyVault({ ...location, trustedVaultId: manifest.vault_id }, manifest)).toBe(
      true,
    );
    expect(isTrustedDailyVault({ ...location, trustedVaultId: "other" }, manifest)).toBe(false);
  });
});
