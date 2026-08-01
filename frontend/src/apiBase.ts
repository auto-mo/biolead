/** Where the API lives, relative to wherever this page is served from.
 *
 *  The app runs at `/` in development and at `/biolead/` in production, so an absolute
 *  `/api/...` is wrong in one of those two places. Every call site goes through `API`.
 *
 *  DERIVED AT RUNTIME, NOT AT BUILD TIME. `document.baseURI` is the page's own URL, so the
 *  same bundle works at any mount point and nothing about the deployment is baked in. It
 *  also keeps the bundle audit's no-build-time-environment rule true, which is the rule that
 *  guarantees no build-time value can be inlined into the JavaScript.
 *
 *  /biolead/         -> /biolead/api
 *  /biolead/index.html -> /biolead/api
 *  /                 -> /api
 *
 *  Nginx redirects `/biolead` to `/biolead/`, so the trailing-slash case is the only one
 *  that reaches the browser.
 */
export const API = new URL("./api", document.baseURI).pathname.replace(/\/$/, "");
