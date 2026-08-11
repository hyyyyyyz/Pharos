'use strict';

const fs = require('fs-extra');
const path = require('path');
const util = require('util');
const exec = util.promisify(require('child_process').exec);
const {
	getSignatures,
	writeSignatures,
	onSuccess,
	onError,
	getModulePin,
	npmExecOptions,
	shouldBuildModuleFromSource,
} = require('./utils');
const { buildsURL } = require('./config');

async function getReader(signatures) {
	const t1 = Date.now();

	const modulePath = path.join(__dirname, '..', 'reader');

	async function buildFromSource(targetDir) {
		let npmOptions = npmExecOptions(modulePath);
		await exec('npm ci', npmOptions);
		await exec('npm run build', {
			...npmOptions,
			env: { ...npmOptions.env, PDFJS_CONFIG: 'zotero' },
		});
		await fs.copy(path.join(modulePath, 'build', 'zotero'), targetDir);
	}
	
	// Pharos: this tree is a detached copy of Zotero with no git metadata, so
	// `git rev-parse HEAD` is unavailable. A content digest of the module's own
	// sources serves the same purpose -- see getModuleDigest for why it must
	// cover src/ and not just the manifests.
	const hash = getModulePin('reader', modulePath);
	
	if (!('reader' in signatures) || signatures['reader'].hash !== hash) {
		const targetDir = path.join(__dirname, '..', 'build', 'resource', 'reader');
		if (shouldBuildModuleFromSource('reader')) {
			await fs.remove(targetDir);
			await fs.ensureDir(targetDir);
			await buildFromSource(targetDir);
		}
		else {
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
				console.error(e);
				await buildFromSource(targetDir);
			}
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
