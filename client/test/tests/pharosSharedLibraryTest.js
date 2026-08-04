"use strict";

/**
 * The rule that keeps a shared library openable by the application that owns it.
 *
 * Pharos and Zotero read and write the same database. Zotero's own rule is "this
 * database is older than me, so upgrade it", and that upgrade is ONE-WAY: once
 * raised, the Zotero the user actually runs refuses to open its own data. There
 * is no undo and no warning -- it happens on the first launch after any drift
 * between the two builds.
 *
 * So the policy is exact-match-or-refuse, and refusing is the correct outcome:
 * a user who cannot open Pharos today still has their library and their Zotero.
 * A user whose library was migrated has neither until someone ships a fix.
 *
 * These tests exist because the guard cannot be observed from outside. Watching
 * a real library survive a launch proves nothing on its own -- a guard that
 * never ran and a guard that fired look identical from the filesystem, and the
 * first version of this one WAS silently inert: it was written with optional
 * chaining, so a module that failed to load would have evaluated to `undefined`
 * and let the migration through while appearing to protect it.
 */
describe("Pharos shared library", function () {
	var Shared;

	before(function () {
		Shared = Zotero.Pharos.SharedLibrary;
	});

	describe("#isShared()", function () {
		it("should report that this build shares Zotero's database", function () {
			// Derived from the config rather than a flag, so this also pins that
			// DB_NAME and ID have not been collapsed back into one value -- which
			// is what would happen if someone "simplified" the config and
			// accidentally handed Pharos the `zotero://` URL scheme as well.
			assert.isTrue(Shared.isShared());
			assert.equal(Shared.hostName(), 'Zotero');
		});
	});

	describe("#assertMigrationAllowed()", function () {
		it("should allow an exact match", function () {
			Shared.assertMigrationAllowed(123, 123);
		});

		it("should refuse a library older than this build", function () {
			// The dangerous direction. Zotero would migrate here, and the user's
			// own Zotero could then never open the result.
			var e = null;
			try {
				Shared.assertMigrationAllowed(122, 123);
			}
			catch (err) {
				e = err;
			}
			assert.ok(e, 'an older shared library was accepted -- it would be migrated');
			assert.include(e.message, '122');
			assert.include(e.message, '123');
			// The message has to say what the user loses, not just that a number
			// mismatched: this is the one error where continuing is destructive
			// and stopping is not.
			assert.include(e.message, 'cannot be undone');
		});

		it("should refuse a library newer than this build", function () {
			var e = null;
			try {
				Shared.assertMigrationAllowed(124, 123);
			}
			catch (err) {
				e = err;
			}
			assert.ok(e, 'a newer shared library was accepted');
			assert.include(e.message, 'newer build');
		});

		it("should not refuse anything when the library is not shared", function () {
			// An unshared build is an ordinary Zotero-derived application with its
			// own database, and migrating its own data is exactly what it should
			// do. Stubbed rather than reconfigured because ZOTERO_CONFIG is read
			// at startup.
			var real = Shared.isShared;
			Shared.isShared = () => false;
			try {
				Shared.assertMigrationAllowed(100, 123);
			}
			finally {
				Shared.isShared = real;
			}
		});
	});

	describe("#sidecarPath()", function () {
		it("should sit beside the shared database, not inside it", function () {
			// Pharos-native records must never become tables in zotero.sqlite:
			// Zotero owns, migrates and syncs that file, and its own integrity
			// check reports tables it does not recognise as damage.
			var p = Shared.sidecarPath();
			assert.include(p, Zotero.DataDirectory.dir);
			assert.notInclude(p, 'zotero.sqlite');
			assert.match(p, /pharos-local\.sqlite$/);
		});
	});
});
