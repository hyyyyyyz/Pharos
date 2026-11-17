import { getNestedBlockPlainText } from './text.js';

function flattenOutline(items, ancestors) {
	const result = [];
	if (!Array.isArray(items)) return result;
	for (const item of items) {
		const blockIndex =
			Array.isArray(item.ref) && item.ref.length > 0 ? item.ref[0] : null;
		const path = [...ancestors, item.title];
		if (Number.isInteger(blockIndex) && blockIndex >= 0) {
			result.push({ blockIndex, path });
		}
		if (Array.isArray(item.children)) {
			result.push(...flattenOutline(item.children, path));
		}
	}
	return result;
}

function collectBlockPageRects(block, map) {
	const pageRects = block?.anchor?.pageRects;
	if (!Array.isArray(pageRects)) return;
	for (const pr of pageRects) {
		if (!Array.isArray(pr) || pr.length < 5) continue;
		const pageIndex = pr[0];
		if (!Number.isFinite(pageIndex)) continue;
		const rect = [pr[1], pr[2], pr[3], pr[4]];
		if (!map.has(pageIndex)) {
			map.set(pageIndex, []);
		}
		map.get(pageIndex).push(rect);
	}
}

function countLeafBlocks(block) {
	if (!block) return 0;
	if (!Array.isArray(block.content) || block.content.length === 0) return 1;
	let count = 0;
	for (const child of block.content) {
		count += countLeafBlocks(child);
	}
	return count;
}

const MIN_CHUNK_TEXT_LENGTH = 30;

export function getNextChunk(structure, startBlockIndex) {
	const content = structure?.content;
	if (
		!Array.isArray(content) ||
		startBlockIndex < 0 ||
		startBlockIndex >= content.length
	) {
		return null;
	}

	const headings = flattenOutline(structure.catalog.outline, []);
	headings.sort((a, b) => a.blockIndex - b.blockIndex);

	while (startBlockIndex < content.length) {
		while (
			startBlockIndex < content.length &&
			content[startBlockIndex]?.flowClass === 'excluded'
		) {
			startBlockIndex++;
		}
		if (startBlockIndex >= content.length) {
			return null;
		}

		let endBlockIndex = content.length - 1;
		for (const h of headings) {
			if (h.blockIndex > startBlockIndex) {
				endBlockIndex = h.blockIndex - 1;
				break;
			}
		}

		while (endBlockIndex > startBlockIndex) {
			const block = content[endBlockIndex];
			if (
				block &&
				block.flowClass !== 'excluded' &&
				getNestedBlockPlainText(block)
			)
				break;
			endBlockIndex--;
		}

		let outlinePath = '';
		for (let i = headings.length - 1; i >= 0; i--) {
			if (headings[i].blockIndex <= startBlockIndex) {
				outlinePath = headings[i].path.join(' > ');
				break;
			}
		}

		let contentStartIndex = startBlockIndex;
		for (const h of headings) {
			if (h.blockIndex === startBlockIndex) {
				contentStartIndex = startBlockIndex + 1;
				break;
			}
		}

		const textParts = [];
		const pageRectsMap = new Map();
		if (contentStartIndex !== startBlockIndex) {
			collectBlockPageRects(content[startBlockIndex], pageRectsMap);
		}
		let leafBlockCount = 0;

		for (let i = contentStartIndex; i <= endBlockIndex; i++) {
			const block = content[i];
			if (!block || block.flowClass === 'excluded') continue;

			const blockText = getNestedBlockPlainText(block);
			if (blockText) {
				textParts.push(blockText);
			}

			leafBlockCount += countLeafBlocks(block);
			collectBlockPageRects(block, pageRectsMap);
		}

		const text = textParts.join('\n');

		if (!text || text.length < MIN_CHUNK_TEXT_LENGTH) {
			startBlockIndex = endBlockIndex + 1;
			continue;
		}

		const pageRects = [];
		for (const [pageIndex, rects] of pageRectsMap) {
			for (const rect of rects) {
				if (!Array.isArray(rect) || rect.length < 4) continue;
				pageRects.push([pageIndex, rect[0], rect[1], rect[2], rect[3]]);
			}
		}
		pageRects.sort((a, b) => a[0] - b[0]);

		return {
			startBlockIndex,
			endBlockIndex,
			leafBlockCount,
			outlinePath,
			text,
			pageRects,
		};
	}
	return null;
}
