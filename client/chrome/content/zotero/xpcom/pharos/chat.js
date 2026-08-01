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
 * Ask questions about the paper you are reading.
 *
 * The model never sees the PDF. The backend extracts the text once, keeps it as
 * a "context", and answers against that -- so the same paper can be asked about
 * repeatedly without re-uploading or re-parsing it.
 */
Zotero.Pharos.Chat = new function () {
	/**
	 * Zotero attachment id -> Pharos paper id, for this session only.
	 *
	 * Not persisted, and deliberately so. The backend content-addresses uploads
	 * by sha256 and returns the existing row for a file it already has
	 * (services/library.py add_upload), which makes resolution idempotent. A
	 * stored mapping would need somewhere to live -- a schema change, or an
	 * unbounded blob in a pref -- and would then have to be invalidated whenever
	 * the file behind an attachment changed. Re-resolving costs one upload of a
	 * file the server already holds.
	 */
	let _paperIDs = new Map();

	/**
	 * Whether chat is possible for an item.
	 *
	 * @param {Zotero.Item} item
	 * @return {Boolean}
	 */
	this.canChat = function (item) {
		return !!this.getAttachment(item);
	};

	/**
	 * The PDF this item's conversation should be about.
	 *
	 * Synchronous, because the item pane decides whether to show the section
	 * while rendering. Mirrors Zotero.Pharos.Translate.hasTranslatableAttachment.
	 *
	 * @param {Zotero.Item} item
	 * @return {Zotero.Item|null}
	 */
	this.getAttachment = function (item) {
		if (!item) {
			return null;
		}
		if (Zotero.Pharos.Translate.canTranslate(item)) {
			return item;
		}
		if (!item.isRegularItem()) {
			return null;
		}
		return Zotero.Items.get(item.getAttachments())
			.find(a => Zotero.Pharos.Translate.canTranslate(a)) || null;
	};

	/**
	 * Get the backend's paper id for an attachment, uploading it if needed.
	 *
	 * @param {Zotero.Item} attachment
	 * @return {Promise<String>}
	 */
	this.resolvePaperID = async function (attachment) {
		if (_paperIDs.has(attachment.id)) {
			return _paperIDs.get(attachment.id);
		}
		let path = await attachment.getFilePathAsync();
		if (!path) {
			throw new Error(Zotero.getString('pharos-translate-error-missing-file'));
		}
		let bytes = await IOUtils.read(path);
		let form = new FormData();
		form.append(
			'file',
			new Blob([bytes], { type: 'application/pdf' }),
			attachment.attachmentFilename
		);
		let paper = await Zotero.Pharos.API.request('POST', '/api/papers', { body: form });
		_paperIDs.set(attachment.id, paper.id);
		return paper.id;
	};

	/**
	 * Make sure the backend has extracted this paper's text.
	 *
	 * @param {String} paperID
	 * @param {Function} [onProgress] - called with a status string
	 * @return {Promise<Object>} the context
	 */
	this.ensureContext = async function (paperID, onProgress) {
		let context = await Zotero.Pharos.API.request(
			'GET', `/api/ai/papers/${paperID}/context`
		);
		if (context && context.status == 'ready') {
			return context;
		}

		if (onProgress) {
			onProgress(Zotero.getString('pharos-chat-status-preparing'));
		}
		context = await Zotero.Pharos.API.request(
			'POST', `/api/ai/papers/${paperID}/prepare`
		);

		// prepare() hands the extraction to a background task, so a "pending"
		// reply means it started, not that it finished.
		const POLL_INTERVAL = 1500;
		const TIMEOUT = 5 * 60 * 1000;
		let deadline = Date.now() + TIMEOUT;
		while (context.status != 'ready') {
			if (context.status == 'error') {
				throw new Error(context.error || Zotero.getString('pharos-chat-error-prepare'));
			}
			if (Date.now() > deadline) {
				throw new Error(Zotero.getString('pharos-chat-error-prepare-timeout'));
			}
			await Zotero.Promise.delay(POLL_INTERVAL);
			context = await Zotero.Pharos.API.request(
				'GET', `/api/ai/papers/${paperID}/context`
			);
			if (!context) {
				throw new Error(Zotero.getString('pharos-chat-error-prepare'));
			}
		}
		return context;
	};

	/**
	 * The conversation to continue for this paper, creating one if there is none.
	 *
	 * Reuses the most recent rather than starting fresh each time the section is
	 * opened: closing and reopening the pane should not lose the thread.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object>} the conversation summary
	 */
	this.getOrCreateConversation = async function (paperID) {
		let conversations = await Zotero.Pharos.API.request(
			'GET', `/api/ai/papers/${paperID}/conversations`
		);
		if (conversations && conversations.length) {
			// The backend returns these newest-first.
			return conversations[0];
		}
		return Zotero.Pharos.API.request(
			'POST', `/api/ai/papers/${paperID}/conversations`, { body: {} }
		);
	};

	/**
	 * @param {String} conversationID
	 * @return {Promise<Object>} conversation with its messages
	 */
	this.getConversation = function (conversationID) {
		return Zotero.Pharos.API.request(
			'GET', `/api/ai/conversations/${conversationID}`
		);
	};

	/**
	 * Send a message and stream the reply.
	 *
	 * @param {String} conversationID
	 * @param {String} message
	 * @param {Object} options
	 * @param {Function} options.onDelta - called with each fragment of the reply
	 * @param {AbortSignal} [options.signal]
	 * @return {Promise<String>} the complete reply
	 */
	this.sendMessage = async function (conversationID, message, { onDelta, signal } = {}) {
		let reply = '';
		let failure = null;
		await Zotero.Pharos.API.stream(
			`/api/ai/conversations/${conversationID}/messages/stream`,
			{
				body: {
					// Identifies this run to the backend so that a retry cannot be
					// mistaken for a second question.
					runId: Zotero.Utilities.randomString(16),
					message,
				},
				signal,
				onEvent: (event) => {
					switch (event.type) {
						case 'delta':
							reply += event.text;
							if (onDelta) {
								onDelta(event.text);
							}
							break;
						case 'error':
							// Reported inside the stream rather than as a status
							// code: by the time the model fails, headers are long
							// since sent.
							failure = new Error(
								event.message || Zotero.getString('pharos-chat-error-failed')
							);
							break;
						default:
							// 'started' and 'done' need no handling here.
							break;
					}
				},
			}
		);
		if (failure) {
			throw failure;
		}
		return reply;
	};

	/** Test seam: drop the cached paper ids. */
	this._clearCache = function () {
		_paperIDs.clear();
	};
};
