import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { splitContentPoint } from '../src/range.js';

describe('content range helpers', () => {
	it('requires content when splitting compact content points', () => {
		assert.throws(
			() => splitContentPoint([0, 0, 12]),
			/requires content/
		);
	});
});
