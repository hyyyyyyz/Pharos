import { getContentRangeBlockSpan } from './range.js';

export function getPageBlockSpan(structure, pageIndex) {
	const content = Array.isArray(structure?.content) ? structure.content : [];
	const pages = Array.isArray(structure?.catalog?.pages) ? structure.catalog.pages : [];
	return getContentRangeBlockSpan(pages[pageIndex]?.contentRange, content.length);
}
