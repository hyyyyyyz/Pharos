'use strict';

const fs = require('fs-extra');
const path = require('path');
const util = require('util');
const exec = util.promisify(require('child_process').exec);
const { getSignatures, writeSignatures, onSuccess, onError, getModuleDigest, npmExecOptions } = require('./utils');
const { buildsURL } = require('./config');

async function getReader(signatures) {
	const t1 = Date.now();

	const modulePath = path.join(__dirname, '..', 'reader');
	
	// Pharos: this tree is a detached copy of Zotero with no git metadata, so
	// `git rev-parse HEAD` is unavailable. A content digest of the module's own
	// sources serves the same purpose -- see getModuleDigest for why it must
	// cover src/ and not just the manifests.
	const hash = getModuleDigest(modulePath);
	
	if (!('reader' in signatures) || signatures['reader'].hash !== hash) {
		const targetDir = path.join(__dirname, '..', 'build', 'resource', 'reader');
		try {
			const filename = hash + '.zip';
			const tmpDir = path.join(__dirname, '..', 'tmp', 'builds', 'reader');
			const url = buildsURL + 'reader/' + filename;

			await fs.remove(targetDir);
			await fs.ensureDir(targetDir);
			await fs.ensureDir(tmpDir);

			await exec(
				`cd ${tmpDir}`
				+ ` && (test -f ${filename} || curl -f ${url} -o ${filename})`
				+ ` && unzip ${filename} zotero/* -d ${targetDir}`
				+ ` && mv ${path.join(targetDir, 'zotero', '*')} ${targetDir}`
			);

			await fs.remove(path.join(targetDir, 'zotero'));
		}
		catch (e) {
			if (!e.message?.includes('The requested URL returned error: 403')) {
				console.error(e);
			}
			await exec('npm ci', npmExecOptions(modulePath));
			await exec('npm run build:zotero', npmExecOptions(modulePath));
			// `await` matters: fs-extra's pathExists returns a promise, and a
			// promise is always truthy, so without it this guard never fired --
			// which is how a failed reader build got as far as leaving
			// build/resource/reader empty and the app shipping with no reader.
			if (!await fs.pathExists(path.join(modulePath, 'build', 'zotero', 'pdf', 'build', 'pdf.mjs'))) {
				throw new Error('pdf.js build failed to produce output');
			}
			await fs.copy(path.join(modulePath, 'build', 'zotero'), targetDir);
		}
		signatures['reader'] = { hash };
	}
	
	const t2 = Date.now();

	return {
		action: 'reader',
		count: 1,
		totalCount: 1,
		processingTime: t2 - t1
	};
}

module.exports = getReader;

if (require.main === module) {
	(async () => {
		try {
			const signatures = await getSignatures();
			onSuccess(await getReader(signatures));
			await writeSignatures(signatures);
		}
		catch (err) {
			process.exitCode = 1;
			global.isError = true;
			onError(err);
		}
	})();
}
