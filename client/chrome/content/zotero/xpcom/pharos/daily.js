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
 * The daily arXiv digest.
 *
 * The backend sweeps arXiv against the directions the user wrote, has a model
 * read what it finds, and keeps the result per day. This is the client's view of
 * that, plus the one thing only the client can do: put a paper into the local
 * library, PDF and all, so it is there to read and annotate.
 *
 * Note the backend's daily schemas are snake_case, unlike the AI chat ones --
 * they are plain BaseModels rather than the CamelModel used there. Nothing here
 * renames a wire field, because the day a key is camel-cased on the way in is
 * the day it has to be un-camel-cased on the way back out.
 *
 * Nothing in this module catches. SignedOutError, the {"detail": ...} the API
 * layer has already unwrapped into an Error with `.status`, and a transport
 * failure all reach the view, which is the only layer that can tell the reader
 * which of those happened. The single exception is findInLibrary(), which is a
 * badge and must never take the list down with it.
 */
Zotero.Pharos.Daily = new function () {
	/**
	 * The backend allows one read 90s (READ_TIMEOUT_SECONDS in
	 * pharos/daily/service.py). The API layer's default is 30s, which would abort
	 * a read the server is still perfectly happy to finish -- and the row would be
	 * written anyway, so the user sees a failure that did not happen.
	 */
	this.READ_TIMEOUT = 180000;

	/**
	 * Today's date in the digest's terms.
	 *
	 * Local, not UTC. The digest is keyed by the date the user is having, and a
	 * reader in UTC+8 asking at 09:00 for "today" must not be handed yesterday.
	 *
	 * The server's own today() is local to the server, so the two agree only in a
	 * shared timezone. A view must not assume this date appears in getDates().
	 *
	 * @return {String} YYYY-MM-DD
	 */
	this.today = function () {
		let now = new Date();
		let pad = n => String(n).padStart(2, '0');
		return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
	};

	/**
	 * Days with something in them for THIS account, newest first.
	 *
	 * Matching happens per request against the caller's own directions, so a day
	 * the shared sweep filled but this account matches nothing in is absent
	 * altogether rather than present with a total of zero.
	 *
	 * These counts are also the only honest progress indicator during a sweep:
	 * the reading phase commits one paper at a time, whereas a run row's
	 * fetched/read_done/read_failed columns are written once at the end and read
	 * zero for the whole time anyone would want to watch them.
	 *
	 * @return {Promise<Object[]>} [{ date, total, read, pending, failed }]
	 */
	this.getDates = async function () {
		let res = await Zotero.Pharos.API.request('GET', '/api/daily/dates');
		// A body of "null" or an empty response is not a day with no papers, but
		// the caller has nothing to do with the difference and every caller would
		// otherwise have to write the same guard before iterating.
		return Array.isArray(res) ? res : [];
	};

	/**
	 * One day's papers, best first and already capped.
	 *
	 * A day with no papers is a 200 with an empty list, never a 404. What tells
	 * the two empty days apart is `run`: null means this date was never swept in
	 * this install, non-null with no papers means it was swept and nothing the
	 * caller cares about came back. The two want different words on screen, so
	 * do not collapse them.
	 *
	 * @param {String} [date] - YYYY-MM-DD, defaulting to today. Anything else is
	 *     a 400 from the path regex rather than an empty day.
	 * @return {Promise<Object>} { date, total, run, papers }
	 */
	this.getDay = function (date) {
		return Zotero.Pharos.API.request(
			'GET', `/api/daily/${encodeURIComponent(date || this.today())}`
		);
	};

	/**
	 * Whether the reading layer is configured, and what the digest has done
	 * lately.
	 *
	 * Normalised here rather than in the view: every field below is optional on
	 * the wire, and a view left to invent its own defaults invents a different
	 * one at each use site.
	 *
	 * Two of those defaults are load-bearing:
	 *
	 *   - `llm_configured` absent means TRUE. Never accuse the operator of a
	 *     misconfiguration on the strength of a field that did not arrive.
	 *   - `sweeping` is the sweeper's own in-memory state and is the ONLY
	 *     authoritative "a sweep is running now". `last_run.status` reads
	 *     "running" forever for a row orphaned by a backend restart, so polling
	 *     on it polls forever.
	 *
	 * @return {Promise<Object>} { llm_configured, provider, directions, last_run,
	 *     today, sweeping }
	 */
	this.getStatus = async function () {
		let res = await Zotero.Pharos.API.request('GET', '/api/daily/status') || {};
		let provider = res.provider || null;
		return {
			llm_configured: res.llm_configured !== false,
			provider: provider && {
				name: provider.name || '',
				model: provider.model || '',
				base_url: provider.base_url || '',
				configured: !!provider.configured,
			},
			directions: Array.isArray(res.directions) ? res.directions : [],
			last_run: res.last_run || null,
			today: res.today || null,
			sweeping: res.sweeping || null,
		};
	};

	/**
	 * Ask the backend to sweep arXiv again.
	 *
	 * The run comes back already persisted, so its date and id can seed the
	 * progress display instead of waiting a poll interval for the first status.
	 * Its counters cannot: they are zero until the sweep finishes.
	 *
	 * @param {Object} [options]
	 * @param {String} [options.date] - YYYY-MM-DD, defaulting to the SERVER's
	 *     today, which is not necessarily this machine's
	 * @param {Integer} [options.days] - 1..7, a backfill window ending at `date`
	 * @param {Boolean} [options.reread] - re-read papers already read
	 * @return {Promise<Object>} the run row, status "running"
	 */
	this.refresh = function ({ date, days, reread } = {}) {
		// Built by omission rather than by listing: RefreshRequest is declared
		// extra="forbid", so a key it does not know is a 422, and a key it knows
		// carrying undefined would serialise to null and overrule the default.
		let body = {};
		if (date !== undefined) {
			body.date = date;
		}
		if (days !== undefined) {
			body.days = days;
		}
		if (reread !== undefined) {
			body.reread = reread;
		}
		return Zotero.Pharos.API.request('POST', '/api/daily/refresh', { body });
	};

	/**
	 * Have the model read one paper now. Blocking: the answer is the response.
	 *
	 * Three failures, and the view has to tell them apart:
	 *
	 *   - A 200 whose read_status is "error" and whose read_error is set. The
	 *     provider failed and the row was written; the promise RESOLVES. Treating
	 *     a resolved promise as success shows a spinner turning into an unchanged
	 *     card with nothing said.
	 *   - 503. No provider is configured, nothing was attempted and nothing was
	 *     written. The fix is configuration, not a retry.
	 *   - 404. No such paper, or it has no abstract to read.
	 *
	 * What comes back is the whole paper as THIS caller sees it -- relevance and
	 * recommendation are scored against their directions, not a shared rubric --
	 * so replace the row wholesale rather than merging fields into the old one.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object>} the updated DailyPaperOut
	 */
	this.readPaper = function (paperID) {
		return Zotero.Pharos.API.request(
			'POST',
			`/api/daily/papers/${encodeURIComponent(paperID)}/read`,
			{ timeout: this.READ_TIMEOUT }
		);
	};

	/**
	 * Save a digest paper into the local Zotero library.
	 *
	 * Deliberately NOT the backend's /import endpoint: that files the paper in
	 * the Pharos library, which is the web client's library. Here the point is to
	 * get it into Zotero, where the reader, the annotations and the citation
	 * machinery already are.
	 *
	 * @param {Object} paper - a DailyPaperOut
	 * @param {Object} [options]
	 * @param {Integer} [options.libraryID]
	 * @param {Integer[]} [options.collections]
	 * @return {Promise<Zotero.Item>} the created item
	 */
	this.saveToLibrary = function (paper, { libraryID, collections } = {}) {
		return Zotero.Pharos.Library.saveExternalPaper({
			itemType: 'preprint',
			fields: {
				title: paper.title,
				abstractNote: paper.abstract,
				archiveID: paper.arxiv_id ? `arXiv:${paper.arxiv_id}` : null,
				repository: paper.arxiv_id ? 'arXiv' : null,
				url: paper.arxiv_url,
				// The API returns an ISO timestamp; the day is the part that
				// matters for a preprint.
				date: paper.published_at ? String(paper.published_at).slice(0, 10) : null,
			},
			authors: paper.authors,
			noteHTML: this.buildNote(paper),
			pdfURL: paper.pdf_url,
			pdfTitle: `${paper.arxiv_id || 'arXiv'}.pdf`,
			libraryID,
			collections,
		});
	};

	/**
	 * The Zotero item this digest paper was already saved as, if any.
	 *
	 * Matched on archiveID rather than on imported_paper_id: that column names a
	 * row in the Pharos web library, is a single column on a SHARED row, and is
	 * blanked by the API for anyone who does not own it. archiveID is what
	 * saveToLibrary() writes, so it is the only field that answers "is this one
	 * already in MY library".
	 *
	 * A trashed item is deliberately NOT found -- someone who binned it should be
	 * offered the import again.
	 *
	 * @param {Object} paper
	 * @param {Object} [options]
	 * @param {Integer} [options.libraryID]
	 * @return {Promise<Zotero.Item|null>}
	 */
	this.findInLibrary = async function (paper, { libraryID } = {}) {
		if (!paper || !paper.arxiv_id) {
			return null;
		}
		try {
			let search = new Zotero.Search();
			search.libraryID = libraryID || Zotero.Libraries.userLibraryID;
			search.addCondition('archiveID', 'is', `arXiv:${paper.arxiv_id}`);
			let ids = await search.search();
			return ids.length ? Zotero.Items.get(ids[0]) : null;
		}
		catch (e) {
			// The badge fails OFF rather than wrong, and never takes the list with it.
			Zotero.logError(e);
			return null;
		}
	};

	/**
	 * The model's reading of a paper, as note HTML.
	 *
	 * @param {Object} paper
	 * @return {String} empty if the paper has not been read yet
	 */
	this.buildNote = function (paper) {
		// Branching on read_status rather than on whether summary_zh is truthy,
		// which the backend's own schema asks clients to do.
		if (paper.read_status != 'done') {
			return '';
		}

		const HIGHLIGHT_KEYS = ['contribution', 'innovation', 'method', 'results'];

		// Provenance first, and unconditional. This note is filed as a child of a
		// real preprint item that also carries the PDF, in a container Zotero
		// convention treats as user-authored -- so without this line, a model's
		// inference is indistinguishable from the reader's own notes six months
		// later, and gets quoted as such. The model saw the abstract and nothing
		// else (backend/pharos/daily/reader.py), which is what makes the 方法 and
		// 结果 bullets inferences rather than readings, so the line says both
		// where the text came from and how little the model was given.
		let provenance = paper.read_model
			? Zotero.ftl.formatValueSync('pharos-daily-note-provenance',
				{ model: paper.read_model })
			: Zotero.getString('pharos-daily-note-provenance-unknown');

		let footer = [provenance];
		if (paper.matched_domain) {
			footer.push(`${Zotero.getString('pharos-daily-matched')}: ${paper.matched_domain}`
				+ (paper.matched_keywords && paper.matched_keywords.length
					? ` (${paper.matched_keywords.join(', ')})`
					: ''));
		}

		return Zotero.Pharos.Library.buildNote({
			title: paper.title,
			summary: paper.summary_zh,
			sections: HIGHLIGHT_KEYS.map(key => ({
				label: Zotero.getString('pharos-daily-highlight-' + key),
				text: paper.highlights && paper.highlights[key],
			})),
			footer,
		});
	};
};


/**
 * The Daily Vault: the digest, written into a folder the user owns.
 *
 * The format is docs/DAILY_VAULT_FORMAT.md and schemas/daily-vault/v1, shared
 * with the web client byte for byte -- a directory written here opens there and
 * the other way round. What is NOT shared is the machinery: the web needs
 * showDirectoryPicker, an IndexedDB handle store, a permission re-grant dance
 * and a JSON fallback for the browsers that have none of that, and every one of
 * those exists to work around not having a filesystem. This client has one, so
 * a path in a pref, IOUtils and PathUtils replace the lot.
 *
 * What the Vault is FOR, on the desktop, is narrower than it looks, and the UI
 * says so out loud (see pharosDaily.js): the digest is backed up here, the
 * library is not. Three things are genuinely at risk without it --
 *
 *   1. The directions profile and sweep config. Categories, the daily cap, the
 *      enabled switch and every direction with its keywords and position are
 *      edited in the preferences pane and written straight to the server. There
 *      is no local copy anywhere on this machine. Lose the account or move
 *      servers and they are retyped by hand.
 *   2. The model's reading of papers the user did NOT import. saveToLibrary()
 *      files a reading as a Zotero note, but only for papers explicitly
 *      imported, which is a small minority of any digest.
 *   3. Migration between servers, and disaster recovery.
 *
 * -- and one thing is NOT: anything about an imported paper. The Zotero item,
 * its PDF and its provenance note are already in the data directory and are
 * covered by whatever backs THAT up. Nothing here should ever be described in a
 * way that lets someone believe this is a library backup.
 *
 * Everything below treats the chosen directory as untrusted input, because it
 * is: it is an arbitrary path the user pointed at, it may be a sync folder two
 * other machines are also writing to, and its contents may have been edited by
 * anything. Hence the path guard, the per-file digest and the count cross-check,
 * none of which are optional.
 */
Zotero.Pharos.Daily.Vault = new function () {
	/**
	 * The commit marker. Written LAST, always -- see write().
	 *
	 * Its presence is also what identifies a directory as a Vault; the folder's
	 * own name is not significant.
	 */
	this.MANIFEST_NAME = 'pharos-vault.json';

	const MANIFEST_SCHEMA = 'https://raw.githubusercontent.com/hyyyyyyz/Pharos/'
		+ 'main/schemas/daily-vault/v1/manifest.schema.json';

	/** The DIRECTORY format version, not a record version. A client that does
	 *  not recognise it must not write. */
	const FORMAT_VERSION = 1;

	/**
	 * Where the connection is remembered, across sessions and across restarts.
	 *
	 * Two prefs rather than one object, because they answer two different
	 * questions and the second one is a trust decision: the path is where to
	 * write, and the id is which snapshot we last wrote THERE. A path can be
	 * reused, replaced, or synced over by another machine, so a manifest whose
	 * vault_id no longer matches is not ours to overwrite -- automatic saving
	 * stops until the user says which way the data should flow.
	 */
	const PATH_PREF = 'pharos.daily.vault.path';
	const ID_PREF = 'pharos.daily.vault.id';

	const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
	const SHA_RE = /^[a-f0-9]{64}$/;

	/** Both from the manifest schema's own maxItems, so a directory this client
	 *  writes always validates against the published schema. */
	const MAX_DAYS = 3660;
	const MAX_PAPERS_PER_DAY = 500;

	/**
	 * Refuse to read a file larger than this rather than pulling it into memory.
	 *
	 * A decade of digests is a few megabytes; this is three orders of magnitude
	 * above that. It exists because the path being read is one a user chose and
	 * anything at all may have put a file there.
	 */
	const MAX_FILE_BYTES = 64 * 1024 * 1024;

	/**
	 * A localized message that cannot itself take a render down.
	 *
	 * Every string below is used to build an Error, i.e. on a path that is
	 * already handling a failure -- and Zotero.getString() THROWS in en-US for
	 * an id it cannot resolve, so an unresolvable id would replace the error the
	 * caller is trying to report with a different one from inside the reporting.
	 */
	function _msg(id, args) {
		try {
			let value = args
				? Zotero.ftl.formatValueSync(id, args)
				: Zotero.getString(id);
			return value === null || value === undefined ? id : value;
		}
		catch (e) {
			Zotero.logError(e);
			return id;
		}
	}

	/**
	 * Exactly the bytes the format specifies: UTF-8, two-space indent, one
	 * trailing newline.
	 *
	 * Not cosmetic. Content files are named after the SHA-256 of these bytes, so
	 * a client that pretty-prints differently writes a file whose name does not
	 * describe its content and whose verification then fails on the next read.
	 */
	function _encode(value) {
		return JSON.stringify(value, null, 2) + '\n';
	}

	function _now() {
		return new Date().toISOString();
	}

	/* --------------------------------------------------------------- paths */

	/**
	 * The components of a manifest-relative path, or a throw.
	 *
	 * The manifest is the one part of a Vault that names paths, and it is a file
	 * on disk that anything may have written -- so "days/../../../.ssh/id_rsa"
	 * is a path this client must be shown and must refuse, not one it should
	 * find out about by having overwritten a key.
	 *
	 * Returned as components rather than as a string so the caller joins them
	 * with PathUtils and never with a literal separator: the format's separator
	 * is "/" on every platform, and Windows' is not.
	 *
	 * @param {String} path
	 * @return {String[]}
	 */
	this.safeRelativePath = function (path) {
		let parts = typeof path == 'string' ? path.split('/') : [];
		if (typeof path != 'string'
				|| !path
				// An absolute path escapes the root without needing "..".
				|| path.startsWith('/')
				// A backslash is a separator on Windows and an ordinary
				// character here, which is exactly how "a\\..\\..\\b" gets past
				// a component check that only knows about "/".
				|| path.includes('\\')
				|| path.includes('\0')
				|| parts.some(part => !part || part == '.' || part == '..')) {
			throw new Error(_msg('pharos-daily-vault-unsafe-path', { path: String(path) }));
		}
		return parts;
	};

	/**
	 * The absolute path a manifest entry names, guaranteed to be inside `root`.
	 *
	 * The containment test after the join is deliberately redundant with
	 * safeRelativePath(): they are two independent statements of the same
	 * invariant, and this one still holds if the component blacklist above ever
	 * misses a platform-specific way of spelling a separator. Writing outside
	 * the chosen directory is the worst thing this client can do, so it is
	 * checked twice and at the last possible moment.
	 *
	 * @param {String} root - an absolute directory path
	 * @param {String} path - a manifest-relative path
	 * @return {String}
	 */
	this.resolve = function (root, path) {
		let parts = this.safeRelativePath(path);
		let full = PathUtils.join(root, ...parts);
		let separator = Zotero.isWin ? '\\' : '/';
		let prefix = root.endsWith(separator) ? root : root + separator;
		if (!full.startsWith(prefix) || full.length <= prefix.length) {
			throw new Error(_msg('pharos-daily-vault-unsafe-path', { path: String(path) }));
		}
		return full;
	};

	/**
	 * @param {String} root
	 * @return {Promise<Boolean>} whether the chosen directory is there at all
	 */
	this.directoryExists = async function (root) {
		if (!root) {
			return false;
		}
		try {
			let info = await IOUtils.stat(root);
			return info.type == 'directory';
		}
		catch (e) {
			return false;
		}
	};

	/* ---------------------------------------------------------------- files */

	/**
	 * Write one file inside the Vault.
	 *
	 * Public because it is the seam the ordering guarantee is tested through,
	 * and because every write in this module has to go through one function for
	 * the containment check to be worth anything.
	 *
	 * Through a sibling temp file and a rename: a write interrupted halfway
	 * must not leave a file that LOOKS complete. For a content file the digest
	 * would catch it on the next read; for the manifest nothing would, and the
	 * manifest is the commit marker.
	 *
	 * @param {String} root
	 * @param {String} path - manifest-relative
	 * @param {String} text
	 */
	this.writeText = async function (root, path, text) {
		let full = this.resolve(root, path);
		let parent = PathUtils.parent(full);
		if (parent) {
			await IOUtils.makeDirectory(parent, { createAncestors: true, ignoreExisting: true });
		}
		await IOUtils.writeUTF8(full, text, { tmpPath: full + '.tmp' });
	};

	/**
	 * @param {String} root
	 * @param {String} path - manifest-relative
	 * @return {Promise<String>}
	 */
	this.readText = async function (root, path) {
		let full = this.resolve(root, path);
		let info;
		try {
			info = await IOUtils.stat(full);
		}
		catch (e) {
			// The manifest named a file that is not there. Said as itself: the
			// platform's own message for this is "Could not open the file at
			// <path>", which reads like a permissions problem.
			throw new Error(_msg('pharos-daily-vault-missing-file', { path: String(path) }));
		}
		if (info.size > MAX_FILE_BYTES) {
			throw new Error(_msg('pharos-daily-vault-too-large', { path: String(path) }));
		}
		return IOUtils.readUTF8(full);
	};

	/**
	 * @param {String} root
	 * @param {String} path - manifest-relative
	 * @return {Promise<Boolean>}
	 */
	this.exists = function (root, path) {
		return IOUtils.exists(this.resolve(root, path));
	};

	/**
	 * SHA-256 of a string, as lowercase hex.
	 *
	 * nsICryptoHash rather than crypto.subtle: this module is loaded into the
	 * Zotero namespace by zotero.mjs and has no window, and the digest is wanted
	 * synchronously in the middle of building a filename. The idiom is the one
	 * Zotero.Utilities.Internal.sha1() already uses.
	 *
	 * @param {String} text
	 * @return {String}
	 */
	this.sha256 = function (text) {
		let data = new TextEncoder().encode(text);
		let ch = Cc['@mozilla.org/security/hash;1'].createInstance(Ci.nsICryptoHash);
		ch.init(ch.SHA256);
		ch.update(data, data.length);
		// false: binary, as a string of char codes.
		let hash = ch.finish(false);
		let hex = '';
		for (let i = 0; i < hash.length; i++) {
			hex += ('0' + hash.charCodeAt(i).toString(16)).slice(-2);
		}
		return hex;
	};

	/* ---------------------------------------------------------- connection */

	/**
	 * The remembered directory, if there is one.
	 *
	 * @return {Object|null} { path, vaultID }
	 */
	this.getConnection = function () {
		let path = Zotero.Prefs.get(PATH_PREF);
		if (!path) {
			return null;
		}
		return { path, vaultID: Zotero.Prefs.get(ID_PREF) || null };
	};

	/**
	 * @param {String} path
	 * @param {String} vaultID - the id of the manifest now in that directory
	 */
	this.remember = function (path, vaultID) {
		Zotero.Prefs.set(PATH_PREF, path || '');
		Zotero.Prefs.set(ID_PREF, vaultID || '');
	};

	/** Emptied rather than cleared: these prefs have no default, and a cleared
	 *  one comes back from Zotero.Prefs.get() as undefined rather than as ''. */
	this.forget = function () {
		Zotero.Prefs.set(PATH_PREF, '');
		Zotero.Prefs.set(ID_PREF, '');
	};

	/* ------------------------------------------------------------ manifest */

	function _assertEntry(value, label) {
		if (!value || typeof value != 'object') {
			throw new Error(_msg('pharos-daily-vault-entry-missing', { label }));
		}
		if (typeof value.path != 'string' || typeof value.sha256 != 'string') {
			throw new Error(_msg('pharos-daily-vault-entry-invalid', { label }));
		}
		Zotero.Pharos.Daily.Vault.safeRelativePath(value.path);
		if (!SHA_RE.test(value.sha256)) {
			throw new Error(_msg('pharos-daily-vault-entry-digest', { label }));
		}
	}

	/**
	 * Parse and validate pharos-vault.json.
	 *
	 * Validation before use, not after: everything downstream reads paths and
	 * counts out of this object, and a manifest is a file on disk that Pharos
	 * may not have written.
	 *
	 * @param {String} text
	 * @return {Object}
	 */
	this.parseManifest = function (text) {
		let raw;
		try {
			raw = JSON.parse(text);
		}
		catch (e) {
			throw new Error(_msg('pharos-daily-vault-bad-json'));
		}
		if (!raw || typeof raw != 'object') {
			throw new Error(_msg('pharos-daily-vault-bad-manifest'));
		}
		if (raw.kind !== 'pharos.daily.vault' || raw.format_version !== FORMAT_VERSION) {
			throw new Error(_msg('pharos-daily-vault-bad-version'));
		}
		if (typeof raw.vault_id != 'string' || !raw.vault_id
				|| typeof raw.created_at != 'string'
				|| typeof raw.updated_at != 'string'
				|| typeof raw.generator != 'string') {
			throw new Error(_msg('pharos-daily-vault-bad-manifest'));
		}
		_assertEntry(raw.profile, _msg('pharos-daily-vault-label-profile'));
		if (!Array.isArray(raw.days) || raw.days.length > MAX_DAYS) {
			throw new Error(_msg('pharos-daily-vault-bad-index'));
		}
		let seen = new Set();
		for (let entry of raw.days) {
			_assertEntry(entry, _msg('pharos-daily-vault-label-day'));
			if (!DATE_RE.test(entry.date)
					|| !Number.isInteger(entry.paper_count)
					|| entry.paper_count < 0
					|| entry.paper_count > MAX_PAPERS_PER_DAY
					|| seen.has(entry.date)) {
				throw new Error(_msg('pharos-daily-vault-bad-index'));
			}
			seen.add(entry.date);
		}
		return raw;
	};

	/**
	 * @param {String} root
	 * @return {Promise<Object|null>} null when the directory holds no Vault
	 */
	this.readManifest = async function (root) {
		if (!(await this.exists(root, this.MANIFEST_NAME))) {
			return null;
		}
		return this.parseManifest(await this.readText(root, this.MANIFEST_NAME));
	};

	/** Total papers a manifest accounts for, without opening a single day file. */
	this.paperCount = function (manifest) {
		if (!manifest || !Array.isArray(manifest.days)) {
			return 0;
		}
		return manifest.days.reduce((total, day) => total + (day.paper_count || 0), 0);
	};

	/* --------------------------------------------------------------- write */

	/**
	 * Write a complete snapshot into the directory.
	 *
	 * The ORDER is the whole design and must not be rearranged:
	 *
	 *   1. profiles/<sha>.json
	 *   2. days/YYYY/MM/DD/<sha>.json, one per day
	 *   3. pharos-vault.json
	 *
	 * Content files are content-addressed and therefore immutable -- writing one
	 * can only add a path, never change what an existing path means -- and the
	 * manifest is the only file that says which of them make up a snapshot.
	 * So until step 3 lands, the directory still describes the PREVIOUS
	 * snapshot, completely and verifiably, with some unreferenced files beside
	 * it. Crash anywhere before it, run out of disk, unplug the drive: the last
	 * good snapshot is still there and still passes verification. Write the
	 * manifest first, or write it in the middle, and the same interruption
	 * leaves a manifest pointing at files that do not exist yet, which is a
	 * backup that reads as corrupt at the exact moment it is needed.
	 *
	 * Nothing is ever deleted, including revisions this write orphans. Reclaiming
	 * them would mean deciding that a file we did not write in this call is ours
	 * to remove, inside a directory the user may also keep other things in.
	 *
	 * @param {String} root
	 * @param {Object} archive - a DailyVaultArchive from exportArchive()
	 * @param {Object} [previous] - the manifest already in that directory, whose
	 *     vault_id and created_at this snapshot inherits
	 * @return {Promise<Object>} the manifest just written
	 */
	this.write = async function (root, archive, previous) {
		// Never create the root. The user picked an existing directory; if it is
		// gone -- an external disk unmounted, a sync folder removed -- then
		// makeDirectory(createAncestors) would helpfully build an empty local
		// tree at the mount point and report success, and the next mount would
		// hide it. That is a silent failure with a real backup behind it.
		if (!(await this.directoryExists(root))) {
			throw new Error(_msg('pharos-daily-vault-root-missing', { path: String(root) }));
		}
		if (!archive || !archive.profile || !Array.isArray(archive.days)) {
			throw new Error(_msg('pharos-daily-vault-bad-archive'));
		}
		if (archive.days.length > MAX_DAYS) {
			throw new Error(_msg('pharos-daily-vault-bad-index'));
		}

		let now = _now();
		let vaultID = (previous && previous.vault_id) || archive.vault_id || _newVaultID();
		// The client's own zone, not the server's: it is what makes an exported
		// day boundary interpretable on the machine that restores it.
		let timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
		let profile = Object.assign({}, archive.profile, { timezone });
		let profileText = _encode(profile);
		let profileSHA = this.sha256(profileText);
		let profilePath = `profiles/${profileSHA}.json`;
		await this.writeText(root, profilePath, profileText);

		let days = [];
		// Newest first, matching the order the manifest is read in. Plain
		// comparison rather than localeCompare(): these are ASCII dates and the
		// bytes of the manifest must not depend on the application locale.
		let sorted = archive.days.slice().sort((a, b) => {
			if (a.date == b.date) {
				return 0;
			}
			return a.date < b.date ? 1 : -1;
		});
		for (let day of sorted) {
			if (!DATE_RE.test(day.date)) {
				throw new Error(_msg('pharos-daily-vault-bad-date', { date: String(day.date) }));
			}
			let text = _encode(day);
			let sha = this.sha256(text);
			let [year, month, date] = day.date.split('-');
			let path = `days/${year}/${month}/${date}/${sha}.json`;
			await this.writeText(root, path, text);
			days.push({
				date: day.date,
				path,
				sha256: sha,
				paper_count: day.papers.length,
			});
		}

		let manifest = {
			$schema: MANIFEST_SCHEMA,
			kind: 'pharos.daily.vault',
			format_version: FORMAT_VERSION,
			vault_id: vaultID,
			created_at: (previous && previous.created_at) || now,
			updated_at: now,
			generator: archive.generator || 'Pharos',
			profile: { path: profilePath, sha256: profileSHA },
			days,
		};
		// LAST. See the ordering note above -- this line is the commit.
		await this.writeText(root, this.MANIFEST_NAME, _encode(manifest));
		this.remember(root, vaultID);
		return manifest;
	};

	function _newVaultID() {
		// Braces stripped: the manifest schema allows any string up to 64
		// characters, but "{...}" in an id that ends up in filenames and logs
		// is noise, and the web client's crypto.randomUUID() has none.
		return Services.uuid.generateUUID().toString().replace(/[{}]/g, '');
	}

	/* ---------------------------------------------------------------- read */

	/**
	 * Read one manifest entry, verifying it before parsing it.
	 *
	 * @param {String} root
	 * @param {Object} entry - { path, sha256 }
	 * @param {String} label - what to name in a failure message
	 * @return {Promise<Object>}
	 */
	this.readEntry = async function (root, entry, label) {
		let text = await this.readText(root, entry.path);
		if (this.sha256(text) !== entry.sha256) {
			throw new Error(_msg('pharos-daily-vault-checksum', { label: String(label) }));
		}
		try {
			return JSON.parse(text);
		}
		catch (e) {
			throw new Error(_msg('pharos-daily-vault-bad-file', { label: String(label) }));
		}
	};

	/**
	 * Read the directory back as an archive, verifying every file.
	 *
	 * Three checks, and all three earn their place:
	 *
	 *   - the SHA-256 of each file against the manifest, which is what makes a
	 *     torn or truncated file a refusal rather than a partial restore;
	 *   - paper_count against the day file's own list, which catches a manifest
	 *     that is truthful about the BYTES and wrong about what they contain --
	 *     the digest cannot see that, because it was computed over whatever the
	 *     writer actually wrote;
	 *   - the path guard on every path the manifest names, applied again here
	 *     because parseManifest() ran when the file was read and this is where
	 *     the paths are turned into filesystem operations.
	 *
	 * @param {String} root
	 * @return {Promise<Object>} a DailyVaultArchive
	 */
	this.read = async function (root) {
		let manifest = await this.readManifest(root);
		if (!manifest) {
			throw new Error(_msg('pharos-daily-vault-no-manifest'));
		}
		let profileLabel = _msg('pharos-daily-vault-label-profile');
		let profile = await this.readEntry(root, manifest.profile, profileLabel);
		if (!profile || profile.kind !== 'pharos.daily.profile' || profile.schema_version !== 1) {
			throw new Error(_msg('pharos-daily-vault-bad-profile'));
		}

		let days = [];
		for (let entry of manifest.days) {
			let day = await this.readEntry(root, entry, entry.date);
			if (!day || day.kind !== 'pharos.daily.issue' || day.schema_version !== 1
					|| day.date !== entry.date || !Array.isArray(day.papers)) {
				throw new Error(_msg('pharos-daily-vault-bad-day', { date: entry.date }));
			}
			if (day.papers.length !== entry.paper_count) {
				throw new Error(_msg('pharos-daily-vault-bad-count', { date: entry.date }));
			}
			days.push(day);
		}

		return {
			kind: 'pharos.daily.archive',
			schema_version: 1,
			vault_id: manifest.vault_id,
			exported_at: manifest.updated_at,
			generator: manifest.generator,
			profile,
			days,
		};
	};

	/* ----------------------------------------------------------- transport */

	/**
	 * The caller's whole digest, in the portable id-free shape.
	 *
	 * @return {Promise<Object>} a DailyVaultArchive
	 */
	this.exportArchive = function () {
		return Zotero.Pharos.API.request('GET', '/api/daily/vault/export');
	};

	/**
	 * Merge an archive back into the account.
	 *
	 * Papers merge and are idempotent; the PROFILE does not merge. Passing
	 * restoreProfile replaces this account's categories, cap, enabled flag and
	 * every direction with the archive's, which is why no caller may send it
	 * without having said so on screen first.
	 *
	 * @param {Object} archive
	 * @param {Boolean} [restoreProfile=true]
	 * @return {Promise<Object>} { days_seen, papers_added, papers_updated,
	 *     papers_unchanged, directions_restored, profile_restored }
	 */
	this.importArchive = function (archive, restoreProfile = true) {
		return Zotero.Pharos.API.request('POST', '/api/daily/vault/import', {
			body: { archive, restore_profile: restoreProfile },
		});
	};
};
