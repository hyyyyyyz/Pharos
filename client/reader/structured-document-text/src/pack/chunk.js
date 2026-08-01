import { readU32LE, writeU32LE, toUint8Array } from './bytes.js';

const OFFSET_BYTES = 4;

export function encodeContentChunk(blockByteArrays) {
	let blockCount = blockByteArrays.length;
	let offsetsByteLength = blockCount * OFFSET_BYTES;
	let headerByteLength = offsetsByteLength;
	let contentByteLength = 0;
	for (let i = 0; i < blockCount; i++) {
		blockByteArrays[i] = toUint8Array(blockByteArrays[i]);
		contentByteLength += blockByteArrays[i].byteLength;
	}

	let bytes = new Uint8Array(headerByteLength + contentByteLength);
	let contentOffset = 0;
	for (let i = 0; i < blockCount; i++) {
		writeU32LE(bytes, i * OFFSET_BYTES, contentOffset);
		contentOffset += blockByteArrays[i].byteLength;
	}

	let writeOffset = headerByteLength;
	for (let i = 0; i < blockCount; i++) {
		bytes.set(blockByteArrays[i], writeOffset);
		writeOffset += blockByteArrays[i].byteLength;
	}
	return bytes;
}

export function getBlockBytesFromContentChunk(chunkBytes, blockCount, localBlockIndex) {
	chunkBytes = toUint8Array(chunkBytes);
	validateBlockCount(blockCount);
	if (!Number.isInteger(localBlockIndex) || localBlockIndex < 0 || localBlockIndex >= blockCount) {
		throw new RangeError(`Invalid local block index: ${localBlockIndex}`);
	}
	let headerByteLength = blockCount * OFFSET_BYTES;
	if (chunkBytes.byteLength < headerByteLength) {
		throw new Error('Invalid SDTPack content chunk offset table');
	}
	let start = readU32LE(chunkBytes, localBlockIndex * OFFSET_BYTES);
	let end = localBlockIndex + 1 < blockCount
		? readU32LE(chunkBytes, (localBlockIndex + 1) * OFFSET_BYTES)
		: chunkBytes.byteLength - headerByteLength;
	if (start > end || headerByteLength + end > chunkBytes.byteLength) {
		throw new Error('Invalid SDTPack content chunk block range');
	}
	return chunkBytes.subarray(headerByteLength + start, headerByteLength + end);
}

function validateBlockCount(blockCount) {
	if (!Number.isInteger(blockCount) || blockCount < 0) {
		throw new RangeError(`Invalid SDTPack content chunk block count: ${blockCount}`);
	}
}
