// Constants
export {
	HEADER_LAST_IS_SOFT_HYPHEN,
	HEADER_AXIS_DIR_SHIFT,
	HEADER_DIR_RTL,
	EPS,
	isVertical,
} from './constants.js';

// Encoding (chars → text nodes)
export { charsToTextNodes, charsToPreformattedTextNodes } from './encode.js';

// Decoding (textMap → positions)
export { parseTextMap, reconstructCharPositions, buildRunData } from './decode.js';
export { stringifyTextMap, optimizeTextMapRun } from './text-map.js';

// Text node utilities
export { canMergeTextNodes, mergeSequentialTextNodes } from './text-node.js';
export { getBlockPlainText, getNestedBlockPlainText } from '../text.js';

// Block reading (navigation, text, cursors)
export {
	getBlockByRef,
	getBlockText,
	getNextBlockRef,
	getTextNodesAtRange,
	nextChar,
	nextBlockChar,
} from './block.js';

// Block transformations
export {
	applyTextAttributes,
} from './block-transform.js';

// Debug utilities
export { compareRunErrors, printOptimizationReport } from './debug.js';

// Content utilities
export { getRefRangesFromPageRects, getContent, getSentencePageRects } from './content.js';
