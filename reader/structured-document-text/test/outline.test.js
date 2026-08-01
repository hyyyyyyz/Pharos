import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { exportOutline } from '../src/outline.js';
import { discoverFixtures, isUpdateMode, readExpected, writeExpected } from './helpers.js';

const fixtures = discoverFixtures();

describe('exportOutline', () => {
	for (const { format, name, path, data } of fixtures) {
		describe(`${format}/${name}`, () => {
			it('produces expected outline', () => {
				const result = exportOutline(data);
				const resultJSON = JSON.stringify(result, null, 2);

				if (isUpdateMode()) {
					writeExpected(path, name, 'outline.json', resultJSON);
					return;
				}

				const expected = readExpected(path, name, 'outline.json');
				assert.notEqual(expected, undefined, `Missing expected file: ${name}.outline.json (run npm run test:update)`);
				assert.deepStrictEqual(result, JSON.parse(expected));
			});
		});
	}
});
