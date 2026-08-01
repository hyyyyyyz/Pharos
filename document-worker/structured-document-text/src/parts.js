import { HEADER_LAST_IS_SOFT_HYPHEN } from './pdf/constants.js';
import { parseTextMap } from './pdf/decode.js';
import { refKey } from './range.js';
import { getNestedBlockPlainText } from './text.js';

function getNodeByRef(structure, ref) {
	if (!Array.isArray(ref)) {
		return null;
	}
	let node = { content: structure?.content };
	for (const index of ref) {
		if (!Number.isInteger(index) || !Array.isArray(node?.content)) {
			return null;
		}
		node = node.content[index];
		if (!node || typeof node !== 'object') {
			return null;
		}
	}
	return node;
}

function getFirstTextNode(node) {
	if (!node || typeof node !== 'object') {
		return null;
	}
	if (typeof node.text === 'string') {
		return node;
	}
	if (!Array.isArray(node.content)) {
		return null;
	}
	for (const child of node.content) {
		const textNode = getFirstTextNode(child);
		if (textNode) {
			return textNode;
		}
	}
	return null;
}

function getLastTextNode(node) {
	if (!node || typeof node !== 'object') {
		return null;
	}
	if (typeof node.text === 'string') {
		return node;
	}
	if (!Array.isArray(node.content)) {
		return null;
	}
	for (let i = node.content.length - 1; i >= 0; i--) {
		const textNode = getLastTextNode(node.content[i]);
		if (textNode) {
			return textNode;
		}
	}
	return null;
}

function textNodeEndsWithSoftHyphen(node) {
	const runs = parseTextMap(node?.anchor?.textMap);
	const lastRun = runs.at(-1);
	return Array.isArray(lastRun) && (lastRun[0] & HEADER_LAST_IS_SOFT_HYPHEN) !== 0;
}

export function shouldDropHardHyphenAtPartBoundary(prevBlock, nextBlock) {
	const lastNode = getLastTextNode(prevBlock);
	const firstNode = getFirstTextNode(nextBlock);
	const firstChar = firstNode?.text?.charAt(0);
	return typeof lastNode?.text === 'string'
		&& lastNode.text.endsWith('-')
		&& firstChar >= 'a'
		&& firstChar <= 'z';
}

function getPartRoot(structure, ref) {
	let currentRef = Array.isArray(ref) ? [...ref] : null;
	const seen = new Set();
	while (currentRef) {
		const key = refKey(currentRef);
		if (seen.has(key)) {
			return currentRef;
		}
		seen.add(key);
		const node = getNodeByRef(structure, currentRef);
		if (!Array.isArray(node?.previousPart)) {
			return currentRef;
		}
		currentRef = [...node.previousPart];
	}
	return null;
}

export function getPartChain(structure, ref, options = {}) {
	const include = typeof options.include === 'function' ? options.include : null;
	const root = getPartRoot(structure, ref);
	if (!Array.isArray(root)) {
		return [];
	}

	const chain = [];
	const seen = new Set();
	let currentRef = [...root];
	while (currentRef) {
		const key = refKey(currentRef);
		if (seen.has(key)) {
			break;
		}
		seen.add(key);
		const node = getNodeByRef(structure, currentRef);
		if (!node) {
			break;
		}
		if (include && !include(currentRef, node)) {
			break;
		}
		chain.push({ ref: currentRef, block: node });
		if (!Array.isArray(node.nextPart)) {
			break;
		}
		currentRef = [...node.nextPart];
	}
	return chain;
}

export function getPartBoundarySeparator(prevBlock, nextBlock) {
	const lastNode = getLastTextNode(prevBlock);
	const firstNode = getFirstTextNode(nextBlock);
	if (!lastNode || !firstNode) {
		return '';
	}
	if (/\s$/.test(lastNode.text) || /^\s/.test(firstNode.text)) {
		return '';
	}
	if (textNodeEndsWithSoftHyphen(lastNode)) {
		return '';
	}
	if (/[\p{L}\p{N}]$/u.test(lastNode.text) && /^[\p{L}\p{N}]/u.test(firstNode.text)) {
		return ' ';
	}
	return '';
}

export function getLogicalBlockText(structure, ref, options = {}) {
	const chain = getPartChain(structure, ref, options);
	if (!chain.length) {
		const block = getNodeByRef(structure, ref);
		return block ? getNestedBlockPlainText(block) : '';
	}

	let text = '';
	for (let i = 0; i < chain.length; i++) {
		const blockText = getNestedBlockPlainText(chain[i].block);
		if (i > 0) {
			if (shouldDropHardHyphenAtPartBoundary(chain[i - 1].block, chain[i].block) && text.endsWith('-')) {
				text = text.slice(0, -1);
			}
			text += getPartBoundarySeparator(chain[i - 1].block, chain[i].block);
		}
		text += blockText;
	}
	return text;
}
