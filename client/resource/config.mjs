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
// SHARING THE LIBRARY WITH ZOTERO
//
// Pharos and Zotero are meant to open the SAME library -- not at once, but
// either one reading and writing the same papers and collections, the way
// Vibero does. Two values decide that, and they are deliberately NOT the two
// that decide identity:
//
//   DATA_DIR_NAME  the default data directory under the home folder
//   DB_NAME        the SQLite file inside it
//
// They used to be CLIENT_NAME and ID, which meant that sharing a library and
// being a distinct application were the same switch. They are not. ID is also
// the OS-level URL scheme (ZoteroProtocolHandler.mjs) and part of the profile
// identity, so pointing the database at Zotero's by changing ID would have
// registered Pharos for `zotero://` links as well and had the two applications
// fight over them.
//
// THE PRECONDITION, and it is not optional: this only works while the client's
// userdata schema matches the Zotero release the user runs. Zotero migrates any
// database older than itself, so a client built from a newer branch silently
// upgrades the shared library and the user's real Zotero can then never open it
// again. See client/UPSTREAM.txt and docs/CLIENT_DATA_ARCHITECTURE.md. If the
// baseline is ever moved forward, set these two back to 'Pharos'/'pharos' until
// the schema matches again.
export var ZOTERO_CONFIG = {
	GUID: 'pharos@pharos.selab.top',
	ID: 'pharos', // identity: URL scheme, profile, extension id -- NOT the db
	CLIENT_NAME: 'Pharos',
	//: The shared library. Both of these name Zotero's own, on purpose.
	DATA_DIR_NAME: 'Zotero',
	DB_NAME: 'zotero',
	DOMAIN_NAME: 'pharos.selab.top',
	//: The domain of the SYNC AND PUBLISHING service, which is Zotero's.
	//
	// Not the same thing as DOMAIN_NAME, and conflating them told users the
	// wrong thing in three places at once: the sync pane offered to "sync with
	// pharos.selab.top", a missing attachment blamed a file sync that had not
	// yet reached pharos.selab.top, and My Publications pointed there too. None
	// of those is ours. Library sync, file storage and publications all run on
	// Zotero's servers -- API_URL and WWW_BASE_URL below deliberately still
	// point at them, because that is what lets a Pharos user keep using their
	// Zotero account. Sending them to look at our domain for a file that lives
	// on Zotero's is not a cosmetic slip; it is an instruction that cannot work.
	SERVICE_DOMAIN_NAME: 'zotero.org',
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
