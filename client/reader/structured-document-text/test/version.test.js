import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
	SDT_PACK_VERSION,
	SDT_SCHEMA_VERSION,
} from '../src/version.js';

describe('version constants', () => {
	it('uses an explicit numeric pack version', () => {
		assert.equal(SDT_PACK_VERSION, 1);
	});

	it('uses an explicit semver schema version', () => {
		assert.match(SDT_SCHEMA_VERSION, /^[0-9]+\.[0-9]+\.[0-9]+$/u);
	});
});
