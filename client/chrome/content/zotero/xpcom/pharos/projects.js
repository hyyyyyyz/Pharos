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
 * A project is a research question with the papers it rests on and the records
 * written along the way: hypotheses, plans, results, claims, drafts, reviews.
 * The backend keeps the record; it does not run anything, and says so in
 * `automation_notice`, which this client shows rather than paraphrases.
 *
 * What the client adds is the connection to the library: papers found through
 * discovery can be filed into a project, and an artifact can be pulled out as a
 * Zotero note so it lives with the reading it came from.
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
	 * @param {Boolean} [includeArchived]
	 * @return {Promise<Object[]>}
	 */
	this.list = async function (includeArchived) {
		let projects = await Zotero.Pharos.API.request('GET', '/api/projects');
		if (includeArchived) {
			return projects;
		}
		return projects.filter(p => p.status != 'archived');
	};

	/**
	 * @param {String} projectID
	 * @return {Promise<Object>} the project with its sources and artifacts
	 */
	this.get = function (projectID) {
		return Zotero.Pharos.API.request('GET', `/api/projects/${projectID}`);
	};

	/**
	 * @param {Object} fields - name, description, researchQuestion
	 * @return {Promise<Object>}
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
	 * Move a project to the next stage.
	 *
	 * @param {String} projectID
	 * @return {Promise<Object>}
	 */
	this.advance = function (projectID) {
		return Zotero.Pharos.API.request('POST', `/api/projects/${projectID}/advance`);
	};

	/**
	 * Whether a project has a stage left to advance to.
	 *
	 * @param {Object} project
	 * @return {Boolean}
	 */
	this.canAdvance = function (project) {
		return this.STAGES.indexOf(project.stage) < this.STAGES.length - 1;
	};

	/**
	 * File a discovery result into a project.
	 *
	 * Takes a discovery result rather than a Zotero item because that is what
	 * the backend keys sources by; a paper that came from somewhere else has no
	 * result id to reference.
	 *
	 * @param {String} projectID
	 * @param {String} resultID
	 * @param {String} [note]
	 * @return {Promise<Object>}
	 */
	this.addSource = function (projectID, resultID, note) {
		return Zotero.Pharos.API.request('POST', `/api/projects/${projectID}/sources`, {
			body: { result_id: resultID, note: note || null },
		});
	};

	/**
	 * @param {String} projectID
	 * @return {Promise<Object[]>}
	 */
	this.getArtifacts = function (projectID) {
		return Zotero.Pharos.API.request('GET', `/api/projects/${projectID}/artifacts`);
	};

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
				+ `${esc(Zotero.getString('pharos-projects-stage-' + artifact.stage))} · `
				+ `${esc(Zotero.getString('pharos-projects-type-' + artifact.type.replace('_', '-')))} · `
				+ `${esc(Zotero.getString('pharos-projects-status-' + artifact.status))}</p>`,
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
