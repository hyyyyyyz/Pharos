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
	 * dual; users reading to absorb want mono. Both are produced by the same
	 * job, so the choice here only decides which file gets attached.
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
	 * @return {Promise<Zotero.Item>} the newly created attachment
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
		return _importResult(attachment, paper.id, kind);
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
	 * Download the translated PDF and attach it to the same parent item.
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

		try {
			return await Zotero.Attachments.importFromFile({
				file: tmpPath,
				// Attach to the same parent as the source PDF, so the translation
				// sits beside the original rather than becoming a loose item. A
				// top-level PDF has no parent, and passing false is how
				// importFromFile is told that.
				parentItemID: attachment.parentItemID || false,
				libraryID: attachment.libraryID,
				title: filename,
				contentType: 'application/pdf',
			});
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
	 */
	function _describeError(e) {
		if (e instanceof Zotero.Pharos.API.SignedOutError) {
			return Zotero.getString('pharos-error-signed-out');
		}
		if (e.status == 409) {
			return Zotero.getString('pharos-translate-error-disabled');
		}
		return e.message || String(e);
	}
};
