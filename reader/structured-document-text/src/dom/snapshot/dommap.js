/**
 * Snapshot domMap utilities.
 *
 * The domMap (catalog.domMap) is a skeleton of the source DOM: every
 * anchored element and its ancestors, with body-stream text offsets
 * (see DomMapNode in the schema). It allows resolving and generating
 * CssSelector/TextPositionSelector positions rooted at arbitrary
 * ancestor elements without access to the source DOM.
 *
 * Selector grammar covered (everything the encoder and the reader's
 * getUniqueSelectorContaining() produce):
 *
 *   selector  = anchor (' > ' segment)*
 *   anchor    = '#id' | '#id + tag' | 'body' | segment
 *   segment   = tag pseudo?
 *   pseudo    = ':first-child' | ':first-of-type' | ':last-child'
 *             | ':last-of-type' | ':nth-child(n)'
 *
 */

// DomMapNode flags bits:
export const DOM_MAP_FIRST_OF_TYPE = 1;
export const DOM_MAP_LAST_OF_TYPE = 2;
export const DOM_MAP_LAST_CHILD = 4;

/**
 * Indexed domMap: skeleton nodes with parent links, plus lookups.
 *
 * @typedef {Object} DomMapIndexNode
 * @property {import('../../../schema').DomMapNode} node
 * @property {DomMapIndexNode | null} parent
 * @property {DomMapIndexNode[]} children
 */

/**
 * Build an index over a domMap tree.
 *
 * @param {import('../../../schema').DomMapNode[] | undefined} domMap
 * @returns {{ roots: DomMapIndexNode[], nodes: DomMapIndexNode[] } | null}
 */
export function buildDomMapIndex(domMap) {
	if (!Array.isArray(domMap) || !domMap.length) {
		return null;
	}
	let nodes = [];
	let build = (node, parent) => {
		let indexed = { node, parent, children: [] };
		nodes.push(indexed);
		for (let child of node.children || []) {
			indexed.children.push(build(child, indexed));
		}
		return indexed;
	};
	let roots = domMap.map(node => build(node, null));
	return { roots, nodes };
}

/**
 * Find the deepest skeleton node whose subtree text contains
 * [start, end) of the body text stream. Returns null when only <body>
 * itself contains the range (or the range is outside all skeleton text).
 *
 * @param {{ roots: DomMapIndexNode[] }} index
 * @param {number} start - inclusive body-stream offset
 * @param {number} end - exclusive body-stream offset (> start)
 * @returns {DomMapIndexNode | null}
 */
export function findDomMapContaining(index, start, end) {
	let contains = indexed => indexed.node.textStart <= start
		&& end <= indexed.node.textStart + indexed.node.textLength;
	let result = null;
	let candidates = index.roots;
	for (;;) {
		let next = candidates.find(contains);
		if (!next) {
			return result;
		}
		result = next;
		candidates = next.children;
	}
}

/**
 * Generate a CSS selector for a skeleton node, using the same rules as
 * the encoder's anchor selectors: walk up to <body>, short-circuiting at
 * the nearest element with an id; otherwise root the path at 'body'.
 *
 * @param {DomMapIndexNode} indexed
 * @returns {string}
 */
export function generateDomMapSelector(indexed) {
	let segments = [];
	let current = indexed;
	while (current) {
		if (current.node.id) {
			segments.unshift('#' + cssEscape(current.node.id));
			return segments.join(' > ');
		}
		segments.unshift(domMapSegment(current.node));
		current = current.parent;
	}
	segments.unshift('body');
	return segments.join(' > ');
}

/**
 * Generate one path segment ('tag', 'tag:first-child', ...) for an
 * element. Shared by generateDomMapSelector() and the encoder, so anchor
 * selectorMaps and skeleton-generated selectors always agree.
 *
 * @param {{ tag: string, index: number, flags?: number }} node
 * @returns {string}
 */
export function domMapSegment(node) {
	let flags = node.flags || 0;
	let pseudo;
	if ((flags & DOM_MAP_FIRST_OF_TYPE) && (flags & DOM_MAP_LAST_OF_TYPE)) {
		// Only one of its tag among the siblings
		pseudo = '';
	}
	else if (node.index === 0) {
		pseudo = ':first-child';
	}
	else if (flags & DOM_MAP_FIRST_OF_TYPE) {
		pseudo = ':first-of-type';
	}
	else if (flags & DOM_MAP_LAST_CHILD) {
		pseudo = ':last-child';
	}
	else if (flags & DOM_MAP_LAST_OF_TYPE) {
		pseudo = ':last-of-type';
	}
	else {
		pseudo = ':nth-child(' + (node.index + 1) + ')';
	}
	return node.tag + pseudo;
}

/**
 * Find the skeleton node a CSS selector refers to. Returns null when the
 * selector is outside the supported grammar, matches nothing, or matches
 * more than one node (which a correctly-generated selector never does).
 *
 * @param {{ nodes: DomMapIndexNode[] }} index
 * @param {string} selector
 * @returns {DomMapIndexNode | null}
 */
export function matchDomMapSelector(index, selector) {
	let parsed = parseSelectorPath(selector);
	if (!parsed) {
		return null;
	}
	let match = null;
	for (let indexed of index.nodes) {
		if (matchesAt(indexed, parsed)) {
			if (match) {
				// Ambiguous
				return null;
			}
			match = indexed;
		}
	}
	return match;
}

/**
 * Parse a selector into an anchor and a chain of segments.
 * Returns null for selectors outside the grammar.
 */
function parseSelectorPath(selector) {
	let parts = selector.split(' > ');
	let anchor = null;
	let firstSegment = null;
	let first = parts[0];
	if (first === 'body') {
		if (parts.length === 1) {
			// 'body' alone never targets a skeleton node
			return null;
		}
		anchor = { type: 'body' };
		parts = parts.slice(1);
	}
	else if (first.startsWith('#')) {
		let plus = first.indexOf(' + ');
		if (plus !== -1) {
			// '#prevId + tag' (reader's previous-sibling shortcut)
			firstSegment = parseSegment(first.substring(plus + 3));
			if (!firstSegment) {
				return null;
			}
			firstSegment.prevId = first.substring(1, plus);
		}
		else if (parts.length === 1) {
			// '#id' alone: the target is the element with that id
			firstSegment = { id: first.substring(1) };
		}
		else {
			anchor = { type: 'id', id: first.substring(1) };
		}
		parts = parts.slice(1);
	}
	let segments = parts.map(parseSegment);
	if (firstSegment) {
		segments.unshift(firstSegment);
	}
	if (!segments.length || segments.some(s => !s)) {
		return null;
	}
	return { anchor, segments };
}

function parseSegment(part) {
	if (typeof part !== 'string' || !part) {
		return null;
	}
	let m = /^([a-zA-Z][a-zA-Z0-9-]*)(?::(first-child|first-of-type|last-child|last-of-type|nth-child\((\d+)\)))?$/.exec(part);
	if (!m) {
		return null;
	}
	let segment = { tag: m[1].toLowerCase() };
	if (m[2]) {
		if (m[3]) {
			segment.pseudo = 'nth-child';
			segment.n = parseInt(m[3]);
		}
		else {
			segment.pseudo = m[2];
		}
	}
	return segment;
}

function matchesAt(indexed, parsed) {
	let { anchor, segments } = parsed;
	let current = indexed;
	for (let i = segments.length - 1; i >= 0; i--) {
		if (!current || !matchesSegment(current.node, segments[i])) {
			return false;
		}
		current = current.parent;
	}
	if (anchor) {
		if (anchor.type === 'body') {
			// The chain must start directly under <body>
			return current === null;
		}
		if (anchor.type === 'id') {
			return !!current && !!current.node.id && cssEscape(current.node.id) === anchor.id;
		}
	}
	return true;
}

function matchesSegment(node, segment) {
	if (segment.id !== undefined) {
		return !!node.id && cssEscape(node.id) === segment.id;
	}
	if (node.tag !== segment.tag) {
		return false;
	}
	if (segment.prevId !== undefined
			&& (!node.prevId || cssEscape(node.prevId) !== segment.prevId)) {
		return false;
	}
	let flags = node.flags || 0;
	switch (segment.pseudo) {
		case undefined:
			return true;
		case 'first-child':
			return node.index === 0;
		case 'first-of-type':
			return !!(flags & DOM_MAP_FIRST_OF_TYPE);
		case 'last-child':
			return !!(flags & DOM_MAP_LAST_CHILD);
		case 'last-of-type':
			return !!(flags & DOM_MAP_LAST_OF_TYPE);
		case 'nth-child':
			return node.index + 1 === segment.n;
		default:
			return false;
	}
}

/**
 * CSS.escape() for environments without a DOM (per the CSSOM spec /
 * standard polyfill).
 *
 * @param {string} value
 * @returns {string}
 */
export function cssEscape(value) {
	let string = String(value);
	let result = '';
	let firstCodeUnit = string.charCodeAt(0);
	for (let i = 0; i < string.length; i++) {
		let codeUnit = string.charCodeAt(i);
		if (codeUnit === 0x0000) {
			result += '�';
			continue;
		}
		if (
			(codeUnit >= 0x0001 && codeUnit <= 0x001F) || codeUnit === 0x007F
			|| (i === 0 && codeUnit >= 0x0030 && codeUnit <= 0x0039)
			|| (i === 1 && codeUnit >= 0x0030 && codeUnit <= 0x0039 && firstCodeUnit === 0x002D)
		) {
			result += '\\' + codeUnit.toString(16) + ' ';
			continue;
		}
		if (i === 0 && string.length === 1 && codeUnit === 0x002D) {
			result += '\\' + string.charAt(i);
			continue;
		}
		if (
			codeUnit >= 0x0080 || codeUnit === 0x002D || codeUnit === 0x005F
			|| (codeUnit >= 0x0030 && codeUnit <= 0x0039)
			|| (codeUnit >= 0x0041 && codeUnit <= 0x005A)
			|| (codeUnit >= 0x0061 && codeUnit <= 0x007A)
		) {
			result += string.charAt(i);
			continue;
		}
		result += '\\' + string.charAt(i);
	}
	return result;
}
