"use strict";

const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const sandbox = { module: { exports: {} }, URLSearchParams };
vm.runInNewContext(
  readFileSync(path.join(root, "extension", "content", "core.js"), "utf8"),
  sandbox,
);
const core = sandbox.module.exports;
const token = "abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNO";

const request = (overrides = {}) =>
  core.handleRequest({
    method: "GET",
    pathname: "/pharos/v1/capabilities",
    headers: {},
    searchParams: new URLSearchParams(),
    token,
    zoteroVersion: "8.0",
    ...overrides,
  });

test("health is the only public route", () => {
  const response = request({ pathname: "/pharos/v1/health" });
  assert.equal(response.status, 200);
  assert.equal(JSON.parse(response.body).status, "ready");
  assert.equal(request({ pathname: "/unknown" }).status, 404);
});

test("capabilities requires an exact bearer token", () => {
  assert.equal(request().status, 401);
  assert.equal(request({ headers: { Authorization: `Bearer ${token}x` } }).status, 401);
  assert.equal(request({ headers: { Authorization: `Basic ${token}` } }).status, 401);
  const response = request({ headers: { Authorization: `Bearer ${token}` } });
  assert.equal(response.status, 200);
  assert.equal(JSON.parse(response.body).capabilities.metadataWrite, false);
});

test("credentials in query strings are rejected even when correct", () => {
  assert.equal(
    request({
      headers: { Authorization: `Bearer ${token}` },
      searchParams: new URLSearchParams({ token }),
    }).status,
    400,
  );
});

test("unsupported methods fail closed in the pure router", () => {
  assert.equal(request({ method: "POST", headers: { Authorization: `Bearer ${token}` } }).status, 405);
  assert.equal(request({ method: "OPTIONS", headers: { Authorization: `Bearer ${token}` } }).status, 405);
});

test("responses do not expose a token or CORS metadata", () => {
  const response = request({ headers: { Authorization: `Bearer ${token}` } });
  assert.equal(response.body.includes(token), false);
  assert.equal(Object.hasOwn(response, "headers"), false);
  assert.equal(response.contentType, "application/json; charset=utf-8");
});

test("the XPI build is reproducible", () => {
  const script = path.join(root, "scripts", "build.mjs");
  execFileSync(process.execPath, [script], { cwd: root });
  const artifact = path.join(root, "dist", "pharos-zotero-connector-0.1.0.xpi");
  const first = createHash("sha256").update(readFileSync(artifact)).digest("hex");
  execFileSync(process.execPath, [script], { cwd: root });
  const second = createHash("sha256").update(readFileSync(artifact)).digest("hex");
  assert.equal(first, second);
});
