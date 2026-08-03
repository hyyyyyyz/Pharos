"use strict";

/**
 * The 数据目录 (Daily Vault).
 *
 * This is the only part of the client that writes into a directory the user
 * chose, which makes two of its properties load-bearing in a way the rest of the
 * module is not, and both of them fail SILENTLY:
 *
 *   - the manifest is written LAST, so a write interrupted partway leaves the
 *     previous snapshot intact and readable rather than a directory that
 *     verifies as broken;
 *   - every path in a manifest is treated as untrusted input, because a vault
 *     directory can be edited, synced, or handed over by someone else.
 *
 * Neither shows up in normal use. A vault that writes its manifest first works
 * perfectly until the one time it is interrupted, and a missing traversal guard
 * does nothing at all until a manifest says `../../.ssh/authorized_keys`.
 */
describe("Pharos Daily Vault", function () {
	var Vault;

	before(function () {
		Vault = Zotero.Pharos.Daily.Vault;
	});

	/** A directory of our own under the profile's temp dir, per test. */
	async function root() {
		let dir = PathUtils.join(
			Zotero.getTempDirectory().path, `vault-${Zotero.Utilities.randomString()}`
		);
		await IOUtils.makeDirectory(dir);
		return dir;
	}

	/**
	 * The smallest thing write() accepts, and that read() will accept back.
	 *
	 * `kind` and `schema_version` are not decoration: read() rejects a file
	 * lacking them, so a fixture without them exercises the rejection path
	 * instead of the round trip it was written to test.
	 */
	function archive({ days = [] } = {}) {
		return {
			profile: {
				kind: 'pharos.daily.profile',
				schema_version: 1,
				categories: ['cs.AI'],
				max_per_day: 20,
				enabled: true,
				directions: [],
			},
			days,
		};
	}

	function day(date, papers) {
		return {
			kind: 'pharos.daily.issue',
			schema_version: 1,
			date,
			papers: papers || [{ id: 'p1', title: 'A paper' }],
		};
	}

	/**
	 * Assert that a promise rejects.
	 *
	 * Written out rather than assert.isRejected, which this harness does not
	 * have -- chai-as-promised is not among the plugins test/content/support.js
	 * loads, so the call fails on the assertion itself rather than on what it
	 * was asserting, and the message names chai instead of the vault.
	 */
	async function rejects(promise, message) {
		try {
			await promise;
		}
		catch (e) {
			return e;
		}
		assert.fail(message || 'expected the promise to reject, and it resolved');
		return null;
	}

	describe("#safeRelativePath()", function () {
		// Each of these is a way out of the chosen directory, and the last two are
		// the ones a guard written against "does it contain .." misses.
		var REJECTED = [
			['..', 'the parent itself'],
			['../secrets.json', 'a leading parent'],
			['days/../../secrets.json', 'a parent in the middle'],
			['/etc/passwd', 'an absolute path needs no ".." at all'],
			['days\\..\\..\\secrets.json', 'a backslash is a separator on Windows'],
			['days/\0/x.json', 'a NUL truncates the path for whatever consumes it next'],
			['days//x.json', 'an empty component'],
			['./x.json', 'a bare "." component'],
			['', 'the empty path'],
			[null, 'a non-string'],
		];

		for (let [path, why] of REJECTED) {
			it(`should refuse ${JSON.stringify(path)} -- ${why}`, function () {
				assert.throws(() => Vault.safeRelativePath(path));
			});
		}

		it("should accept the paths the format actually uses", function () {
			assert.deepEqual(Vault.safeRelativePath('pharos-vault.json'),
				['pharos-vault.json']);
			assert.deepEqual(Vault.safeRelativePath('profiles/abc123.json'),
				['profiles', 'abc123.json']);
			assert.deepEqual(Vault.safeRelativePath('days/2026/08/02/abc123.json'),
				['days', '2026', '08', '02', 'abc123.json']);
		});
	});

	describe("#resolve()", function () {
		it("should keep a resolved path inside the root", async function () {
			let dir = await root();
			let resolved = Vault.resolve(dir, 'days/2026/08/02/x.json');
			assert.isTrue(resolved.startsWith(dir),
				'a manifest entry resolved outside the directory the user chose');
		});

		it("should refuse an escaping path even though the join would succeed",
			async function () {
				// The containment check and safeRelativePath() are deliberately
				// redundant. This asserts the pair, not either one: if the
				// component blacklist is ever loosened, this is what still holds.
				let dir = await root();
				assert.throws(() => Vault.resolve(dir, '../escaped.json'));
			});
	});

	describe("#write()", function () {
		it("should write the manifest last", async function () {
			// The ordering IS the crash-safety guarantee: until the manifest names
			// them, the new content files are unreferenced bytes and the previous
			// snapshot is still the one a reader finds. Asserting the final state
			// cannot show this -- a manifest-first implementation ends up looking
			// identical -- so this watches the order the writes happen in.
			let dir = await root();
			let order = [];
			let realWrite = Vault.writeText;
			Vault.writeText = async function (r, path, text) {
				order.push(path);
				return realWrite.call(Vault, r, path, text);
			};
			try {
				await Vault.write(dir, archive({ days: [day('2026-08-02')] }));
			}
			finally {
				Vault.writeText = realWrite;
			}

			assert.isAbove(order.length, 1, 'nothing but the manifest was written');
			assert.equal(order[order.length - 1], Vault.MANIFEST_NAME,
				'the manifest was not written last, so an interrupted write leaves '
				+ 'a manifest naming files that may not exist yet');
		});

		it("should leave the previous snapshot readable when a write is interrupted",
			async function () {
				let dir = await root();
				await Vault.write(dir, archive({ days: [day('2026-08-01')] }));
				let before = await Vault.read(dir);
				assert.equal(before.days.length, 1);

				// Fail after the content files and before the manifest -- the
				// window the ordering exists to make survivable.
				let realWrite = Vault.writeText;
				Vault.writeText = async function (r, path, text) {
					if (path == Vault.MANIFEST_NAME) {
						throw new Error('disk full');
					}
					return realWrite.call(Vault, r, path, text);
				};
				try {
					await rejects(
						Vault.write(dir, archive({ days: [day('2026-08-02')] })),
						'the write reported success despite failing to commit'
					);
				}
				finally {
					Vault.writeText = realWrite;
				}

				let after = await Vault.read(dir);
				assert.equal(after.days.length, 1,
					'the interrupted write took the previous snapshot with it');
				assert.equal(after.days[0].date, '2026-08-01');
			});

		it("should keep the vault id across snapshots", async function () {
			// The id is what lets a directory be recognised as the same vault
			// after it moves. A new one per write would make every restore look
			// like a restore from somebody else's backup.
			let dir = await root();
			let first = await Vault.write(dir, archive({ days: [day('2026-08-01')] }));
			let manifest = await Vault.readManifest(dir);
			let second = await Vault.write(
				dir, archive({ days: [day('2026-08-02')] }), manifest
			);
			assert.equal(second.vault_id, first.vault_id);
		});
	});

	describe("#read()", function () {
		it("should refuse a manifest whose content does not hash to its name",
			async function () {
				// The whole point of content-addressing here is that corruption is
				// detectable. A vault directory lives in a sync folder, on a USB
				// stick, or on a disk that is starting to fail.
				let dir = await root();
				await Vault.write(dir, archive({ days: [day('2026-08-02')] }));
				let manifest = await Vault.readManifest(dir);
				let entry = manifest.days[0];

				await IOUtils.writeUTF8(
					Vault.resolve(dir, entry.path),
					JSON.stringify({ date: '2026-08-02', papers: [] })
				);
				await rejects(Vault.read(dir),
					'a tampered day file was accepted as genuine');
			});

		it("should report an empty directory as no vault rather than as damage",
			async function () {
				// A directory the user just chose is not a broken vault, and
				// telling them it is would be a reason not to trust the next
				// message this feature shows them.
				let dir = await root();
				assert.isNull(await Vault.readManifest(dir));
			});
	});
});
