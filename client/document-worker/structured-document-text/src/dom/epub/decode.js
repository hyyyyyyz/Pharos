import { nfcToOriginal, nfcToOriginalLocal } from '../deltamap.js';

/**
 * EPUB selectorMap decode utilities.
 *
 * SelectorMaps are stored relative to their parent block's CFI path.
 * Single-entry: a CFI path suffix starting with '/'.
 * Multi-entry (merged text nodes): newline-separated "charLen suffix" entries,
 * starting with a digit.
 *
 * Use expandSelectorMap() to reconstruct absolute paths from the block anchor.
 * Character offsets are appended as `:offset` per the EPUB CFI spec.
 * Ranges use CFI range syntax: `epubcfi(common_path,local_start,local_end)`.
 */

const CONFORMSTO = 'http://www.idpf.org/epub/linking/cfi/epub-cfi.html';

/**
 * Expand a relative text node selectorMap to absolute paths using the block's selectorMap.
 *
 * @param {string} blockSelectorMap - the block's absolute CFI path
 * @param {string} selectorMap - a relative selectorMap (suffix or multi-entry).
 * 		An empty string means that the block path is already the text node path.
 * @returns {string} absolute selectorMap
 */
export function expandSelectorMap(blockSelectorMap, selectorMap) {
	if (!selectorMap) return blockSelectorMap;
	let entries = parseSelectorMapEntries(selectorMap);
	if (entries) {
		return entries.map(e => e.length + ' ' + blockSelectorMap + e.path).join('\n');
	}
	return blockSelectorMap + selectorMap;
}

/**
 * Expand a block's selectorMap to a full WADM FragmentSelector.
 *
 * @param {string} blockSelectorMap - the block's absolute CFI path
 * @returns {{ type: string, conformsTo: string, value: string }}
 */
export function expandBlockAnchor(blockSelectorMap) {
	return {
		type: 'FragmentSelector',
		conformsTo: CONFORMSTO,
		value: `epubcfi(${blockSelectorMap})`,
	};
}

/**
 * Parse a selectorMap into entries. Returns null for single-entry selectorMaps.
 *
 * @param {string} selectorMap
 * @returns {{ length: number, path: string }[] | null}
 */
export function parseSelectorMapEntries(selectorMap) {
	if (selectorMap.startsWith('/')) return null;
	return selectorMap.split('\n').map(line => {
		let sp = line.indexOf(' ');
		return {
			length: parseInt(line.substring(0, sp)),
			path: line.substring(sp + 1),
		};
	});
}

/**
 * Resolve a character range to a CFI range.
 * Handles both single-entry and multi-entry (merged) selectorMaps.
 *
 * @param {string} selectorMap - absolute CFI path or multi-entry selectorMap
 * @param {number} start - start character index (inclusive)
 * @param {number} end - end character index (exclusive)
 * @returns {{ type: string, conformsTo: string, value: string }}
 */
/**
 * @param {string} selectorMap - absolute CFI path or multi-entry selectorMap
 * @param {number} start - start character index (inclusive, NFC space)
 * @param {number} [end] - end character index (exclusive, NFC space)
 * @param {string} [deltaMap] - optional NFC deltaMap for position translation
 */
export function resolveSelectorMap(selectorMap, start, end, deltaMap) {
	let entries = parseSelectorMapEntries(selectorMap);
	if (entries) {
		return resolveMultiEntry(entries, start, end !== undefined ? end : start + 1, deltaMap);
	}

	let origStart = nfcToOriginal(deltaMap, start);
	let origEnd = nfcToOriginal(deltaMap, end !== undefined ? end : start + 1);
	if (origEnd === origStart + 1) {
		return {
			type: 'FragmentSelector',
			conformsTo: CONFORMSTO,
			value: `epubcfi(${selectorMap}:${origStart})`,
		};
	}
	return {
		type: 'FragmentSelector',
		conformsTo: CONFORMSTO,
		value: `epubcfi(${selectorMap},:${origStart},:${origEnd})`,
	};
}

function resolveMultiEntry(entries, start, end, deltaMap) {
	let sEntry = findEntryAtWithCumulative(entries, start);
	let eEntry = findEntryAtWithCumulative(entries, end - 1);

	let origStartLocal = nfcToOriginalLocal(deltaMap, sEntry.cumulative, sEntry.local);
	let origEndLocal = nfcToOriginalLocal(deltaMap, eEntry.cumulative, eEntry.local) + 1;

	if (sEntry.path === eEntry.path) {
		if (origEndLocal === origStartLocal + 1) {
			return {
				type: 'FragmentSelector',
				conformsTo: CONFORMSTO,
				value: `epubcfi(${sEntry.path}:${origStartLocal})`,
			};
		}
		return {
			type: 'FragmentSelector',
			conformsTo: CONFORMSTO,
			value: `epubcfi(${sEntry.path},:${origStartLocal},:${origEndLocal})`,
		};
	}

	return resolveSelectorMapRange(sEntry.path, origStartLocal, eEntry.path, origEndLocal);
}

/**
 * Like findEntryAt but also returns the cumulative offset for deltaMap translation.
 */
function findEntryAtWithCumulative(entries, offset) {
	let cumulative = 0;
	for (let i = 0; i < entries.length; i++) {
		if (offset < cumulative + entries[i].length) {
			return { path: entries[i].path, local: offset - cumulative, cumulative };
		}
		cumulative += entries[i].length;
	}
	let last = entries[entries.length - 1];
	return { path: last.path, local: last.length, cumulative: cumulative - last.length };
}

/**
 * Find the longest common CFI path prefix between two paths.
 */
export function findCommonCFIPath(a, b) {
	if (a === b) {
		return { common: a, remainderA: '', remainderB: '' };
	}

	let stepsA = a.match(/\/[^/]*/g) || [];
	let stepsB = b.match(/\/[^/]*/g) || [];

	let commonLen = 0;
	let min = Math.min(stepsA.length, stepsB.length);
	for (let i = 0; i < min; i++) {
		if (stepsA[i] !== stepsB[i]) break;
		commonLen = i + 1;
	}

	let common = stepsA.slice(0, commonLen).join('');
	let remainderA = stepsA.slice(commonLen).join('');
	let remainderB = stepsB.slice(commonLen).join('');

	return { common, remainderA, remainderB };
}

/**
 * Resolve a cross-node character range to a CFI range.
 *
 * Each side accepts either an absolute CFI path or a multi-entry selectorMap;
 * multi-entry inputs are resolved to a specific sub-entry based on the offset.
 * If deltaMaps are supplied, offsets are translated from NFC to original space.
 *
 * @param {string} startSelectorMap - absolute CFI path or multi-entry selectorMap
 * @param {number} startOffset - character offset in start node (NFC space if startDeltaMap provided)
 * @param {string} endSelectorMap - absolute CFI path or multi-entry selectorMap
 * @param {number} endOffset - character offset in end node (NFC space if endDeltaMap provided)
 * @param {string} [startDeltaMap]
 * @param {string} [endDeltaMap]
 */
export function resolveSelectorMapRange(
	startSelectorMap, startOffset, endSelectorMap, endOffset,
	startDeltaMap, endDeltaMap,
) {
	if (startSelectorMap === endSelectorMap) {
		return resolveSelectorMap(startSelectorMap, startOffset, endOffset, startDeltaMap);
	}

	let start = resolveOnePoint(startSelectorMap, startOffset, startDeltaMap);
	let end = resolveOnePoint(endSelectorMap, endOffset, endDeltaMap);

	if (start.path === end.path) {
		return {
			type: 'FragmentSelector',
			conformsTo: CONFORMSTO,
			value: `epubcfi(${start.path},:${start.offset},:${end.offset})`,
		};
	}

	let { common, remainderA, remainderB } = findCommonCFIPath(start.path, end.path);

	return {
		type: 'FragmentSelector',
		conformsTo: CONFORMSTO,
		value: `epubcfi(${common},${remainderA}:${start.offset},${remainderB}:${end.offset})`,
	};
}

function resolveOnePoint(selectorMap, offset, deltaMap) {
	let entries = parseSelectorMapEntries(selectorMap);
	if (entries) {
		let { path, local, cumulative } = findEntryAtWithCumulative(entries, offset);
		return { path, offset: nfcToOriginalLocal(deltaMap, cumulative, local) };
	}
	return { path: selectorMap, offset: nfcToOriginal(deltaMap, offset) };
}
