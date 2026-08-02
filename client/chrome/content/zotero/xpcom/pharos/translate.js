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
 * Layout-preserving translation (保排版翻译).
 *
 * The backend runs BabelDOC, which rebuilds the PDF with the translated text in
 * the original layout -- figures, tables, equations and page breaks all stay put.
 * That takes a Python toolchain with native dependencies, so it cannot live in
 * this process; the client uploads, polls, and downloads.
 *
 * The result is imported as an ordinary PDF attachment on the same item rather
 * than shown from a remote URL. That is the whole point of doing this inside a
 * reference manager: the translation opens in the same reader, takes
 * highlights and notes like any other PDF, syncs, and is still there offline.
 *
 * Modelled on Zotero.RecognizeDocument, which is the closest existing shape --
 * a queued, per-item, long-running remote operation with a progress dialog.
 */
Zotero.Pharos.Translate = new function () {
	/** Poll interval for job status. The backend has an SSE endpoint too
	 *  (GET /api/jobs/{id}/events), but polling is what the web client does and
	 *  it needs no streaming support in this environment. */
	const POLL_INTERVAL = 2000;

	/** A translation of a long PDF legitimately takes minutes. This is the point
	 *  at which we stop believing the job is still alive. */
	const POLL_TIMEOUT = 30 * 60 * 1000;

	/** How much of a failure fits in a queue row. A backend failure arrives as
	 *  whatever the engine printed, which can be a whole Python traceback, and
	 *  the row is one line in a small dialog: untruncated, it blows the column
	 *  out and pushes everything else off screen. Same length the web client
	 *  uses (frontend/src/components/ReadingView.tsx ERROR_MAX), and nothing is
	 *  lost by it -- _processQueue() has already called Zotero.logError() on the
	 *  error itself, which logs the message in full and debug-logs the error
	 *  object with its stack. */
	const ERROR_MAX = 200;

	let _queue = [];
	let _queueProcessing = false;
	let _cancelled = false;

	let _progressQueue = Zotero.ProgressQueues.create({
		id: 'pharos-translate',
		title: 'pharos-translate-title',
		columns: [
			'pharos-translate-column-attachment',
			'pharos-translate-column-status'
		]
	});

	_progressQueue.addListener('cancel', function () {
		_queue = [];
		_cancelled = true;
	});

	/**
	 * The two ways BabelDOC can emit a translation.
	 *
	 * "mono" is the translated text alone; "dual" interleaves the original and
	 * the translation page by page. Users who read alongside the source want
	 * dual; users reading to absorb want mono.
	 *
	 * Both come out of the same job, so this no longer decides which file is
	 * kept -- whatever the job produced is imported. What it still decides is
	 * which one the run is reported as having made, and so which one the user
	 * is handed when it finishes.
	 */
	this.MODE_MONO = 'mono';
	this.MODE_DUAL = 'dual';

	/**
	 * Whether an item is something this can translate.
	 *
	 * @param {Zotero.Item} item
	 * @return {Boolean}
	 */
	this.canTranslate = function (item) {
		return item
			&& item.isAttachment()
			&& item.attachmentContentType == 'application/pdf'
			&& item.isStoredFileAttachment();
	};

	/**
	 * Whether an item, or something hanging off it, could be translated.
	 *
	 * Synchronous, because the context menu is built against the current
	 * selection and cannot await a lookup per item. getAttachments() returns ids
	 * from the already-loaded item, so no I/O happens here -- unlike
	 * getBestAttachment(), which translateItems() uses once the user has
	 * actually chosen to translate.
	 *
	 * @param {Zotero.Item} item
	 * @return {Boolean}
	 */
	this.hasTranslatableAttachment = function (item) {
		if (this.canTranslate(item)) {
			return true;
		}
		if (!item.isRegularItem()) {
			return false;
		}
		return Zotero.Items.get(item.getAttachments()).some(a => this.canTranslate(a));
	};

	/**
	 * Queue items for translation and show the progress dialog.
	 *
	 * @param {Zotero.Item[]} items - attachments, or regular items whose best
	 *     PDF attachment will be used
	 * @param {String} mode - MODE_MONO or MODE_DUAL
	 */
	this.translateItems = async function (items, mode) {
		let attachments = [];
		for (let item of items) {
			if (this.canTranslate(item)) {
				attachments.push(item);
				continue;
			}
			// A regular item was selected: translate its best PDF, which is what
			// double-clicking the item would have opened.
			if (item.isRegularItem()) {
				let best = await item.getBestAttachment();
				if (best && this.canTranslate(best)) {
					attachments.push(best);
				}
			}
		}

		if (!attachments.length) {
			return;
		}

		_cancelled = false;
		for (let attachment of attachments) {
			_progressQueue.addRow(attachment);
			_queue.push({ attachment, mode });
		}

		await _processQueue();
	};

	async function _processQueue() {
		if (_queueProcessing) {
			return;
		}
		_queueProcessing = true;
		try {
			while (_queue.length && !_cancelled) {
				let { attachment, mode } = _queue.shift();
				let itemID = attachment.id;
				try {
					let translated = await _translateOne(attachment, mode, (message) => {
						_progressQueue.updateRow(
							itemID, Zotero.ProgressQueue.ROW_PROCESSING, message
						);
					});
					_progressQueue.updateRow(
						itemID,
						Zotero.ProgressQueue.ROW_SUCCEEDED,
						translated.getField('title') || translated.attachmentFilename
					);
				}
				catch (e) {
					Zotero.logError(e);
					_progressQueue.updateRow(
						itemID, Zotero.ProgressQueue.ROW_FAILED, _describeError(e)
					);
				}
			}
		}
		finally {
			_queueProcessing = false;
		}
	}

	/**
	 * Upload one attachment, wait for the job, and import the result.
	 *
	 * @param {Zotero.Item} attachment
	 * @param {String} mode
	 * @param {Function} onProgress - called with a human-readable status string
	 * @return {Promise<Zotero.Item>} the attachment for the requested mode; the
	 *     other rendering, when the job produced one, is imported as well
	 */
	async function _translateOne(attachment, mode, onProgress) {
		let path = await attachment.getFilePathAsync();
		if (!path) {
			throw new Error(Zotero.getString('pharos-translate-error-missing-file'));
		}

		onProgress(Zotero.getString('pharos-translate-status-uploading'));
		let paper = await _upload(path, attachment.attachmentFilename);

		onProgress(Zotero.getString('pharos-translate-status-queued'));
		let job = await Zotero.Pharos.API.request(
			'POST', `/api/papers/${paper.id}/translate`
		);

		job = await _awaitJob(job.id, onProgress);

		// The job produces both renderings; has_mono/has_dual say which actually
		// landed. Asking for one the job did not produce would 404 on a request
		// that otherwise looks fine.
		let kind = mode == Zotero.Pharos.Translate.MODE_DUAL ? 'dual' : 'mono';
		if ((kind == 'dual' && !job.has_dual) || (kind == 'mono' && !job.has_mono)) {
			throw new Error(Zotero.getString('pharos-translate-error-no-output'));
		}

		onProgress(Zotero.getString('pharos-translate-status-downloading'));
		let translated = await _importResult(attachment, paper.id, kind);

		// Both renderings come out of the same job, and the mode was chosen from
		// a context menu before the user had read a word of the paper. Keeping
		// only the chosen one means changing your mind costs another full run --
		// minutes of engine time and API budget already spent, thrown away over
		// a menu click. So the other one is imported too when the job has it,
		// which is what the web client does by choosing at read time instead.
		let other = kind == 'dual' ? 'mono' : 'dual';
		if (other == 'dual' ? job.has_dual : job.has_mono) {
			try {
				await _importResult(attachment, paper.id, other);
			}
			catch (e) {
				// The rendering that was asked for is already attached. Failing
				// the whole translation over the spare one would report failure
				// for a job that succeeded.
				Zotero.logError(e);
			}
		}

		// The requested one, because it is what the progress row names and what
		// the user is waiting for.
		return translated;
	}

	async function _upload(path, filename) {
		let bytes = await IOUtils.read(path);
		let form = new FormData();
		// Blob rather than the path: FormData needs the bytes, and this is the
		// one place the whole file is held in memory. A paper-sized PDF is a few
		// MB, which is the same order as what the reader already loads.
		form.append('file', new Blob([bytes], { type: 'application/pdf' }), filename);
		return Zotero.Pharos.API.request('POST', '/api/papers', { body: form });
	}

	/**
	 * Poll until the job finishes.
	 *
	 * @return {Promise<Object>} the finished job
	 */
	async function _awaitJob(jobID, onProgress) {
		let deadline = Date.now() + POLL_TIMEOUT;
		while (true) {
			if (_cancelled) {
				throw new Error(Zotero.getString('pharos-translate-error-cancelled'));
			}
			if (Date.now() > deadline) {
				throw new Error(Zotero.getString('pharos-translate-error-timeout'));
			}

			await Zotero.Promise.delay(POLL_INTERVAL);
			let job = await Zotero.Pharos.API.request('GET', `/api/jobs/${jobID}`);

			if (job.status == 'done') {
				return job;
			}
			if (job.status == 'error') {
				throw new Error(job.error || Zotero.getString('pharos-translate-error-failed'));
			}
			// stage is the engine's own label for what it is doing right now
			// (parsing, translating, rebuilding); showing it beats a bare
			// percentage because a long stage otherwise looks like a hang.
			onProgress(Zotero.getString(
				'pharos-translate-status-running',
				[job.stage || '', Math.round(job.progress || 0)]
			));
		}
	}

	/**
	 * Download one rendering, attach it beside the original, and relate the two.
	 */
	async function _importResult(attachment, paperID, kind) {
		let buffer = await Zotero.Pharos.API.request(
			'GET', `/api/papers/${paperID}/pdf/${kind}`,
			{ responseType: 'arraybuffer', timeout: 300000 }
		);

		let baseName = _baseName(attachment.attachmentFilename || 'paper.pdf');
		let suffix = Zotero.getString(
			kind == 'dual'
				? 'pharos-translate-suffix-dual'
				: 'pharos-translate-suffix-mono'
		);
		let filename = `${baseName} (${suffix}).pdf`;

		// Written to the temp directory first because importFromFile copies from
		// a path into the storage directory; it has no bytes-in entry point.
		let tmpPath = PathUtils.join(Zotero.getTempDirectory().path, filename);
		await IOUtils.write(tmpPath, new Uint8Array(buffer));

		let options = {
			file: tmpPath,
			libraryID: attachment.libraryID,
			title: filename,
			contentType: 'application/pdf',
		};
		if (attachment.parentItemID) {
			// Attach to the same parent as the source PDF, so the translation
			// sits beside the original rather than becoming a loose item.
			options.parentItemID = attachment.parentItemID;
		}
		else {
			// A top-level PDF has no parent to hang the translation off, and an
			// attachment cannot parent another attachment. Left at that, the
			// translation lands in the library root with no collection at all --
			// findable only by scrolling the whole library, even though the file
			// it was made from is filed. Following the original into its
			// collections is the closest thing to adjacency that exists here;
			// the relation below is what actually links the two. Set instead of
			// parentItemID, never alongside it: importFromFile throws when given
			// both.
			options.collections = attachment.getCollections();
		}

		let translation;
		try {
			translation = await Zotero.Attachments.importFromFile(options);
		}
		finally {
			// The import copied the file; leaving the original behind would grow
			// the temp directory by a PDF per translation.
			try {
				await IOUtils.remove(tmpPath);
			}
			catch (e) {
				Zotero.logError(e);
			}
		}

		await _relate(attachment, translation);
		return translation;
	}

	/**
	 * Relate a translation and the PDF it was made from, both ways.
	 *
	 * Zotero's relations are not symmetric on their own: addRelatedItem() writes
	 * a dc:relation on the item it is called on and nothing on the other, so
	 * relating one side gives a link that exists from the translation and not
	 * from the paper. Everywhere Zotero relates two items it does both halves
	 * (zoteroPane.js duplicateSelectedItem, relatedBox.js), and this follows
	 * that rather than inventing a predicate of its own -- "Related" in the item
	 * pane is where a user would look, and only dc:relation puts it there.
	 *
	 * Deliberately outside any transaction and after the import has committed: a
	 * translation that is attached but unrelated is still a usable translation,
	 * and taking the import down over a failed link would trade the whole job
	 * for the cross-reference.
	 *
	 * skipDateModifiedUpdate because relating is bookkeeping, not an edit to the
	 * paper -- same call Zotero makes when it relates items itself.
	 */
	async function _relate(original, translation) {
		try {
			if (translation.addRelatedItem(original)) {
				await translation.saveTx({ skipDateModifiedUpdate: true });
			}
			if (original.addRelatedItem(translation)) {
				await original.saveTx({ skipDateModifiedUpdate: true });
			}
		}
		catch (e) {
			Zotero.logError(e);
		}
	}

	function _baseName(filename) {
		return filename.replace(/\.[^.]+$/, '');
	}

	/**
	 * Turn a failure into something the progress dialog can show.
	 *
	 * The 409 case is called out specifically: the backend returns it when the
	 * account has whole-document translation switched off, and it deliberately
	 * does not use 404 for that -- a client that reported "not found" would send
	 * the user looking for a problem with the paper instead of a setting they
	 * can change.
	 *
	 * Everything else is whatever the engine said, truncated -- see ERROR_MAX.
	 */
	function _describeError(e) {
		if (e instanceof Zotero.Pharos.API.SignedOutError) {
			return Zotero.getString('pharos-error-signed-out');
		}
		if (e.status == 409) {
			return Zotero.getString('pharos-translate-error-disabled');
		}
		let text = (e.message || String(e) || '').trim();
		if (!text) {
			// A failure with nothing to say still has to say something: an empty
			// cell reads as a row that is still running.
			return Zotero.getString('pharos-translate-error-failed');
		}
		return text.length > ERROR_MAX ? text.slice(0, ERROR_MAX) + '…' : text;
	}
};
