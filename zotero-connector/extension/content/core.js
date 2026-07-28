"use strict";

var PharosConnectorCore = (function () {
  const JSON_TYPE = "application/json; charset=utf-8";
  const CREDENTIAL_QUERY_KEYS = new Set(["token", "access_token", "api_key", "authorization"]);

  function json(status, value) {
    return { status, contentType: JSON_TYPE, body: JSON.stringify(value) };
  }

  function normaliseHeaders(headers) {
    const result = Object.create(null);
    if (!headers || typeof headers !== "object") return result;
    for (const [name, value] of Object.entries(headers)) {
      result[String(name).toLowerCase()] = String(value);
    }
    return result;
  }

  function hasQueryCredential(searchParams) {
    if (!searchParams) return false;
    if (typeof searchParams.keys === "function") {
      for (const key of searchParams.keys()) {
        if (CREDENTIAL_QUERY_KEYS.has(String(key).toLowerCase())) return true;
      }
      return false;
    }
    if (typeof searchParams === "string") {
      return hasQueryCredential(new URLSearchParams(searchParams.replace(/^\?/, "")));
    }
    if (typeof searchParams === "object") {
      return Object.keys(searchParams).some((key) =>
        CREDENTIAL_QUERY_KEYS.has(String(key).toLowerCase()),
      );
    }
    return false;
  }

  function constantTimeEqual(left, right) {
    const a = String(left ?? "");
    const b = String(right ?? "");
    const length = Math.max(a.length, b.length);
    let difference = a.length ^ b.length;
    for (let index = 0; index < length; index += 1) {
      difference |= (a.charCodeAt(index) || 0) ^ (b.charCodeAt(index) || 0);
    }
    return difference === 0;
  }

  function bearerToken(headers) {
    const value = normaliseHeaders(headers).authorization;
    if (!value) return null;
    const match = /^Bearer ([A-Za-z0-9_-]{32,})$/.exec(value);
    return match ? match[1] : null;
  }

  function handleRequest(request) {
    const method = String(request?.method ?? "GET").toUpperCase();
    const pathname = String(request?.pathname ?? "");

    if (hasQueryCredential(request?.searchParams)) {
      return json(400, { error: "credentials_must_use_authorization_header" });
    }

    if (pathname === "/pharos/v1/health") {
      if (method !== "GET") return json(405, { error: "method_not_allowed" });
      return json(200, {
        service: "pharos-zotero-connector",
        protocolVersion: 1,
        status: "ready",
      });
    }

    if (pathname === "/pharos/v1/capabilities") {
      if (method !== "GET") return json(405, { error: "method_not_allowed" });
      const supplied = bearerToken(request?.headers);
      if (!supplied || !constantTimeEqual(supplied, request?.token)) {
        return json(401, { error: "unauthorized" });
      }
      return json(200, {
        service: "pharos-zotero-connector",
        protocolVersion: 1,
        provider: "connector",
        zoteroVersion: request?.zoteroVersion ?? null,
        capabilities: {
          metadataRead: false,
          fileRead: false,
          fulltextRead: false,
          metadataWrite: false,
          notesWrite: false,
          annotationsWrite: false,
          realtimeEvents: false,
        },
      });
    }

    return json(404, { error: "not_found" });
  }

  return Object.freeze({
    bearerToken,
    constantTimeEqual,
    handleRequest,
    hasQueryCredential,
    normaliseHeaders,
  });
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = PharosConnectorCore;
}
