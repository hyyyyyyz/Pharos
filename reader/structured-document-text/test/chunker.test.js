import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { getNextChunk } from '../src/chunker.js';
import { discoverFixtures, isUpdateMode, readExpected, writeExpected } from './helpers.js';

const fixtures = discoverFixtures();

function getAllChunks(structure) {
	const chunks = [];
	let blockIndex = 0;
	while (true) {
		const chunk = getNextChunk(structure, blockIndex);
		if (!chunk) break;
		chunks.push(chunk);
		blockIndex = chunk.endBlockIndex + 1;
	}
	return chunks;
}

describe('getNextChunk', () => {
	for (const { format, name, path, data } of fixtures) {
		describe(`${format}/${name}`, () => {
			it('produces expected chunks', () => {
				const result = getAllChunks(data);
				const resultJSON = JSON.stringify(result, null, 2);

				if (isUpdateMode()) {
					writeExpected(path, name, 'chunks.json', resultJSON);
					return;
				}

				const expected = readExpected(path, name, 'chunks.json');
				assert.notEqual(expected, undefined, `Missing expected file: ${name}.chunks.json (run npm run test:update)`);
				assert.deepStrictEqual(result, JSON.parse(expected));
			});
		});
	}
});
