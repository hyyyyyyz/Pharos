import {
	compareRefs,
	refKey,
	sameRef,
	walkContentRangeLeafBlocks,
} from './range.js';
import { getPartBoundarySeparator, shouldDropHardHyphenAtPartBoundary } from './parts.js';

/**
 * Convert a structure object into a fulltext string for the given page indexes.
 *
 * @param {Object} structure - The structured data from getFullStructure/getStructure
 * @param {number[]} pageIndexes - Array of page indexes to include
 * @returns {string} The fulltext string, NFC normalized
 */
export function getFulltextFromStructuredText(structure, pageIndexes) {
	const content = Array.isArray(structure?.content) ? structure.content : [];
	const pages = Array.isArray(structure?.catalog?.pages) ? structure.catalog.pages : [];
	const entries = [];
	const entriesByRef = new Map();
	const blockTexts = [];
	let previous = null;

	for (const pageIndex of pageIndexes) {
		const page = pages[pageIndex];
		if (!page) {
			continue;
		}
		for (const entry of getRangeBlockTexts(content, page.contentRange)) {
			if (!entry.text) {
				continue;
			}
			entry.index = entries.length;
			entries.push(entry);
			const key = refKey(entry.ref);
			const slices = entriesByRef.get(key);
			if (slices) {
				slices.push(entry);
			}
			else {
				entriesByRef.set(key, [entry]);
			}
		}
	}

	const emittedIndexes = new Set();
	for (const entry of entries) {
		if (emittedIndexes.has(entry.index)) {
			continue;
		}

		const chain = getSelectedPartChain(entry, entriesByRef, emittedIndexes);
		for (let i = 0; i < chain.length; i++) {
			const part = chain[i];
			if (i === 0) {
				if (previous) {
					blockTexts.push(getEntrySeparator(previous, part));
				}
			}
			else if (sameRef(chain[i - 1].ref, part.ref)) {
				blockTexts.push(getEntrySeparator(chain[i - 1], part));
			}
			else {
				if (shouldDropHardHyphenAtPartBoundary(chain[i - 1].block, part.block)) {
					stripTrailingHyphen(blockTexts);
				}
				blockTexts.push(getPartBoundarySeparator(chain[i - 1].block, part.block));
			}
			blockTexts.push(part.text);
			emittedIndexes.add(part.index);
			previous = part;
		}
	}

	return blockTexts.join('').replace(/[ \t]+$/gm, '').trim();
}

function stripTrailingHyphen(blockTexts) {
	const lastIndex = blockTexts.length - 1;
	if (lastIndex < 0 || !blockTexts[lastIndex].endsWith('-')) {
		return;
	}
	blockTexts[lastIndex] = blockTexts[lastIndex].slice(0, -1);
}

function getSelectedPartChain(entry, entriesByRef, emittedIndexes) {
	const chain = [];
	const seenRefs = new Set();
	let current = entry;
	while (current && !emittedIndexes.has(current.index)) {
		const key = refKey(current.ref);
		if (seenRefs.has(key)) {
			break;
		}
		seenRefs.add(key);
		// A block split by page boundaries yields one slice per page; keep
		// them all together before following the part link.
		for (const slice of entriesByRef.get(key)) {
			if (!emittedIndexes.has(slice.index)) {
				chain.push(slice);
			}
		}

		if (!Array.isArray(current.block?.nextPart)) {
			break;
		}
		current = entriesByRef.get(refKey(current.block.nextPart))?.[0];
	}
	return chain;
}

function getEntrySeparator(previous, current) {
	if (sameBlockSlicesTouch(previous, current)) {
		return '';
	}
	if (arePartNeighbors(previous, current)) {
		return getPartBoundarySeparator(previous.block, current.block);
	}
	return getOutputSeparator(previous.ref, current.ref);
}

function sameBlockSlicesTouch(previous, current) {
	return sameRef(previous.ref, current.ref)
		&& (
			samePoint(previous.endPoint, current.startPoint)
			|| samePoint(current.endPoint, previous.startPoint)
		);
}

function arePartNeighbors(previous, current) {
	return sameRef(previous.block?.nextPart, current.ref)
		|| sameRef(current.block?.previousPart, previous.ref)
		|| sameRef(previous.block?.previousPart, current.ref)
		|| sameRef(current.block?.nextPart, previous.ref);
}

function samePoint(a, b) {
	return sameRef(a?.ref, b?.ref) && a?.offset === b?.offset;
}

function getRangeBlockTexts(content, range) {
	const blockTexts = [];
	walkContentRangeLeafBlocks(content, range, ({ block, ref, startPoint, endPoint }) => {
		if (block.flowClass === 'excluded') {
			return;
		}
		const text = getBlockRangeText(block, ref, startPoint, endPoint);
		if (text) {
			blockTexts.push({ ref, block, text, startPoint, endPoint });
		}
	});
	return blockTexts;
}

function getBlockRangeText(block, blockRef, startPoint, endPoint) {
	const segments = flattenPlainTextSegments(block, blockRef);
	if (!segments.some(segment => segment.type === 'text')) {
		return '';
	}

	const blockStartPoint = getPointInBlock(startPoint, blockRef);
	const blockEndPoint = getPointInBlock(endPoint, blockRef);
	const startIndex = blockStartPoint
		? findStartSegmentIndex(segments, blockStartPoint)
		: findFirstTextSegmentIndex(segments);
	const endIndex = blockEndPoint
		? findEndSegmentIndex(segments, blockEndPoint)
		: findLastTextSegmentIndex(segments);

	if (startIndex === -1 || endIndex === -1 || startIndex > endIndex) {
		return '';
	}

	const parts = [];
	let pendingSeparator = '';
	for (let i = startIndex; i <= endIndex; i++) {
		const segment = segments[i];
		if (segment.type === 'separator') {
			pendingSeparator += segment.text;
			continue;
		}

		const text = getSegmentRangeText(
			segment,
			i === startIndex ? blockStartPoint : null,
			i === endIndex ? blockEndPoint : null
		);
		if (!text) {
			continue;
		}

		if (parts.length && pendingSeparator) {
			parts.push(pendingSeparator);
		}
		pendingSeparator = '';
		parts.push(text);
	}
	return parts.join('');
}

function getPointInBlock(point, blockRef) {
	if (!isRefPrefix(blockRef, point?.ref)) {
		return null;
	}
	return point;
}

function isRefPrefix(prefix, ref) {
	return Array.isArray(prefix)
		&& Array.isArray(ref)
		&& prefix.length <= ref.length
		&& prefix.every((value, index) => value === ref[index]);
}

function flattenPlainTextSegments(node, ref) {
	if (!node) {
		return [];
	}
	if (typeof node.text === 'string') {
		return [{ type: 'text', ref, text: node.text }];
	}
	if (!Array.isArray(node.content)) {
		return [];
	}

	const hasChildBlock = node.content.some(child => child && typeof child.text !== 'string');
	if (!hasChildBlock) {
		const segments = [];
		for (let i = 0; i < node.content.length; i++) {
			const child = node.content[i];
			if (child && typeof child.text === 'string') {
				segments.push({ type: 'text', ref: [...ref, i], text: child.text });
			}
		}
		return segments;
	}

	const segments = [];
	for (let i = 0; i < node.content.length; i++) {
		const child = node.content[i];
		if (!child || typeof child.text === 'string') {
			continue;
		}
		const childSegments = flattenPlainTextSegments(child, [...ref, i]);
		if (!childSegments.length) {
			continue;
		}
		if (segments.length) {
			segments.push({ type: 'separator', text: '\n' });
		}
		segments.push(...childSegments);
	}
	return segments;
}

function getSegmentRangeText(segment, startPoint, endPoint) {
	const length = segment.text.length;
	if (!length) {
		return '';
	}

	let startOffset = sameRef(startPoint?.ref, segment.ref) && Number.isInteger(startPoint.offset)
		? startPoint.offset
		: 0;
	let endOffset = sameRef(endPoint?.ref, segment.ref) && Number.isInteger(endPoint.offset)
		? endPoint.offset
		: length;

	startOffset = clamp(startOffset, 0, length);
	endOffset = clamp(endOffset, 0, length);
	if (endOffset <= startOffset) {
		return '';
	}

	return segment.text.slice(startOffset, endOffset);
}

function findStartSegmentIndex(segments, point) {
	if (Number.isInteger(point.offset)) {
		return findExactTextSegmentIndex(segments, point.ref);
	}
	for (let i = 0; i < segments.length; i++) {
		const segment = segments[i];
		if (segment.type === 'text' && compareRefs(segment.ref, point.ref) >= 0) {
			return i;
		}
	}
	return -1;
}

function findEndSegmentIndex(segments, point) {
	if (Number.isInteger(point.offset)) {
		return findExactTextSegmentIndex(segments, point.ref);
	}
	for (let i = 0; i < segments.length; i++) {
		const segment = segments[i];
		if (segment.type === 'text' && compareRefs(segment.ref, point.ref) >= 0) {
			return findPreviousTextSegmentIndex(segments, i - 1);
		}
	}
	return findLastTextSegmentIndex(segments);
}

function findExactTextSegmentIndex(segments, ref) {
	for (let i = 0; i < segments.length; i++) {
		const segment = segments[i];
		if (segment.type !== 'text') {
			continue;
		}
		if (sameRef(segment.ref, ref)) {
			return i;
		}
	}
	return -1;
}

function findFirstTextSegmentIndex(segments) {
	for (let i = 0; i < segments.length; i++) {
		if (segments[i].type === 'text') {
			return i;
		}
	}
	return -1;
}

function findPreviousTextSegmentIndex(segments, index) {
	for (let i = index; i >= 0; i--) {
		if (segments[i].type === 'text') {
			return i;
		}
	}
	return -1;
}

function findLastTextSegmentIndex(segments) {
	for (let i = segments.length - 1; i >= 0; i--) {
		if (segments[i].type === 'text') {
			return i;
		}
	}
	return -1;
}

function getOutputSeparator(previousRef, currentRef) {
	if (sameRef(previousRef, currentRef)) {
		return '\n\n';
	}
	if (Array.isArray(previousRef) && Array.isArray(currentRef) && previousRef[0] === currentRef[0]) {
		return '\n';
	}
	return '\n\n';
}

function clamp(value, min, max) {
	return Math.max(min, Math.min(value, max));
}
