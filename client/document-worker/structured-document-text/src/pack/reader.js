import { getBlockBytesFromContentChunk } from './chunk.js';
import { getContentRangeBlockSpan } from '../range.js';
import {
	HEADER_SIZE,
	decodeIndex,
	findChunkIndex,
	getCatalogRange,
	getContentStart,
	getMetadataRange,
	readHeader,
	validatePackLayout,
} from './format.js';
import {
	isByteBuffer,
	toUint8Array,
} from './bytes.js';

const TEXT_DECODER = new TextDecoder();
const FRONT_MATTER_READ_BYTES = 64 * 1024;

export function openStructuredDocumentTextPack(input, options = {}) {
	return StructuredDocumentTextPackReader.open(input, options);
}

class StructuredDocumentTextPackReader {
	static async open(input, options = {}) {
		let source = normalizeByteSource(input);
		let inflate = normalizeInflate(options.inflate);
		if (source.byteLength < HEADER_SIZE) {
			throw new Error('Invalid SDTPack: file too small');
		}

		let frontMatterBytes = await readExactly(
			source,
			0,
			Math.min(source.byteLength, FRONT_MATTER_READ_BYTES)
		);
		let header = readHeader(frontMatterBytes);
		if (HEADER_SIZE + header.indexLength > source.byteLength) {
			throw new Error('Invalid SDTPack index bounds');
		}

		let indexEnd = HEADER_SIZE + header.indexLength;
		let indexBytes = indexEnd <= frontMatterBytes.byteLength
			? frontMatterBytes.subarray(HEADER_SIZE, indexEnd)
			: await readExactly(source, HEADER_SIZE, header.indexLength);
		let index = decodeIndex(indexBytes);
		validatePackLayout(header, index, source.byteLength);

		return new StructuredDocumentTextPackReader(source, header, index, inflate, frontMatterBytes);
	}

	constructor(source, header, index, inflate, frontMatterBytes) {
		this.source = source;
		this.header = header;
		this.index = index;
		this._inflate = inflate;
		this._frontMatterBytes = frontMatterBytes;
		this._metadataPromise = null;
		this._catalogPromise = null;
	}

	async getMetadata() {
		if (!this._metadataPromise) {
			this._metadataPromise = this._readJsonRange(getMetadataRange(this.header, this.index));
		}
		return this._metadataPromise;
	}

	async getCatalog() {
		if (!this._catalogPromise) {
			this._catalogPromise = this._readJsonRange(getCatalogRange(this.header, this.index));
		}
		return this._catalogPromise;
	}

	getTopLevelBlockCount() {
		return this.index.chunkBlockStarts[this.index.chunkBlockStarts.length - 1];
	}

	async getBlock(ref) {
		if (!Array.isArray(ref) || !Number.isInteger(ref[0])) {
			return null;
		}
		let block = await this._getTopLevelBlock(ref[0]);
		if (!block) {
			return null;
		}
		let node = block;
		for (let i = 1; i < ref.length; i++) {
			let index = ref[i];
			if (!Number.isInteger(index) || !node || !Array.isArray(node.content)) {
				return null;
			}
			node = node.content[index];
			if (!node || typeof node !== 'object') {
				return null;
			}
		}
		return node;
	}

	async getBlocks(startBlock, endBlock) {
		if (!Number.isInteger(startBlock) || !Number.isInteger(endBlock) || startBlock > endBlock) {
			return [];
		}
		let totalTopLevelBlocks = this.index.chunkBlockStarts[this.index.chunkBlockStarts.length - 1];
		startBlock = Math.max(0, startBlock);
		endBlock = Math.min(totalTopLevelBlocks - 1, endBlock);
		if (startBlock > endBlock) {
			return [];
		}

		let chunkStart = findChunkIndex(this.index.chunkBlockStarts, startBlock);
		let chunkEnd = findChunkIndex(this.index.chunkBlockStarts, endBlock);
		if (chunkStart === -1 || chunkEnd === -1) {
			return [];
		}

		let contentStart = getContentStart(this.header, this.index);
		let compressedStart = this.index.chunkByteOffsets[chunkStart];
		let compressedEnd = this.index.chunkByteOffsets[chunkEnd + 1];
		let compressedBytes = await this._readRange(
			contentStart + compressedStart,
			compressedEnd - compressedStart
		);
		let blocks = [];
		for (let chunkIndex = chunkStart; chunkIndex <= chunkEnd; chunkIndex++) {
			let chunkOffset = this.index.chunkByteOffsets[chunkIndex] - compressedStart;
			let nextChunkOffset = this.index.chunkByteOffsets[chunkIndex + 1] - compressedStart;
			let chunkBytes = this._inflate(compressedBytes.subarray(chunkOffset, nextChunkOffset));
			let firstBlock = this.index.chunkBlockStarts[chunkIndex];
			let blockCount = this.index.chunkBlockStarts[chunkIndex + 1] - firstBlock;
			let localStart = Math.max(startBlock - firstBlock, 0);
			let localEnd = Math.min(endBlock - firstBlock, blockCount - 1);
			for (let local = localStart; local <= localEnd; local++) {
				blocks.push(parseJsonBytes(getBlockBytesFromContentChunk(chunkBytes, blockCount, local)));
			}
		}
		return blocks;
	}

	async getPageBlocks(pageIndex) {
		if (!Number.isInteger(pageIndex) || pageIndex < 0) {
			return [];
		}
		let catalog = await this.getCatalog();
		let pages = Array.isArray(catalog?.pages) ? catalog.pages : [];
		let page = pages[pageIndex];
		let totalTopLevelBlocks = this.getTopLevelBlockCount();
		let span = getContentRangeBlockSpan(page?.contentRange, totalTopLevelBlocks);
		if (!span || span.startIndex >= span.endIndexExclusive) {
			return [];
		}
		return this.getBlocks(span.startIndex, span.endIndexExclusive - 1);
	}

	async materialize() {
		let metadata = await this.getMetadata();
		let catalog = await this.getCatalog();
		let content = [];
		let contentLength = this.index.chunkByteOffsets[this.index.chunkByteOffsets.length - 1];
		let compressedContent = contentLength
			? await this._readRange(getContentStart(this.header, this.index), contentLength)
			: new Uint8Array(0);
		for (let chunkIndex = 0; chunkIndex < this.index.chunkByteOffsets.length - 1; chunkIndex++) {
			let chunkOffset = this.index.chunkByteOffsets[chunkIndex];
			let nextChunkOffset = this.index.chunkByteOffsets[chunkIndex + 1];
			let chunkBytes = this._inflate(compressedContent.subarray(chunkOffset, nextChunkOffset));
			let blockCount = this._getChunkBlockCount(chunkIndex);
			for (let local = 0; local < blockCount; local++) {
				content.push(parseJsonBytes(getBlockBytesFromContentChunk(chunkBytes, blockCount, local)));
			}
		}
		return {
			schemaVersion: this.header.schemaVersion,
			metadata,
			catalog,
			content,
		};
	}

	async _getTopLevelBlock(blockIndex) {
		let chunkIndex = findChunkIndex(this.index.chunkBlockStarts, blockIndex);
		if (chunkIndex === -1) {
			return null;
		}
		let chunkBytes = await this._readInflatedChunk(chunkIndex);
		let localBlockIndex = blockIndex - this.index.chunkBlockStarts[chunkIndex];
		return parseJsonBytes(getBlockBytesFromContentChunk(
			chunkBytes,
			this._getChunkBlockCount(chunkIndex),
			localBlockIndex
		));
	}

	async _readInflatedChunk(chunkIndex) {
		let contentStart = getContentStart(this.header, this.index);
		let offset = this.index.chunkByteOffsets[chunkIndex];
		let nextOffset = this.index.chunkByteOffsets[chunkIndex + 1];
		if (offset > nextOffset) {
			throw new Error('Invalid SDTPack chunk bounds');
		}
		return this._inflate(await this._readRange(contentStart + offset, nextOffset - offset));
	}

	_getChunkBlockCount(chunkIndex) {
		return this.index.chunkBlockStarts[chunkIndex + 1] - this.index.chunkBlockStarts[chunkIndex];
	}

	async _readRange(offset, length) {
		if (!Number.isInteger(offset) || !Number.isInteger(length) || offset < 0 || length < 0 || offset + length > this.source.byteLength) {
			throw new Error('Invalid SDTPack read range');
		}
		if (offset + length <= this._frontMatterBytes.byteLength) {
			return this._frontMatterBytes.subarray(offset, offset + length);
		}
		return readExactly(this.source, offset, length);
	}

	async _readJsonRange({ offset, length }) {
		return parseJsonBytes(this._inflate(await this._readRange(offset, length)));
	}
}

async function readExactly(source, offset, length) {
	let bytes = toUint8Array(await source.read(offset, length));
	if (bytes.byteLength !== length) {
		throw new Error('Short SDTPack read');
	}
	return bytes;
}

function normalizeByteSource(input) {
	if (isByteBuffer(input)) {
		let bytes = toUint8Array(input);
		return {
			byteLength: bytes.byteLength,
			async read(offset, length) {
				return bytes.subarray(offset, offset + length);
			},
		};
	}
	if (
		input
		&& Number.isInteger(input.byteLength)
		&& input.byteLength >= 0
		&& typeof input.read === 'function'
	) {
		return input;
	}
	throw new TypeError('Expected ArrayBuffer, Uint8Array, or byte source');
}

function normalizeInflate(inflate) {
	if (typeof inflate !== 'function') {
		throw new TypeError('Expected SDTPack raw DEFLATE inflate function');
	}
	return (bytes) => toUint8Array(inflate(toUint8Array(bytes)));
}

function parseJsonBytes(bytes) {
	return JSON.parse(TEXT_DECODER.decode(bytes));
}
