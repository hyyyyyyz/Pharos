const path = require('path');
const fs = require('fs-extra');
const colors = require('colors/safe');
const green = colors.green;
const blue = colors.blue;
const yellow = colors.yellow;
const isWindows = /^win/.test(process.platform);

const ROOT = path.resolve(__dirname, '..');
const NODE_ENV = process.env.NODE_ENV;


function onError(err) {
	console.log('\u0007'); //🔔
	console.log(colors.red('Error:'), err);
}

function onSuccess(result) {
	var msg = `${green('Success:')} ${blue(`[${result.action}]`)} ${result.count} files processed`;
	if (result.totalCount) {
		msg += ` | ${result.totalCount} checked`;
	}

	msg += ` [${yellow(`${result.processingTime.toFixed(2)}ms`)}]`;

	console.log(msg);
}

function onProgress(sourcefile, outfile, operation) {
	if ('isError' in global && global.isError) {
		return;
	}
	if (NODE_ENV === 'debug' && outfile) {
		console.log(`${colors.blue(`[${operation}]`)} ${sourcefile} -> ${outfile}`);
	}
	else {
		console.log(`${colors.blue(`[${operation}]`)} ${sourcefile}`);
	}
}

async function getSignatures() {
	let signaturesFile = path.resolve(ROOT, '.signatures.json');
	var signatures = {};
	try {
		signatures = await fs.readJson(signaturesFile);
	}
	catch (_) {
		// if signatures files doesn't exist, return empty object instead
	}
	return signatures;
}

async function writeSignatures(signatures) {
	let signaturesFile = path.resolve(ROOT, '.signatures.json');
	NODE_ENV == 'debug' && console.log('writing signatures to .signatures.json');
	await fs.outputJson(signaturesFile, signatures);
}


async function recursivelyRemoveEmptyDirsUp(dirsSeen, invalidDirsCount = 0, removedDirsCount = 0) {
	const newDirsSeen = new Set();
	for (let dir of dirsSeen) {
		try {
			// check if dir from signatures exists in source
			await fs.access(dir, fs.constants.F_OK);
		}
		catch (_) {
			invalidDirsCount++;
			NODE_ENV == 'debug' && console.log(`Dir ${dir} found in signatures but not in src, deleting from build`);
			try {
				await fs.remove(path.join('build', dir));
				const parentDir = path.dirname(dir);
				if (!dirsSeen.has(parentDir) && parentDir !== ROOT) {
					newDirsSeen.add(path.dirname(dir));
				}
				removedDirsCount++;
			}
			catch (_) {
				// dir wasn't in the build either
			}
		}
	}
	if (newDirsSeen.size) {
		return recursivelyRemoveEmptyDirsUp(newDirsSeen, invalidDirsCount, removedDirsCount);
	}
	return { invalidDirsCount, removedDirsCount };
}

async function cleanUp(signatures) {
	const t1 = Date.now();
	let dirsSeen = new Set();
	var removedCount = 0, invalidCount = 0;

	for (let f of Object.keys(signatures)) {
		let dir = path.dirname(f);
		dirsSeen.add(dir);
		try {
			// check if file from signatures exists in source
			await fs.access(f, fs.constants.F_OK);
		}
		catch (_) {
			invalidCount++;
			NODE_ENV == 'debug' && console.log(`File ${f} found in signatures but not in src, deleting from build`);
			try {
				await fs.remove(path.join('build', f));
				removedCount++;
			}
			catch (_) {
				// file wasn't in the build either
			}
			delete signatures[f];
		}
	}

	const { invalidDirsCount, removedDirsCount } = await recursivelyRemoveEmptyDirsUp(dirsSeen);
	invalidCount += invalidDirsCount;
	removedCount += removedDirsCount;

	const t2 = Date.now();
	return {
		action: 'cleanup',
		count: removedCount,
		totalCount: invalidCount,
		processingTime: t2 - t1
	};
}

async function getFileSignature(file) {
	let stats = await fs.stat(file);
	return {
		mode: stats.mode,
		mtime: stats.mtimeMs || stats.mtime.getTime(),
		isDirectory: stats.isDirectory(),
		isFile: stats.isFile()
	};
}

function compareSignatures(a, b) {
	return typeof a === 'object'
	&& typeof b === 'object'
	&& a !== null
	&& b !== null
	&& ['mode', 'mtime', 'isDirectory', 'isFile'].reduce((acc, k) => {
		return acc ? k in a && k in b && a[k] == b[k] : false;
	}, true);
}

function getPathRelativeTo(f, dirName) {
	return path.relative(path.join(ROOT, dirName), path.join(ROOT, f));
}

const formatDirsForMatcher = (dirs) => {
	return dirs.length > 1 ? `{${dirs.join(',')}}` : dirs[0];
};

function comparePaths(actualPath, testedPath) {
	// compare paths after normalizing os-specific path separator
	return path.normalize(actualPath) === path.normalize(testedPath);
}

function debounce(func, timeout = 200) {
	let timer;
	return (...args) => {
		clearTimeout(timer);
		timer = setTimeout(() => func.apply(this, args), timeout);
	};
}

const envCheckTrue = env => !!(env && (parseInt(env) || env === true || env === "true"));

/**
 * Content digest of a submodule, used by the reader / note-editor /
 * document-worker builds to decide whether the module changed.
 *
 * Upstream derives that hash from `git rev-parse HEAD`. Pharos is a detached
 * copy with no git metadata, so it is computed from the module's own sources
 * instead. Hashing only the manifests -- as the first version of this
 * workaround did -- meant editing a stylesheet inside a module left its hash
 * untouched, so the module silently kept serving its previous bundle and the
 * edit never reached the app.
 *
 * Paths are sorted so the digest does not depend on directory iteration order.
 *
 * @param {String} modulePath  absolute path to the module
 * @param {String[]} dirs      source directories to walk, relative to modulePath
 * @param {String[]} files     individual files to include, relative to modulePath
 * @return {String} sha1 hex digest
 */
function getModuleDigest(modulePath, dirs = ['src'], files = ['package.json', 'package-lock.json']) {
	const crypto = require('crypto');
	const hash = crypto.createHash('sha1');
	const collected = [];

	const walk = (dir) => {
		let entries;
		try {
			entries = fs.readdirSync(dir, { withFileTypes: true });
		}
		catch (e) {
			return; // A module may legitimately not have every directory.
		}
		for (const entry of entries) {
			const full = path.join(dir, entry.name);
			if (entry.isDirectory()) {
				if (entry.name === 'node_modules' || entry.name === '.git') {
					continue;
				}
				walk(full);
			}
			else if (entry.isFile()) {
				collected.push(full);
			}
		}
	};

	for (const dir of dirs) {
		walk(path.join(modulePath, dir));
	}
	for (const file of files) {
		const full = path.join(modulePath, file);
		if (fs.existsSync(full)) {
			collected.push(full);
		}
	}

	for (const file of collected.sort()) {
		hash.update(path.relative(modulePath, file));
		hash.update(fs.readFileSync(file));
	}

	return hash.digest('hex');
}


/**
 * Options for running npm inside a submodule during a source build.
 *
 * Routes npm through a cache under the repo's own tmp/ rather than ~/.npm.
 * A `sudo npm` at some point in this machine's past left root-owned files in
 * the shared cache, so `npm ci` there dies with EACCES -- and because the
 * submodule builds clear their output directory *before* rebuilding, that
 * failure does not merely skip the module, it leaves it empty. The permanent
 * fix is `sudo chown -R $(whoami) ~/.npm`, which needs the user's password;
 * an isolated cache gets the build working without it.
 *
 * @param {String} modulePath  absolute path to the module, used as npm's cwd
 */
/**
 * The upstream commit a bundled submodule is pinned to.
 *
 * Upstream reads this with `git rev-parse HEAD` inside the module. That does not
 * work here for two separate reasons: this tree is a detached copy with no git
 * metadata of its own, and inside the monorepo the command resolves to the
 * PARENT repository's HEAD -- a hash that changes on every unrelated commit and
 * has never named a published artefact.
 *
 * So the pins are recorded in js-build/submodule-pins.json instead. When a pin
 * is present the build downloads the artefact Zotero itself published for that
 * commit. Modules listed in `_build_from_source` intentionally use a content
 * digest, which misses the upstream artefact cache and builds locally. That is
 * the correct behaviour for a modified module: its sources are no longer what
 * any published artefact was built from.
 */
function shouldBuildModuleFromSource(moduleName) {
	try {
		const pins = require(path.join(ROOT, 'js-build', 'submodule-pins.json'));
		return Array.isArray(pins._build_from_source)
			&& pins._build_from_source.includes(moduleName);
	}
	catch (e) {
		return false;
	}
}

function getModulePin(moduleName, modulePath) {
	try {
		const pins = require(path.join(ROOT, 'js-build', 'submodule-pins.json'));
		if (shouldBuildModuleFromSource(moduleName)) {
			return getModuleDigest(modulePath);
		}
		if (pins[moduleName]) {
			return pins[moduleName];
		}
	}
	catch (e) {
		// No pin file is not an error -- fall through to the digest.
	}
	return getModuleDigest(modulePath);
}

function npmExecOptions(modulePath) {
	return {
		cwd: modulePath,
		env: {
			...process.env,
			npm_config_cache: path.join(ROOT, 'tmp', 'npm-cache'),
		},
	};
}


module.exports = {
	cleanUp,
	comparePaths,
	compareSignatures,
	debounce,
	envCheckTrue,
	formatDirsForMatcher,
	getFileSignature,
	getModuleDigest,
	getModulePin,
	getPathRelativeTo,
	getSignatures,
	isWindows,
	npmExecOptions,
	onError,
	onProgress,
	onSuccess,
	shouldBuildModuleFromSource,
	writeSignatures,
};
