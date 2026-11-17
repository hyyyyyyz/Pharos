export function exportOutline(structure) {
	const outline = Array.isArray(structure?.catalog?.outline) ? structure.catalog.outline : [];

	function normalizeItem(item) {
		if (!item || typeof item !== 'object') return null;
		if (typeof item.title !== 'string' || !item.title) return null;

		const children = Array.isArray(item.children)
			? item.children.map(normalizeItem).filter(Boolean)
			: [];
		const normalized = {
			title: item.title,
		};
		if (Array.isArray(item.ref) && item.ref.length) {
			normalized.startRef = item.ref.join('.');
		}
		if (typeof item.target?.url === 'string') {
			normalized.url = item.target.url;
		}
		if (item.target?.position && typeof item.target.position === 'object') {
			normalized.position = item.target.position;
		}
		if (item.source === 'native' || item.source === 'detected') {
			normalized.source = item.source;
		}
		if (children.length) {
			normalized.children = children;
		}
		return normalized;
	}

	return outline.map(normalizeItem).filter(Boolean);
}
