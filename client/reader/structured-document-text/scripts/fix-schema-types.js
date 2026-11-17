/**
 * Post-process json-schema-to-typescript output to fix known issues:
 * 1. Remove index signature intersections ({ [k: string]: unknown } &)
 *    caused by anyOf/allOf patterns — convert to plain interfaces
 * 2. Remove duplicate numbered type aliases (RefPath1, RefsArray1, etc.)
 *    and rewrite references to the base name
 * 3. Fix PageRect tuple items typed as unknown instead of number
 */
import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const file = resolve(__dirname, '..', 'schema.d.ts');
let src = readFileSync(file, 'utf8');

// 1. Remove "{ [k: string]: unknown; } & " intersections
//    and convert the remaining "export type Foo = { ... };" to "export interface Foo { ... }"
src = src.replace(
	/export type (\w+) = \{\n\s+\[k: string\]: unknown;\n\} & \{/g,
	'export interface $1 {'
);
// Remove trailing `;` from interfaces to satisfy TS compiler
{
	let lines = src.split('\n');
	let inConvertedInterface = false;
	for (let i = 0; i < lines.length; i++) {
		if (/^export interface /.test(lines[i])) {
			inConvertedInterface = true;
		}
		else if (/^export (type|declare) /.test(lines[i])) {
			inConvertedInterface = false;
		}
		else if (lines[i] === '};' && inConvertedInterface) {
			lines[i] = '}';
			inConvertedInterface = false;
		}
	}
	src = lines.join('\n');
}

// 2. Remove standalone numbered intermediate types like:
//    export type Target1 = { [k: string]: unknown; };
src = src.replace(/export type \w+\d+ = \{\n\s+\[k: string\]: unknown;\n\};\n/g, '');

// 3. Remove duplicate numbered type aliases (e.g. RefPath1, RefsArray1, RefsArray2)
//    Split into lines, find and remove blocks that define "TypeName<digit>" aliases
let lines = src.split('\n');
let result = [];
let i = 0;
while (i < lines.length) {
	let match = lines[i].match(/^export type (\w+?)(\d+) = .+;$/);
	if (match) {
		let baseName = match[1];
		let baseExists = lines.some(l => l.startsWith(`export type ${baseName} = `) || l.startsWith(`export type ${baseName} `) || l.startsWith(`export interface ${baseName} `));
		if (baseExists) {
			// Remove preceding JSDoc comment if present
			while (result.length && (result[result.length - 1].startsWith(' *') || result[result.length - 1].startsWith('/**'))) {
				result.pop();
			}
			i++;
			continue;
		}
	}
	result.push(lines[i]);
	i++;
}
src = result.join('\n');

// 4. Rewrite remaining references to numbered types back to base names
src = src.replace(/\b(RefPath|RefsArray|BackRefsArray|Target|PdfAnchor|DomAnchor)(\d+)\b/g, '$1');

// 5. Fix circular self-references created by step 4 (e.g. "type Target = Target & {" → "type Target = {")
src = src.replace(/export type (\w+) = \1 & \{/g, 'export type $1 = {');

// 6. Fix PageRect tuple: unknown -> number
src = src.replace(
	/export type PageRect = \[unknown, unknown, unknown, unknown, unknown]/,
	'export type PageRect = [number, number, number, number, number]'
);

writeFileSync(file, src);
