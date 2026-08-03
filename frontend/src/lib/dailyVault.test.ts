import { describe, expect, it } from "vitest";

import { readDailyVaultManifest, type DailyVaultLocation } from "./dailyVault";

/**
 * The manifest reader is the only place a Daily Vault directory's contents are
 * trusted, and everything it accepts is later used to open more files. Driving
 * it through a fake directory handle rather than exporting the private
 * validators keeps the checks pinned at the boundary they actually guard.
 */
type VaultHandle = DailyVaultLocation["handle"];

/** An in-memory File System Access directory, keyed by full relative path. */
function fakeVault(files: Record<string, string>): DailyVaultLocation {
  const dir = (prefix: string): VaultHandle => ({
    name: prefix,
    async getDirectoryHandle(name: string) {
      return dir(prefix === "" ? name : `${prefix}/${name}`);
    },
    async getFileHandle(name: string) {
      const path = prefix === "" ? name : `${prefix}/${name}`;
      const text = files[path];
      // The real handle's absence signal, which `readDailyVaultManifest`
      // distinguishes from a read failure.
      if (text === undefined) throw new DOMException(`${path} missing`, "NotFoundError");
      return {
        async getFile() {
          return new File([text], name);
        },
        async createWritable(): Promise<never> {
          throw new Error("the manifest reader must never write");
        },
      };
    },
  });
  return { name: "vault", handle: dir(""), trustedVaultId: null };
}

const SHA = "a".repeat(64);
const OTHER_SHA = "b".repeat(64);

interface ManifestPatch {
  [key: string]: unknown;
}

const manifest = (patch: ManifestPatch = {}): Record<string, string> => ({
  "pharos-vault.json": JSON.stringify({
    kind: "pharos.daily.vault",
    format_version: 1,
    vault_id: "v-1",
    created_at: "2026-08-01T00:00:00.000Z",
    updated_at: "2026-08-02T00:00:00.000Z",
    generator: "pharos-frontend",
    profile: { path: `profiles/${SHA}.json`, sha256: SHA },
    days: [
      { date: "2026-08-01", path: `days/2026/08/01/${OTHER_SHA}.json`, sha256: OTHER_SHA, paper_count: 3 },
    ],
    ...patch,
  }),
});

describe("readDailyVaultManifest", () => {
  it("returns null for a directory that is not a vault", () => {
    // Distinct from throwing: choosing an empty folder is how a vault is
    // created, so "no manifest here" is a normal answer and not an error.
    return expect(readDailyVaultManifest(fakeVault({}))).resolves.toBeNull();
  });

  it("accepts a well-formed v1 manifest", async () => {
    const read = await readDailyVaultManifest(fakeVault(manifest()));
    expect(read?.vault_id).toBe("v-1");
    expect(read?.days).toHaveLength(1);
  });

  it("refuses a format it does not understand", async () => {
    // A future format_version read with v1's rules would mis-address every
    // file it names. Failing loudly leaves the directory untouched.
    await expect(readDailyVaultManifest(fakeVault(manifest({ format_version: 2 })))).rejects.toThrow();
    await expect(readDailyVaultManifest(fakeVault(manifest({ kind: "something.else" })))).rejects.toThrow();
  });

  it("refuses a path that would escape the chosen directory", async () => {
    // The manifest names every file the app then opens, and a vault directory
    // can be synced, shared or edited outside Pharos. Without this the reader
    // would follow `..` straight out of the folder the user granted.
    const escapes = manifest({
      days: [{ date: "2026-08-01", path: "../../secrets.json", sha256: OTHER_SHA, paper_count: 1 }],
    });
    await expect(readDailyVaultManifest(fakeVault(escapes))).rejects.toThrow();

    const absolute = manifest({ profile: { path: "/etc/passwd", sha256: SHA } });
    await expect(readDailyVaultManifest(fakeVault(absolute))).rejects.toThrow();
  });

  it("refuses a duplicated date, which would make one day silently win", async () => {
    const day = { path: `days/2026/08/01/${OTHER_SHA}.json`, sha256: OTHER_SHA, paper_count: 3 };
    const duplicated = manifest({
      days: [
        { date: "2026-08-01", ...day },
        { date: "2026-08-01", ...day },
      ],
    });
    await expect(readDailyVaultManifest(fakeVault(duplicated))).rejects.toThrow();
  });

  it("refuses a checksum it could never verify against", async () => {
    // The hash is what makes a day file's contents provable; a malformed one
    // must be rejected up front rather than failing per-file later.
    const badSha = manifest({ profile: { path: "profiles/x.json", sha256: "not-a-hash" } });
    await expect(readDailyVaultManifest(fakeVault(badSha))).rejects.toThrow();
  });
});
