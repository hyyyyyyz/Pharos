'use strict';

const fs = require('fs-extra');
const path = require('path');
const util = require('util');
const exec = util.promisify(require('child_process').exec);
const { getSignatures, writeSignatures, onSuccess, onError, getModuleDigest, npmExecOptions } = require('./utils');
const { buildsURL } = require('./config');

async function getZoteroNoteEditor(signatures) {
	const t1 = Date.now();

	const modulePath = path.join(__dirname, '..', 'note-editor');

	// Pharos: this tree is a detached copy of Zotero with no git metadata, so
	// `git rev-parse HEAD` is unavailable. A content digest of the module's own
	// sources serves the same purpose -- see getModuleDigest for why it must
	// cover src/ and not just the manifests.
	const hash = getModuleDigest(modulePath);

	if (!('note-editor' in signatures) || signatures['note-editor'].hash !== hash) {
		const targetDir = path.join(__dirname, '..', 'build', 'resource', 'note-editor');
		try {
			const filename = hash + '.zip';
			const tmpDir = path.join(__dirname, '..', 'tmp', 'builds', 'note-editor');
			const url = buildsURL + 'note-editor/' + filename;

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
			await exec('npm ci', npmExecOptions(modulePath));
			// Pharos: only the `zotero` target is consumed here. The default
			// `build` script also builds the standalone `web` bundle, which is
			// unused by the app and whose failure would abort an otherwise good
			// build.
			await exec('npm run build:zotero', npmExecOptions(modulePath));
			await fs.copy(path.join(modulePath, 'build', 'zotero'), targetDir);
		}
		signatures['note-editor'] = { hash };
	}
	
	const t2 = Date.now();

	return {
		action: 'note-editor',
		count: 1,
		totalCount: 1,
		processingTime: t2 - t1
	};
}

module.exports = getZoteroNoteEditor;

if (require.main === module) {
	(async () => {
		try {
			const signatures = await getSignatures();
			onSuccess(await getZoteroNoteEditor(signatures));
			await writeSignatures(signatures);
		}
		catch (err) {
			process.exitCode = 1;
			global.isError = true;
			onError(err);
		}
	})();
}
