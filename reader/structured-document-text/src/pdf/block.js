/**
 * Block operations: navigation, text extraction, ranges, and cursors.
 */

import { parseTextMap, buildRunData } from './decode.js';
import { isWhitespaceChar, sameRef } from './utils.js';

function mapTextToRuns(text, runData) {
	const rects = [];
	const pageIndexes = [];
	let runIndex = 0;

	for (let i = 0; i < text.length; i++) {
		const ch = text[i];
		if (isWhitespaceChar(ch)) {
			rects.push(null);
			pageIndexes.push(null);
			continue;
		}
		const current = runIndex < runData.length ? runData[runIndex++] : null;
		rects.push(current ? current.rect : null);
		pageIndexes.push(current ? current.pageIndex : null);
	}

	return { rects, pageIndexes };
}

function appendText(output, text, property, rects, pageIndexes) {
	if (!text) {
		return;
	}
	output.textParts.push(text);
	for (let i = 0; i < text.length; i++) {
		output.attrs.push(property);
		output.rects.push(rects ? rects[i] : null);
		output.pageIndexes.push(pageIndexes ? pageIndexes[i] : null);
	}
}

function appendTextNodes(output, nodes) {
	if (!Array.isArray(nodes)) {
		return;
	}
	for (const node of nodes) {
		if (!node || typeof node.text !== 'string') {
			continue;
		}
		const text = node.text;
		const property = {
			style: node.style ?? null,
			target: node.target ?? null,
			refs: node.refs ?? null,
			backRefs: node.backRefs ?? null,
		};
		const runs = buildRunData(parseTextMap(node.anchor?.textMap));
		const mapped = runs.length ? mapTextToRuns(text, runs) : null;
		appendText(output, text, property, mapped?.rects ?? null, mapped?.pageIndexes ?? null);
	}
}

function walkTextNodes(node, output) {
	if (!node || typeof node !== 'object') {
		return;
	}
	if (typeof node.text === 'string') {
		appendTextNodes(output, [node]);
		return;
	}
	if (Array.isArray(node.content)) {
		for (const child of node.content) {
			walkTextNodes(child, output);
		}
	}
}

/**
 * Get block by reference path.
 */
export function getBlockByRef(structure, blockRef) {
	if (!structure || !Array.isArray(blockRef)) {
		return null;
	}
	let node = { content: structure.content };
	for (const index of blockRef) {
		if (!node || !Array.isArray(node.content)) {
			return null;
		}
		node = node.content[index];
		if (!node || typeof node !== 'object') {
			return null;
		}
	}
	return node;
}

/**
 * Get text content with attributes and positions from a block.
 */
export function getBlockText(structure, blockRef) {
	const output = {
		textParts: [],
		attrs: [],
		rects: [],
		pageIndexes: [],
	};

	const block = getBlockByRef(structure, blockRef);
	if (block) {
		walkTextNodes(block, output);
	}

	return {
		text: output.textParts.join(''),
		attrs: output.attrs,
		rects: output.rects,
		pageIndexes: output.pageIndexes,
	};
}

/**
 * Get next block reference in document order.
 */
export function getNextBlockRef(structure, currentBlockRef = null) {
	const isTextNode = (node) => !!node.text;
	const isBlockNode = (node) => node && !isTextNode(node);

	const ref = currentBlockRef;
	const state = { found: ref === null };

	const walk = (content, baseRef) => {
		for (let i = 0; i < content.length; i++) {
			const node = content[i];
			if (!isBlockNode(node)) continue;
			const nodeRef = [...baseRef, i];

			if (state.found) {
				return nodeRef;
			}
			if (ref && sameRef(nodeRef, ref)) {
				state.found = true;
			}

			const childRef = walk(node.content, nodeRef);
			if (childRef) {
				return childRef;
			}
		}
		return null;
	};

	return walk(structure.content, []);
}

/**
 * Get text nodes that overlap with a range.
 */
export function getTextNodesAtRange(structure, blockRef, offsetStart, offsetEnd) {
	const block = getBlockByRef(structure, blockRef);
	if (!block) {
		return null;
	}

	if (!Number.isInteger(offsetStart) || !Number.isInteger(offsetEnd)) {
		return null;
	}

	if (offsetEnd < offsetStart) {
		[offsetStart, offsetEnd] = [offsetEnd, offsetStart];
	}

	let currentOffset = 0;
	const results = [];

	const walkTextNodesWithRefs = (node, path) => {
		if (!node || typeof node !== 'object') {
			return true;
		}
		if (typeof node.text === 'string') {
			const len = node.text.length;
			const nodeStart = currentOffset;
			const nodeEnd = currentOffset + len;
			currentOffset = nodeEnd;

			// Check if this node overlaps with the range (inclusive)
			if (nodeEnd - 1 >= offsetStart && nodeStart <= offsetEnd) {
				results.push({
					ref: [...blockRef, ...path],
					offset: Math.max(0, offsetStart - nodeStart),
					endOffset: Math.min(len - 1, offsetEnd - nodeStart)
				});
			}

			// Stop if we've passed the range
			if (nodeStart > offsetEnd) {
				return false;
			}

			return true;
		}
		if (Array.isArray(node.content)) {
			for (let i = 0; i < node.content.length; i++) {
				const child = node.content[i];
				const shouldContinue = walkTextNodesWithRefs(child, [...path, i]);
				if (!shouldContinue) {
					return false;
				}
			}
		}
		return true;
	};

	walkTextNodesWithRefs(block, []);

	return results;
}

// ═══════════════════════════════════════════════════════════════════════════
// Cursor Navigation
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Get next character in document order, advancing cursor.
 */
export function nextChar(structure, cursor) {
	if (!structure || !Array.isArray(structure.content) || !cursor || typeof cursor !== 'object') {
		return null;
	}

	const isTextNode = (node) => node && typeof node.text === 'string';

	const getNodeByRef = (content, ref) => {
		if (!Array.isArray(ref) || ref.length === 0) {
			return null;
		}
		let current = { content };
		for (const index of ref) {
			if (!current || !Array.isArray(current.content)) {
				return null;
			}
			if (!Number.isInteger(index) || index < 0 || index >= current.content.length) {
				return null;
			}
			current = current.content[index];
		}
		return current;
	};

	const findFirstTextRefInNode = (node, baseRef) => {
		if (!node) {
			return null;
		}
		if (isTextNode(node)) {
			return baseRef;
		}
		if (!Array.isArray(node.content)) {
			return null;
		}
		for (let i = 0; i < node.content.length; i++) {
			const childRef = findFirstTextRefInNode(node.content[i], [...baseRef, i]);
			if (childRef) {
				return childRef;
			}
		}
		return null;
	};

	const getPathInfo = (content, ref) => {
		if (!Array.isArray(ref) || ref.length === 0) {
			return null;
		}
		const pathInfo = [];
		let currentContent = content;
		for (const index of ref) {
			if (!Array.isArray(currentContent)) {
				return null;
			}
			if (!Number.isInteger(index) || index < 0 || index >= currentContent.length) {
				return null;
			}
			pathInfo.push({ parentContent: currentContent, index });
			const node = currentContent[index];
			currentContent = Array.isArray(node?.content) ? node.content : null;
		}
		return pathInfo;
	};

	const findNextTextRef = (content, ref) => {
		const pathInfo = getPathInfo(content, ref);
		if (!pathInfo) {
			return null;
		}
		for (let depth = pathInfo.length - 1; depth >= 0; depth--) {
			const { parentContent, index } = pathInfo[depth];
			for (let nextIndex = index + 1; nextIndex < parentContent.length; nextIndex++) {
				const baseRef = [...ref.slice(0, depth), nextIndex];
				const found = findFirstTextRefInNode(parentContent[nextIndex], baseRef);
				if (found) {
					return found;
				}
			}
		}
		return null;
	};

	let currentRef = Array.isArray(cursor.ref) ? cursor.ref : null;
	let currentOffset = Number.isInteger(cursor.offset) ? cursor.offset : 0;

	while (true) {
		if (!currentRef) {
			cursor.ref = null;
			cursor.offset = 0;
			return null;
		}

		const node = getNodeByRef(structure.content, currentRef);
		if (!node) {
			currentRef = findNextTextRef(structure.content, currentRef);
			currentOffset = 0;
			if (!currentRef) {
				cursor.ref = null;
				cursor.offset = 0;
				return null;
			}
			continue;
		}

		if (!isTextNode(node)) {
			const nestedRef = findFirstTextRefInNode(node, currentRef);
			if (nestedRef) {
				currentRef = nestedRef;
				currentOffset = 0;
			} else {
				currentRef = findNextTextRef(structure.content, currentRef);
				currentOffset = 0;
			}
			if (!currentRef) {
				cursor.ref = null;
				cursor.offset = 0;
				return null;
			}
			continue;
		}

		if (!Number.isInteger(currentOffset) || currentOffset < 0) {
			currentOffset = 0;
		}

		if (currentOffset < node.text.length) {
			const ch = node.text.charAt(currentOffset);
			cursor.ref = currentRef;
			cursor.offset = currentOffset + 1;
			return ch;
		}

		currentRef = findNextTextRef(structure.content, currentRef);
		currentOffset = 0;
		if (!currentRef) {
			cursor.ref = null;
			cursor.offset = 0;
			return null;
		}
	}
}

/**
 * Get next character within a single top-level block.
 */
export function nextBlockChar(structure, cursor) {
	if (!structure || !Array.isArray(structure.content) || !cursor || typeof cursor !== 'object') {
		return null;
	}

	const blockIndex = Array.isArray(cursor.ref) ? cursor.ref[0] : null;
	if (!Number.isInteger(blockIndex) || blockIndex < 0 || blockIndex >= structure.content.length) {
		cursor.ref = null;
		cursor.offset = 0;
		return null;
	}

	const block = structure.content[blockIndex];
	const localCursor = {
		ref: Array.isArray(cursor.ref) ? [0, ...cursor.ref.slice(1)] : [0],
		offset: Number.isInteger(cursor.offset) ? cursor.offset : 0
	};

	const ch = nextChar({ content: [block] }, localCursor);
	if (ch === null) {
		cursor.ref = null;
		cursor.offset = 0;
		return null;
	}

	cursor.ref = [blockIndex, ...localCursor.ref.slice(1)];
	cursor.offset = localCursor.offset;
	return ch;
}
