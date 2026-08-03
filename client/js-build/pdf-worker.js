'use strict';

const fs = require('fs-extra');
const path = require('path');
const util = require('util');
const exec = util.promisify(require('child_process').exec);
const { getSignatures, writeSignatures, onSuccess, onError, getModuleDigest, getModulePin } = require('./utils');
const { buildsURL } = require('./config');

async function getPDFWorker(signatures) {
	const t1 = Date.now();

	const modulePath = path.join(__dirname, '..', 'pdf-worker');

	// Pharos: this tree is a detached copy of Zotero with no git metadata, so
	// `git rev-parse HEAD` is unavailable -- inside the monorepo it resolves to
	// the PARENT repository's HEAD, which changes on every unrelated commit and
	// makes the prebuilt-artifact lookup ask for a hash that has never existed.
	// A content digest of the module's own sources answers the real question,
	// which is whether this module changed. Same treatment as reader.js and
	// note-editor.js.
	const hash = getModulePin('pdf-worker', modulePath);

	if (!('pdf-worker' in signatures) || signatures['pdf-worker'].hash !== hash) {
		const targetDir = path.join(__dirname, '..', 'build', 'chrome', 'content', 'zotero', 'xpcom', 'pdfWorker');
		try {
			const filename = hash + '.zip';
			const tmpDir = path.join(__dirname, '..', 'tmp', 'builds', 'pdf-worker');
			const url = buildsURL + 'document-worker/' + filename;

			await fs.remove(targetDir);
			await fs.ensureDir(targetDir);
			await fs.ensureDir(tmpDir);

			await exec(
				`cd ${tmpDir}`
				+ ` && (test -f ${filename} || curl -f ${url} -o ${filename})`
				+ ` && unzip -o ${filename} worker.js -d ${targetDir}`
			);
		}
		catch (e) {
			console.error(e);
			await exec('npm ci', { cwd: modulePath });
			await exec('npm run build', { cwd: modulePath });
			await fs.copy(path.join(modulePath, 'build', 'worker.js'), path.join(targetDir, 'worker.js'));
		}
		signatures['pdf-worker'] = { hash };
	}

	const t2 = Date.now();

	return {
		action: 'pdf-worker',
		count: 1,
		totalCount: 1,
		processingTime: t2 - t1
	};
}

module.exports = getPDFWorker;

if (require.main === module) {
	(async () => {
		try {
			const signatures = await getSignatures();
			onSuccess(await getPDFWorker(signatures));
			await writeSignatures(signatures);
		}
		catch (err) {
			process.exitCode = 1;
			global.isError = true;
			onError(err);
		}
	})();
}
