import type { FragmentSelector } from '../../schema';

export function expandSelectorMap(blockSelectorMap: string, selectorMap: string): string;
export function expandBlockAnchor(blockSelectorMap: string): FragmentSelector;
export function parseSelectorMapEntries(selectorMap: string): { length: number; path: string }[] | null;
export function resolveSelectorMap(selectorMap: string, start: number, end?: number, deltaMap?: string): FragmentSelector;
export function findCommonCFIPath(a: string, b: string): {
	common: string;
	remainderA: string;
	remainderB: string;
};
export function resolveSelectorMapRange(
	startSelectorMap: string,
	startOffset: number,
	endSelectorMap: string,
	endOffset: number,
	startDeltaMap?: string,
	endDeltaMap?: string,
): FragmentSelector;
