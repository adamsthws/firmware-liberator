// scripts/admin-preview.mjs: the admin view's own page, from made-up figures,
// the theme's script it loads, and nothing else.
import assert from "node:assert/strict";
import { test } from "node:test";
import { previewer } from "../scripts/admin-preview.mjs";

const answer = await previewer(new Date("2026-12-01T09:00:00Z"));

test("GET / is the admin view's page, filled from the sample", async () => {
  const { status, body } = await answer("GET", "/");
  assert.equal(status, 200);
  assert.match(body, /^<!doctype html>/);
  assert.match(body, /sonoff-s60/);
});

test("the theme's script the page loads is answered, as the view answers it", async () => {
  const { status, type, body } = await answer("GET", "/assets/theme.js");
  assert.deepEqual([status, type], [200, "text/javascript"]);
  assert.match(body, /localStorage/);
});

test("nothing else is answered: the preview changes nothing", async () => {
  for (const [method, pathname] of [["POST", "/held"], ["POST", "/settings"], ["POST", "/approve"], ["GET", "/v1/emulator"]]) {
    assert.equal((await answer(method, pathname)).status, 404, `${method} ${pathname}`);
  }
});
