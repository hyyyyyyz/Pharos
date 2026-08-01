// Pharos registers pharos:// at the OS level and leaves zotero:// to an installed Zotero, so the
// internal handler has to answer to both. Every assertion below comes in a pair: the alias must
// work, and the canonical scheme must keep working exactly as it did.

var { ZoteroProtocolHandler } = ChromeUtils.importESModule(
	"chrome://zotero/content/ZoteroProtocolHandler.mjs"
);

function getHandler(scheme) {
	return Services.io.getProtocolHandler(scheme).wrappedJSObject;
}

function dispatch(url) {
	var uri = Services.io.newURI(url, null, null);
	return getHandler(uri.scheme).newChannel(uri, null);
}

describe("Pharos protocol handler", function () {
	var win;
	var zp;

	before(async function () {
		win = await loadZoteroPane();
		zp = win.ZoteroPane;
	});

	after(function () {
		win.close();
	});

	describe("registration", function () {
		it("should register both schemes", function () {
			assert.include(ZoteroProtocolHandler.schemes, 'zotero');
			assert.include(ZoteroProtocolHandler.schemes, ZOTERO_CONFIG.ID);
		});

		it("should report its own scheme from each registration", function () {
			// Necko matches nsIProtocolHandler.scheme against the name the handler was registered
			// under, so a shared instance reporting "zotero" for both would be rejected for the
			// alias.
			assert.equal(getHandler('zotero').scheme, 'zotero');
			assert.equal(getHandler(ZOTERO_CONFIG.ID).scheme, ZOTERO_CONFIG.ID);
		});

		it("should survive being initialized twice", function () {
			// Zotero.reinit() only unregisters the canonical scheme, so init() has to clear the
			// aliases itself -- re-registering a live scheme throws and would abort Zotero.init().
			assert.doesNotThrow(() => ZoteroProtocolHandler.init());
			assert.equal(getHandler(ZOTERO_CONFIG.ID).scheme, ZOTERO_CONFIG.ID);
			assert.equal(getHandler('zotero').scheme, 'zotero');
		});
	});

	describe("#canonicalizeSpec()", function () {
		it("should rewrite the alias scheme onto zotero://", function () {
			assert.equal(
				ZoteroProtocolHandler.canonicalizeSpec(`${ZOTERO_CONFIG.ID}://select/library/items/ABCD2345`),
				'zotero://select/library/items/ABCD2345'
			);
		});

		it("should leave zotero:// untouched", function () {
			var spec = 'zotero://select/library/items/ABCD2345';
			assert.equal(ZoteroProtocolHandler.canonicalizeSpec(spec), spec);
		});

		it("should leave other schemes untouched", function () {
			// Substring surgery on the scheme would corrupt anything that merely starts with the
			// same letters
			var spec = 'https://pharos.selab.top/select';
			assert.equal(ZoteroProtocolHandler.canonicalizeSpec(spec), spec);
		});

		it("should accept an nsIURI as well as a string", function () {
			var uri = Services.io.newURI(`${ZOTERO_CONFIG.ID}://select/library/items/ABCD2345`, null, null);
			assert.equal(
				ZoteroProtocolHandler.canonicalizeSpec(uri),
				'zotero://select/library/items/ABCD2345'
			);
		});

		it("should preserve the query string", function () {
			assert.equal(
				ZoteroProtocolHandler.canonicalizeSpec(`${ZOTERO_CONFIG.ID}://select/library/items?itemKey=ABCD2345`),
				'zotero://select/library/items?itemKey=ABCD2345'
			);
		});
	});

	describe("#getExtension()", function () {
		it("should resolve both schemes to the same extension", function () {
			// _extensions is keyed on zotero://, so an unnormalized alias URI silently matches
			// nothing and yields a cancelled channel rather than an error
			var handler = getHandler(ZOTERO_CONFIG.ID);
			for (let host of ['attachment', 'data', 'report', 'select', 'debug', 'open-pdf']) {
				assert.strictEqual(
					handler.getExtension(`${ZOTERO_CONFIG.ID}://${host}/`),
					handler.getExtension(`zotero://${host}/`),
					host
				);
			}
		});

		it("should return false for an unknown host in either scheme", function () {
			var handler = getHandler(ZOTERO_CONFIG.ID);
			assert.isFalse(handler.getExtension(`${ZOTERO_CONFIG.ID}://nonexistent/`));
			assert.isFalse(handler.getExtension('zotero://nonexistent/'));
		});
	});

	describe("#newChannel()", function () {
		it("should hand the extension a canonical zotero:// URI", function () {
			// DataExtension re-parses the literal "zotero://" prefix back out of uri.spec, so an
			// alias URI reaching it unrewritten throws on a null match
			var channel = dispatch(`${ZOTERO_CONFIG.ID}://data/library/items/top?format=json`);
			assert.equal(channel.URI.spec, 'zotero://data/library/items/top?format=json');
		});

		it("should leave a zotero:// URI alone", function () {
			var channel = dispatch('zotero://data/library/items/top?format=json');
			assert.equal(channel.URI.spec, 'zotero://data/library/items/top?format=json');
		});

		it("should return a cancelled channel for an unknown host", function () {
			for (let scheme of ZoteroProtocolHandler.schemes) {
				let channel = dispatch(`${scheme}://nonexistent/`);
				assert.equal(channel.status, Cr.NS_BINDING_ABORTED, scheme);
			}
		});
	});

	describe("select", function () {
		async function waitForItemSelect(items) {
			if (items instanceof Zotero.Item) {
				items = [items];
			}
			while (true) {
				let selected = zp.getSelectedItems();
				if (selected.length && selected.every(item => items.includes(item))) {
					return;
				}
				await Zotero.Promise.delay(20);
			}
		}

		it("should select an item from an alias-scheme URI", async function () {
			var item = await createDataObject('item', { title: 'Pharos scheme' });
			await createDataObject('item', { title: 'Other' });
			dispatch(`${ZOTERO_CONFIG.ID}://select/library/items/${item.key}`);
			await waitForItemSelect(item);
		});

		it("should still select an item from a zotero:// URI", async function () {
			var item = await createDataObject('item', { title: 'Zotero scheme' });
			await createDataObject('item', { title: 'Other' });
			dispatch(`zotero://select/library/items/${item.key}`);
			await waitForItemSelect(item);
		});
	});
});


describe("Pharos user-facing URLs", function () {
	// Left at zotero.org, these route our bug reports and support questions to Zotero's volunteer
	// forums. Pinned here because the failure is invisible from inside the app -- the link opens
	// and looks fine.
	var USER_FACING = [
		'START_URL',
		'QUICK_START_URL',
		'SUPPORT_URL',
		'TROUBLESHOOTING_URL',
		'FEEDBACK_URL',
		'CHANGELOG_URL',
		'CREDITS_URL',
		'LICENSING_URL',
		'GET_INVOLVED_URL',
		'PLUGINS_URL',
		'NEW_FEATURES_URL',
	];

	// Zotero's actual services. Repointing any of these at a host that does not speak the Zotero
	// API breaks account sync, or bundled translator and citation style updates, silently.
	var ZOTERO_SERVICES = [
		'API_URL',
		'STREAMING_URL',
		'SERVICES_URL',
		'BASE_URI',
		'REPOSITORY_URL',
	];

	it("should point user-facing links at Pharos", function () {
		for (let key of USER_FACING) {
			assert.match(
				ZOTERO_CONFIG[key],
				/^https:\/\/(pharos\.selab\.top|github\.com\/hyyyyyyz\/Pharos)/,
				key
			);
		}
	});

	it("should keep Zotero's service endpoints", function () {
		for (let key of ZOTERO_SERVICES) {
			assert.include(ZOTERO_CONFIG[key], 'zotero.org', key);
		}
	});
});
