// Pharos identity. Derived from Zotero (AGPL-3.0) -- see COPYING and UPSTREAM.txt.
//
// Two kinds of URL live in here and they are rebranded on opposite rules:
//
//   * Anything a *user* is sent to -- support, feedback, changelog, credits --
//     must point at Pharos. Left at zotero.org, they route our bug reports to
//     Zotero's volunteer forums, which is both useless to the reporter and rude
//     to Zotero.
//   * Anything a *machine* talks to -- API_URL, STREAMING_URL, SERVICES_URL,
//     BASE_URI, REPOSITORY_URL -- must keep pointing at Zotero. Those are
//     Zotero's real services, and they are what makes linking a Zotero Web API
//     account and updating the bundled translators and citation styles work.
//     Repointing them at a host that does not implement the Zotero API breaks
//     sync silently.
//
// CLIENT_NAME drives the default data directory and ID drives the database
// filename, so those two are what keep Pharos off the user's real ~/Zotero
// library. ID is also the OS-level URL scheme -- see ZoteroProtocolHandler.mjs.
export var ZOTERO_CONFIG = {
	GUID: 'pharos@pharos.selab.top',
	ID: 'pharos', // used for db filename, etc.
	CLIENT_NAME: 'Pharos',
	DOMAIN_NAME: 'pharos.selab.top',
	PRODUCER: 'Pharos',
	PRODUCER_URL: 'https://pharos.selab.top',
	REPOSITORY_URL: 'https://repo.zotero.org/repo/',
	BASE_URI: 'http://zotero.org/',
	WWW_BASE_URL: 'https://www.zotero.org/',
	PROXY_AUTH_URL: 'https://zoteroproxycheck.s3.amazonaws.com/test',
	API_URL: 'https://api.zotero.org/',
	STREAMING_URL: 'wss://stream.zotero.org/',
	SERVICES_URL: 'https://services.zotero.org/',
	API_VERSION: 3,
	CONNECTOR_MIN_VERSION: '5.0.39', // show upgrade prompt for requests from below this version
	PREF_BRANCH: 'extensions.zotero.',
	BOOKMARKLET_ORIGIN: 'https://www.zotero.org',
	BOOKMARKLET_URL: 'https://www.zotero.org/bookmarklet/',
	START_URL: "https://pharos.selab.top/",
	QUICK_START_URL: "https://github.com/hyyyyyyz/Pharos#run-the-desktop-client",
	PDF_TOOLS_URL: "https://www.zotero.org/download/xpdf/",
	SUPPORT_URL: "https://github.com/hyyyyyyz/Pharos#readme",
	SYNC_INFO_URL: "https://www.zotero.org/support/sync",
	TROUBLESHOOTING_URL: "https://github.com/hyyyyyyz/Pharos/issues",
	FEEDBACK_URL: "https://github.com/hyyyyyyz/Pharos/issues/new",
	CONNECTORS_URL: "https://www.zotero.org/download/connectors",
	CHANGELOG_URL: "https://github.com/hyyyyyyz/Pharos/releases",
	// No fragment on purpose: about.xhtml appends '#third-party_software' to this
	// value, and a second '#' would swallow the anchor into the first fragment.
	CREDITS_URL: 'https://github.com/hyyyyyyz/Pharos/blob/main/README.md',
	LICENSING_URL: 'https://github.com/hyyyyyyz/Pharos/blob/main/LICENSE',
	GET_INVOLVED_URL: 'https://github.com/hyyyyyyz/Pharos/blob/main/CONTRIBUTING.md',
	DICTIONARIES_URL: 'https://download.zotero.org/dictionaries/',
	// No plugin directory of our own yet, so this lands on the README rather than
	// on zotero.org -- sending people there to shop for plugins implies a
	// compatibility promise Pharos has not tested and cannot make.
	PLUGINS_URL: 'https://github.com/hyyyyyyz/Pharos#readme',
	// Upstream had a per-major blog post; we have release notes. No {version}
	// placeholder here on purpose -- Pharos tags are 0.x.y, so a URL built from
	// the major version alone would 404. zoteroPane.js's replace() on the
	// missing placeholder is a harmless no-op.
	NEW_FEATURES_URL: 'https://github.com/hyyyyyyz/Pharos/releases',
};
