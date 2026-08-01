export function deepEqual(a, b) {
	if (a === b) return true;
	if (a == null || b == null) return false;

	if (Array.isArray(a)) {
		if (!Array.isArray(b) || a.length !== b.length) return false;
		for (let i = 0; i < a.length; i++) {
			if (!deepEqual(a[i], b[i])) return false;
		}
		return true;
	}
	if (Array.isArray(b)) return false;

	if (typeof a !== 'object' || typeof b !== 'object') return false;

	let aKeys = Object.keys(a);
	if (aKeys.length !== Object.keys(b).length) return false;

	for (let key of aKeys) {
		if (!(key in b)) return false;
		if (!deepEqual(a[key], b[key])) return false;
	}

	return true;
}
