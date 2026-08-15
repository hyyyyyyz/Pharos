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
 * Dormant desktop Harness transport.
 *
 * H1 deliberately adds no user-visible module. This is the owner-authenticated
 * run/status/event polling client later workflow surfaces will build on: it
 * goes through Zotero.Pharos.API, so bearer auth, 401 token clearing, timeout
 * and the official service origin behave exactly like every other Pharos
 * call. Execution state lives in the backend database, never in a window: a
 * caller that closes and reopens restores by run id, and nothing here starts
 * a canary -- the release channel offers no canary start.
 */
Zotero.Pharos.Harness = new function () {
	/** The backend's hard cap; asking for more is a 422. */
	const MAX_LIMIT = 200;

	/**
	 * The workflows this account may currently see.
	 *
	 * @return {Promise<Array<Object>>}
	 */
	this.listWorkflows = function () {
		return Zotero.Pharos.API.request('GET', '/api/harness/workflows');
	};

	/**
	 * One page of runs, newest first.
	 *
	 * @param {Object} [options]
	 * @param {Number} [options.limit]
	 * @param {Number} [options.after] - created_at_us cursor for pagination
	 * @return {Promise<Object>} { runs, nextCursor }
	 */
	this.listRuns = function ({ limit, after } = {}) {
		let params = new URLSearchParams();
		params.set('limit', String(Math.min(limit || 50, MAX_LIMIT)));
		if (after) {
			params.set('after', String(after));
		}
		return Zotero.Pharos.API.request('GET', `/api/harness/runs?${params}`);
	};

	/**
	 * One run with its step snapshot.
	 *
	 * @param {String} runID
	 * @return {Promise<Object>}
	 */
	this.getRun = function (runID) {
		return Zotero.Pharos.API.request('GET', `/api/harness/runs/${encodeURIComponent(runID)}`);
	};

	/**
	 * Persistent pause: no new steps are claimed; running steps finish at a
	 * safe boundary. The database is the truth, so a closed window changes
	 * nothing about the request.
	 *
	 * @param {String} runID
	 * @return {Promise<Object>}
	 */
	this.pause = function (runID) {
		return Zotero.Pharos.API.request(
			'POST', `/api/harness/runs/${encodeURIComponent(runID)}/pause`
		);
	};

	/**
	 * @param {String} runID
	 * @return {Promise<Object>}
	 */
	this.resume = function (runID) {
		return Zotero.Pharos.API.request(
			'POST', `/api/harness/runs/${encodeURIComponent(runID)}/resume`
		);
	};

	/**
	 * @param {String} runID
	 * @return {Promise<Object>}
	 */
	this.cancel = function (runID) {
		return Zotero.Pharos.API.request(
			'POST', `/api/harness/runs/${encodeURIComponent(runID)}/cancel`
		);
	};

	/**
	 * Events after a durable cursor, oldest first.
	 *
	 * @param {String} runID
	 * @param {Object} [options]
	 * @param {Number} [options.afterSeq]
	 * @return {Promise<Object>} { events, nextSeq }
	 */
	this.events = function (runID, { afterSeq = 0 } = {}) {
		return Zotero.Pharos.API.request(
			'GET',
			`/api/harness/runs/${encodeURIComponent(runID)}/events?after_seq=${afterSeq}`
		);
	};

	/**
	 * Artifacts produced by a run.
	 *
	 * @param {String} runID
	 * @return {Promise<Array<Object>>}
	 */
	this.artifacts = function (runID) {
		return Zotero.Pharos.API.request(
			'GET', `/api/harness/runs/${encodeURIComponent(runID)}/artifacts`
		);
	};

	/**
	 * Pending approvals for a run.
	 *
	 * @param {String} runID
	 * @return {Promise<Array<Object>>}
	 */
	this.approvals = function (runID) {
		return Zotero.Pharos.API.request(
			'GET', `/api/harness/runs/${encodeURIComponent(runID)}/approvals`
		);
	};

	/**
	 * Approve or reject one pending approval.
	 *
	 * @param {String} approvalID
	 * @param {String} decision - "approved" | "rejected"
	 * @param {String} reason
	 * @return {Promise<Object>}
	 */
	this.decideApproval = function (approvalID, decision, reason = '') {
		return Zotero.Pharos.API.request(
			'POST', `/api/harness/approvals/${encodeURIComponent(approvalID)}/decision`,
			{ body: { decision, reason } }
		);
	};
};
