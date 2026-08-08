describe("ZoteroPluginInstaller", function () {
	const PREF_BRANCH = "extensions.zoteroTestWordProcessorIntegration.";
	const VERSION_URL = "resource://zotero-test-word-processor/version.txt";

	let ZoteroPluginInstaller;
	let originalUIReadyPromise;

	function resetPrefs() {
		let branch = Services.prefs.getBranch(PREF_BRANCH);
		branch.setCharPref("version", "0.9");
		branch.setCharPref("lastAttemptedVersion", "0.9");
		branch.setBoolPref("skipInstallation", false);
	}

	function makeAddon(install) {
		return {
			EXTENSION_STRING: "Test Word Processor Integration",
			EXTENSION_PREF_BRANCH: PREF_BRANCH,
			EXTENSION_DIR: "test-word-processor-integration",
			APP: "Test Writer",
			VERSION_FILE: VERSION_URL,
			LAST_INSTALLED_FILE_UPDATE: "1.0",
			DISABLE_PROGRESS_WINDOW: true,
			install,
		};
	}

	before(function () {
		({ ZoteroPluginInstaller } = ChromeUtils.importESModule(
			"resource://zotero/word-processor-plugin-installer.mjs"
		));
	});

	beforeEach(function () {
		originalUIReadyPromise = Zotero.uiReadyPromise;
		resetPrefs();
		sinon.stub(Zotero, "getActiveZoteroPane").returns(null);
		sinon.stub(Zotero.File, "getContentsFromURLAsync").callsFake(async function (url) {
			assert.equal(url, VERSION_URL);
			return "1.0";
		});
	});

	afterEach(function () {
		Zotero.uiReadyPromise = originalUIReadyPromise;
		Zotero.getActiveZoteroPane.restore();
		Zotero.File.getContentsFromURLAsync.restore();
		Services.prefs.getBranch(PREF_BRANCH).deleteBranch("");
	});

	it("should wait for the main interface before installing automatically", async function () {
		let uiReady = Zotero.Promise.defer();
		Zotero.uiReadyPromise = uiReady.promise;
		let install = sinon.stub().resolves();

		let installer = new ZoteroPluginInstaller(makeAddon(install), true, false);
		await Zotero.Promise.delay(0);

		assert.isFalse(Zotero.File.getContentsFromURLAsync.called);
		assert.isFalse(install.called);
		assert.equal(
			Services.prefs.getBranch(PREF_BRANCH).getCharPref("lastAttemptedVersion"),
			"0.9"
		);

		uiReady.resolve();
		await installer._initPromise;

		assert.isTrue(Zotero.File.getContentsFromURLAsync.calledOnce);
		assert.isTrue(install.calledOnce);
	});

	it("should preserve the normal startup path when a main pane already exists", async function () {
		let uiReady = Zotero.Promise.defer();
		Zotero.uiReadyPromise = uiReady.promise;
		Zotero.getActiveZoteroPane.returns({});
		let install = sinon.stub().resolves();

		let installer = new ZoteroPluginInstaller(makeAddon(install), true, false);
		await installer._initPromise;

		assert.isTrue(Zotero.File.getContentsFromURLAsync.calledOnce);
		assert.isTrue(install.calledOnce);
	});

	it("should run a manual installation while the main interface wait is pending", async function () {
		let uiReady = Zotero.Promise.defer();
		Zotero.uiReadyPromise = uiReady.promise;
		let install = sinon.stub().resolves();

		let installer = new ZoteroPluginInstaller(makeAddon(install), false, true);
		await installer._initPromise;

		assert.isTrue(Zotero.File.getContentsFromURLAsync.calledOnce);
		assert.isTrue(install.calledOnce);
	});
});
