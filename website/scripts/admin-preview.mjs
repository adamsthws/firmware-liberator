// The admin view's page (admin/index.js) from made-up figures, for seeing it
// before a deploy: sample-stats.mjs's database, read by the view's own code.
// Access is left out, and with it every change the page offers (its forms
// answer 404 here), and Cloudflare's analytics, which need a token. Run by
// preview.sh; never deployed.
//   node website/scripts/admin-preview.mjs ADDRESS PORT
import http from "node:http";
import { page } from "../admin/index.js";
import { THEME_SCRIPT } from "../src/shell.mjs";
import { sampleDatabase } from "./sample-stats.mjs";

// What the preview answers a request: the page for GET /, and the theme's
// script it loads, as the view serves it; else 404.
export async function previewer(now = new Date()) {
  const { db } = await sampleDatabase(now);
  return async (method, pathname) => {
    if (method === "GET" && pathname === "/") {
      return { status: 200, type: "text/html", body: await page({ DB: db }, { email: "owner@example.com" }, now) };
    }
    if (method === "GET" && pathname === "/assets/theme.js") {
      return { status: 200, type: "text/javascript", body: THEME_SCRIPT };
    }
    return { status: 404, type: "text/plain", body: "Not in the preview: it shows the page, and changes nothing." };
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [address, port] = process.argv.slice(2);
  const answer = await previewer();
  http.createServer(async (request, response) => {
    const { status, type, body } = await answer(request.method, new URL(request.url, "http://preview").pathname);
    response.writeHead(status, { "Content-Type": `${type}; charset=utf-8` });
    response.end(body);
  }).listen(Number(port), address, () => console.log(`Admin view on ${address}:${port}`));
}
