/**
 * DeltaMap utilities for NFC normalization position mapping.
 *
 * A deltaMap is a compact string encoding position shifts between
 * NFC-normalized text and the original DOM text. Format: space-separated
 * "nfcPos delta" pairs where delta = nfcIdx - origIdx.
 *
 * When absent/empty, all positions are 1:1 (text was already NFC).
 *
 * Decode formula: origPos = nfcPos - getDelta(deltaMap, nfcPos)
 */

/**
 * Parse a deltaMap string into an array of [nfcPos, delta] pairs.
 *
 * @param {string} deltaMap
 * @returns {number[][]}
 */
function parseDeltaMap(deltaMap) {
	let pairs = deltaMap.split(' ');
	let result = [];
	for (let i = 0; i < pairs.length - 1; i += 2) {
		result.push([parseInt(pairs[i], 10), parseInt(pairs[i + 1], 10)]);
	}
	return result;
}

function getDeltaFromPairs(pairs, nfcPos) {
	let delta = 0;
	for (let i = 0; i < pairs.length; i++) {
		if (pairs[i][0] > nfcPos) break;
		delta = pairs[i][1];
	}
	return delta;
}

/**
 * Get the running delta at a given NFC position.
 * delta = nfcIdx - origIdx (negative when NFC is shorter than original).
 *
 * @param {string | undefined} deltaMap
 * @param {number} nfcPos
 * @returns {number}
 */
export function getDelta(deltaMap, nfcPos) {
	if (!deltaMap) return 0;
	return getDeltaFromPairs(parseDeltaMap(deltaMap), nfcPos);
}

/**
 * Translate an NFC position to the original DOM position.
 *
 * @param {string | undefined} deltaMap
 * @param {number} nfcPos
 * @returns {number}
 */
export function nfcToOriginal(deltaMap, nfcPos) {
	return nfcPos - getDelta(deltaMap, nfcPos);
}

/**
 * Translate a local NFC offset within a multi-entry selectorMap entry
 * to the corresponding local original offset.
 *
 * @param {string | undefined} deltaMap
 * @param {number} entryStartNFC - cumulative NFC position where the entry starts
 * @param {number} localNFCPos - position within the entry (NFC space)
 * @returns {number}
 */
export function nfcToOriginalLocal(deltaMap, entryStartNFC, localNFCPos) {
	if (!deltaMap) return localNFCPos;
	let entryDelta = getDelta(deltaMap, entryStartNFC);
	let posDelta = getDelta(deltaMap, entryStartNFC + localNFCPos);
	return localNFCPos - (posDelta - entryDelta);
}

/**
 * Compose two deltaMaps applied in sequence: `outer` maps positions in the
 * final text to an intermediate text, `inner` maps the intermediate text to
 * the original. Returns a deltaMap from final positions straight to original
 * positions (e.g. NFC -> whitespace-collapsed -> raw DOM text).
 *
 * @param {string | undefined} outer - final -> intermediate
 * @param {string | undefined} inner - intermediate -> original
 * @param {number} finalLength - character length of the final text
 * @returns {string} composed deltaMap, or '' when all positions are 1:1
 */
export function composeDeltaMaps(outer, inner, finalLength) {
	if (!outer) return inner || '';
	if (!inner) return outer;
	let outerPairs = parseDeltaMap(outer);
	let innerPairs = parseDeltaMap(inner);
	let entries = [];
	let prevDelta = 0;
	for (let pos = 0; pos <= finalLength; pos++) {
		let intermediate = pos - getDeltaFromPairs(outerPairs, pos);
		let original = intermediate - getDeltaFromPairs(innerPairs, intermediate);
		let delta = pos - original;
		if (delta !== prevDelta) {
			entries.push(pos + ' ' + delta);
			prevDelta = delta;
		}
	}
	return entries.join(' ');
}

/**
 * Merge two deltaMaps when concatenating text nodes.
 *
 * @param {string | undefined} mapA - deltaMap for the first text node
 * @param {string | undefined} mapB - deltaMap for the second text node
 * @param {number} nfcLenA - NFC text length of the first node
 * @returns {string} merged deltaMap, or '' if empty
 */
export function mergeDeltaMaps(mapA, mapB, nfcLenA) {
	let entriesA = mapA ? parseDeltaMap(mapA) : [];
	let entriesB = mapB ? parseDeltaMap(mapB) : [];

	// Trailing delta from A: the delta that carries over into B's range
	let trailingDeltaA = entriesA.length > 0
		? entriesA[entriesA.length - 1][1]
		: 0;

	let merged = entriesA.slice();

	for (let i = 0; i < entriesB.length; i++) {
		let shiftedPos = entriesB[i][0] + nfcLenA;
		let adjustedDelta = entriesB[i][1] + trailingDeltaA;
		// Only add if delta actually changes from the current running value
		let lastDelta = merged.length > 0 ? merged[merged.length - 1][1] : 0;
		if (adjustedDelta !== lastDelta) {
			merged.push([shiftedPos, adjustedDelta]);
		}
	}

	if (merged.length === 0) return '';
	return merged.map(([p, d]) => p + ' ' + d).join(' ');
}
