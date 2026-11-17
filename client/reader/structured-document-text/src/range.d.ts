import type { ContentBlockNode, PageContentRange, RefPath } from '../schema';

export interface PageBlockSpan {
	startIndex: number;
	endIndexExclusive: number;
}

export function makeContentPoint(ref: RefPath, offset?: number): number[];
export function makeContentRange(
	startRef: RefPath,
	endRef: RefPath,
	startOffset?: number,
	endOffset?: number
): PageContentRange;
export function setContentRangeStart(range: PageContentRange, ref: RefPath, offset?: number): void;
export function setContentRangeEnd(range: PageContentRange, ref: RefPath, offset?: number): void;
export function splitContentPoint(point: unknown, content: ContentBlockNode[]): {
	ref: RefPath | null;
	offset?: number;
};
export function splitContentRange(range: PageContentRange, content: ContentBlockNode[]): {
	start: {
		ref: RefPath | null;
		offset?: number;
	};
	end: {
		ref: RefPath | null;
		offset?: number;
	};
};
export function getContentRangeBlockSpan(contentRange: unknown, topLevelBlockCount: number): PageBlockSpan | null;
export function isContentRange(value: unknown): boolean;
export function isContentBoundary(value: unknown): boolean;
export function compareRefs(a: RefPath, b: RefPath): number;
export function sameRef(a: unknown, b: unknown): boolean;
export function refKey(ref: unknown): string;
export function isLeafBlock(node: unknown): boolean;
export function walkContentRangeLeafBlocks(
	content: ContentBlockNode[],
	range: PageContentRange,
	callback: (entry: {
		block: object;
		ref: RefPath;
		startPoint: {
			ref: RefPath | null;
			offset?: number;
		};
		endPoint: {
			ref: RefPath | null;
			offset?: number;
		};
	}) => void
): void;
