export function sameRef(a, b) {
	if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
		return false;
	}
	for (let i = 0; i < a.length; i++) {
		if (a[i] !== b[i]) {
			return false;
		}
	}
	return true;
}

export function isWhitespaceChar(ch) {
	return ch === ' ' || ch === '\n' || ch === '\t';
}
