import { readFileSync, writeFileSync, readdirSync, existsSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixturesDir = resolve(__dirname, 'fixtures');

/**
 * Discover all fixture JSON files across all format directories.
 * Returns [{ format, name, path, data }].
 */
export function discoverFixtures() {
	const fixtures = [];
	for (const format of readdirSync(fixturesDir)) {
		const formatDir = join(fixturesDir, format);
		for (const file of readdirSync(formatDir)) {
			if (!file.endsWith('.json')) continue;
			const name = file.replace(/\.json$/, '');
			const path = join(formatDir, file);
			// Check it's an input fixture (not an expected output like 1.outline.json)
			if (name.includes('.')) continue;
			const data = JSON.parse(readFileSync(path, 'utf8'));
			fixtures.push({ format, name, path: formatDir, data });
		}
	}
	return fixtures;
}

export function isUpdateMode() {
	return !!process.env.UPDATE;
}

export function readExpected(fixturePath, name, suffix) {
	const file = join(fixturePath, `${name}.${suffix}`);
	if (!existsSync(file)) return undefined;
	return readFileSync(file, 'utf8');
}

export function writeExpected(fixturePath, name, suffix, content) {
	const file = join(fixturePath, `${name}.${suffix}`);
	writeFileSync(file, content, 'utf8');
}
