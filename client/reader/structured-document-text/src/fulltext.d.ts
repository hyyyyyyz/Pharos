import type { ContentBlockNode, PageInfo } from '../../schema';

export interface FulltextStructuredDocumentText {
	catalog: {
		pages: PageInfo[];
	};
	content: ContentBlockNode[];
}

export function getFulltextFromStructuredText(structure: FulltextStructuredDocumentText, pageIndexes: number[]): string;
