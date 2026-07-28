"use strict";

const PREF_TOKEN = "extensions.zotero.pharos.connector.token";
const ROUTES = ["/pharos/v1/health", "/pharos/v1/capabilities"];

let connectorCore = null;

function randomToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function getOrCreateToken() {
  let token = Services.prefs.getStringPref(PREF_TOKEN, "");
  if (!/^[A-Za-z0-9_-]{32,}$/.test(token)) {
    token = randomToken();
    Services.prefs.setStringPref(PREF_TOKEN, token);
  }
  return token;
}

function endpointFor(pathname) {
  function PharosEndpoint() {}
  PharosEndpoint.prototype = {
    supportedMethods: ["GET"],
    supportedDataTypes: ["application/json"],
    permitBookmarklet: false,
    init(options) {
      const result = connectorCore.handleRequest({
        method: options.method,
        pathname,
        searchParams: options.searchParams,
        headers: options.headers,
        token: getOrCreateToken(),
        zoteroVersion: Zotero.version,
      });
      return [result.status, result.contentType, result.body];
    },
  };
  return PharosEndpoint;
}

async function startup({ rootURI }) {
  await Zotero.initializationPromise;
  const scope = {};
  Services.scriptloader.loadSubScript(`${rootURI}content/core.js`, scope);
  connectorCore = scope.PharosConnectorCore;
  if (!connectorCore) throw new Error("Pharos Connector core failed to load");
  getOrCreateToken();
  for (const route of ROUTES) Zotero.Server.Endpoints[route] = endpointFor(route);
  Zotero.debug("Pharos Connector: secure localhost endpoints registered");
}

function shutdown(_data, reason) {
  if (reason === APP_SHUTDOWN) return;
  for (const route of ROUTES) delete Zotero.Server.Endpoints[route];
  connectorCore = null;
}

function install() {}

function uninstall(_data, reason) {
  if (reason !== ADDON_UNINSTALL) return;
  if (Services.prefs.prefHasUserValue(PREF_TOKEN)) Services.prefs.clearUserPref(PREF_TOKEN);
}
