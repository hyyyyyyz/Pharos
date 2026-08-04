/*
    ***** BEGIN LICENSE BLOCK *****

    Copyright © 2026 Pharos Contributors
                     https://pharos.selab.top

    This file is part of Pharos, which is derived from Zotero.

    Pharos is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Pharos is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with Pharos.  If not, see <http://www.gnu.org/licenses/>.

    ***** END LICENSE BLOCK *****
*/

/**
 * Whether this client is a guest in Zotero's library, and what that forbids.
 *
 * Pharos and Zotero open the same database -- not at the same time, but either
 * one reading and writing the same papers and collections. That is the product's
 * premise: a user who has to maintain two unconnected libraries has been given
 * two products, not one.
 *
 * Being a guest has exactly one hard consequence, and it is the reason this
 * module exists rather than the check living inline: **a guest never migrates
 * the schema.** Zotero's own rule is "this database is older than me, so upgrade
 * it", and the upgrade is one-way -- once raised, the Zotero the user actually
 * runs refuses to open its own data. Not a warning; the failure itself, on the
 * first launch after any baseline drift.
 *
 * So the schema must match EXACTLY, and refusing to open is the correct answer
 * when it does not. Refusing is recoverable: the user keeps using Zotero while
 * the build is fixed. Migrating is not.
 */
// Not `= {}`: these modules are loaded in a list, and whichever ran second used
// to wipe what the first had attached. The order in zotero.mjs is chosen for
// other reasons -- sharedLibrary has to exist before schema.js asks it anything
// -- so no file here may assume it is the one that creates the namespace.
Zotero.Pharos = Zotero.Pharos || {};

Zotero.Pharos.SharedLibrary = new function () {
	/**
	 * Whether the configured database is Zotero's own.
	 *
	 * Derived from the config rather than stored as a flag, so there is one
	 * source of truth and no way for the two to disagree. `DB_NAME` is what
	 * `Zotero.DB` opens (see xpcom/zotero.js); when it is not this client's own
	 * `ID`, the database belongs to another application and this client is the
	 * guest.
	 *
	 * @return {Boolean}
	 */
	this.isShared = function () {
		let db = ZOTERO_CONFIG.DB_NAME || ZOTERO_CONFIG.ID;
		return db !== ZOTERO_CONFIG.ID;
	};

	/**
	 * The application whose library this is, for a message to a person.
	 *
	 * @return {String}
	 */
	this.hostName = function () {
		let db = ZOTERO_CONFIG.DB_NAME || ZOTERO_CONFIG.ID;
		return db === 'zotero' ? 'Zotero' : db;
	};

	/**
	 * Where Pharos's own records go.
	 *
	 * NOT in the shared database. Adding a table to `zotero.sqlite` would put
	 * rows in a file another application owns, migrates and syncs, and Zotero's
	 * own integrity check reports unknown tables as damage. A sidecar beside it
	 * keeps Pharos-native state on the same disk, moving with the library when
	 * the user moves it, while leaving the shared file exactly as Zotero wrote
	 * it. This is the shape Vibero uses -- its `vibeDB.sqlite` sits next to
	 * `zotero.sqlite` in the same directory.
	 *
	 * Nothing writes here yet: every Pharos-native record currently lives on the
	 * server, and annotations and items are Zotero's own objects. The path is
	 * defined now so that the first thing that needs it does not have to invent
	 * the convention, and so that "do not extend zotero.sqlite" has somewhere to
	 * point.
	 *
	 * @return {String}
	 */
	this.sidecarPath = function () {
		return PathUtils.join(Zotero.DataDirectory.dir, 'pharos-local.sqlite');
	};

	/**
	 * Throw unless this client may open a library at `dbVersion`.
	 *
	 * Separated from the call site in schema.js so the POLICY can be tested
	 * without driving a database migration, and so there is one place to read
	 * when someone asks what the rule actually is.
	 *
	 * The rule: an unshared library follows Zotero's ordinary behaviour and may
	 * be migrated. A SHARED one may not be touched unless it matches exactly --
	 * older means we would upgrade it out from under the user's Zotero, and
	 * newer means it was written by a build we do not understand.
	 *
	 * @param {Integer} dbVersion - the library's userdata schema version
	 * @param {Integer} ourVersion - the version this build ships
	 * @throws {Error} when the library must not be opened
	 */
	this.assertMigrationAllowed = function (dbVersion, ourVersion) {
		if (!this.isShared() || dbVersion === ourVersion) {
			return;
		}
		let host = this.hostName();
		throw new Error(
			`Pharos will not open a shared ${host} library at userdata `
			+ `${dbVersion}: this build expects ${ourVersion}. `
			+ (dbVersion < ourVersion
				? `Upgrading it would leave ${host} unable to open its own `
					+ `database, and the upgrade cannot be undone.`
				: `It was written by a newer build than this one.`)
			+ ` Install a Pharos build matching your ${host} version, or point `
			+ `Pharos at its own data directory.`
		);
	};
};
