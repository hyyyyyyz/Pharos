import {
	concatUint8Arrays,
	isByteBuffer,
	toUint8Array,
} from './bytes.js';
import { encodeContentChunk } from './chunk.js';
import {
	encodeHeader,
	encodeIndex,
} from './format.js';

const TARGET_RAW_CHUNK_BYTES = 32 * 1024;
const LARGE_BLOCK_WARNING_RAW_BYTES = 64 * 1024;
const MAX_PACK_BYTES = 0xffffffff;
const SOURCE_HASH_RE = /^[0-9a-f]{32}$/u;
const VERSION_RE = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const TEXT_ENCODER = new TextEncoder();

export function packStructuredDocumentText(structure, options = {}) {
	validateStructure(structure);

	let {
		destructive = false,
		deflate,
	} = options;
	deflate = normalizeDeflate(deflate);

	let metadataBytes = deflateJson(structure.metadata, deflate);
	let catalogBytes = deflateJson(structure.catalog, deflate);
	let contentChunks = [];
	let contentLength = 0;
	let { content } = structure;
	let chunkByteOffsets = [0];
	let chunkBlockStarts = [0];
	let currentBlocks = [];
	let currentRawBytes = 0;
	let oversizedCount = 0;
	let largestOversizedBlockBytes = 0;

	let flushChunk = () => {
		if (!currentBlocks.length) {
			return;
		}
		contentLength = appendContentChunk(contentChunks, contentLength, currentBlocks, deflate);
		chunkByteOffsets.push(contentLength);
		chunkBlockStarts.push(chunkBlockStarts[chunkBlockStarts.length - 1] + currentBlocks.length);
		currentBlocks = [];
		currentRawBytes = 0;
	};

	for (let i = 0; i < content.length; i++) {
		let block = content[i];
		let blockBytes = TEXT_ENCODER.encode(JSON.stringify(block));
		if (destructive) {
			content[i] = null;
		}

		if (blockBytes.byteLength > TARGET_RAW_CHUNK_BYTES) {
			flushChunk();
			if (blockBytes.byteLength > LARGE_BLOCK_WARNING_RAW_BYTES) {
				oversizedCount++;
				largestOversizedBlockBytes = Math.max(largestOversizedBlockBytes, blockBytes.byteLength);
			}
			contentLength = appendContentChunk(contentChunks, contentLength, [blockBytes], deflate);
			chunkByteOffsets.push(contentLength);
			chunkBlockStarts.push(i + 1);
			continue;
		}

		if (currentBlocks.length && currentRawBytes + blockBytes.byteLength > TARGET_RAW_CHUNK_BYTES) {
			flushChunk();
		}

		currentBlocks.push(blockBytes);
		currentRawBytes += blockBytes.byteLength;
	}
	flushChunk();

	if (oversizedCount) {
		console.warn(`SDTPack: ${oversizedCount} oversized top-level blocks written as single-block chunks; largest ${formatBytes(largestOversizedBlockBytes)}`);
	}

	let indexPayload = encodeIndex({
		metadataLength: metadataBytes.byteLength,
		catalogLength: catalogBytes.byteLength,
		chunkByteOffsets,
		chunkBlockStarts,
	});
	let headerBytes = encodeHeader({
		schemaVersion: structure.schemaVersion,
		indexLength: indexPayload.byteLength,
	});

	let totalLength = headerBytes.byteLength
		+ indexPayload.byteLength
		+ metadataBytes.byteLength
		+ catalogBytes.byteLength
		+ contentLength;
	if (totalLength > MAX_PACK_BYTES) {
		throw new Error('SDTPack exceeds v1 4 GiB size limit');
	}

	let bytes = concatUint8Arrays([
		headerBytes,
		indexPayload,
		metadataBytes,
		catalogBytes,
		...contentChunks,
	], totalLength);

	return bytes.buffer;
}

function deflateJson(value, deflate) {
	let json = JSON.stringify(value);
	return deflate(TEXT_ENCODER.encode(json));
}

function appendContentChunk(contentChunks, contentLength, blockByteArrays, deflate) {
	let payload = encodeContentChunk(blockByteArrays);
	let chunkBytes = deflate(payload);
	let nextContentLength = contentLength + chunkBytes.byteLength;
	if (nextContentLength > MAX_PACK_BYTES) {
		throw new Error('SDTPack exceeds v1 4 GiB size limit');
	}
	contentChunks.push(chunkBytes);
	return nextContentLength;
}

function normalizeDeflate(deflate) {
	if (typeof deflate !== 'function') {
		throw new TypeError('Expected SDTPack raw DEFLATE deflate function');
	}
	return (bytes) => {
		let compressed = deflate(bytes);
		if (isByteBuffer(compressed)) {
			return toUint8Array(compressed);
		}
		throw new TypeError('Expected SDTPack deflate function to return Uint8Array or ArrayBuffer');
	};
}

function validateStructure(structure) {
	if (!structure || typeof structure !== 'object') {
		throw new TypeError('Expected StructuredDocumentText object');
	}
	if (!VERSION_RE.test(structure.schemaVersion)) {
		throw new TypeError('Expected StructuredDocumentText schemaVersion');
	}
	if (!structure.metadata || typeof structure.metadata !== 'object') {
		throw new TypeError('Expected StructuredDocumentText metadata object');
	}
	if (!structure.metadata.processor || typeof structure.metadata.processor !== 'object') {
		throw new TypeError('Expected StructuredDocumentText metadata.processor object');
	}
	if (typeof structure.metadata.processor.type !== 'string' || !structure.metadata.processor.type) {
		throw new TypeError('Expected StructuredDocumentText metadata.processor.type');
	}
	if (!Number.isInteger(structure.metadata.processor.version) || structure.metadata.processor.version < 1) {
		throw new TypeError('Expected StructuredDocumentText metadata.processor.version');
	}
	if (typeof structure.metadata.dateCreated !== 'string' || !structure.metadata.dateCreated) {
		throw new TypeError('Expected StructuredDocumentText metadata.dateCreated');
	}
	if (!SOURCE_HASH_RE.test(structure.metadata.source?.hash)) {
		throw new TypeError('Expected StructuredDocumentText metadata.source.hash');
	}
	if (!structure.catalog || typeof structure.catalog !== 'object') {
		throw new TypeError('Expected StructuredDocumentText catalog object');
	}
	if (!Array.isArray(structure.catalog.pages)) {
		throw new TypeError('Expected StructuredDocumentText catalog.pages array');
	}
	if (!Array.isArray(structure.catalog.outline)) {
		throw new TypeError('Expected StructuredDocumentText catalog.outline array');
	}
	if (!Array.isArray(structure.content)) {
		throw new TypeError('Expected StructuredDocumentText content array');
	}
}

function formatBytes(bytes) {
	if (bytes < 1024) {
		return `${bytes} B`;
	}
	if (bytes < 1024 * 1024) {
		return `${(bytes / 1024).toFixed(1)} KiB`;
	}
	return `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}
