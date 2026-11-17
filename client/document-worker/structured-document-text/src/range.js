export function makeContentPoint(ref, offset = undefined) {
	if (!Array.isArray(ref)) {
		throw new TypeError('Content point ref must be an array');
	}
	let point = [...ref];
	if (Number.isInteger(offset)) {
		point.push(offset);
	}
	return point;
}

export function makeContentRange(startRef, endRef, startOffset = undefined, endOffset = undefined) {
	return [
		makeContentPoint(startRef, startOffset),
		makeContentPoint(endRef, endOffset),
	];
}

export function setContentRangeStart(range, ref, offset = undefined) {
	range[0] = makeContentPoint(ref, offset);
}

export function setContentRangeEnd(range, ref, offset = undefined) {
	range[1] = makeContentPoint(ref, offset);
}

export function splitContentPoint(point, content) {
	if (!Array.isArray(content)) {
		throw new TypeError('splitContentPoint requires content');
	}
	if (!point) {
		return { ref: null, offset: undefined };
	}
	if (!Array.isArray(point)) {
		return { ref: null, offset: undefined };
	}

	let ref = [];
	let node = null;
	for (let i = 0; i < point.length; i++) {
		let value = point[i];
		if (!Number.isInteger(value)) {
			return { ref: null, offset: undefined };
		}
		if (isTextNode(node) && i === point.length - 1) {
			return { ref, offset: value };
		}
		ref.push(value);
		node = ref.length === 1
			? content[value]
			: Array.isArray(node?.content)
				? node.content[value]
				: null;
	}
	return { ref, offset: undefined };
}

export function splitContentRange(range, content) {
	return {
		start: splitContentPoint(range?.[0], content),
		end: splitContentPoint(range?.[1], content),
	};
}

export function getContentRangeBlockSpan(contentRange, topLevelBlockCount) {
	if (!isContentRange(contentRange) || !Number.isInteger(topLevelBlockCount) || topLevelBlockCount < 0) {
		return null;
	}

	const start = contentRange[0];
	const end = contentRange[1];
	const startIndex = getBoundaryTopLevelIndex(start, topLevelBlockCount);
	if (startIndex === null) {
		return null;
	}

	if (sameBoundary(start, end)) {
		return { startIndex, endIndexExclusive: startIndex };
	}

	const endIndexExclusive = getBoundaryEndIndexExclusive(end, topLevelBlockCount);
	if (endIndexExclusive === null) {
		return null;
	}

	return {
		startIndex,
		endIndexExclusive: Math.max(startIndex, endIndexExclusive),
	};
}

export function isContentRange(value) {
	return Array.isArray(value)
		&& value.length === 2
		&& isContentBoundary(value[0])
		&& isContentBoundary(value[1]);
}

export function isContentBoundary(value) {
	return Array.isArray(value)
		&& value.length > 0
		&& value.every(index => Number.isInteger(index) && index >= 0);
}

function isTextNode(node) {
	return !!node && typeof node === 'object' && typeof node.text === 'string';
}

export function compareRefs(a, b) {
	const length = Math.min(a.length, b.length);
	for (let i = 0; i < length; i++) {
		if (a[i] !== b[i]) {
			return a[i] - b[i];
		}
	}
	return a.length - b.length;
}

export function sameRef(a, b) {
	return Array.isArray(a)
		&& Array.isArray(b)
		&& a.length === b.length
		&& a.every((value, index) => value === b[index]);
}

export function refKey(ref) {
	return Array.isArray(ref) ? ref.join(',') : '';
}

export function isLeafBlock(node) {
	if (!node || typeof node.text === 'string') {
		return false;
	}
	if (!Array.isArray(node.content) || node.content.length === 0) {
		return true;
	}
	return !node.content.some(child => child && typeof child.text !== 'string');
}

export function walkContentRangeLeafBlocks(content, range, callback) {
	if (!Array.isArray(content) || typeof callback !== 'function') {
		return;
	}

	const span = getContentRangeBlockSpan(range, content.length);
	if (!span || span.startIndex >= span.endIndexExclusive) {
		return;
	}

	let parts;
	try {
		parts = splitContentRange(range, content);
	}
	catch (_) {
		return;
	}

	for (let i = span.startIndex; i < span.endIndexExclusive; i++) {
		const block = content[i];
		if (!block || typeof block.text === 'string') {
			continue;
		}
		walkLeafBlocks(block, [i], range, parts, callback);
	}
}

function walkLeafBlocks(node, ref, range, parts, callback) {
	if (!node || typeof node.text === 'string') {
		return;
	}

	if (isLeafBlock(node)) {
		const leafEnd = getLeafEndBoundary(ref);
		if (compareRefs(leafEnd, range[0]) <= 0 || compareRefs(ref, range[1]) >= 0) {
			return;
		}
		callback({
			block: node,
			ref,
			startPoint: parts.start,
			endPoint: parts.end,
		});
		return;
	}

	for (let i = 0; i < node.content.length; i++) {
		const child = node.content[i];
		if (child && typeof child.text !== 'string') {
			walkLeafBlocks(child, [...ref, i], range, parts, callback);
		}
	}
}

function getLeafEndBoundary(ref) {
	const end = [...ref];
	end[end.length - 1]++;
	return end;
}

function sameBoundary(a, b) {
	return Array.isArray(a)
		&& Array.isArray(b)
		&& a.length === b.length
		&& a.every((value, index) => value === b[index]);
}

function getBoundaryTopLevelIndex(boundary, topLevelBlockCount) {
	if (!isContentBoundary(boundary)) {
		return null;
	}
	const index = boundary[0];
	if (index > topLevelBlockCount) {
		return null;
	}
	return index;
}

function getBoundaryEndIndexExclusive(boundary, topLevelBlockCount) {
	const index = getBoundaryTopLevelIndex(boundary, topLevelBlockCount);
	if (index === null) {
		return null;
	}
	if (index === topLevelBlockCount) {
		return topLevelBlockCount;
	}
	return boundary.length === 1 ? index : index + 1;
}
