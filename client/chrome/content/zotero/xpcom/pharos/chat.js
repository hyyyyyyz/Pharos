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
	 * The in-flight or settled promise for the account's model provider.
	 *
	 * One per session rather than one per call. Every chat box asks for this on
	 * every item switch -- a paper whose account has no model has to say so
	 * before the composer is used, not after a question has been typed and lost
	 * -- and arrowing down a list of papers would otherwise be one GET per row.
	 * The answer only changes when the user edits it in the web app, which they
	 * cannot do without leaving this window, so callers that are about to give
	 * up pass { refresh: true } instead of paying for freshness everywhere.
	 */
	let _provider = null;

	/**
	 * How much of a long thread is worth showing.
	 *
	 * Deliberately the backend's own MAX_HISTORY_CHARS (services/ai_chat.py:47),
	 * applied by the same rule and in the same direction: newest first, stop
	 * before the budget is exceeded, keep at least one. That budget is what the
	 * model is actually given -- prepare_chat_request() trims the stored turns
	 * to it before every question -- so matching it makes the box hold exactly
	 * what the model remembers. A smaller number here would hide turns it still
	 * knows, which is the bug this whole path exists to close; a larger one
	 * would show turns it has already forgotten, which is the same lie the
	 * other way round.
	 *
	 * If the backend's constant moves, this one has to move with it.
	 */
	const MAX_HISTORY_CHARS = 48000;

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
	 * The paper id already resolved for an attachment, or null.
	 *
	 * Synchronous and free, unlike resolvePaperID(), which is how an id is
	 * obtained in the first place and uploads the file to do it. Anything that
	 * runs merely because an item was selected has to be able to ask the cheap
	 * question: arrowing down the items list must not upload the library.
	 *
	 * @param {Zotero.Item} attachment
	 * @return {String|null}
	 */
	this.getKnownPaperID = function (attachment) {
		return (attachment && _paperIDs.get(attachment.id)) || null;
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
	 * Which model the backend would answer with, if any.
	 *
	 * A missing or unusable provider is the one failure the chat box has to know
	 * about *before* a question is asked: without it the stream fails with a 503
	 * whose text is the only explanation the user gets, after they have written
	 * the question. Cheap enough to ask on selection -- it reads configuration,
	 * not the paper -- which is why this one is not subject to the laziness that
	 * governs resolvePaperID().
	 *
	 * @param {Object} [options]
	 * @param {Boolean} [options.refresh] - ask again rather than reuse the
	 *     session's answer
	 * @return {Promise<Object>} { configured, source, model, baseUrl, ... }
	 */
	this.getProvider = function ({ refresh } = {}) {
		if (!_provider || refresh) {
			_provider = Zotero.Pharos.API.request('GET', '/api/ai/provider')
				.catch((e) => {
					// A rejection must not be cached: every later caller would
					// get this same failure, including after the server came
					// back, and nothing would ever ask again.
					_provider = null;
					throw e;
				});
		}
		return _provider;
	};

	/**
	 * What the backend has already extracted for a paper, or null.
	 *
	 * Read-only and free, unlike ensureContext(), which starts the extraction.
	 * This is what lets the chat box report a paper it already understands
	 * without paying to prepare one it does not.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object|null>} { status, charCount, hasSummary, error, ... }
	 */
	this.getContext = function (paperID) {
		return Zotero.Pharos.API.request('GET', `/api/ai/papers/${paperID}/context`);
	};

	/**
	 * Make sure the backend has extracted this paper's text.
	 *
	 * @param {String} paperID
	 * @param {Function} [onProgress] - called with a status string
	 * @return {Promise<Object>} the context
	 */
	this.ensureContext = async function (paperID, onProgress) {
		let context = await this.getContext(paperID);
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
			context = await this.getContext(paperID);
			if (!context) {
				throw new Error(Zotero.getString('pharos-chat-error-prepare'));
			}
		}
		return context;
	};

	/**
	 * Every conversation this paper has, newest first.
	 *
	 * The order is the backend's (updated_at desc, created_at desc) and is left
	 * alone: it is what makes conversations[0] the thread the user was last in,
	 * which is the one getOrCreateConversation() resumes.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object[]>} conversation summaries
	 */
	this.listConversations = async function (paperID) {
		let conversations = await Zotero.Pharos.API.request(
			'GET', `/api/ai/papers/${paperID}/conversations`
		);
		return conversations || [];
	};

	/**
	 * The conversation this paper's thread is already in, or null.
	 *
	 * Split out from getOrCreateConversation() because selecting an item is not
	 * a reason to write a row on the server. A caller that only wants to redraw
	 * what exists must not create anything by asking.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object|null>} the conversation summary
	 */
	this.getLatestConversation = async function (paperID) {
		let conversations = await this.listConversations(paperID);
		return conversations.length ? conversations[0] : null;
	};

	/**
	 * Start a second thread about the same paper.
	 *
	 * No title: the backend names a conversation after its first question the
	 * moment one arrives (services/ai_chat.py, prepare_chat_request), so a title
	 * invented here would be a worse one that also stuck.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object>} the conversation summary
	 */
	this.createConversation = function (paperID) {
		return Zotero.Pharos.API.request(
			'POST', `/api/ai/papers/${paperID}/conversations`, { body: {} }
		);
	};

	/**
	 * Delete a conversation and the turns in it.
	 *
	 * What survives is the expensive half. The paper's extracted text and the
	 * model's understanding profile live in their own table keyed by (user,
	 * paper) -- PaperAiContext -- and delete_conversation() does not touch it,
	 * so the next question about this paper still costs no upload and no
	 * re-reading. The turns themselves do go: ai_messages cascades off
	 * ai_conversations.id and the backend opens SQLite with foreign keys on.
	 *
	 * Refused with 409 while that conversation is still generating, so a caller
	 * has to stop the answer first rather than expect this to stop it.
	 *
	 * @param {String} conversationID
	 * @return {Promise}
	 */
	this.deleteConversation = function (conversationID) {
		return Zotero.Pharos.API.request(
			'DELETE', `/api/ai/conversations/${conversationID}`
		);
	};

	/**
	 * The conversation to continue for this paper, creating one if there is none.
	 *
	 * Still the entry point for the first question about a paper. Callers that
	 * are managing a list explicitly hold their own conversation id and go
	 * straight to createConversation().
	 *
	 * Reuses the most recent rather than starting fresh each time the section is
	 * opened: closing and reopening the pane should not lose the thread.
	 *
	 * The backend then appends to whatever this returns, which means the caller
	 * owes the user its stored turns -- see getMessages(). Reusing a thread
	 * without redrawing it is what made the model answer with a memory of turns
	 * the user could not see.
	 *
	 * @param {String} paperID
	 * @return {Promise<Object>} the conversation summary
	 */
	this.getOrCreateConversation = async function (paperID) {
		let conversation = await this.getLatestConversation(paperID);
		if (conversation) {
			return conversation;
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
	 * The turns already stored for a conversation, oldest first.
	 *
	 * Nothing is re-sorted here. The backend returns them ordered by
	 * (created_at, id) -- services/ai_chat.conversation_messages -- and that is
	 * the order the model was given them in; re-sorting on a client clock would
	 * be a second opinion about a question the server has already answered.
	 * Roles are likewise taken as stored: only 'user' and 'assistant' are ever
	 * written, and anything else would be a schema change this cannot guess at.
	 *
	 * A restored thread can legitimately end on a user turn. An aborted or
	 * failed run keeps the question and saves no partial answer, by design on
	 * the server side (stream_chat_events, GeneratorExit). That is not damage
	 * and is shown as it is.
	 *
	 * @param {String} conversationID
	 * @return {Promise<Object[]>} [{ role, content }], oldest first
	 */
	this.getMessages = async function (conversationID) {
		let conversation = await this.getConversation(conversationID);
		let stored = (conversation && conversation.messages) || [];

		let kept = [];
		let chars = 0;
		for (let i = stored.length - 1; i >= 0; i--) {
			let message = stored[i];
			if (!message || !message.content
					|| (message.role != 'user' && message.role != 'assistant')) {
				continue;
			}
			// "kept.length &&" keeps the newest turn whatever its size, so a
			// single over-long message shows rather than rendering nothing.
			if (kept.length && chars + message.content.length > MAX_HISTORY_CHARS) {
				break;
			}
			chars += message.content.length;
			kept.push({ role: message.role, content: message.content });
		}
		kept.reverse();
		return kept;
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

	/** Test seam: drop everything cached for this session. */
	this._clearCache = function () {
		_paperIDs.clear();
		_provider = null;
	};
};
