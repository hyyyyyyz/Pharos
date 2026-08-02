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
 * The research projects window.
 *
 * Read-mostly. Creating and writing artifacts belongs in the web client, which
 * is a writing surface; what this offers is the view from the library side --
 * which papers a project rests on, what has been written, and the one action
 * that changes state here, advancing the stage.
 */
var Zotero_Pharos_Projects = new function () {
	let _resolveInit;
	let _project = null;

	/**
	 * A Fluent string with arguments.
	 *
	 * Not Zotero.getString(): handed params, that routes to the .properties
	 * bundle, where none of these ids exist. Fluent's own formatter is what
	 * reads pharos.ftl.
	 */
	function _fmt(id, args) {
		return Zotero.ftl.formatValueSync(id, args);
	}

	/** See pharosDaily.js for why this is created here rather than in onload. */
	this.initialized = new Promise((resolve) => {
		_resolveInit = resolve;
	});

	this.init = async function () {
		try {
			await this._init();
		}
		finally {
			_resolveInit();
		}
	};

	this._init = async function () {
		this._list = document.getElementById('pharos-daily-list');
		this._status = document.getElementById('pharos-daily-status');
		this._summary = document.getElementById('pharos-daily-summary');
		this._select = document.getElementById('pharos-projects-select');
		this._advance = document.getElementById('pharos-projects-advance');

		this._select.addEventListener('change', () => this.load(this._select.value));
		this._advance.addEventListener('command', () => this.advance());
		this._advance.addEventListener('click', () => this.advance());
		this._advance.disabled = true;

		if (!Zotero.Pharos.API.hasCredentials()) {
			this._setStatus(Zotero.getString('pharos-error-signed-out-detail'));
			this._select.disabled = true;
			return;
		}

		await this.loadProjects();
	};

	this._setStatus = function (text) {
		this._status.textContent = text || '';
		this._status.hidden = !text;
	};

	this.loadProjects = async function () {
		this._setStatus(Zotero.getString('pharos-projects-loading'));
		try {
			let projects = await Zotero.Pharos.Projects.list();
			this._select.replaceChildren();
			if (!projects.length) {
				this._setStatus(Zotero.getString('pharos-projects-empty'));
				this._select.disabled = true;
				return;
			}
			for (let project of projects) {
				let option = document.createElement('option');
				option.value = project.id;
				option.textContent = project.name;
				this._select.append(option);
			}
			await this.load(projects[0].id);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-projects-error'));
		}
	};

	this.load = async function (projectID) {
		this._setStatus(Zotero.getString('pharos-projects-loading'));
		this._list.replaceChildren();
		try {
			_project = await Zotero.Pharos.Projects.get(projectID);
			this._render(_project);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-projects-error'));
		}
	};

	this._render = function (project) {
		this._setStatus('');
		this._summary.textContent = Zotero.getString(
			'pharos-projects-stage-' + project.stage
		);
		this._advance.disabled = !Zotero.Pharos.Projects.canAdvance(project);

		if (project.research_question) {
			let question = document.createElement('div');
			question.className = 'pharos-daily-paper';
			let heading = document.createElement('div');
			heading.className = 'pharos-daily-title';
			heading.textContent = Zotero.getString('pharos-projects-question');
			let body = document.createElement('div');
			body.textContent = project.research_question;
			question.append(heading, body);
			this._list.append(question);
		}

		// Shown verbatim, not paraphrased: the backend words it carefully to
		// avoid implying that anything was executed, and it is the client's job
		// to carry that rather than restate it.
		if (project.automation_notice) {
			let notice = document.createElement('div');
			notice.className = 'pharos-daily-pending';
			notice.textContent = project.automation_notice;
			this._list.append(notice);
		}

		this._renderSection(
			_fmt('pharos-projects-sources', { count: project.source_count }),
			(project.sources || []).map(source => this._renderSource(source))
		);
		this._renderSection(
			_fmt('pharos-projects-artifacts', { count: project.artifact_count }),
			(project.artifacts || []).map(artifact => this._renderArtifact(artifact, project))
		);
	};

	this._renderSection = function (title, rows) {
		let heading = document.createElement('div');
		heading.className = 'pharos-daily-title';
		heading.style.marginBlock = '12px 6px';
		heading.textContent = title;
		this._list.append(heading);
		if (!rows.length) {
			let empty = document.createElement('div');
			empty.className = 'pharos-daily-pending';
			empty.textContent = Zotero.getString('pharos-projects-none');
			this._list.append(empty);
			return;
		}
		for (let row of rows) {
			this._list.append(row);
		}
	};

	this._renderSource = function (source) {
		let row = document.createElement('div');
		row.className = 'pharos-daily-paper';

		let title = document.createElement('div');
		title.className = 'pharos-daily-title';
		title.textContent = source.result.title;
		row.append(title);

		let meta = document.createElement('div');
		meta.className = 'pharos-daily-meta';
		let bits = [];
		if (source.result.authors && source.result.authors.length) {
			bits.push(source.result.authors.slice(0, 3).join(', ')
				+ (source.result.authors.length > 3 ? ' et al.' : ''));
		}
		if (source.result.year) {
			bits.push(source.result.year);
		}
		if (source.result.venue) {
			bits.push(source.result.venue);
		}
		meta.textContent = bits.join(' · ');
		row.append(meta);

		if (source.note) {
			let note = document.createElement('div');
			note.className = 'pharos-daily-summary-text';
			note.textContent = source.note;
			row.append(note);
		}

		let actions = document.createElement('div');
		actions.className = 'pharos-daily-actions';
		let save = document.createElement('button');
		save.textContent = Zotero.getString('pharos-daily-save');
		save.addEventListener('click', () => this.saveSource(source, save));
		actions.append(save);
		row.append(actions);
		return row;
	};

	this._renderArtifact = function (artifact, project) {
		let row = document.createElement('div');
		row.className = 'pharos-daily-paper';

		let title = document.createElement('div');
		title.className = 'pharos-daily-title';
		title.textContent = artifact.title;
		row.append(title);

		let meta = document.createElement('div');
		meta.className = 'pharos-daily-meta';
		meta.textContent = [
			Zotero.getString('pharos-projects-type-' + artifact.type.replace('_', '-')),
			Zotero.getString('pharos-projects-stage-' + artifact.stage),
			Zotero.getString('pharos-projects-status-' + artifact.status),
		].join(' · ');
		row.append(meta);

		if (artifact.body) {
			let body = document.createElement('div');
			body.className = 'pharos-daily-summary-text';
			body.textContent = artifact.body;
			row.append(body);
		}

		let actions = document.createElement('div');
		actions.className = 'pharos-daily-actions';
		let save = document.createElement('button');
		save.textContent = Zotero.getString('pharos-projects-save-note');
		save.addEventListener('click', () => this.saveArtifact(artifact, project, save));
		actions.append(save);
		row.append(actions);
		return row;
	};

	this.saveSource = async function (source, button) {
		button.disabled = true;
		let original = button.textContent;
		button.textContent = Zotero.getString('pharos-daily-saving');
		try {
			await Zotero.Pharos.Discovery.saveToLibrary(source.result);
			button.textContent = Zotero.getString('pharos-daily-saved');
		}
		catch (e) {
			Zotero.logError(e);
			button.textContent = original;
			button.disabled = false;
			this._setStatus(e.message || Zotero.getString('pharos-daily-save-failed'));
		}
	};

	this.saveArtifact = async function (artifact, project, button) {
		button.disabled = true;
		let original = button.textContent;
		button.textContent = Zotero.getString('pharos-daily-saving');
		try {
			await Zotero.Pharos.Projects.saveArtifactAsNote(artifact, project);
			button.textContent = Zotero.getString('pharos-daily-saved');
		}
		catch (e) {
			Zotero.logError(e);
			button.textContent = original;
			button.disabled = false;
			this._setStatus(e.message || Zotero.getString('pharos-daily-save-failed'));
		}
	};

	this.advance = async function () {
		if (!_project) {
			return;
		}
		this._advance.disabled = true;
		try {
			let updated = await Zotero.Pharos.Projects.advance(_project.id);
			// advance() returns the project without its sources and artifacts
			// populated, so reload rather than render the response.
			await this.load(updated.id);
		}
		catch (e) {
			Zotero.logError(e);
			this._setStatus(e.message || Zotero.getString('pharos-projects-error'));
			this._advance.disabled = false;
		}
	};
};
