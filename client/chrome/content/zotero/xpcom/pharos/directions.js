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
 * Research directions for the daily digest, and the sweep settings around them.
 *
 * The split this client has to be honest about, because it is not guessable
 * from the controls:
 *
 *   - The sweep is GLOBAL. One arXiv fetch and one model reading serve every
 *     user, so a paper's summary is a fact about the paper, not about who reads
 *     it. Adding an arXiv category widens the shared net from the NEXT sweep
 *     onward; days already fetched can never gain a paper nobody fetched.
 *   - MATCHING is per-user and happens at query time. Editing a direction
 *     re-ranks the next digest immediately, with nothing re-fetched and nothing
 *     re-read.
 *
 * The daily API's schemas are snake_case, unlike the AI chat ones -- they are
 * plain BaseModels rather than the CamelModel used there.
 *
 * The parsers below mirror `pharos/daily/user_directions.py`. They exist to be
 * SHOWN, never to be sent: what the user typed goes to the server verbatim and
 * the server's parse is the one that gets stored. Normalising here and posting
 * the result would quietly make this file the authority on matching, and would
 * mean matching against something the user never saw.
 */
Zotero.Pharos.Directions = new function () {
	/**
	 * Mirrors of the service's ceilings in `pharos/daily/user_directions.py`.
	 *
	 * Copied so the editor can warn WHILE typing instead of after a round trip.
	 * Advisory here and authoritative there: nothing in this client refuses a
	 * save on its own reading of a limit, because a copy that has drifted from
	 * the backend must not be able to block something the server would accept.
	 */
	this.LIMITS = Object.freeze({
		directions: 40,
		nameChars: 64,
		keywords: 80,
		keywordChars: 80,
		keywordsTotalChars: 2000,
		categories: 24,
		minPerDay: 1,
		maxPerDay: 200,
	});

	/**
	 * A keyword written as `"wam"` matches as a whole word rather than as a
	 * substring. Mirrors `_QUOTED` in `pharos/daily/directions.py`.
	 *
	 * `.` excludes newlines in both regex flavours, and a keyword can never
	 * contain one (the parse splits on newlines), so the two agree.
	 */
	const QUOTED_RE = /^"(.+)"$/;

	/**
	 * arXiv's category grammar, mirroring `_CATEGORY_RE`: an archive, optionally
	 * followed by `.subject`. A shape check rather than an allow-list --
	 * `cond-mat.stat-mech` and `econ.EM` are a user, not a typo.
	 */
	const CATEGORY_RE
		= /^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?:\.[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)?$/;

	/** Belt and braces, so the regex is never handed a megabyte to backtrack over. */
	const MAX_CATEGORY_CHARS = 32;

	/**
	 * The seven defaults, mirrored from `pharos/daily/directions.py`.
	 *
	 * Duplicated deliberately and narrowly. The backend seeds an account exactly
	 * once, on purpose, so a user who deletes every direction is not handed them
	 * back on the next request -- which is the right behaviour and leaves exactly
	 * one gap: someone who deleted everything and changed their mind has no way
	 * back. This list fills only that gap, and is posted through the ordinary
	 * create endpoint so the backend still parses, validates and orders it.
	 *
	 * Note `"wam"` and `"dit"`: quoted, so they match as whole words. The older
	 * space-padded spelling (`"wam "`) could not survive a round trip through a
	 * text box, and the moment it lost its padding "dit" began firing on edit,
	 * audit, credit and condition.
	 */
	this.DEFAULTS = Object.freeze([
		{
			name: 'VLA',
			keywords: [
				'vision-language-action',
				'vision language action',
				'vla model',
				'vla policy',
				'robot policy',
				'embodied policy',
				'manipulation policy',
				'robotic manipulation',
				'openvla',
				'rt-2',
				'rt2',
				'pi-0',
				'pi0',
				'language-conditioned policy',
				'instruction-following manipulation',
			],
		},
		{
			name: 'World Model',
			keywords: [
				'world model',
				'world models',
				'neural simulator',
				'latent dynamics',
				'dynamics model',
				'video prediction',
				'video generation for robotics',
				'video world model',
				'genie',
				'navworld',
				'dreamerv3',
				'policy world model',
			],
		},
		{
			name: 'WAM',
			keywords: [
				'world action model',
				'"wam"',
				'action world model',
				'joint action prediction',
				'unified action model',
			],
		},
		{
			name: 'VGGT',
			keywords: [
				'vggt',
				'vggsfm',
				'dust3r',
				'mast3r',
				'feed-forward 3d',
				'feedforward 3d',
				'3d foundation model',
				'monocular 3d reconstruction',
				'novel view synthesis',
				'neural radiance',
				'gaussian splatting',
				'3d scene reconstruction',
				'geometry grounded',
				'visual geometry',
			],
		},
		{
			name: 'Agent',
			keywords: [
				'llm agent',
				'llm-based agent',
				'llm-powered agent',
				'embodied agent',
				'multi-agent',
				'multi agent',
				'agentic',
				'agent framework',
				'react agent',
				'reasoning and acting',
				'tool-use agent',
				'tool use agent',
				'gui agent',
				'web agent',
				'planning agent',
				'language agent',
				'foundation model agent',
				'agentic workflow',
				'autonomous agent',
				'agent benchmark',
			],
		},
		{
			name: 'Diffusion',
			keywords: [
				'diffusion policy',
				'diffusion model',
				'diffusion transformer',
				'"dit"',
				'denoising diffusion',
				'flow matching',
				'latent diffusion',
				'consistency model',
				'score-based',
				'score based generative',
				'rectified flow',
				'video diffusion',
				'stable diffusion',
				'diffusion-based',
				'diffusion based policy',
				'image diffusion',
				'guided diffusion',
				'classifier-free guidance',
			],
		},
		{
			name: 'Multi-modal',
			keywords: [
				'multimodal large language model',
				'multi-modal large language model',
				'mllm',
				'vision-language model',
				'vision language model',
				'vlm',
				'video-llm',
				'video llm',
				'audio-visual',
				'embodied chain-of-thought',
				'spatial reasoning',
				'embodied reasoning',
				'long-horizon planning',
			],
		},
	]);


	//
	// Parsing -- for display only
	//

	/**
	 * The client's reading of `parse_keywords`: split on newlines and commas,
	 * trim, lower-case, drop blanks, de-duplicate preserving first-seen order.
	 *
	 * Order is preserved because it is user-visible; re-sorting would silently
	 * rewrite what they typed. If this and Python's `str.lower` ever disagree on
	 * some exotic character, the stored value wins -- the editor re-renders from
	 * whatever the server returns.
	 *
	 * @param {String} raw
	 * @return {String[]}
	 */
	this.parseKeywords = function (raw) {
		let seen = new Set();
		let terms = [];
		for (let part of String(raw ?? '').split(/[\n,]/)) {
			let term = part.trim().toLowerCase();
			if (!term || seen.has(term)) {
				continue;
			}
			seen.add(term);
			terms.push(term);
		}
		return terms;
	};

	/**
	 * Whether a keyword will be matched as a whole word rather than a substring.
	 *
	 * @param {String} keyword
	 * @return {Boolean}
	 */
	this.isWholeWord = function (keyword) {
		return QUOTED_RE.test(String(keyword ?? ''));
	};

	/**
	 * A keyword as it should be SHOWN.
	 *
	 * Leading and trailing spaces are load-bearing in legacy padded terms such as
	 * `"wam "` -- that padding is what kept it off "swam" before quoting existed
	 * -- so they are rendered as a visible glyph rather than left to collapse
	 * into a word the user did not write. The quotes on a whole-word term are
	 * kept for the same reason: they are the syntax, not decoration.
	 *
	 * @param {String} keyword
	 * @return {String}
	 */
	this.displayKeyword = function (keyword) {
		return String(keyword ?? '').replace(/^ /, '␣').replace(/ $/, '␣');
	};

	/**
	 * Mirrors `_canonical_category`: two-letter subject classes upper-case
	 * (`cs.RO`), longer hyphenated ones lower-case (`cond-mat.stat-mech`).
	 *
	 * @param {String} value
	 * @return {String}
	 */
	this.canonicalCategory = function (value) {
		let dot = value.indexOf('.');
		if (dot < 0) {
			return value.toLowerCase();
		}
		let archive = value.slice(0, dot).toLowerCase();
		let subject = value.slice(dot + 1);
		return archive + '.' + (subject.length == 2 ? subject.toUpperCase() : subject.toLowerCase());
	};

	/**
	 * Mirrors `parse_categories`. Splits on commas, newlines and whitespace.
	 *
	 * Anything that does not look like a category is returned rather than
	 * dropped, so a typo is visible before the save instead of after it.
	 *
	 * @param {String} raw
	 * @return {Object} { categories: String[], invalid: String[] }
	 */
	this.parseCategories = function (raw) {
		let seen = new Set();
		let categories = [];
		let invalid = [];
		for (let part of String(raw ?? '').split(/[\n,\s]+/)) {
			let token = part.trim();
			if (!token) {
				continue;
			}
			if (token.length > MAX_CATEGORY_CHARS || !CATEGORY_RE.test(token)) {
				if (!invalid.includes(token)) {
					invalid.push(token);
				}
				continue;
			}
			let canonical = this.canonicalCategory(token);
			if (seen.has(canonical)) {
				continue;
			}
			seen.add(canonical);
			categories.push(canonical);
		}
		return { categories, invalid };
	};


	//
	// Directions
	//

	/**
	 * Every direction the account has, disabled ones included, in match order.
	 *
	 * The first call for a new account seeds the seven defaults server-side, so
	 * this is never an empty form the user has to guess how to fill in.
	 *
	 * @return {Promise<Object[]>}
	 */
	this.list = function () {
		return Zotero.Pharos.API.request('GET', '/api/daily/directions');
	};

	/**
	 * @param {Object} options
	 * @param {String} options.name
	 * @param {String} options.keywords - the RAW text the user typed, not a parse
	 *     of it. The backend re-parses and its answer is what gets stored.
	 * @param {Boolean} [options.enabled]
	 * @return {Promise<Object>}
	 */
	this.create = function ({ name, keywords, enabled }) {
		let body = { name, keywords };
		if (enabled !== undefined) {
			body.enabled = enabled;
		}
		return Zotero.Pharos.API.request('POST', '/api/daily/directions', { body });
	};

	/**
	 * Partial update. Keys left out of `changes` are left alone, which is what
	 * separates "leave this field" from "set it to this".
	 *
	 * @param {String} directionID
	 * @param {Object} changes - any of name, keywords, enabled, position
	 * @return {Promise<Object>}
	 */
	this.update = function (directionID, changes) {
		return Zotero.Pharos.API.request(
			'PATCH',
			`/api/daily/directions/${encodeURIComponent(directionID)}`,
			{ body: changes }
		);
	};

	/**
	 * Delete one direction. No paper is touched -- matching is derived at query
	 * time, so a direction's disappearance is fully expressed by its absence.
	 * Deleting the last one leaves an empty digest, deliberately, and the backend
	 * will not re-seed. `restoreDefaults()` is the way back.
	 *
	 * @param {String} directionID
	 * @return {Promise}
	 */
	this.remove = function (directionID) {
		return Zotero.Pharos.API.request(
			'DELETE',
			`/api/daily/directions/${encodeURIComponent(directionID)}`
		);
	};

	/**
	 * Rewrite positions from an explicit id order, and return the new order.
	 *
	 * Not cosmetic: position is the tie-break when a paper matches several
	 * directions, so this changes which one a paper is filed under. A partial
	 * list is accepted -- anything unmentioned keeps its relative order after
	 * the listed ones.
	 *
	 * @param {String[]} directionIDs
	 * @return {Promise<Object[]>}
	 */
	this.reorder = function (directionIDs) {
		return Zotero.Pharos.API.request('POST', '/api/daily/directions/reorder', {
			body: { direction_ids: directionIDs },
		});
	};

	/**
	 * Re-create the defaults, one create call each.
	 *
	 * Sequential rather than parallel: position is assigned on insert, and
	 * position is the tie-break, so concurrent requests would restore the seven
	 * in an arbitrary order. A create that is refused -- a name the user still
	 * has -- is skipped rather than aborting the rest, and the count of what
	 * actually landed is reported back so the caller can say "none of them".
	 *
	 * @return {Promise<Number>} how many were created
	 */
	this.restoreDefaults = async function () {
		let added = 0;
		for (let preset of this.DEFAULTS) {
			try {
				await this.create({ name: preset.name, keywords: preset.keywords.join('\n') });
				added++;
			}
			catch (e) {
				// Already present, or refused. Keep going and report the total.
				Zotero.debug(`Pharos: could not restore default direction ${preset.name}: ${e.message}`);
			}
		}
		return added;
	};


	//
	// Sweep configuration
	//

	/**
	 * @return {Promise<Object>} { categories, max_per_day, enabled, seeded, updated_at }
	 */
	this.getConfig = function () {
		return Zotero.Pharos.API.request('GET', '/api/daily/config');
	};

	/**
	 * @param {Object} changes - any of categories, max_per_day, enabled. Omitted
	 *     keys are left alone; `categories` takes raw text or a list.
	 * @return {Promise<Object>} the saved config, in the server's own spelling
	 */
	this.updateConfig = function (changes) {
		return Zotero.Pharos.API.request('PATCH', '/api/daily/config', { body: changes });
	};
};
