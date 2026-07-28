# Pharos Connector for Zotero

This directory contains the Zotero 7/8 extension that will become Pharos's
realtime and safe-write provider. The current `0.1.0` package intentionally
implements only a hardened transport bootstrap. Complete read access continues
to use Pharos's Local API provider until each Connector capability has its own
transaction and notifier tests.

## Current endpoints

The extension registers two paths on Zotero's existing loopback server
(`127.0.0.1:23119`):

| Endpoint | Authentication | Purpose |
| --- | --- | --- |
| `GET /pharos/v1/health` | Public | Detect that the extension is installed |
| `GET /pharos/v1/capabilities` | Bearer token | Negotiate protocol and enabled capabilities |

The capability response reports every data and write capability as `false` in
this release. Pharos must therefore keep using the Local API provider instead
of silently routing data through an incomplete Connector.

## Security model

- A 256-bit random token is generated on first startup and stored in the Zotero
  preference `extensions.zotero.pharos.connector.token`.
- Credentials are accepted only as `Authorization: Bearer ...`; credential-like
  query parameters are rejected.
- Endpoint constructors set `permitBookmarklet: false` and never emit CORS
  headers or include the token in a response or log.
- Zotero's server answers `OPTIONS` itself before endpoint dispatch. It does not
  add `Access-Control-Allow-Origin`, so browser JavaScript cannot read protected
  responses. The pure router still fails closed for unsupported methods.
- The extension never reads or writes `zotero.sqlite` directly.

The pairing UI and operating-system credential-store handoff will be added
before any protected data route is enabled. Do not distribute the preference
token manually as an end-user workflow.

## Build and test

Node.js 20 or newer is sufficient; there are no npm dependencies.

```bash
npm test
npm run build
```

The deterministic builder writes
`dist/pharos-zotero-connector-0.1.0.xpi`. It sorts every input path, stores files
without platform-specific metadata, and uses a fixed timestamp, so identical
sources produce the same SHA-256 digest.

For development, install the XPI from Zotero's Add-ons Manager. The extension
supports Zotero 7 and 8 and removes its endpoint registrations immediately when
disabled or upgraded.
