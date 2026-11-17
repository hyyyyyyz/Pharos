/**
 * Block transformations: attribute application.
 */

import {
	HEADER_AXIS_DIR_SHIFT,
	HEADER_LAST_IS_SOFT_HYPHEN,
	EPS,
	isVertical,
} from './constants.js';
import { parseTextMap, reconstructCharPositions } from './decode.js';
import { getBlockByRef } from './block.js';
import { isWhitespaceChar } from './utils.js';
import {
	setContentRangeEnd,
	setContentRangeStart,
	splitContentRange,
} from '../range.js';
import { stringifyTextMap } from './text-map.js';

function sliceTextMap(textMap, text, startOffset, endOffset) {
	const runs = parseTextMap(textMap);
	if (!runs.length) {
		return null;
	}

	// `startOffset` and `endOffset` are offsets into `text` (counting whitespace),
	// while textMap runs encode one position per non-whitespace character. Convert
	// text offsets to non-whitespace position offsets before slicing so the slice
	// lines up with the right characters.
	let nwStart = 0, nwEnd = 0;
	for (let i = 0; i < endOffset; i++) {
		if (isWhitespaceChar(text[i])) continue;
		if (i < startOffset) nwStart++;
		nwEnd++;
	}
	startOffset = nwStart;
	endOffset = nwEnd;

	const newRuns = [];
	let charIndex = 0;

	for (const run of runs) {
		if (!Array.isArray(run) || run.length < 6) {
			continue;
		}

		const [header, pageIndex, minX, minY, maxX, maxY] = run;
		const allPositions = reconstructCharPositions(run);
		const hasSoftHyphen = !!(header & HEADER_LAST_IS_SOFT_HYPHEN);
		const charPositions = hasSoftHyphen ? allPositions.slice(0, -1) : allPositions;

		const runStart = charIndex;
		const runEnd = charIndex + charPositions.length;

		// Skip runs completely outside range
		if (runEnd <= startOffset || runStart >= endOffset) {
			charIndex = runEnd;
			continue;
		}

		// Calculate slice boundaries within this run
		const sliceStart = Math.max(0, startOffset - runStart);
		const sliceEnd = Math.min(charPositions.length, endOffset - runStart);
		const slicedPositions = charPositions.slice(sliceStart, sliceEnd);

		if (slicedPositions.length > 0) {
			const keepSoftHyphen = hasSoftHyphen && sliceEnd === charPositions.length;
			const newHeader = keepSoftHyphen
				? header
				: header & ~HEADER_LAST_IS_SOFT_HYPHEN;

			// Re-encode sliced positions as per-glyph widths
			const axisDir = (header >> HEADER_AXIS_DIR_SHIFT) & 0b11;
			const vertical = isVertical(axisDir);
			const newRun = [newHeader, pageIndex, minX, minY, maxX, maxY];
			const emit = keepSoftHyphen
				? [...slicedPositions, allPositions[allPositions.length - 1]]
				: slicedPositions;
			let pos = vertical ? minY : minX;
			for (const emitPos of emit) {
				if (!emitPos || !Number.isFinite(emitPos.x1) || !Number.isFinite(emitPos.x2)) continue;
				const delta = emitPos.x1 - pos;
				const width = emitPos.x2 - emitPos.x1;
				if (Math.abs(delta) > EPS) {
					newRun.push([delta, width]);
				}
				else {
					newRun.push(width);
				}
				pos = emitPos.x2;
			}

			newRuns.push(newRun);
		}

		charIndex = runEnd;
	}

	return newRuns.length ? stringifyTextMap(newRuns) : null;
}

/**
 * Apply a callback to text nodes within a range, splitting nodes if necessary.
 */
export function applyTextAttributes(structure, blockRef, offsetStart, offsetEnd, callback) {
	const block = getBlockByRef(structure, blockRef);
	if (!block) {
		return null;
	}

	const rangeUpdates = collectContentRangeBoundaryUpdates(structure, block, blockRef);

	// Treat offsetEnd as inclusive; normalize to a sane range.
	if (!Number.isInteger(offsetStart) || !Number.isInteger(offsetEnd)) {
		return null;
	}
	if (offsetEnd < offsetStart) {
		[offsetStart, offsetEnd] = [offsetEnd, offsetStart];
	}

	let currentOffset = 0;
	let targetTextNodeRef = null;
	let didSplit = false;

	const processNode = (node, parentRef) => {
		if (!node || typeof node !== 'object') {
			return node;
		}

		// If it's a text node
		if (typeof node.text === 'string') {
			const text = node.text;
			const nodeStart = currentOffset;
			const nodeEnd = currentOffset + text.length;
			currentOffset = nodeEnd;

			// No overlap (inclusive end)
			if (nodeEnd - 1 < offsetStart || nodeStart > offsetEnd) {
				return node;
			}

			// Complete overlap - apply callback to entire node
			if (nodeStart >= offsetStart && nodeEnd - 1 <= offsetEnd) {
				const result = callback(node);
				if (!targetTextNodeRef) {
					targetTextNodeRef = parentRef;
				}
				return result;
			}

			// Partial overlap - need to split
			const result = [];
			didSplit = true;
			const hasAnchor = node.anchor?.textMap;

			// Before range
			if (nodeStart < offsetStart) {
				const beforeText = text.substring(0, offsetStart - nodeStart);
				const beforeNode = {
					...node,
					text: beforeText,
				};

				if (hasAnchor && node.anchor) {
					const slicedMap = sliceTextMap(node.anchor.textMap, text, 0, offsetStart - nodeStart);
					if (slicedMap) {
						beforeNode.anchor = { ...node.anchor, textMap: slicedMap };
					} else {
						delete beforeNode.anchor;
					}
				}

				result.push(beforeNode);
			}

			// Inside range - apply callback (offsetEnd inclusive => end is +1)
			const rangeStart = Math.max(0, offsetStart - nodeStart);
			const rangeEnd = Math.min(text.length, offsetEnd - nodeStart + 1);
			const rangeText = text.substring(rangeStart, rangeEnd);
			const rangeNode = {
				...node,
				text: rangeText,
			};

			if (hasAnchor && node.anchor) {
				const slicedMap = sliceTextMap(node.anchor.textMap, text, rangeStart, rangeEnd);
				if (slicedMap) {
					rangeNode.anchor = { ...node.anchor, textMap: slicedMap };
				} else {
					delete rangeNode.anchor;
				}
			}

			const callbackResult = callback(rangeNode);
			if (!targetTextNodeRef) {
				const indexInResult = nodeStart < offsetStart ? 1 : 0;
				const parentPath = parentRef.slice(0, -1);
				const parentIndex = parentRef[parentRef.length - 1];
				targetTextNodeRef = [...parentPath, parentIndex + indexInResult];
			}
			result.push(callbackResult);

			// After range
			if (nodeEnd - 1 > offsetEnd) {
				const afterText = text.substring(offsetEnd - nodeStart + 1);
				const afterNode = {
					...node,
					text: afterText,
				};

				if (hasAnchor && node.anchor) {
					const slicedMap = sliceTextMap(node.anchor.textMap, text, offsetEnd - nodeStart + 1, text.length);
					if (slicedMap) {
						afterNode.anchor = { ...node.anchor, textMap: slicedMap };
					} else {
						delete afterNode.anchor;
					}
				}

				result.push(afterNode);
			}

			return result;
		}

		// If it has content array, recurse
		if (Array.isArray(node.content)) {
			const newContent = [];
			for (let i = 0; i < node.content.length; i++) {
				const child = node.content[i];
				const childRef = [...parentRef, newContent.length];
				const processed = processNode(child, childRef);
				if (Array.isArray(processed)) {
					newContent.push(...processed);
				} else if (processed) {
					newContent.push(processed);
				}
			}
			return {
				...node,
				content: newContent,
			};
		}

		return node;
	};

	// Persist processed changes back into the referenced block
	const updatedBlock = processNode(block, blockRef);

	if (Array.isArray(updatedBlock)) {
		// Unexpected, but handle defensively by replacing block content
		block.content = updatedBlock;
		return targetTextNodeRef;
	}

	if (updatedBlock && updatedBlock !== block) {
		Object.assign(block, updatedBlock);
	}

	if (didSplit) {
		applyContentRangeBoundaryUpdates(block, blockRef, rangeUpdates);
	}

	return targetTextNodeRef;
}

function collectContentRangeBoundaryUpdates(structure, block, blockRef) {
	if (!Array.isArray(structure?.catalog?.pages) || !Array.isArray(blockRef)) {
		return [];
	}

	const updates = [];
	for (const page of structure.catalog.pages) {
		if (!Array.isArray(page?.contentRange)) {
			continue;
		}
		let parts;
		try {
			parts = splitContentRange(page.contentRange, structure.content);
		}
		catch (_) {
			continue;
		}
		for (const point of ['start', 'end']) {
			const boundary = parts[point];
			if (!Array.isArray(boundary?.ref) || !startsWithRef(boundary.ref, blockRef)) {
				continue;
			}
			if (sameRef(boundary.ref, blockRef)) {
				continue;
			}
			const absoluteOffset = getAbsoluteOffsetForBoundary(block, blockRef, boundary.ref, boundary.offset);
			if (!Number.isInteger(absoluteOffset)) {
				continue;
			}
			updates.push({
				range: page.contentRange,
				point,
				absoluteOffset,
				hasOffset: Number.isInteger(boundary.offset),
			});
		}
	}
	return updates;
}

function applyContentRangeBoundaryUpdates(block, blockRef, updates) {
	for (const update of updates) {
		const mapped = getBoundaryForAbsoluteOffset(block, blockRef, update.absoluteOffset, update.hasOffset);
		if (!mapped) {
			continue;
		}
		if (update.point === 'start') {
			setContentRangeStart(update.range, mapped.ref, mapped.offset);
		}
		else {
			setContentRangeEnd(update.range, mapped.ref, mapped.offset);
		}
	}
}

function startsWithRef(ref, prefix) {
	if (!Array.isArray(ref) || !Array.isArray(prefix) || prefix.length > ref.length) {
		return false;
	}
	for (let i = 0; i < prefix.length; i++) {
		if (ref[i] !== prefix[i]) {
			return false;
		}
	}
	return true;
}

function sameRef(a, b) {
	return Array.isArray(a)
		&& Array.isArray(b)
		&& a.length === b.length
		&& a.every((value, index) => value === b[index]);
}

function getTextSegments(root, rootRef) {
	const segments = [];
	let currentOffset = 0;
	const visit = (node, localRef) => {
		if (!node || typeof node !== 'object') {
			return;
		}
		if (typeof node.text === 'string') {
			const start = currentOffset;
			const end = start + node.text.length;
			segments.push({
				ref: [...rootRef, ...localRef],
				start,
				end,
				length: node.text.length,
			});
			currentOffset = end;
			return;
		}
		if (!Array.isArray(node.content)) {
			return;
		}
		for (let i = 0; i < node.content.length; i++) {
			visit(node.content[i], [...localRef, i]);
		}
	};
	visit(root, []);
	return segments;
}

function getAbsoluteOffsetForBoundary(block, blockRef, targetRef, targetOffset) {
	const segments = getTextSegments(block, blockRef);
	for (let i = 0; i < segments.length; i++) {
		const segment = segments[i];
		if (sameRef(segment.ref, targetRef)) {
			if (Number.isInteger(targetOffset)) {
				return segment.start + Math.max(0, Math.min(targetOffset, segment.length));
			}
			return segment.start;
		}
		if (startsWithRef(segment.ref, targetRef)) {
			// Half-open boundaries that ref a container resolve to the
			// container's first character for both start and end points.
			return segment.start;
		}
	}

	for (let i = 0; i < segments.length; i++) {
		const segment = segments[i];
		if (compareRefs(segment.ref, targetRef) >= 0) {
			return segment.start;
		}
	}
	return segments.length ? segments[segments.length - 1].end : 0;
}

function getBoundaryForAbsoluteOffset(block, blockRef, absoluteOffset, forceOffset) {
	const segments = getTextSegments(block, blockRef);
	if (!segments.length) {
		return { ref: [...blockRef], offset: undefined };
	}

	const totalLength = segments[segments.length - 1].end;
	const offset = Math.max(0, Math.min(absoluteOffset, totalLength));

	if (!forceOffset) {
		for (const segment of segments) {
			if (segment.start === offset) {
				return { ref: segment.ref, offset: undefined };
			}
		}
	}

	for (const segment of segments) {
		if (offset >= segment.start && offset <= segment.end) {
			return {
				ref: segment.ref,
				offset: forceOffset || offset !== segment.start ? offset - segment.start : undefined,
			};
		}
	}

	const last = segments[segments.length - 1];
	return {
		ref: last.ref,
		offset: forceOffset ? last.length : undefined,
	};
}

function compareRefs(a, b) {
	const length = Math.min(a.length, b.length);
	for (let i = 0; i < length; i++) {
		if (a[i] !== b[i]) {
			return a[i] - b[i];
		}
	}
	return a.length - b.length;
}
