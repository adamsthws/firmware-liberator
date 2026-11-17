// /assets/theme.js: the theme button's script (src/shell.mjs), built into a
// file of its own as the site's Content-Security-Policy asks.
import { THEME_SCRIPT } from "../../shell.mjs";

export const GET = () => new Response(THEME_SCRIPT, { headers: { "Content-Type": "text/javascript; charset=utf-8" } });
