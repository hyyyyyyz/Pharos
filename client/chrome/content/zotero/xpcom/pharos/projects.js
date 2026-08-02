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
 * Research projects.
 *
 * A project is a research question, the papers it rests on, and the records
 * written along the way: hypotheses, plans, results, claims, drafts, reviews.
 * The desktop owns all of it -- creating a project, correcting a hypothesis and
 * filing a source are reading-side work, done while looking at the paper that
 * prompted them (`docs/DECISIONS.md` §4, as amended). What stays web-only is
 * composing the manuscript itself.
 *
 * The backend persists; it runs nothing, and says so in `automation_notice`,
 * which this client shows verbatim rather than paraphrases (§9).
 *
 * What the client adds on top is the connection to the library: papers found
 * through discovery can be filed into a project, and a record can be pulled out
 * as a Zotero note so it lives with the reading it came from.
 *
 * Nothing here catches. `Zotero.Pharos.API.request` already turns a FastAPI
 * `detail` into the error's message, a validation list into one joined string,
 * and a 401 into a SignedOutError; the messages that arrive are written for the
 * person reading them ("Archived projects cannot advance; reactivate the project
 * first"), so a wrapper that replaced them with a generic failure would be
 * throwing away the only specific thing the user is told. In one case it would
 * also leak: the backend answers 404, never 403, for a project belonging to
 * another account, so that a stranger's id is indistinguishable from one that
 * never existed. Rewording that as "forbidden" would give the existence away.
 */
Zotero.Pharos.Projects = new function () {
	/** In order. `advance` walks this list; the last one has nowhere to go. */
	this.STAGES = [
		'discovery',
		'ideation',
		'planning',
		'experimentation',
		'analysis',
		'claims',
		'drafting',
		'review',
		'complete',
	];

	this.ARTIFACT_TYPES = [
		'hypothesis',
		'experiment_plan',
		'result',
		'claim',
		'draft',
		'review',
	];

	/**
	 * What a record's state can be. All four are the user's own judgement --
	 * `verified` says a person checked the record, not that Pharos reproduced
	 * anything, which is why the label reads "Verified by you" (DECISIONS §9).
	 */
	this.ARTIFACT_STATUSES = [
		'draft',
		'ready',
		'verified',
		'rejected',
	];

	/**
	 * The type a blank record is seeded with, by the stage it is written in.
	 *
	 * Value for value the web client's map, so that the same stage offers the
	 * same starting point on both surfaces; a researcher moving between them
	 * should not find the type quietly different. Every stage has an entry
	 * because the record form opens from any of them, including `complete`.
	 */
	this.DEFAULT_TYPE = {
		discovery: 'hypothesis',
		ideation: 'hypothesis',
		planning: 'experiment_plan',
		experimentation: 'result',
		analysis: 'result',
		claims: 'claim',
		drafting: 'draft',
		review: 'review',
		complete: 'claim',
	};


	//
	// Labels
	//

	/**
	 * The id derivation lives here rather than at each call site because it is
	 * easy to get subtly wrong in each one: `experiment_plan` is the single value
	 * whose id is not its own name, and a site written `replace('_', '-')` is
	 * accidentally right for it -- it has exactly one underscore -- while leaving
	 * every later underscore in place, which resolves to nothing and renders as a
	 * blank label rather than an error.
	 *
	 * All five take no parameters, and that is load-bearing rather than
	 * incidental. `Zotero.getString(id)` resolves through Fluent, which is where
	 * every pharos-* string lives; `Zotero.getString(id, params)` routes to the
	 * .properties bundle instead, where none of them exist -- it throws in en-US,
	 * taking the whole render down, and returns the bare id in zh-CN. Anything
	 * needing an argument belongs in the window's own _fmt helper, which goes
	 * through Zotero.ftl.formatValueSync.
	 */
	this.stageLabel = stage => Zotero.getString('pharos-projects-stage-' + stage);
	this.stageShort = stage => Zotero.getString('pharos-projects-stage-' + stage + '-short');
	this.stageNote = stage => Zotero.getString('pharos-projects-stage-' + stage + '-note');
	this.typeLabel = type => Zotero.getString('pharos-projects-type-' + type.replace(/_/g, '-'));
	this.statusLabel = status => Zotero.getString('pharos-projects-status-' + status);


	//
	// Reads
	//

	/**
	 * Every project, newest first.
	 *
	 * The archived filter is applied here, not by the backend: GET /api/projects
	 * returns every row whatever its status. So a caller that only ever wants
	 * live projects -- discovery's "file this paper into a project" -- gets that
	 * by asking for nothing, and the projects window asks once with `true` and
	 * filters the cached array, which makes its archived toggle free.
	 *
	 * @param {Boolean} [includeArchived]
	 * @return {Promise<Object[]>} full ProjectOuts, sources and artifacts included
	 */
	this.list = async function (includeArchived) {
		let projects = await Zotero.Pharos.API.request('GET', '/api/projects');
		if (includeArchived) {
			return projects;
		}
		return projects.filter(p => p.status != 'archived');
	};

	/**
	 * One project, with its sources and artifacts.
	 *
	 * Both arrays arrive in full, which is why there is no separate
	 * artifact-listing call: `GET /{id}/artifacts` answers with the same rows in
	 * the same order, and a second wrapper for it would be one more piece of
	 * finished work that reaches nobody.
	 *
	 * @param {String} projectID
	 * @return {Promise<Object>}
	 */
	this.get = function (projectID) {
		return Zotero.Pharos.API.request('GET', `/api/projects/${projectID}`);
	};

	/**
	 * Whether the advance control should be live.
	 *
	 * Both halves of the backend's guard, not just the stage: `advance_project`
	 * refuses an archived project with a 409 as much as a finished one. Testing
	 * the stage alone was safe only while archived projects never reached the
	 * window; now that they do, it would enable a control whose one outcome is an
	 * error message.
	 *
	 * Setting the stage by hand is deliberately *not* gated the same way -- a
	 * PATCH corrects recorded metadata rather than starting work, and the backend
	 * allows it while archived -- so the stage control stays usable exactly where
	 * this returns false.
	 *
	 * @param {Object} project
	 * @return {Boolean}
	 */
	this.canAdvance = function (project) {
		return project.status == 'active'
			&& this.STAGES.indexOf(project.stage) < this.STAGES.length - 1;
	};


	//
	// Projects
	//

	/**
	 * Start a project.
	 *
	 * camelCase in, snake_case on the wire: `ProjectCreate` is declared
	 * `extra="forbid"`, so a stray `researchQuestion` would be a 422 rather than
	 * a field quietly ignored.
	 *
	 * @param {Object} fields - name, description, researchQuestion
	 * @return {Promise<Object>} the new project; its `sources` and `artifacts`
	 *     are empty by construction, so it can be rendered as it stands
	 */
	this.create = function ({ name, description, researchQuestion }) {
		return Zotero.Pharos.API.request('POST', '/api/projects', {
			body: {
				name,
				description: description || '',
				research_question: researchQuestion || '',
			},
		});
	};

	/**
	 * Correct what a project records about itself.
	 *
	 * The patch goes to the wire as given -- `name`, `description`,
	 * `research_question`, `status`, `stage` -- rather than through a camelCase
	 * mapping like create()'s. A patch is assembled field by field from what the
	 * user actually changed, so there is no form shape to translate, and a key
	 * the backend does not know comes back named in the error instead of being
	 * dropped.
	 *
	 * A `null` value does NOT clear a field: patch_project drops nulls before it
	 * applies anything, so `{ description: null }` succeeds and changes nothing.
	 * Send `''` to empty a text field. (A source note is the one place where null
	 * really clears -- see updateSource.) An empty patch is a 400, not a no-op.
	 *
	 * @param {String} projectID
	 * @param {Object} patch
	 * @return {Promise<Object>} the complete project, sources and artifacts
	 *     included -- render the response rather than reloading
	 */
	this.update = function (projectID, patch) {
		return Zotero.Pharos.API.request('PATCH', `/api/projects/${projectID}`, {
			body: patch,
		});
	};

	/**
	 * Put a project aside. It stays readable and still editable; what it loses is
	 * the ability to advance.
	 *
	 * A wrapper over update() so that no caller carries the literal, and so that
	 * the pair with restore() is visible in one place.
	 *
	 * @param {String} projectID
	 * @return {Promise<Object>}
	 */
	this.archive = function (projectID) {
		return this.update(projectID, { status: 'archived' });
	};

	/**
	 * @param {String} projectID
	 * @return {Promise<Object>}
	 */
	this.restore = function (projectID) {
		return this.update(projectID, { status: 'active' });
	};

	/**
	 * Set the stage directly, forwards or backwards.
	 *
	 * The rewind path, and a first-class research operation rather than an escape
	 * hatch: an experiment that failed sends the project back to `ideation`, and
	 * a workflow that could only move forward would force the record to lie.
	 *
	 * @param {String} projectID
	 * @param {String} stage
	 * @return {Promise<Object>}
	 */
	this.setStage = function (projectID, stage) {
		return this.update(projectID, { stage });
	};

	/**
	 * Delete a project and everything filed under it.
	 *
	 * Cascades to its sources and its records, and there is no undo, so the
	 * confirmation is the caller's job. The 204 arrives as null: drop the row
	 * locally rather than re-listing for a project that is gone.
	 *
	 * @param {String} projectID
	 * @return {Promise<null>}
	 */
	this.remove = function (projectID) {
		return Zotero.Pharos.API.request('DELETE', `/api/projects/${projectID}`);
	};

	/**
	 * Move a project to the next stage.
	 *
	 * @param {String} projectID
	 * @return {Promise<Object>} the complete project. `advance_project` resolves
	 *     it through the same eager-loading path a GET uses and serialises both
	 *     arrays, so the response is shaped exactly like get()'s and there is
	 *     nothing to reload
	 */
	this.advance = function (projectID) {
		return Zotero.Pharos.API.request('POST', `/api/projects/${projectID}/advance`);
	};


	//
	// Sources
	//

	/**
	 * File a discovery result into a project.
	 *
	 * Takes a discovery result rather than a Zotero item because that is what
	 * the backend keys sources by; a paper that came from somewhere else has no
	 * result id to reference.
	 *
	 * Idempotent: adding the same result twice returns the row that already
	 * exists and retries the library match, on the reasoning that the PDF may
	 * have arrived since the first add.
	 *
	 * @param {String} projectID
	 * @param {String} resultID
	 * @param {String} [note]
	 * @return {Promise<Object>} the source alone
	 */
	this.addSource = function (projectID, resultID, note) {
		return Zotero.Pharos.API.request('POST', `/api/projects/${projectID}/sources`, {
			body: { result_id: resultID, note: note || null },
		});
	};

	/**
	 * Write or correct the reason a source is in the project.
	 *
	 * `null` clears the note, and this is the one call in the module where null
	 * means clear: `SourcePatch.note` is required-but-nullable and
	 * patch_project_source passes it straight through, where the project and
	 * record patches drop nulls before applying. The asymmetry is deliberate on
	 * the backend -- it is what allowed the library link to become a sub-resource
	 * instead of another field no client could set without also rewriting the
	 * researcher's note -- so it is carried here rather than smoothed over.
	 *
	 * `undefined` is coerced for a duller reason: JSON.stringify drops a key
	 * whose value is undefined, and a body with no `note` at all is a 422, since
	 * the field has no default to fall back on.
	 *
	 * @param {String} projectID
	 * @param {String} sourceID
	 * @param {String|null} note
	 * @return {Promise<Object>} the changed source alone. The copy inside a
	 *     cached project is now stale, so re-get() before re-rendering
	 */
	this.updateSource = function (projectID, sourceID, note) {
		return Zotero.Pharos.API.request(
			'PATCH', `/api/projects/${projectID}/sources/${sourceID}`,
			{ body: { note: note === undefined ? null : note } }
		);
	};

	/**
	 * Take a source out of a project.
	 *
	 * Removes the project's row and nothing else: the search that found the paper
	 * keeps its result, which is what the window's notice promises. Dropping a
	 * paper from the evidence list is not deleting the record of having found it.
	 *
	 * @param {String} projectID
	 * @param {String} sourceID
	 * @return {Promise<null>} -- and `source_count` on any cached project is now
	 *     wrong, so re-get() before re-rendering
	 */
	this.removeSource = function (projectID, sourceID) {
		return Zotero.Pharos.API.request(
			'DELETE', `/api/projects/${projectID}/sources/${sourceID}`
		);
	};

	// Three more source endpoints exist and are deliberately not wrapped:
	// POST /sources/autolink, and PUT and DELETE /sources/{id}/paper. No screen
	// calls them, and a wrapper no screen reaches is the second failure
	// DECISIONS §4 names -- finished work that never got to anyone. The link
	// itself needs none of them to be shown: `source.paper` arrives in every GET
	// already. Wrap them when a screen wants them.
	//
	// Worth knowing when that day comes: what `source.paper` means is that the
	// *backend* holds the PDF. 加入文库 files a Zotero item into the local
	// library, which is a different library entirely, and nothing in the schema
	// connects the two -- so the two states must not be worded as if they were
	// one.


	//
	// Records
	//

	/**
	 * File a new research record.
	 *
	 * `body` and `status` carry the backend's own defaults so that omitting them
	 * here sends what the web client sends. What creating a `result` is not is a
	 * claim that anything ran: it is a result the researcher wrote down
	 * (DECISIONS §9).
	 *
	 * @param {String} projectID
	 * @param {Object} fields - stage, type, title, body, status
	 * @return {Promise<Object>} the new record alone. `artifact_count` on the
	 *     cached project is now wrong, so re-get() before re-rendering
	 */
	this.createArtifact = function (projectID, { stage, type, title, body, status }) {
		return Zotero.Pharos.API.request('POST', `/api/projects/${projectID}/artifacts`, {
			body: {
				stage,
				type,
				title,
				body: body || '',
				status: status || 'draft',
			},
		});
	};

	/**
	 * Correct a record.
	 *
	 * Same null rule as update(): patch_project_artifact drops nulls, so `''` is
	 * how a body is emptied, and an empty patch is a 400 rather than a no-op.
	 *
	 * @param {String} projectID
	 * @param {String} artifactID
	 * @param {Object} patch - any of stage, type, title, body, status
	 * @return {Promise<Object>} the changed record alone
	 */
	this.updateArtifact = function (projectID, artifactID, patch) {
		return Zotero.Pharos.API.request(
			'PATCH', `/api/projects/${projectID}/artifacts/${artifactID}`,
			{ body: patch }
		);
	};

	/**
	 * @param {String} projectID
	 * @param {String} artifactID
	 * @return {Promise<null>} -- `artifact_count` on the cached project is now
	 *     wrong, so re-get() before re-rendering
	 */
	this.removeArtifact = function (projectID, artifactID) {
		return Zotero.Pharos.API.request(
			'DELETE', `/api/projects/${projectID}/artifacts/${artifactID}`
		);
	};


	//
	// The library
	//

	/**
	 * Save an artifact into the Zotero library as a standalone note.
	 *
	 * The point is that a claim or a draft ends up beside the papers it was
	 * argued from, searchable with everything else, instead of only in a browser
	 * tab.
	 *
	 * @param {Object} artifact
	 * @param {Object} project - for the heading, so the note stands alone
	 * @param {Object} [options] - libraryID, collections
	 * @return {Promise<Zotero.Item>}
	 */
	this.saveArtifactAsNote = async function (artifact, project, { libraryID, collections } = {}) {
		libraryID = libraryID || Zotero.Libraries.userLibraryID;

		let esc = str => Zotero.Utilities.htmlSpecialChars(String(str || ''));
		let parts = [
			`<h2>${esc(artifact.title)}</h2>`,
			`<p>${esc(project.name)} · `
				+ `${esc(this.stageLabel(artifact.stage))} · `
				+ `${esc(this.typeLabel(artifact.type))} · `
				+ `${esc(this.statusLabel(artifact.status))}</p>`,
		];
		if (artifact.body) {
			// Written by a person or a model into a plain-text field, so newlines
			// are the only structure it has and <pre> is what preserves them.
			parts.push(`<pre>${esc(artifact.body)}</pre>`);
		}
		// Carried into the note rather than left in the UI: a record that claims
		// an experiment ran, detached from the caveat that nothing executed it,
		// is exactly the misreading the backend's notice exists to prevent.
		if (project.automation_notice) {
			parts.push(`<p><em>${esc(project.automation_notice)}</em></p>`);
		}

		let note = new Zotero.Item('note');
		note.libraryID = libraryID;
		note.setNote(parts.join('\n'));
		if (collections && collections.length) {
			note.setCollections(collections);
		}
		await note.saveTx();
		return note;
	};
};
