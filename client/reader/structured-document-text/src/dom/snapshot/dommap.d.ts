import type { DomMapNode } from '../../../schema';

export const DOM_MAP_FIRST_OF_TYPE: number;
export const DOM_MAP_LAST_OF_TYPE: number;
export const DOM_MAP_LAST_CHILD: number;

export interface DomMapIndexNode {
	node: DomMapNode;
	parent: DomMapIndexNode | null;
	children: DomMapIndexNode[];
}

export interface DomMapIndex {
	roots: DomMapIndexNode[];
	nodes: DomMapIndexNode[];
}

export function buildDomMapIndex(domMap: DomMapNode[] | undefined): DomMapIndex | null;

export function findDomMapContaining(index: DomMapIndex, start: number, end: number): DomMapIndexNode | null;

export function generateDomMapSelector(indexed: DomMapIndexNode): string;

export function domMapSegment(node: { tag: string, index: number, flags?: number }): string;

export function matchDomMapSelector(index: DomMapIndex, selector: string): DomMapIndexNode | null;

export function cssEscape(value: string): string;
