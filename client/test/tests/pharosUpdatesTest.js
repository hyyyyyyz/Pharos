describe("Zotero.Pharos.Updates", function () {
	var origRequest = null;
	var requests = [];
	var observers = [];
	var observed = [];
	var origIgnored;

	function captureRequests(responder) {
		requests = [];
		origRequest = Zotero.Pharos.API.request;
		Zotero.Pharos.API.request = function (method, path, options) {
			requests.push({ method, path, options });
			if (typeof responder == 'function') {
				return Promise.resolve().then(() => responder(method, path, options));
			}
			return Promise.resolve(responder === undefined ? null : responder);
		};
	}

	function restoreRequests() {
		if (origRequest) {
			Zotero.Pharos.API.request = origRequest;
			origRequest = null;
		}
	}

	/** Subscribe to a topic with an observer that records what it was told. */
	function watch(topic) {
		let observer = {
			observe: function (subject, t, data) {
				observed.push({ topic: t, data: JSON.parse(data) });
			},
		};
		Services.obs.addObserver(observer, topic, false);
		observers.push({ observer, topic });
		return observer;
	}

	function cleanupObservers() {
		for (let entry of observers) {
			Services.obs.removeObserver(entry.observer, entry.topic);
		}
		observers = [];
		observed = [];
	}

	before(function () {
		origIgnored = Zotero.Prefs.get('pharos.update.ignoredVersion');
	});

	afterEach(function () {
		restoreRequests();
		cleanupObservers();
	});

	after(function () {
		restoreRequests();
		cleanupObservers();
		Zotero.Prefs.set('pharos.update.ignoredVersion', origIgnored || '');
	});

	describe("compareVersions", function () {
		it("should order versions numerically, not lexically", function () {
			assert.equal(Zotero.Pharos.Updates.compareVersions('1.4.0', '1.3.1'), 1);
			assert.equal(Zotero.Pharos.Updates.compareVersions('1.3.1', '1.4.0'), -1);
			assert.equal(Zotero.Pharos.Updates.compareVersions('1.3.1', '1.3.1'), 0);
			// 10 > 9, which lexicographic comparison gets backwards.
			assert.equal(Zotero.Pharos.Updates.compareVersions('1.10.0', '1.9.0'), 1);
		});

		it("should compare different segment counts by padding with zero", function () {
			assert.equal(Zotero.Pharos.Updates.compareVersions('1.4', '1.4.0'), 0);
			assert.equal(Zotero.Pharos.Updates.compareVersions('1.4.1', '1.4'), 1);
		});

		it("should strip the source-build suffix and prerelease labels", function () {
			// A dev build and a release of the same number are the same version.
			assert.equal(
				Zotero.Pharos.Updates.compareVersions('1.3.1.SOURCE', '1.3.1'),
				0
			);
			assert.equal(
				Zotero.Pharos.Updates.compareVersions('1.4.0-beta', '1.3.9'),
				1
			);
		});

		it("should return null for versions with no numeric part", function () {
			assert.equal(Zotero.Pharos.Updates.compareVersions('SOURCE', '1.0.0'), null);
			assert.equal(Zotero.Pharos.Updates.compareVersions('', '1.0.0'), null);
			assert.equal(Zotero.Pharos.Updates.compareVersions('1.0.0', 'latest'), null);
		});
	});

	describe("check", function () {
		it("should ask the public endpoint anonymously", async function () {
			captureRequests({ version: '1.0.0' });
			await Zotero.Pharos.Updates.check();
			assert.lengthOf(requests, 1);
			assert.equal(requests[0].method, 'GET');
			assert.equal(requests[0].path, '/api/updates/desktop/latest');
			assert.isTrue(requests[0].options.anon,
				"a signed-out user must still learn about new builds");
		});

		it("should report available and publish both topics", async function () {
			watch(Zotero.Pharos.Updates.TOPIC_CHECKED);
			watch(Zotero.Pharos.Updates.TOPIC_AVAILABLE);
			captureRequests({ version: '99.0.0', url: 'https://example/rel' });
			let state = await Zotero.Pharos.Updates.check();
			assert.equal(state.status, 'available');
			assert.equal(state.version, '99.0.0');
			assert.equal(state.url, 'https://example/rel');
			assert.equal(Zotero.Pharos.Updates.getState(), state);
			let checked = observed.filter(o => o.topic == Zotero.Pharos.Updates.TOPIC_CHECKED);
			let available = observed.filter(o => o.topic == Zotero.Pharos.Updates.TOPIC_AVAILABLE);
			assert.lengthOf(checked, 1);
			assert.lengthOf(available, 1, "only an unignored update announces itself");
		});

		it("should report latest when nothing newer is advertised", async function () {
			watch(Zotero.Pharos.Updates.TOPIC_AVAILABLE);
			captureRequests({ version: '0.0.1', url: null });
			let state = await Zotero.Pharos.Updates.check();
			assert.equal(state.status, 'latest');
			assert.lengthOf(observed, 0, "no update, no announcement");
		});

		it("should report unavailable for an explicit server no", async function () {
			captureRequests({ version: null, url: null, notes: null });
			let state = await Zotero.Pharos.Updates.check();
			assert.equal(state.status, 'unavailable');
		});

		it("should not advertise a version the user dismissed", async function () {
			watch(Zotero.Pharos.Updates.TOPIC_AVAILABLE);
			Zotero.Prefs.set('pharos.update.ignoredVersion', '99.0.0');
			captureRequests({ version: '99.0.0', url: 'https://example/rel' });
			let state = await Zotero.Pharos.Updates.check();
			assert.equal(state.status, 'ignored');
			assert.lengthOf(observed, 0);
		});

		it("should treat a failed request as an error state, not a throw", async function () {
			captureRequests(() => {
				throw new Error('unreachable');
			});
			let state = await Zotero.Pharos.Updates.check();
			assert.equal(state.status, 'error');
		});

		it("should treat an unparseable version as latest, not an update", async function () {
			captureRequests({ version: 'banana', url: null });
			let state = await Zotero.Pharos.Updates.check();
			// compareVersions returns null for a non-numeric latest, and null
			// must mean "no update" rather than "invent one".
			assert.equal(state.status, 'latest');
		});
	});

	describe("ignore", function () {
		it("should remember the version and move the state to ignored", async function () {
			captureRequests({ version: '99.0.0', url: 'https://example/rel' });
			await Zotero.Pharos.Updates.check();
			Zotero.Pharos.Updates.ignore('99.0.0');
			assert.equal(Zotero.Prefs.get('pharos.update.ignoredVersion'), '99.0.0');
			assert.equal(Zotero.Pharos.Updates.getState().status, 'ignored');
			assert.isTrue(Zotero.Pharos.Updates.isIgnored('99.0.0'));
		});

		it("should clear the marker on demand", async function () {
			Zotero.Prefs.set('pharos.update.ignoredVersion', '99.0.0');
			Zotero.Pharos.Updates.clearIgnored();
			assert.isFalse(Zotero.Pharos.Updates.isIgnored('99.0.0'));
			assert.isFalse(Zotero.Prefs.prefHasUserValue('pharos.update.ignoredVersion'));
		});
	});

	describe("start", function () {
		it("should be idempotent and schedule nothing under tests", async function () {
			// start() is gated on Zotero.test, so a rail opening during another
			// file's stubbed world never fires a real check later. Calling it
			// repeatedly (every window's rail does) must not throw or request.
			captureRequests({ version: '99.0.0', url: 'https://example/rel' });
			Zotero.Pharos.Updates.start();
			Zotero.Pharos.Updates.start();
			await Zotero.Promise.delay(5);
			assert.lengthOf(requests, 0, "no timer may fire a check in tests");
		});
	});
	describe("self-install", function () {
		var origIsMac;
		var origLaunchURL;
		var launched = [];

		before(function () {
			origIsMac = Zotero.isMac;
			origLaunchURL = Zotero.launchURL;
			Zotero.launchURL = function (url) {
				launched.push(url);
			};
		});

		afterEach(function () {
			launched = [];
			Zotero.isMac = origIsMac;
		});

		after(function () {
			Zotero.launchURL = origLaunchURL;
		});

		it("should open the release page where self-install is unavailable", async function () {
			Zotero.isMac = false;
			await Zotero.Pharos.Updates.downloadAndInstall({
				status: 'available',
				version: '9.9.9',
				url: 'https://example.test/releases',
			});
			assert.deepEqual(launched, ['https://example.test/releases'],
				"portable platforms keep the browser handoff");
		});

		it("should refuse to restart without an installed state", function () {
			Zotero.Pharos.Updates.restartAfterInstall();
			// No throw, no quit: there is nothing installed to restart into.
		});
	});
});
