'use strict';

const fs = require('fs-extra');
const path = require('path');
const util = require('util');
const exec = util.promisify(require('child_process').exec);
const { getSignatures, writeSignatures, onSuccess, onError, getModulePin, npmExecOptions } = require('./utils');
const { buildsURL } = require('./config');

const sharedAssetDirs = ['cmaps', 'standard_fonts'];
const requiredFiles = ['worker.js', 'metadata.json', 'structured-document-text.js'];

async function getDocumentWorker(signatures) {
	const t1 = Date.now();

	const modulePath = path.join(__dirname, '..', 'document-worker');
	const targetDir = path.join(__dirname, '..', 'build', 'resource', 'document-worker');

	// Pharos: this tree is a detached copy of Zotero with no git metadata, and
	// inside the monorepo `git rev-parse HEAD` resolves to the PARENT
	// repository's HEAD -- a hash no prebuilt artifact was ever published
	// under. The pin recorded in js-build/submodule-pins.json is the real
	// submodule commit. Same treatment as reader.js and note-editor.js.
	const hash = getModulePin('document-worker', modulePath);

	if (!('document-worker' in signatures)
			|| signatures['document-worker'].hash !== hash
			|| !(await isBuildReady(targetDir))) {
		try {
			const filename = hash + '.zip';
			const tmpDir = path.join(__dirname, '..', 'tmp', 'builds', 'document-worker');
			const url = buildsURL + 'document-worker/' + filename;

			await fs.remove(targetDir);
			await fs.ensureDir(targetDir);
			await fs.ensureDir(tmpDir);

			// Skip the shared asset directories, which are served from the
			// reader build instead (see the cleanup loop below)
			await exec(
				`cd ${tmpDir}`
				+ ` && (test -f ${filename} || curl -f ${url} -o ${filename})`
				+ ` && unzip -o ${filename} -d ${targetDir} -x ${sharedAssetDirs.map(dir => `'${dir}/*'`).join(' ')}`
			);
			let missingFiles = await getMissingFiles(targetDir);
			if (missingFiles.length) {
				throw new Error(`Downloaded document-worker build is missing ${missingFiles.join(', ')}`);
			}
		}
		catch (e) {
			console.error(e);
			await exec('npm ci', npmExecOptions(modulePath));
			await exec('npm run build', npmExecOptions(modulePath));
			await fs.copy(path.join(modulePath, 'build'), targetDir);
			let missingFiles = await getMissingFiles(targetDir);
			if (missingFiles.length) {
				throw new Error(`Local document-worker build is missing ${missingFiles.join(', ')}`);
			}
		}
		signatures['document-worker'] = { hash };
	}

	for (let dir of sharedAssetDirs) {
		await fs.remove(path.join(targetDir, dir));
	}

	const t2 = Date.now();

	return {
		action: 'document-worker',
		count: 1,
		totalCount: 1,
		processingTime: t2 - t1
	};
}

async function isBuildReady(targetDir) {
	return !(await getMissingFiles(targetDir)).length;
}

async function getMissingFiles(targetDir) {
	let missingFiles = [];
	for (let file of requiredFiles) {
		if (!(await fs.pathExists(path.join(targetDir, file)))) {
			missingFiles.push(file);
		}
	}
	return missingFiles;
}

module.exports = getDocumentWorker;

if (require.main === module) {
	(async () => {
		try {
			const signatures = await getSignatures();
			onSuccess(await getDocumentWorker(signatures));
			await writeSignatures(signatures);
		}
		catch (err) {
			process.exitCode = 1;
			global.isError = true;
			onError(err);
		}
	})();
}
