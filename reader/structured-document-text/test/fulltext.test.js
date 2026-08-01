import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { getFulltextFromStructuredText } from '../src/fulltext.js';
import { discoverFixtures, isUpdateMode, readExpected, writeExpected } from './helpers.js';

const fixtures = discoverFixtures();

describe('getFulltextFromStructuredText', () => {
	it('uses half-open page content ranges over top-level blocks', () => {
		const structure = {
			catalog: {
				pages: [
					{ contentRange: [[0], [2]] },
					{ contentRange: [[2], [3]] },
				],
			},
			content: [
				{
					type: 'paragraph',
					content: [{ text: 'abcdef' }],
				},
				{
					type: 'list',
					content: [
						{ type: 'listitem', content: [{ text: 'hello' }] },
						{ type: 'listitem', content: [{ text: 'world' }] },
					],
				},
				{
					type: 'paragraph',
					content: [{ text: 'outside' }],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'abcdef\n\nhello\nworld');
		assert.equal(getFulltextFromStructuredText(structure, [1]), 'outside');
		assert.equal(getFulltextFromStructuredText(structure, [1, 0]), 'outside\n\nabcdef\n\nhello\nworld');
	});

	it('emits continuation chains once when all parts are in the selected pages', () => {
		const structure = {
			catalog: {
				pages: [{ contentRange: [[0], [2]] }],
			},
			content: [
				{
					type: 'paragraph',
					nextPart: [1],
					content: [{ text: 'abcdef' }],
				},
				{
					type: 'paragraph',
					previousPart: [0],
					content: [{ text: 'ghij' }],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'abcdef ghij');
	});

	it('keeps page-split slices of a block together in part chains', () => {
		const structure = {
			catalog: {
				pages: [
					{ contentRange: [[0], [0, 0, 11]] },
					{ contentRange: [[0, 0, 11], [2]] },
				],
			},
			content: [
				{
					type: 'paragraph',
					nextPart: [1],
					content: [{ text: 'first-half second-half' }],
				},
				{
					type: 'paragraph',
					previousPart: [0],
					content: [{ text: 'continuation.' }],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0, 1]), 'first-half second-half continuation.');
		assert.equal(getFulltextFromStructuredText(structure, [0]), 'first-half');
		assert.equal(getFulltextFromStructuredText(structure, [1]), 'second-half continuation.');
	});

	it('drops hard hyphen at explicit part boundaries', () => {
		const structure = {
			catalog: {
				pages: [{ contentRange: [[0], [2]] }],
			},
			content: [
				{
					type: 'paragraph',
					nextPart: [1],
					content: [{ text: 'hyphen-' }],
				},
				{
					type: 'paragraph',
					previousPart: [0],
					content: [{ text: 'ated' }],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'hyphenated');
	});

	it('keeps hard hyphen when the next part is not a lowercase continuation', () => {
		const structure = {
			catalog: {
				pages: [{ contentRange: [[0], [2]] }],
			},
			content: [
				{
					type: 'paragraph',
					nextPart: [1],
					content: [{ text: 'Object-' }],
				},
				{
					type: 'paragraph',
					previousPart: [0],
					content: [{ text: 'Shape' }],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'Object-Shape');
	});

	it('treats a nested end boundary as before that child', () => {
		const structure = {
			catalog: {
				pages: [
					{ contentRange: [[0], [1, 0]] },
					{ contentRange: [[1, 0], [2]] },
				],
			},
			content: [
				{
					type: 'paragraph',
					content: [{ text: 'before list' }],
				},
				{
					type: 'list',
					content: [
						{ type: 'listitem', content: [{ text: 'first item' }] },
						{ type: 'listitem', content: [{ text: 'second item' }] },
					],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'before list');
		assert.equal(getFulltextFromStructuredText(structure, [1]), 'first item\nsecond item');
	});

	it('keeps separators when selected pages split sibling children of one container', () => {
		const structure = {
			catalog: {
				pages: [
					{ contentRange: [[0, 0], [0, 1]] },
					{ contentRange: [[0, 1], [0, 2]] },
				],
			},
			content: [
				{
					type: 'list',
					content: [
						{ type: 'listitem', content: [{ text: 'first item' }] },
						{ type: 'listitem', content: [{ text: 'second item' }] },
					],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'first item');
		assert.equal(getFulltextFromStructuredText(structure, [1]), 'second item');
		assert.equal(getFulltextFromStructuredText(structure, [0, 1]), 'first item\nsecond item');
	});

	it('emits nested continuation chains once', () => {
		const structure = {
			catalog: {
				pages: [{ contentRange: [[0], [2]] }],
			},
			content: [
				{
					type: 'list',
					content: [{
						type: 'listitem',
						nextPart: [1, 0],
						content: [{ text: 'programming' }],
					}],
				},
				{
					type: 'list',
					content: [{
						type: 'listitem',
						previousPart: [0, 0],
						content: [{ text: 'and is used' }],
					}],
				},
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'programming and is used');
	});

	it('respects text-offset page boundaries', () => {
		const structure = {
			catalog: {
				pages: [
					{ contentRange: [[0, 0, 0], [0, 0, 3]] },
					{ contentRange: [[0, 0, 3], [0, 0, 6]] },
				],
			},
			content: [{
				type: 'paragraph',
				content: [{ text: 'abcdef' }],
			}],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'abc');
		assert.equal(getFulltextFromStructuredText(structure, [1]), 'def');
		assert.equal(getFulltextFromStructuredText(structure, [0, 1]), 'abcdef');
		assert.equal(getFulltextFromStructuredText(structure, [1, 0]), 'defabc');
	});

	it('respects text-offset boundaries across multiple blocks', () => {
		const structure = {
			catalog: {
				pages: [
					{ contentRange: [[0, 0, 1], [2, 0, 2]] },
				],
			},
			content: [
				{ type: 'paragraph', content: [{ text: 'abc' }] },
				{ type: 'paragraph', content: [{ text: 'def' }] },
				{ type: 'paragraph', content: [{ text: 'ghi' }] },
			],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), 'bc\n\ndef\n\ngh');
	});

	it('treats equal boundaries as known empty pages', () => {
		const structure = {
			catalog: {
				pages: [
					{ contentRange: [[0], [0]] },
					{ contentRange: [[0], [1]] },
					{ contentRange: [[1], [1]] },
				],
			},
			content: [{
				type: 'paragraph',
				content: [{ text: 'visible' }],
			}],
		};

		assert.equal(getFulltextFromStructuredText(structure, [0]), '');
		assert.equal(getFulltextFromStructuredText(structure, [1]), 'visible');
		assert.equal(getFulltextFromStructuredText(structure, [2]), '');
	});

	for (const { format, name, path, data } of fixtures) {
		describe(`${format}/${name}`, () => {
			it('produces expected fulltext', () => {
				const pageIndexes = data.catalog.pages.map((_, i) => i);
				const result = getFulltextFromStructuredText(data, pageIndexes);

				if (isUpdateMode()) {
					writeExpected(path, name, 'fulltext.txt', result);
					return;
				}

				const expected = readExpected(path, name, 'fulltext.txt');
				assert.notEqual(expected, undefined, `Missing expected file: ${name}.fulltext.txt (run npm run test:update)`);
				assert.equal(result, expected);
			});
		});
	}
});
