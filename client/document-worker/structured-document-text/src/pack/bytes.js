const MAX_U32 = 0xffffffff;

export function concatUint8Arrays(chunks, totalLength = null) {
	if (totalLength === null) {
		totalLength = 0;
		for (let chunk of chunks) {
			totalLength += chunk.byteLength;
		}
	}
	let bytes = new Uint8Array(totalLength);
	let offset = 0;
	for (let chunk of chunks) {
		bytes.set(chunk, offset);
		offset += chunk.byteLength;
	}
	return bytes;
}

export function toUint8Array(bytes) {
	if (ArrayBuffer.isView(bytes)) {
		return isUint8Array(bytes) && bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength
			? bytes
			: new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
	}
	if (isArrayBuffer(bytes)) {
		return new Uint8Array(bytes);
	}
	throw new TypeError('Expected Uint8Array or ArrayBuffer');
}

export function isByteBuffer(bytes) {
	return isArrayBuffer(bytes) || ArrayBuffer.isView(bytes);
}

function isArrayBuffer(bytes) {
	return Object.prototype.toString.call(bytes) === '[object ArrayBuffer]';
}

function isUint8Array(bytes) {
	return Object.prototype.toString.call(bytes) === '[object Uint8Array]';
}

export function writeU32LE(bytes, offset, value) {
	validateUInt(value, MAX_U32, 'u32');
	bytes[offset] = value & 0xff;
	bytes[offset + 1] = (value >>> 8) & 0xff;
	bytes[offset + 2] = (value >>> 16) & 0xff;
	bytes[offset + 3] = (value >>> 24) & 0xff;
}

export function readU32LE(bytes, offset) {
	return (bytes[offset]
		+ bytes[offset + 1] * 0x100
		+ bytes[offset + 2] * 0x10000
		+ bytes[offset + 3] * 0x1000000) >>> 0;
}

export function decodeU32Array(bytes, offset, length) {
	let values = new Array(length);
	for (let i = 0; i < length; i++) {
		values[i] = readU32LE(bytes, offset + i * 4);
	}
	return values;
}

function validateUInt(value, max, label) {
	if (!Number.isInteger(value) || value < 0 || value > max) {
		throw new RangeError(`Invalid ${label} value: ${value}`);
	}
}
