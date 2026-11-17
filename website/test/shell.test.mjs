// What src/shell.mjs gives every page, and that every page takes it.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { SITE, STYLE, THEME_SCRIPT, page } from "../src/shell.mjs";

const HERE = new URL("../", import.meta.url);
const files = execFileSync("git", ["ls-files", "-z"], { cwd: HERE, encoding: "utf-8" }).split("\0").filter(Boolean);
const read = (path) => readFileSync(new URL(path, HERE), "utf-8");

test("every page Astro builds is put in the layout", () => {
  for (const path of files.filter((path) => /^src\/pages\/.*\.astro$/.test(path))) {
    assert.match(read(path), /import Page from "(\.\.\/)+layouts\/Page\.astro"/, path);
  }
});

test("no page is written anywhere else but the layout and page(), so none can miss the look", () => {
  const writers = files.filter((path) => !path.startsWith("test/") && /\.(m?js|astro|html)$/.test(path) && /<!doctype html/i.test(read(path)));
  assert.deepEqual(writers.sort(), ["src/layouts/Page.astro", "src/shell.mjs"]);
});

test("a page made outside Astro has the site's stylesheets, script, button and style", () => {
  const html = page("Confirmed", "<p>Done.</p>");
  for (const file of ["github-markdown-dark.css", "github-markdown-light.css", "theme.js", "favicon.svg"]) {
    assert.ok(html.includes(`"${SITE}/assets/${file}"`), file);
  }
  assert.match(html, /<button id="theme" type="button"/);
  assert.match(html, /<a id="github" href="https:\/\/github\.com\//);
  assert.ok(html.includes(STYLE), "the same style as the layout's");
  assert.match(html, /<article class="markdown-body">\n<h1>Confirmed<\/h1>\n<p>Done\.<\/p>/);
  assert.match(html, /<meta name="robots" content="noindex">/);
});

test("a page's title is text, never markup", () => {
  const html = page("<b>Hi</b>", "");
  assert.ok(!html.includes("<b>"));
  assert.match(html, /<h1>&lt;b&gt;Hi&lt;\/b&gt;<\/h1>/);
});

test("a page with a heading of its own, more in its head, and the theme's script from its own origin", () => {
  const html = page("Admin", "<h1>Admin</h1>", { heading: false, script: "", more: "<style>.x{}</style>" });
  assert.equal(html.match(/<h1>/g).length, 1);
  assert.match(html, /<script src="\/assets\/theme\.js"><\/script><style>\.x\{\}<\/style>\n<\/head>/);
  assert.ok(html.includes(`"${SITE}/assets/github-markdown-dark.css"`), "the stylesheets still from the site");
});

test("the theme's script is one a browser runs, starting dark", () => {
  const element = () => ({ disabled: false });
  const sheets = { dark: element(), light: element() };
  const root = { dataset: {} };
  new Function("document", "localStorage", THEME_SCRIPT)(
    { documentElement: root, getElementById: (id) => sheets[id], addEventListener: () => {} },
    { getItem: () => null });
  assert.equal(root.dataset.theme, "dark");
  assert.deepEqual([sheets.dark.disabled, sheets.light.disabled], [false, true]);
});
