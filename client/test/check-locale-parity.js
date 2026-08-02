#!/usr/bin/env node
'use strict';

/**
 * Both Pharos locale files must define the same set of ids.
 *
 * Why this matters more than it looks: a missing id fails LOUDLY only in en-US,
 * where Zotero.getString() throws and takes the surrounding render down. In
 * zh-CN the same id comes back as itself, so it ships as a stray
 * `pharos-projects-stage-analysis` on screen. A contributor working in one
 * locale therefore cannot see the damage they do to the other, and this has
 * already broken twice.
 *
 * Not part of test/runtests.sh: Fluent resolves these through the L10nRegistry,
 * not through chrome://, so the in-app harness has no path to the sibling
 * locale. Reading two files is trivial here and was not there.
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', 'chrome', 'locale');
const LOCALES = ['en-US', 'zh-CN'];
const FILE = path.join('zotero', 'pharos.ftl');

/** Value messages only. An attributes-only message (`x =` with just `.label`)
 *  is a different thing and getString() cannot read it either way. */
function ids(text) {
	return new Set(
		[...text.matchAll(/^([a-z][\w-]*)\s*=[ \t]*\S/gm)].map(m => m[1])
	);
}

let sets = {};
for (const locale of LOCALES) {
	const file = path.join(ROOT, locale, FILE);
	if (!fs.existsSync(file)) {
		console.error(`missing: ${file}`);
		process.exit(2);
	}
	sets[locale] = ids(fs.readFileSync(file, 'utf8'));
}

const [a, b] = LOCALES;
const onlyA = [...sets[a]].filter(id => !sets[b].has(id)).sort();
const onlyB = [...sets[b]].filter(id => !sets[a].has(id)).sort();

// Compared both ways rather than by count: adding an id to one file while
// removing a different one from the other keeps the totals equal.
if (onlyA.length || onlyB.length) {
	for (const id of onlyA) console.error(`only in ${a}: ${id}`);
	for (const id of onlyB) console.error(`only in ${b}: ${id}`);
	console.error(`\n${onlyA.length + onlyB.length} id(s) differ.`);
	process.exit(1);
}

// A regex that matched nothing would make the comparison above pass silently.
if (sets[a].size < 100) {
	console.error(`only ${sets[a].size} ids parsed -- the parser, not the files, `
		+ 'is probably what is wrong');
	process.exit(2);
}

console.log(`ok: ${sets[a].size} ids, identical in ${LOCALES.join(' and ')}`);
