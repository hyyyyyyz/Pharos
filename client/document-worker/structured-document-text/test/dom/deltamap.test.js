import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
	composeDeltaMaps,
	getDelta,
	nfcToOriginal,
} from '../../src/dom/deltamap.js';

describe('deltamap: composeDeltaMaps', () => {
	it('passes through when either map is empty', () => {
		assert.equal(composeDeltaMaps('', '', 10), '');
		assert.equal(composeDeltaMaps('4 -1', '', 10), '4 -1');
		assert.equal(composeDeltaMaps(undefined, '6 -2', 10), '6 -2');
	});

	it('composes NFC and whitespace-collapse shifts', () => {
		// raw "café \n bar" --(collapse)--> "café bar" --(NFC)--> "café bar"
		// collapse map (collapsed -> raw): '6 -2'; NFC map (NFC -> collapsed): '4 -1'
		let composed = composeDeltaMaps('4 -1', '6 -2', 8);
		assert.equal(composed, '4 -1 5 -3');
		// é spans raw [3, 5); space maps to raw 5; 'b' maps to raw 8; end maps to raw 11
		assert.equal(nfcToOriginal(composed, 3), 3);
		assert.equal(nfcToOriginal(composed, 4), 5);
		assert.equal(nfcToOriginal(composed, 5), 8);
		assert.equal(nfcToOriginal(composed, 8), 11);
	});

	it('keeps positions before the first shift unchanged', () => {
		let composed = composeDeltaMaps('4 -1', '6 -2', 8);
		for (let pos = 0; pos <= 3; pos++) {
			assert.equal(getDelta(composed, pos), 0);
		}
	});

	it('accumulates shifts from both maps', () => {
		// Inner shift becomes visible at final position 1, outer adds at 2
		let composed = composeDeltaMaps('2 -1', '1 -1', 4);
		assert.equal(composed, '1 -1 2 -2');
		assert.equal(nfcToOriginal(composed, 1), 2);
		assert.equal(nfcToOriginal(composed, 2), 4);
		assert.equal(nfcToOriginal(composed, 4), 6);
	});
});
