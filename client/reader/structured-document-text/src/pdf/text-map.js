const TEXT_MAP_MAX_GEOMETRY_ERROR = 0.25;

function roundShortest(value, maxError) {
	if (!Number.isFinite(value)) return value;
	for (let d = 0; d <= 6; d++) {
		const f = 10 ** d;
		const rounded = Math.round(value * f) / f;
		if (Math.abs(rounded - value) <= maxError + 1e-9) return rounded;
	}
	return value;
}

export function optimizeTextMapRun(run, maxError = TEXT_MAP_MAX_GEOMETRY_ERROR) {
	if (!Array.isArray(run) || run.length < 6) return run;

	const [header, pageIndex, minX, minY, maxX, maxY, ...widths] = run;
	const result = [
		header,
		pageIndex,
		roundShortest(minX, maxError),
		roundShortest(minY, maxError),
		roundShortest(maxX, maxError),
		roundShortest(maxY, maxError),
	];

	let cumErr = result[2] - minX;

	for (const w of widths) {
		if (Array.isArray(w)) {
			const [delta, width] = w;
			const targetDelta = delta - cumErr;
			const roundedDelta = roundShortest(targetDelta, maxError);
			cumErr = roundedDelta - targetDelta;

			let targetWidth = width - cumErr;
			if (targetWidth < 0) {
				cumErr += targetWidth;
				targetWidth = 0;
			}
			const roundedWidth = roundShortest(targetWidth, maxError);
			cumErr = roundedWidth - targetWidth;

			result.push([roundedDelta, roundedWidth]);
		}
		else {
			let targetWidth = w - cumErr;
			if (targetWidth < 0) {
				cumErr += targetWidth;
				targetWidth = 0;
			}
			const roundedWidth = roundShortest(targetWidth, maxError);
			cumErr = roundedWidth - targetWidth;

			result.push(roundedWidth);
		}
	}

	return result;
}

export function stringifyTextMap(runs) {
	if (!Array.isArray(runs)) {
		return null;
	}
	return JSON.stringify(runs.map(run => optimizeTextMapRun(run)));
}
