import type { RefPath } from '../schema';

export interface PartChainEntry {
	ref: RefPath;
	block: object;
}

export function getPartChain(
	structure: unknown,
	ref: RefPath,
	options?: {
		include?: (ref: RefPath, block: object) => boolean;
	}
): PartChainEntry[];
export function shouldDropHardHyphenAtPartBoundary(prevBlock: object, nextBlock: object): boolean;
export function getPartBoundarySeparator(prevBlock: object, nextBlock: object): string;
export function getLogicalBlockText(
	structure: unknown,
	ref: RefPath,
	options?: {
		include?: (ref: RefPath, block: object) => boolean;
	}
): string;
