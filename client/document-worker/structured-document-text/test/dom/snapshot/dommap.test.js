import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
	buildDomMapIndex,
	findDomMapContaining,
	generateDomMapSelector,
	domMapSegment,
	matchDomMapSelector,
	cssEscape,
	DOM_MAP_FIRST_OF_TYPE,
	DOM_MAP_LAST_OF_TYPE,
	DOM_MAP_LAST_CHILD,
} from '../../../src/dom/snapshot/dommap.js';

// <body>
//   <div> (index 0, only div => first/last-of-type, text [0, 100))
//     <h1> [0, 20)
//     <p>  [21, 50)
//     <p>  [52, 80)
//     <div id="sidebar"> [81, 100)
//       <p> [82, 99)
//   <footer> (index 1, last-child, text [101, 120))
const FIXTURE = [
	{
		tag: 'div',
		index: 0,
		flags: DOM_MAP_FIRST_OF_TYPE | DOM_MAP_LAST_OF_TYPE,
		textStart: 0,
		textLength: 100,
		children: [
			{
				tag: 'h1',
				index: 0,
				flags: DOM_MAP_FIRST_OF_TYPE | DOM_MAP_LAST_OF_TYPE,
				textStart: 0,
				textLength: 20,
			},
			{
				tag: 'p',
				index: 1,
				flags: DOM_MAP_FIRST_OF_TYPE,
				textStart: 21,
				textLength: 29,
			},
			{
				tag: 'p',
				index: 2,
				flags: DOM_MAP_LAST_OF_TYPE,
				textStart: 52,
				textLength: 28,
			},
			{
				tag: 'div',
				id: 'sidebar',
				index: 3,
				flags: DOM_MAP_FIRST_OF_TYPE | DOM_MAP_LAST_OF_TYPE | DOM_MAP_LAST_CHILD,
				prevId: undefined,
				textStart: 81,
				textLength: 19,
				children: [
					{
						tag: 'p',
						index: 0,
						flags: DOM_MAP_FIRST_OF_TYPE | DOM_MAP_LAST_OF_TYPE,
						textStart: 82,
						textLength: 17,
					},
				],
			},
		],
	},
	{
		tag: 'footer',
		index: 1,
		flags: DOM_MAP_FIRST_OF_TYPE | DOM_MAP_LAST_OF_TYPE | DOM_MAP_LAST_CHILD,
		textStart: 101,
		textLength: 19,
	},
];

function index() {
	return buildDomMapIndex(FIXTURE);
}

function nodeAt(idx, ...path) {
	let current = { children: idx.roots };
	for (let i of path) {
		current = current.children[i];
	}
	return current;
}

describe('domMap: buildDomMapIndex', () => {
	it('returns null for missing/empty maps', () => {
		assert.equal(buildDomMapIndex(undefined), null);
		assert.equal(buildDomMapIndex([]), null);
	});

	it('builds parent links and a flat node list', () => {
		let idx = index();
		assert.equal(idx.roots.length, 2);
		assert.equal(idx.nodes.length, 7);
		let sidebarP = nodeAt(idx, 0, 3, 0);
		assert.equal(sidebarP.node.tag, 'p');
		assert.equal(sidebarP.parent.node.id, 'sidebar');
		assert.equal(sidebarP.parent.parent.node.tag, 'div');
		assert.equal(sidebarP.parent.parent.parent, null);
	});
});

describe('domMap: domMapSegment', () => {
	it('omits the pseudo-class for only-of-type elements', () => {
		assert.equal(domMapSegment({ tag: 'h1', index: 0, flags: 3 }), 'h1');
	});

	it('prefers :first-child', () => {
		assert.equal(domMapSegment({ tag: 'p', index: 0, flags: DOM_MAP_FIRST_OF_TYPE }), 'p:first-child');
	});

	it('uses :first-of-type when not first child', () => {
		assert.equal(domMapSegment({ tag: 'p', index: 1, flags: DOM_MAP_FIRST_OF_TYPE }), 'p:first-of-type');
	});

	it('uses :last-child over :last-of-type', () => {
		assert.equal(
			domMapSegment({ tag: 'p', index: 3, flags: DOM_MAP_LAST_OF_TYPE | DOM_MAP_LAST_CHILD }),
			'p:last-child'
		);
	});

	it('falls back to :nth-child', () => {
		assert.equal(domMapSegment({ tag: 'p', index: 2, flags: 0 }), 'p:nth-child(3)');
	});
});

describe('domMap: generateDomMapSelector', () => {
	it('roots paths at body', () => {
		let idx = index();
		assert.equal(generateDomMapSelector(nodeAt(idx, 0, 1)), 'body > div > p:first-of-type');
		assert.equal(generateDomMapSelector(nodeAt(idx, 1)), 'body > footer');
	});

	it('short-circuits at elements with ids', () => {
		let idx = index();
		assert.equal(generateDomMapSelector(nodeAt(idx, 0, 3)), '#sidebar');
		assert.equal(generateDomMapSelector(nodeAt(idx, 0, 3, 0)), '#sidebar > p');
	});
});

describe('domMap: matchDomMapSelector', () => {
	it('matches encoder-style body-rooted paths', () => {
		let idx = index();
		assert.equal(matchDomMapSelector(idx, 'body > div > p:first-of-type'), nodeAt(idx, 0, 1));
		assert.equal(matchDomMapSelector(idx, 'body > div > h1'), nodeAt(idx, 0, 0));
	});

	it('matches reader-style suffix paths', () => {
		let idx = index();
		assert.equal(matchDomMapSelector(idx, 'h1'), nodeAt(idx, 0, 0));
		assert.equal(matchDomMapSelector(idx, 'div > p:nth-child(3)'), nodeAt(idx, 0, 2));
		assert.equal(matchDomMapSelector(idx, 'footer'), nodeAt(idx, 1));
	});

	it('matches CSS semantics, not generation choices', () => {
		let idx = index();
		// h1 is index 0, so :first-child matches even though the generator
		// would emit plain 'h1'
		assert.equal(matchDomMapSelector(idx, 'div > h1:first-child'), nodeAt(idx, 0, 0));
		assert.equal(matchDomMapSelector(idx, 'div > p:nth-child(2)'), nodeAt(idx, 0, 1));
	});

	it('matches #id anchors and bare #id', () => {
		let idx = index();
		assert.equal(matchDomMapSelector(idx, '#sidebar'), nodeAt(idx, 0, 3));
		assert.equal(matchDomMapSelector(idx, '#sidebar > p'), nodeAt(idx, 0, 3, 0));
	});

	it('rejects ambiguous suffixes', () => {
		let idx = index();
		// Three <p>s match bare 'p'
		assert.equal(matchDomMapSelector(idx, 'p'), null);
		// Both the outer div's first <p> and the sidebar's <p> match
		assert.equal(matchDomMapSelector(idx, 'p:first-of-type'), null);
		assert.equal(matchDomMapSelector(idx, 'div > p:first-of-type'), null);
	});

	it('rejects non-matching and out-of-grammar selectors', () => {
		let idx = index();
		assert.equal(matchDomMapSelector(idx, 'article > p'), null);
		assert.equal(matchDomMapSelector(idx, 'div p'), null);
		assert.equal(matchDomMapSelector(idx, 'p.intro'), null);
		assert.equal(matchDomMapSelector(idx, 'body'), null);
	});

	it('matches #prevId + tag shortcuts', () => {
		let fixture = [
			{
				tag: 'div',
				id: 'top',
				index: 0,
				flags: 3,
				textStart: 0,
				textLength: 10,
			},
			{
				tag: 'div',
				index: 1,
				flags: 3 | DOM_MAP_LAST_CHILD,
				prevId: 'top',
				textStart: 10,
				textLength: 20,
				children: [
					{ tag: 'p', index: 0, flags: 3, textStart: 10, textLength: 20 },
				],
			},
		];
		// Both divs match bare 'div' pseudo-wise; flags say each is
		// only-of-type because tags collide -- use realistic flags instead
		fixture[0].flags = DOM_MAP_FIRST_OF_TYPE;
		fixture[1].flags = DOM_MAP_LAST_OF_TYPE | DOM_MAP_LAST_CHILD;
		let idx = buildDomMapIndex(fixture);
		assert.equal(matchDomMapSelector(idx, '#top + div'), idx.roots[1]);
		assert.equal(matchDomMapSelector(idx, '#top + div > p'), idx.roots[1].children[0]);
		assert.equal(matchDomMapSelector(idx, '#missing + div'), null);
	});
});

describe('domMap: findDomMapContaining', () => {
	it('finds the deepest containing node', () => {
		let idx = index();
		assert.equal(findDomMapContaining(idx, 25, 40), nodeAt(idx, 0, 1));
		assert.equal(findDomMapContaining(idx, 85, 95), nodeAt(idx, 0, 3, 0));
	});

	it('returns the common ancestor for cross-element ranges', () => {
		let idx = index();
		// Spans both <p>s
		assert.equal(findDomMapContaining(idx, 25, 70), nodeAt(idx, 0));
		// Spans a <p> and the sidebar
		assert.equal(findDomMapContaining(idx, 60, 90), nodeAt(idx, 0));
	});

	it('returns null when only body contains the range', () => {
		let idx = index();
		assert.equal(findDomMapContaining(idx, 50, 110), null);
	});

	it('treats end offsets as exclusive at boundaries', () => {
		let idx = index();
		// End exactly at the end of the first <p>'s text
		assert.equal(findDomMapContaining(idx, 21, 50), nodeAt(idx, 0, 1));
		// Start exactly at the start of the second <p>'s text
		assert.equal(findDomMapContaining(idx, 52, 80), nodeAt(idx, 0, 2));
	});
});

describe('domMap: cssEscape', () => {
	it('passes through simple identifiers', () => {
		assert.equal(cssEscape('sidebar'), 'sidebar');
		assert.equal(cssEscape('main-content_2'), 'main-content_2');
	});

	it('escapes special characters like CSS.escape()', () => {
		assert.equal(cssEscape('foo.bar'), 'foo\\.bar');
		assert.equal(cssEscape('a b'), 'a\\ b');
		assert.equal(cssEscape('1abc'), '\\31 abc');
		assert.equal(cssEscape('-5x'), '-\\35 x');
		assert.equal(cssEscape('-'), '\\-');
	});
});
