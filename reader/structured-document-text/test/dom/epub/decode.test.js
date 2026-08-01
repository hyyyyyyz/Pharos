import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {
	expandSelectorMap,
	expandBlockAnchor,
	resolveSelectorMap,
	resolveSelectorMapRange,
	findCommonCFIPath,
	parseSelectorMapEntries,
} from '../../../src/dom/epub/decode.js';
import { mergeNodesWithSelectorMap } from '../../../src/text.js';

const CONFORMSTO = 'http://www.idpf.org/epub/linking/cfi/epub-cfi.html';

describe('EPUB decode: resolveSelectorMap', () => {
	it('resolves a point (single character)', () => {
		let result = resolveSelectorMap('/6/2[ch1.xhtml]!/2/4/1', 5);
		assert.deepStrictEqual(result, {
			type: 'FragmentSelector', conformsTo: CONFORMSTO,
			value: 'epubcfi(/6/2[ch1.xhtml]!/2/4/1:5)',
		});
	});

	it('resolves a point when end === start + 1', () => {
		let result = resolveSelectorMap('/6/2[ch1.xhtml]!/2/4/1', 5, 6);
		assert.equal(result.value, 'epubcfi(/6/2[ch1.xhtml]!/2/4/1:5)');
	});

	it('resolves a range within one text node', () => {
		let result = resolveSelectorMap('/6/2[ch1.xhtml]!/2/4/1', 5, 10);
		assert.equal(result.value, 'epubcfi(/6/2[ch1.xhtml]!/2/4/1,:5,:10)');
	});

	it('resolves offset 0', () => {
		let result = resolveSelectorMap('/6/4[imprint.xhtml]!/2[imprint]/4/1', 0);
		assert.equal(result.value, 'epubcfi(/6/4[imprint.xhtml]!/2[imprint]/4/1:0)');
	});

	it('handles deeply nested CFI paths', () => {
		let p = '/6/10[ch5.xhtml]!/2[body]/4/2/6/2/1';
		let result = resolveSelectorMap(p, 42, 100);
		assert.equal(result.value, `epubcfi(${p},:42,:100)`);
	});
});

describe('EPUB decode: findCommonCFIPath', () => {
	it('finds common path for same text node', () => {
		let p = '/6/2[ch1.xhtml]!/2/4/1';
		let { common, remainderA, remainderB } = findCommonCFIPath(p, p);
		assert.equal(common, p);
		assert.equal(remainderA, '');
		assert.equal(remainderB, '');
	});

	it('finds common path for sibling text nodes', () => {
		let { common, remainderA, remainderB } = findCommonCFIPath(
			'/6/2[ch1.xhtml]!/2/4/1', '/6/2[ch1.xhtml]!/2/4/3');
		assert.equal(common, '/6/2[ch1.xhtml]!/2/4');
		assert.equal(remainderA, '/1');
		assert.equal(remainderB, '/3');
	});

	it('finds common path for text nodes in different elements', () => {
		let { common, remainderA, remainderB } = findCommonCFIPath(
			'/6/2[ch1.xhtml]!/2/4/2/1', '/6/2[ch1.xhtml]!/2/4/3');
		assert.equal(common, '/6/2[ch1.xhtml]!/2/4');
		assert.equal(remainderA, '/2/1');
		assert.equal(remainderB, '/3');
	});

	it('finds common path across spine items', () => {
		let { common, remainderA, remainderB } = findCommonCFIPath(
			'/6/2[ch1.xhtml]!/2/4/1', '/6/4[ch2.xhtml]!/2/2/1');
		assert.equal(common, '/6');
		assert.equal(remainderA, '/2[ch1.xhtml]!/2/4/1');
		assert.equal(remainderB, '/4[ch2.xhtml]!/2/2/1');
	});

	it('handles paths with id assertions', () => {
		let { common, remainderA, remainderB } = findCommonCFIPath(
			'/6/4[imprint.xhtml]!/2[imprint]/4/1', '/6/4[imprint.xhtml]!/2[imprint]/4/2/1');
		assert.equal(common, '/6/4[imprint.xhtml]!/2[imprint]/4');
		assert.equal(remainderA, '/1');
		assert.equal(remainderB, '/2/1');
	});
});

describe('EPUB decode: resolveSelectorMapRange', () => {
	it('resolves same-node range', () => {
		let p = '/6/2[ch1.xhtml]!/2/4/1';
		let result = resolveSelectorMapRange(p, 5, p, 10);
		assert.equal(result.value, `epubcfi(${p},:5,:10)`);
	});

	it('resolves cross-node range (sibling text nodes)', () => {
		let result = resolveSelectorMapRange(
			'/6/2[ch1.xhtml]!/2/4/1', 5, '/6/2[ch1.xhtml]!/2/4/3', 10);
		assert.equal(result.value, 'epubcfi(/6/2[ch1.xhtml]!/2/4,/1:5,/3:10)');
	});

	it('resolves cross-node range (text in inline element to bare text)', () => {
		let result = resolveSelectorMapRange(
			'/6/2[titlepage.xhtml]!/2[titlepage]/4/2/1', 3,
			'/6/2[titlepage.xhtml]!/2[titlepage]/4/3', 0);
		assert.equal(result.value,
			'epubcfi(/6/2[titlepage.xhtml]!/2[titlepage]/4,/2/1:3,/3:0)');
	});

	it('resolves cross-spine range', () => {
		let result = resolveSelectorMapRange(
			'/6/2[ch1.xhtml]!/2/4/1', 100, '/6/4[ch2.xhtml]!/2/2/1', 0);
		assert.equal(result.value,
			'epubcfi(/6,/2[ch1.xhtml]!/2/4/1:100,/4[ch2.xhtml]!/2/2/1:0)');
	});

	it('resolves multi-entry start to specific sub-entry path', () => {
		let multiEntry = '5 /6/2[ch1.xhtml]!/2/4/1\n10 /6/2[ch1.xhtml]!/2/4/2/1';
		let result = resolveSelectorMapRange(
			multiEntry, 7, '/6/4[ch2.xhtml]!/2/2/1', 0);
		// offset 7 falls in the second sub-entry at local offset 2
		assert.equal(result.value,
			'epubcfi(/6,/2[ch1.xhtml]!/2/4/2/1:2,/4[ch2.xhtml]!/2/2/1:0)');
	});

	it('resolves multi-entry end to specific sub-entry path', () => {
		let multiEntry = '5 /6/4[ch2.xhtml]!/2/2/1\n10 /6/4[ch2.xhtml]!/2/2/3';
		let result = resolveSelectorMapRange(
			'/6/2[ch1.xhtml]!/2/4/1', 100, multiEntry, 6);
		// offset 6 falls in the second sub-entry at local offset 1
		assert.equal(result.value,
			'epubcfi(/6,/2[ch1.xhtml]!/2/4/1:100,/4[ch2.xhtml]!/2/2/3:1)');
	});

	it('translates NFC offsets to original via deltaMaps', () => {
		// deltaMap "5 -1" means: at NFC pos 5 and beyond, delta = -1 (NFC is 1 char shorter)
		// So NFC 7 → original 8
		let result = resolveSelectorMapRange(
			'/6/2[ch1.xhtml]!/2/4/1', 7,
			'/6/4[ch2.xhtml]!/2/2/1', 3,
			'5 -1', undefined);
		assert.equal(result.value,
			'epubcfi(/6,/2[ch1.xhtml]!/2/4/1:8,/4[ch2.xhtml]!/2/2/1:3)');
	});
});

describe('EPUB decode: expandSelectorMap', () => {
	let blockSM = '/6/2[ch1.xhtml]!/2/4';

	it('expands single-entry relative suffix', () => {
		assert.equal(expandSelectorMap(blockSM, '/1'), '/6/2[ch1.xhtml]!/2/4/1');
	});

	it('expands deeper relative suffix', () => {
		assert.equal(expandSelectorMap(blockSM, '/2/1'), '/6/2[ch1.xhtml]!/2/4/2/1');
	});

	it('expands multi-entry relative suffixes', () => {
		let multi = '6 /1\n5 /2/1\n1 /3';
		assert.equal(expandSelectorMap(blockSM, multi),
			'6 /6/2[ch1.xhtml]!/2/4/1\n5 /6/2[ch1.xhtml]!/2/4/2/1\n1 /6/2[ch1.xhtml]!/2/4/3');
	});

	it('returns block path when relative suffix is empty', () => {
		// Top-level text-node blocks: the block path already targets the text
		// node, so the relative suffix on the textAnchor is empty.
		assert.equal(expandSelectorMap(blockSM, ''), blockSM);
	});
});

describe('EPUB decode: multi-entry selectorMap', () => {
	let multiEntry = '14 /6/2[ch1.xhtml]!/2/4/1\n17 /6/2[ch1.xhtml]!/2/4/2/1\n1 /6/2[ch1.xhtml]!/2/4/3';

	it('parseSelectorMapEntries returns null for single-entry', () => {
		assert.equal(parseSelectorMapEntries('/6/2[ch1.xhtml]!/2/4/1'), null);
	});

	it('parseSelectorMapEntries parses multi-entry', () => {
		assert.deepStrictEqual(parseSelectorMapEntries(multiEntry), [
			{ length: 14, path: '/6/2[ch1.xhtml]!/2/4/1' },
			{ length: 17, path: '/6/2[ch1.xhtml]!/2/4/2/1' },
			{ length: 1, path: '/6/2[ch1.xhtml]!/2/4/3' },
		]);
	});

	it('resolves point in first entry', () => {
		assert.equal(resolveSelectorMap(multiEntry, 5).value, 'epubcfi(/6/2[ch1.xhtml]!/2/4/1:5)');
	});

	it('resolves point in second entry', () => {
		assert.equal(resolveSelectorMap(multiEntry, 14).value, 'epubcfi(/6/2[ch1.xhtml]!/2/4/2/1:0)');
	});

	it('resolves point in last entry', () => {
		assert.equal(resolveSelectorMap(multiEntry, 31).value, 'epubcfi(/6/2[ch1.xhtml]!/2/4/3:0)');
	});

	it('resolves range within one entry', () => {
		assert.equal(resolveSelectorMap(multiEntry, 2, 10).value, 'epubcfi(/6/2[ch1.xhtml]!/2/4/1,:2,:10)');
	});

	it('resolves range spanning two entries', () => {
		let result = resolveSelectorMap(multiEntry, 10, 20);
		assert.equal(result.type, 'FragmentSelector');
		assert.match(result.value, /^epubcfi\(/);
		assert.match(result.value, /,/);
	});

	it('resolves range spanning all entries', () => {
		let result = resolveSelectorMap(multiEntry, 0, 32);
		assert.equal(result.type, 'FragmentSelector');
		assert.match(result.value, /^epubcfi\(/);
	});
});

describe('mergeNodesWithSelectorMap', () => {
	it('merges adjacent text nodes with different selectorMaps and same style', () => {
		let content = [
			{ text: 'Hello ', anchor: { selectorMap: '/6/2!/2/4/1' } },
			{ text: 'world', anchor: { selectorMap: '/6/2!/2/4/3' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 1);
		assert.equal(content[0].text, 'Hello world');
		assert.ok(/^\d/.test(content[0].anchor.selectorMap), 'should be multi-entry');
		assert.deepStrictEqual(parseSelectorMapEntries(content[0].anchor.selectorMap), [
			{ length: 6, path: '/6/2!/2/4/1' },
			{ length: 5, path: '/6/2!/2/4/3' },
		]);
	});

	it('does not merge when styles differ', () => {
		let content = [
			{ text: 'plain', anchor: { selectorMap: '/6/2!/2/4/1' } },
			{ text: 'bold', style: { bold: true }, anchor: { selectorMap: '/6/2!/2/4/2/1' } },
			{ text: '.', anchor: { selectorMap: '/6/2!/2/4/3' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 3);
	});

	it('does not merge when refs differ', () => {
		let content = [
			{ text: 'text', anchor: { selectorMap: '/6/2!/2/4/1' } },
			{ text: '9', refs: [[205]], anchor: { selectorMap: '/6/2!/2/4/2/1' } },
			{ text: ' more', anchor: { selectorMap: '/6/2!/2/4/3' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 3);
		assert.equal(content[1].text, '9');
	});

	it('does not merge when targets differ', () => {
		let content = [
			{ text: 'see ', anchor: { selectorMap: '/6/2!/2/4/1' } },
			{ text: 'link', target: { url: 'http://x.com' }, anchor: { selectorMap: '/6/2!/2/4/2/1' } },
			{ text: ' text', anchor: { selectorMap: '/6/2!/2/4/3' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 3);
	});

	it('merges same selectorMap without creating multi-entry', () => {
		let content = [
			{ text: 'a', anchor: { selectorMap: '/6/2!/2/4/1' } },
			{ text: 'b', anchor: { selectorMap: '/6/2!/2/4/1' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 1);
		assert.equal(content[0].text, 'ab');
		assert.equal(content[0].anchor.selectorMap, '/6/2!/2/4/1');
	});

	it('merges three nodes into multi-entry', () => {
		let content = [
			{ text: 'aaa', anchor: { selectorMap: '/6/2!/2/4/1' } },
			{ text: 'bb', anchor: { selectorMap: '/6/2!/2/4/2/1' } },
			{ text: 'c', anchor: { selectorMap: '/6/2!/2/4/3' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 1);
		assert.equal(content[0].text, 'aaabbc');
		let entries = parseSelectorMapEntries(content[0].anchor.selectorMap);
		assert.equal(entries.length, 3);
	});

	it('skips non-text nodes (block children)', () => {
		let content = [
			{ text: 'a', anchor: { selectorMap: '/6/2!/2/4/1' } },
			{ type: 'listitem', content: [] },
			{ text: 'b', anchor: { selectorMap: '/6/2!/2/4/3' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 3);
	});

	it('does not merge non-DomAnchor anchors', () => {
		let content = [
			{ text: 'a', anchor: { type: 'CssSelector', value: 'p' } },
			{ text: 'b', anchor: { type: 'CssSelector', value: 'p' } },
		];
		mergeNodesWithSelectorMap(content);
		assert.equal(content.length, 2);
	});
});

let fixtureDir = path.resolve(import.meta.dirname, '../../fixtures/epub');
let fixtureFiles = fs.readdirSync(fixtureDir).filter(f => /^\d+\.json$/.test(f));
let fixtures = fixtureFiles.map(f => ({
	name: f,
	data: JSON.parse(fs.readFileSync(path.join(fixtureDir, f), 'utf8')),
}));

describe('EPUB decode: roundtrip on real fixture data', () => {
	for (let { name, data } of fixtures) {
		it(`${name}: all text node selectorMaps are valid`, () => {
			for (let block of data.content) {
				if (!Array.isArray(block.content)) continue;
				for (let tn of block.content) {
					if (!tn.anchor?.selectorMap) continue;
					let sm = tn.anchor.selectorMap;
					let isSingle = sm.startsWith('/');
					let isMulti = /^\d/.test(sm);
					assert.ok(isSingle || isMulti,
						`selectorMap should start with / or digit: ${sm.substring(0, 60)}`);
					if (isMulti) {
						let entries = parseSelectorMapEntries(sm);
						assert.ok(entries && entries.length >= 2);
						for (let entry of entries) {
							assert.ok(entry.path.startsWith('/'));
							assert.ok(entry.length > 0);
						}
					}
				}
			}
		});

		it(`${name}: expandSelectorMap + resolveSelectorMap produces valid CFIs`, () => {
			for (let block of data.content) {
				if (!Array.isArray(block.content)) continue;
				let blockSM = block.anchor?.selectorMap;
				if (!blockSM) continue;
				for (let tn of block.content) {
					if (!tn.text || !tn.anchor?.selectorMap) continue;
					let abs = expandSelectorMap(blockSM, tn.anchor.selectorMap);
					let point = resolveSelectorMap(abs, 0);
					assert.match(point.value, /^epubcfi\(\/.*:0\)$/,
						`invalid point CFI: ${point.value}`);

					if (tn.text.length > 1) {
						let range = resolveSelectorMap(abs, 0, tn.text.length);
						assert.match(range.value, /^epubcfi\(\//,
							`invalid range CFI: ${range.value}`);
					}
				}
			}
		});

		it(`${name}: block anchors have absolute CFI path selectorMaps`, () => {
			for (let block of data.content) {
				assert.ok(block.anchor, 'block missing anchor');
				assert.ok(block.anchor.selectorMap, 'block anchor should have selectorMap');
				assert.match(block.anchor.selectorMap, /^\//);
				let wadm = expandBlockAnchor(block.anchor.selectorMap);
				assert.equal(wadm.type, 'FragmentSelector');
				assert.match(wadm.value, /^epubcfi\(\//);
			}
		});

		it(`${name}: cross-node range between adjacent text nodes`, () => {
			for (let block of data.content) {
				if (!Array.isArray(block.content) || block.content.length < 2) continue;
				let blockSM = block.anchor?.selectorMap;
				if (!blockSM) continue;
				let nodes = block.content.filter(tn =>
					tn.text && tn.anchor?.selectorMap && tn.anchor.selectorMap.startsWith('/'));
				if (nodes.length < 2) continue;

				let a = nodes[0];
				let b = nodes[nodes.length - 1];
				let absA = expandSelectorMap(blockSM, a.anchor.selectorMap);
				let absB = expandSelectorMap(blockSM, b.anchor.selectorMap);
				let result = resolveSelectorMapRange(absA, 0, absB, b.text.length);
				assert.equal(result.type, 'FragmentSelector');
				assert.match(result.value, /^epubcfi\(\//);
				if (absA !== absB) {
					assert.match(result.value, /,/);
				}
			}
		});
	}
});
