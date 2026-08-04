"use strict";

/**
 * The application is called Pharos. The strings it says are Zotero's.
 *
 * The `.ftl` rebrand was structural -- upstream writes `{ -app-name }` and
 * `app/assets/branding/locale/brand.ftl` binds that to Pharos, so every message
 * that goes through it renamed itself. `.properties` has no such indirection:
 * upstream typed the literal word "Zotero" into roughly a hundred values per
 * locale, and every one of them shipped unchanged. "Welcome to Zotero!",
 * "A Zotero operation is currently in progress", "Zotero could not find a
 * record" -- all of it, in a product that is not Zotero.
 *
 * So this file exists because the leak is invisible to the mechanism that fixed
 * its neighbours, and a contributor adding a `.properties` string has nothing
 * telling them the word is loaded.
 *
 * WHY THIS IS NOT A GREP FOR "Zotero"
 *
 * Because the client now opens the user's real Zotero library
 * (`resource/config.mjs`: DATA_DIR_NAME is `Zotero`, DB_NAME is `zotero`; see
 * docs/CLIENT_DATA_ARCHITECTURE.md), a large minority of these strings are
 * TRUE. `Select a Zotero data directory` is the correct instruction -- the
 * directory it wants is `~/Zotero`, and it will contain `zotero.sqlite`.
 * `Zotero File Storage` is a product the user pays for. `click Refresh in the
 * Zotero toolbar` names a ribbon tab that this client installs from an
 * unmodified `Zotero.dotm`. Renaming any of those makes the product lie in the
 * other direction, which is not an improvement over the leak.
 *
 * Hence ALLOWED below, which is a list of REASONS with keys attached rather
 * than a list of exceptions. Every entry belongs to one of four things that
 * genuinely are Zotero's and stay Zotero's no matter what this application is
 * called: the shared library on disk, the services on zotero.org, other Zotero
 * products, and the word-processor document format. If a new string does not
 * fit one of those four, it is describing THIS application and the answer is
 * Pharos -- not a fifth category.
 */
describe("Pharos branding in the shipped locale files", function () {
	// Capitalised on purpose. Every remaining lowercase `zotero` in these files
	// is a hostname (zotero.org), a URL path, a directory name, or a key prefix
	// (`zotero.preferences.*`) -- none of which are user-visible product names,
	// and one of which would break `getString()` if it were renamed.
	const NAMES_ZOTERO = /\bZotero\b/;

	/**
	 * The four reasons a user-facing string may still say "Zotero".
	 *
	 * These are written out rather than inlined so that a failure can quote the
	 * one a contributor should have matched against, and so that adding a fifth
	 * requires writing down what it is.
	 */
	const REASONS = {
		LIBRARY: "the shared Zotero library on disk -- zotero.sqlite, the ~/Zotero "
			+ "data directory, storage/, and the items, tags and groups inside it. "
			+ "Pharos reads and writes Zotero's own database file; calling it a "
			+ "Pharos database would name a file that does not exist.",
		SERVICE: "a service running on zotero.org -- the sync server, Zotero File "
			+ "Storage, the Zotero Forums, the error-report endpoint. Pharos still "
			+ "uses all of these (config.mjs keeps API_URL, STREAMING_URL, "
			+ "SERVICES_URL and REPOSITORY_URL pointed at Zotero), so the user "
			+ "really is talking to Zotero when these appear.",
		PRODUCT: "another Zotero product the user is being told about or told to "
			+ "use -- Zotero Standalone, Zotero for Firefox, the Zotero Connector, "
			+ "Zotero 4.0. Renaming these would send the user looking for a Pharos "
			+ "thing that was never built.",
		DOCUMENT: "the word-processor document format and the plugin that reads it. "
			+ "The field codes really are named ZOTERO_ITEM and ZOTERO_BIBL, and "
			+ "the installed template really is Zotero.dotm with a ribbon tab "
			+ "labelled Zotero. A document written here must stay openable in "
			+ "Zotero, so the format keeps its name.",
	};

	/**
	 * key -> reason. One list covers both locales: the ids are shared, and a
	 * translation that drops the word in one language does not change what the
	 * string is about.
	 */
	const ALLOWED = {
		// The library on disk.
		'upgrade.failed': REASONS.LIBRARY,
		'upgrade.dbUpdateRequired': REASONS.LIBRARY,
		'upgrade.integrityCheckFailed': REASONS.LIBRARY,
		'upgrade.nonupgradeableDB2': REASONS.LIBRARY,
		'dataDir.selectDir': REASONS.LIBRARY,
		'dataDir.selectedDirNonEmpty.text': REASONS.LIBRARY,
		'dataDir.selectedDirEmpty.text': REASONS.LIBRARY,
		'dataDir.moveFilesToNewLocation': REASONS.LIBRARY,
		'startupError.databaseInUse': REASONS.LIBRARY,
		'db.integrityCheck.failed': REASONS.LIBRARY,
		'db.integrityCheck.errorsFixed': REASONS.LIBRARY,
		'zotero.preferences.sync.reset.restoreFromServer': REASONS.LIBRARY,
		'integration.missingItem.single': REASONS.LIBRARY,
		'integration.missingItem.multiple': REASONS.LIBRARY,
		'sync.conflict.autoChange.alert': REASONS.LIBRARY,
		'sync.conflict.autoChange.log': REASONS.LIBRARY,
		'sync.conflict.collectionItemMerge.alert': REASONS.LIBRARY,
		'sync.conflict.collectionItemMerge.log': REASONS.LIBRARY,
		'sync.conflict.tagItemMerge.alert': REASONS.LIBRARY,
		'sync.conflict.tagItemMerge.log': REASONS.LIBRARY,
		'sync.storage.error.fileNotCreated': REASONS.LIBRARY,
		// The WebDAV layout is Zotero's: the client creates a folder literally
		// named `zotero` on the user's server, and Zotero reads the same one.
		'sync.storage.error.permissionDeniedAtAddress': REASONS.LIBRARY,
		'pharos-daily-vault-scope-out': REASONS.LIBRARY,

		// Services on zotero.org.
		'errorReport.advanceMessage': REASONS.SERVICE,
		'db.integrityCheck.reportInForums': REASONS.SERVICE,
		'zotero.preferences.sync.purgeStorage.title': REASONS.SERVICE,
		'zotero.preferences.sync.purgeStorage.desc': REASONS.SERVICE,
		'sync.error.usernameNotSet.text': REASONS.SERVICE,
		'sync.error.invalidLogin.text': REASONS.SERVICE,
		'sync.error.loginManagerCorrupted2': REASONS.SERVICE,
		'sync.error.invalidClock': REASONS.SERVICE,
		'sync.storage.error.default': REASONS.SERVICE,
		'sync.storage.error.defaultRestart': REASONS.SERVICE,
		'sync.storage.error.fileEditingAccessLost': REASONS.SERVICE,
		'sync.storage.error.webdav.fileMissingAfterUpload': REASONS.SERVICE,
		'sync.storage.error.zfs.personalQuotaReached1': REASONS.SERVICE,
		'sync.storage.error.zfs.groupQuotaReached1': REASONS.SERVICE,
		'sync.storage.error.zfs.fileWouldExceedQuota': REASONS.SERVICE,

		// Other Zotero products.
		'dataDir.incompatibleDbVersion.text': REASONS.PRODUCT,
		'dataDir.migration.failure.full.firefoxOpen': REASONS.PRODUCT,
		'app.standalone': REASONS.PRODUCT,
		'app.firefox': REASONS.PRODUCT,
		'connector.error.title': REASONS.PRODUCT,
		// Zotero-for-Firefox-era guidance. Unreachable in this codebase -- the
		// only guidance-panel `about` values still wired up are citationDialog
		// and authorMenu -- but the text describes the Firefox extension's
		// toolbar button, not this window, so renaming it would invent history.
		'firstRunGuidance.toolbarButton.new': REASONS.PRODUCT,
		'firstRunGuidance.toolbarButton.upgrade': REASONS.PRODUCT,
		'firstRunGuidance.saveButton': REASONS.PRODUCT,
		'import-source-file': REASONS.PRODUCT, // "Zotero RDF", an export format
		'import-mendeley-encrypted': REASONS.PRODUCT, // links a Zotero KB article

		// The document format and the word-processor plugin.
		'integration.error.incompatibleVersion': REASONS.DOCUMENT,
		'integration.error.cannotInsertHere': REASONS.DOCUMENT,
		'integration.error.notInCitation': REASONS.DOCUMENT,
		'integration.error.fieldTypeMismatch': REASONS.DOCUMENT,
		'integration.replace': REASONS.DOCUMENT,
		'integration.corruptField': REASONS.DOCUMENT,
		'integration.corruptBibliography': REASONS.DOCUMENT,
		'integration.delayCitationUpdates.alert.text2.toolbar': REASONS.DOCUMENT,
		'integration.delayCitationUpdates.alert.text2.tab': REASONS.DOCUMENT,
		'integration.delayCitationUpdates.bibliography.toolbar': REASONS.DOCUMENT,
		'integration.delayCitationUpdates.bibliography.tab': REASONS.DOCUMENT,
		'integration.importInstructions': REASONS.DOCUMENT,
	};

	const LOCALES = ['en-US', 'zh-CN'];
	const FILES = ['zotero.properties', 'zotero.ftl', 'pharos.ftl'];

	// locale -> file -> Map(id -> value). Filled once in before().
	let bundles = {};

	/**
	 * The URL of one locale's copy of one file.
	 *
	 * `chrome://zotero/locale/...` resolves to whichever locale the application
	 * is currently running in, and there is no chrome syntax for asking for a
	 * different one -- so a test that only read the chrome URL would check the
	 * active locale and silently ignore the other, which is exactly how a leak
	 * survives in the file nobody is looking at.
	 *
	 * Resolving through the chrome registry and substituting the locale segment
	 * gets both without hardcoding a directory layout: the source tree, the
	 * staged app and a packaged build disagree about where `chrome/` lives, but
	 * all three register locales as `.../locale/<locale>/zotero/`.
	 */
	function localeURL(locale, file) {
		let registry = Cc['@mozilla.org/chrome/chrome-registry;1']
			.getService(Ci.nsIChromeRegistry);
		let resolved = registry.convertChromeURL(
			Services.io.newURI('chrome://zotero/locale/' + file)
		).spec;
		let swapped = resolved.replace(
			/\/locale\/[^/]+\/zotero\//, '/locale/' + locale + '/zotero/'
		);
		if (swapped === resolved && !resolved.includes('/locale/' + locale + '/')) {
			throw new Error(`cannot locate the ${locale} copy of ${file}: `
				+ `chrome resolved to ${resolved}, which does not look like `
				+ `.../locale/<locale>/zotero/`);
		}
		return swapped;
	}

	/** `key = value`, one line each, `#` comments. Keys are left alone. */
	function parseProperties(text) {
		let map = new Map();
		for (let line of text.split('\n')) {
			if (/^\s*[#!]/.test(line)) continue;
			let m = line.match(/^([A-Za-z][A-Za-z0-9._-]*)[ \t]*=[ \t]*(.*)$/);
			if (m) map.set(m[1], m[2]);
		}
		return map;
	}

	/**
	 * Fluent. Attributes and multi-line values are folded into the message they
	 * belong to, because a hardcoded name is just as visible on a `.label` line
	 * as on the value line, and the id is what an allow-list can be keyed on.
	 *
	 * Comments are dropped: pharos.ftl explains itself at length and mentions
	 * Zotero a dozen times doing so, none of which any user ever sees.
	 */
	function parseFluent(text) {
		let map = new Map();
		let id = null;
		for (let line of text.split('\n')) {
			if (/^\s*#/.test(line)) continue;
			let m = line.match(/^(-?[a-zA-Z][\w-]*)\s*=(.*)$/);
			if (m) {
				id = m[1];
				map.set(id, (map.get(id) || '') + m[2] + '\n');
			}
			else if (id && /^\s+\S/.test(line)) {
				map.set(id, map.get(id) + line + '\n');
			}
			else if (!line.trim()) {
				id = null;
			}
		}
		return map;
	}

	before(async function () {
		for (let locale of LOCALES) {
			bundles[locale] = {};
			for (let file of FILES) {
				let text = await Zotero.File.getContentsFromURLAsync(localeURL(locale, file));
				bundles[locale][file] = file.endsWith('.ftl')
					? parseFluent(text)
					: parseProperties(text);
			}
		}
	});

	function explain(offenders) {
		let lines = offenders.map(function (o) {
			return `\n  ${o.locale}/${o.file}\n    ${o.id} = ${o.value.trim().slice(0, 160)}`;
		});
		return `${offenders.length} user-facing string(s) still call this application `
			+ `Zotero:\n${lines.join('\n')}\n\n`
			+ `Each one is in exactly one of three situations. Decide which, then act:\n\n`
			+ `  1. It describes THIS running application -- what it is doing, failed to `
			+ `do, or is asking for. Rename it to Pharos. This is the default and it is `
			+ `most of them.\n\n`
			+ `  2. It names something that is genuinely Zotero's and stays Zotero's. `
			+ `There are four of those, and a new string has to match one of them as `
			+ `written, not merely resemble it:\n`
			+ Object.entries(REASONS).map(([k, v]) => `     ${k}: ${v}`).join('\n\n')
			+ `\n\n     If it matches, add the id to ALLOWED in test/tests/`
			+ `pharosBrandingTest.js under that reason.\n\n`
			+ `  3. It is about the shared library and you are not sure. Ask whether the `
			+ `sentence is about the FILE or about the APPLICATION. "Your Zotero database `
			+ `must be repaired" is the file -- it is ~/Zotero/zotero.sqlite and it really `
			+ `is Zotero's. "Zotero can attempt to correct these errors" is the `
			+ `application, and the application is Pharos. Both halves of that sentence `
			+ `pair sit in db.integrityCheck.* and they are decided differently on `
			+ `purpose.\n\n`
			+ `Adding an id to ALLOWED without it matching one of the four reasons `
			+ `defeats the point: the list is the reasons, and the keys are only `
			+ `attached to them.`;
	}

	it("should not call this application Zotero in any user-facing string", function () {
		let offenders = [];
		for (let locale of LOCALES) {
			for (let file of FILES) {
				for (let [id, value] of bundles[locale][file]) {
					if (!NAMES_ZOTERO.test(value)) continue;
					if (Object.prototype.hasOwnProperty.call(ALLOWED, id)) continue;
					offenders.push({ locale, file, id, value });
				}
			}
		}
		assert.lengthOf(offenders, 0, explain(offenders));
	});

	// An allow-list that outlives the strings it excuses turns into folklore: the
	// next contributor reads it as evidence that a whole area is off-limits. If
	// upstream rewrites one of these, or a later rebrand pass renames it, the
	// entry should go rather than sit there implying a decision nobody made.
	it("should not carry allow-list entries for strings that no longer name Zotero", function () {
		let stale = Object.keys(ALLOWED).filter((id) => {
			for (let locale of LOCALES) {
				for (let file of FILES) {
					let value = bundles[locale][file].get(id);
					// Present in at least one locale still saying "Zotero" is
					// enough: translations legitimately drop the word (zh-CN's
					// collectionItemMerge.log does) without changing the subject.
					if (value !== undefined && NAMES_ZOTERO.test(value)) return false;
				}
			}
			return true;
		});
		assert.deepEqual(stale, [],
			"these ids are allow-listed but no longer say Zotero in either locale -- "
			+ "either the string was renamed and the entry should be deleted, or the "
			+ "id was removed upstream and the entry is pointing at nothing");
	});

	// The two locale files must agree on which ids exist, or a string that is
	// clean in the locale you read is missing entirely in the one you don't --
	// which in en-US throws out of getString() and in zh-CN renders the raw id.
	// check-locale-parity.js covers pharos.ftl offline; this covers the file
	// that has ~1400 of them and no other guard.
	it("should define the same .properties ids in both locales", function () {
		let [a, b] = LOCALES;
		let onlyA = [...bundles[a]['zotero.properties'].keys()]
			.filter(id => !bundles[b]['zotero.properties'].has(id));
		let onlyB = [...bundles[b]['zotero.properties'].keys()]
			.filter(id => !bundles[a]['zotero.properties'].has(id));
		assert.deepEqual({ onlyA, onlyB }, { onlyA: [], onlyB: [] },
			`ids present in only one of ${a}/${b}`);
	});

	// A parser that silently matched nothing would make all of the above pass
	// unconditionally, which is the one failure mode this file cannot detect
	// from the inside.
	it("should actually have parsed the files", function () {
		for (let locale of LOCALES) {
			assert.isAbove(bundles[locale]['zotero.properties'].size, 1000,
				`${locale}/zotero.properties parsed as ${bundles[locale]['zotero.properties'].size} ids`);
			assert.isAbove(bundles[locale]['zotero.ftl'].size, 100,
				`${locale}/zotero.ftl parsed as ${bundles[locale]['zotero.ftl'].size} ids`);
			assert.isAbove(bundles[locale]['pharos.ftl'].size, 100,
				`${locale}/pharos.ftl parsed as ${bundles[locale]['pharos.ftl'].size} ids`);
		}
		// And that it can still see a name when one is there.
		assert.match(bundles['en-US']['zotero.properties'].get('app.firefox'),
			NAMES_ZOTERO, "the detector no longer detects anything");
	});
});
