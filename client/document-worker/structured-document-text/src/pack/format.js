import {
	decodeU32Array,
	readU32LE,
	toUint8Array,
	writeU32LE,
} from './bytes.js';
import { SDT_PACK_VERSION as PACK_VERSION } from '../version.js';

export const MAGIC = Uint8Array.of(0x89, 0x53, 0x44, 0x54, 0x0d, 0x0a, 0x1a, 0x0a); // \x89SDT\r\n\x1A\n
export const HEADER_SIZE = 16;

const INDEX_FIXED_SIZE = 8;
const U32_BYTES = 4;
const SCHEMA_VERSION_RE = /^([0-9]+)\.([0-9]+)\.([0-9]+)$/u;

export function encodeHeader({ schemaVersion, indexLength }) {
	let version = parseSchemaVersion(schemaVersion);
	if (!Number.isInteger(indexLength) || indexLength <= 0 || indexLength > 0xffffffff) {
		throw new Error('Invalid SDTPack index length');
	}
	let bytes = new Uint8Array(HEADER_SIZE);
	bytes.set(MAGIC, 0);
	bytes.set(Uint8Array.of(PACK_VERSION, version.major, version.minor, version.patch), 8);
	writeU32LE(bytes, 12, indexLength);
	return bytes;
}

export function readHeader(bytes) {
	bytes = toUint8Array(bytes);
	if (bytes.byteLength < HEADER_SIZE) {
		throw new Error('Invalid SDTPack: file too small');
	}
	assertMagic(bytes, 0, MAGIC);
	let packVersion = bytes[8];
	if (packVersion !== PACK_VERSION) {
		throw new Error(`Unsupported SDTPack version: ${packVersion}`);
	}
	let header = {
		packVersion,
		schemaVersion: `${bytes[9]}.${bytes[10]}.${bytes[11]}`,
		indexLength: readU32LE(bytes, 12),
	};
	if (header.indexLength < INDEX_FIXED_SIZE + U32_BYTES * 2) {
		throw new Error('Invalid SDTPack index length');
	}
	if ((header.indexLength - INDEX_FIXED_SIZE) % (U32_BYTES * 2) !== 0) {
		throw new Error('Invalid SDTPack index length');
	}
	return header;
}

export function encodeIndex({
	metadataLength,
	catalogLength,
	chunkByteOffsets,
	chunkBlockStarts,
}) {
	validateIndexShape({ metadataLength, catalogLength, chunkByteOffsets, chunkBlockStarts });
	let bytes = new Uint8Array(
		INDEX_FIXED_SIZE
		+ chunkByteOffsets.length * U32_BYTES
		+ chunkBlockStarts.length * U32_BYTES
	);
	writeU32LE(bytes, 0, metadataLength);
	writeU32LE(bytes, 4, catalogLength);

	let offset = INDEX_FIXED_SIZE;
	for (let value of chunkByteOffsets) {
		writeU32LE(bytes, offset, value);
		offset += U32_BYTES;
	}
	for (let value of chunkBlockStarts) {
		writeU32LE(bytes, offset, value);
		offset += U32_BYTES;
	}
	return bytes;
}

export function decodeIndex(bytes) {
	bytes = toUint8Array(bytes);
	if (bytes.byteLength < INDEX_FIXED_SIZE + U32_BYTES * 2) {
		throw new Error('Invalid SDTPack index');
	}
	if ((bytes.byteLength - INDEX_FIXED_SIZE) % (U32_BYTES * 2) !== 0) {
		throw new Error('Invalid SDTPack index length');
	}
	let metadataLength = readU32LE(bytes, 0);
	let catalogLength = readU32LE(bytes, 4);
	let entryCount = (bytes.byteLength - INDEX_FIXED_SIZE) / (U32_BYTES * 2);
	let chunkByteOffsets = decodeU32Array(bytes, INDEX_FIXED_SIZE, entryCount);
	let chunkBlockStarts = decodeU32Array(bytes, INDEX_FIXED_SIZE + entryCount * U32_BYTES, entryCount);
	let index = { metadataLength, catalogLength, chunkByteOffsets, chunkBlockStarts };
	validateIndexShape(index);
	return index;
}

export function validatePackLayout(header, index, fileLength) {
	let contentEnd = getContentStart(header, index) + index.chunkByteOffsets[index.chunkByteOffsets.length - 1];
	if (contentEnd !== fileLength) {
		throw new Error('Invalid SDTPack layout outside file');
	}
}

export function getMetadataRange(header, index) {
	return {
		offset: HEADER_SIZE + header.indexLength,
		length: index.metadataLength,
	};
}

export function getCatalogRange(header, index) {
	return {
		offset: HEADER_SIZE + header.indexLength + index.metadataLength,
		length: index.catalogLength,
	};
}

export function getContentStart(header, index) {
	return HEADER_SIZE + header.indexLength + index.metadataLength + index.catalogLength;
}

export function findChunkIndex(chunkBlockStarts, blockIndex) {
	if (!Number.isInteger(blockIndex) || blockIndex < 0 || blockIndex >= chunkBlockStarts[chunkBlockStarts.length - 1]) {
		return -1;
	}
	let lo = 0;
	let hi = chunkBlockStarts.length - 2;
	while (lo <= hi) {
		let mid = (lo + hi) >>> 1;
		if (blockIndex < chunkBlockStarts[mid]) {
			hi = mid - 1;
		}
		else if (blockIndex >= chunkBlockStarts[mid + 1]) {
			lo = mid + 1;
		}
		else {
			return mid;
		}
	}
	return -1;
}

function parseSchemaVersion(schemaVersion) {
	let match = typeof schemaVersion === 'string' ? SCHEMA_VERSION_RE.exec(schemaVersion) : null;
	if (!match) {
		throw new Error('SDTPack schemaVersion must be major.minor.patch');
	}
	let [major, minor, patch] = match.slice(1).map(Number);
	validateU8(major, 'schema major version');
	validateU8(minor, 'schema minor version');
	validateU8(patch, 'schema patch version');
	return { major, minor, patch };
}

function validateIndexShape(index) {
	let {
		metadataLength,
		catalogLength,
		chunkByteOffsets,
		chunkBlockStarts,
	} = index;
	if (!Number.isInteger(metadataLength) || metadataLength <= 0) {
		throw new Error('Invalid SDTPack metadata length');
	}
	if (!Number.isInteger(catalogLength) || catalogLength <= 0) {
		throw new Error('Invalid SDTPack catalog length');
	}
	if (!Array.isArray(chunkByteOffsets) || !Array.isArray(chunkBlockStarts)) {
		throw new Error('Invalid SDTPack chunk index shape');
	}
	if (chunkByteOffsets.length !== chunkBlockStarts.length || chunkByteOffsets.length === 0) {
		throw new Error('Invalid SDTPack chunk index shape');
	}
	if (chunkByteOffsets[0] !== 0) {
		throw new Error('Invalid SDTPack first chunk offset');
	}
	if (chunkBlockStarts[0] !== 0) {
		throw new Error('Invalid SDTPack chunk block bounds');
	}
	if (chunkByteOffsets.length > 1) {
		assertStrictlyIncreasing(chunkByteOffsets, 'chunkByteOffsets');
		assertStrictlyIncreasing(chunkBlockStarts, 'chunkBlockStarts');
	}
}

function assertStrictlyIncreasing(values, name) {
	for (let i = 1; i < values.length; i++) {
		if (values[i] <= values[i - 1]) {
			throw new Error(`Invalid SDTPack ${name}`);
		}
	}
}

function assertMagic(bytes, offset, magic) {
	for (let i = 0; i < magic.length; i++) {
		if (bytes[offset + i] !== magic[i]) {
			throw new Error('Invalid SDTPack magic');
		}
	}
}

function validateU8(value, label) {
	if (!Number.isInteger(value) || value < 0 || value > 0xff) {
		throw new RangeError(`Invalid SDTPack ${label}: ${value}`);
	}
}
