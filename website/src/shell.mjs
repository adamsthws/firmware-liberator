// The look every page shares: GitHub's Markdown styling, dark unless the
// reader chose light with the button (THEME_SCRIPT, below), the GitHub
// link beside it, and the form controls. The layout (src/layouts/Page.astro)
// puts it round each page the site builds; page() puts it round a page made
// anywhere else, such as the API's email-link pages and the admin view,
// which load the stylesheets from the site. So a change here restyles every
// page, and a new page made outside Astro is made with page(), never with
// HTML of its own (test/shell.test.mjs holds the website to that).
// Nothing from Node here: the API's Worker bundles this file.

export const REPOSITORY = "adamsthws/firmware-liberator";
export const SITE = "https://firmwareliberator.com";

export const STYLE = `
  :root { color-scheme: dark; --bg: #0d1117; --fg: #e6edf3; --line: #30363d; }
  :root[data-theme="light"] { color-scheme: light; --bg: #ffffff; --fg: #1f2328; --line: #d0d7de; }
  body { margin: 0; background: var(--bg); }
  .markdown-body { box-sizing: border-box; max-width: 980px; margin: 0 auto; padding: 45px; }
  @media (max-width: 767px) { .markdown-body { padding: 15px; } }
  #theme, #github { position: fixed; top: 12px; height: 36px; box-sizing: border-box; border: 1px solid var(--line);
                    border-radius: 8px; background: var(--bg); color: var(--fg); font-size: 18px; line-height: 1; }
  #theme { right: 12px; width: 36px; cursor: pointer; }
  #github { right: 56px; padding: 0 10px; display: flex; align-items: center; gap: 6px; font-size: 14px; text-decoration: none; }
  @media print { #theme, #github { display: none; } }
  .markdown-body input, .markdown-body button { font: inherit; padding: 6px 10px; border: 1px solid var(--line);
                                               border-radius: 6px; background: var(--bg); color: var(--fg); }
  .markdown-body input { width: 100%; max-width: 360px; box-sizing: border-box; }
  .markdown-body button { cursor: pointer; }
`;

// The icons, both stylesheets and the theme's script, from `origin` ("" on
// the site itself), the script from `script`. Safari takes the PNG, as it
// ignores SVG icons; the others prefer the SVG, listed last and for any size,
// whose colour follows their theme. The script is loaded after both
// stylesheets, so the reader's choice is on before the page is drawn.
export const head = (origin = "", script = origin) => `
<link rel="icon" href="${origin}/assets/favicon.png" type="image/png" sizes="128x128">
<link rel="icon" href="${origin}/assets/favicon.svg" type="image/svg+xml" sizes="any">
<link rel="apple-touch-icon" href="${origin}/assets/apple-touch-icon.png">
<link rel="stylesheet" href="${origin}/assets/github-markdown-dark.css" id="dark">
<link rel="stylesheet" href="${origin}/assets/github-markdown-light.css" id="light" disabled>
<style>${STYLE}</style>
<script src="${script}/assets/theme.js"></script>`;

// The theme button's script, served as /assets/theme.js by the site
// (src/pages/assets/theme.js.js) and by the admin view, which runs no script
// from elsewhere. Run in each page's head, after both stylesheets, so the
// reader's choice is on before the page is drawn; the button is wired once
// the page is there. A file, not inline, so each Content-Security-Policy can
// allow scripts from a file alone.
// A string, not a function, so each copy is the same to the byte whatever
// bundles it (test/shell.test.mjs runs it).
export const THEME_SCRIPT = `(function () {
  function setTheme(chosen) {
    document.documentElement.dataset.theme = chosen;
    document.getElementById("dark").disabled = chosen === "light";
    document.getElementById("light").disabled = chosen !== "light";
  }

  var start = "dark";
  try { start = localStorage.getItem("theme") || start; } catch (e) {}
  setTheme(start);

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("theme");
    function label() {
      button.textContent = document.documentElement.dataset.theme === "light" ? "\u263E\uFE0E" : "\u2600\uFE0E";
    }
    button.addEventListener("click", function () {
      var chosen = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      setTheme(chosen);
      label();
      try { localStorage.setItem("theme", chosen); } catch (e) {}
    });
    label();
  });
})();
`;

// The GitHub link and the theme button, at the top of the body.
export const header = () => `
<a id="github" href="https://github.com/${REPOSITORY}"><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>GitHub</a>
<button id="theme" type="button" aria-label="Switch between dark and light">&#x263E;&#xFE0E;</button>`;

const escape = (value) => String(value).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// A whole page made outside Astro: `title` as text, `body` as HTML, kept out
// of search results. Its stylesheets come from the site, and its script from
// `script` (the site unless said). `more` goes at the end of the head;
// `heading: false` leaves the title out of the body, for a page with its own.
export const page = (title, body, { more = "", heading = true, script = SITE } = {}) => `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${escape(title)} · FirmwareLiberator</title>${head(SITE, script)}${more}
</head>
<body>${header()}
<article class="markdown-body">
${heading ? `<h1>${escape(title)}</h1>\n` : ""}${body}
</article>
</body>
</html>`;
