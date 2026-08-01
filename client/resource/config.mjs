// Pharos identity. Derived from Zotero (AGPL-3.0) -- see COPYING and UPSTREAM.txt.
//
// Only the *identity* fields below are rebranded. The zotero.org service URLs
// further down are deliberately left alone: CLIENT_NAME drives the default data
// directory and ID drives the database filename, so changing those two is what
// keeps Pharos off the user's real ~/Zotero library. The API/sync/repository
// endpoints must keep pointing at Zotero so that linking a Zotero Web API
// account, and updating bundled translators and styles, still work.
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
	START_URL: "https://www.zotero.org/start",
	QUICK_START_URL: "https://www.zotero.org/support/quick_start_guide",
	PDF_TOOLS_URL: "https://www.zotero.org/download/xpdf/",
	SUPPORT_URL: "https://www.zotero.org/support/",
	SYNC_INFO_URL: "https://www.zotero.org/support/sync",
	TROUBLESHOOTING_URL: "https://www.zotero.org/support/getting_help",
	FEEDBACK_URL: "https://forums.zotero.org/",
	CONNECTORS_URL: "https://www.zotero.org/download/connectors",
	CHANGELOG_URL: "https://www.zotero.org/support/changelog",
	CREDITS_URL: 'https://www.zotero.org/support/credits_and_acknowledgments',
	LICENSING_URL: 'https://www.zotero.org/support/licensing',
	GET_INVOLVED_URL: 'https://www.zotero.org/getinvolved',
	DICTIONARIES_URL: 'https://download.zotero.org/dictionaries/',
	PLUGINS_URL: 'https://www.zotero.org/support/plugins',
	NEW_FEATURES_URL: 'https://www.zotero.org/blog/zotero-{version}/',
	READ_ALOUD_URL: 'https://www.zotero.org/settings/readaloud'
};
