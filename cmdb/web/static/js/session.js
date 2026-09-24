/* Stale-session guard.

   CMDB sits behind an Authentik forward-auth outpost. When the browser session
   lapses (12h), the outpost answers the request itself with a 302 -- CMDB never
   sees it, so no server-side handler can help. What the user sees is their
   action apparently rejected: a plain form POST loses its body on the 302->GET
   and the login page replaces the app (observed 2026-09-24: "Collect now" ->
   302 -> login), and a mutating htmx request just fails mid-card. Both look
   like the application refusing a valid action rather than an expired login.

   HOW THE LAPSE IS DETECTED -- measured 2026-09-24, and not the obvious way.

   The redirect chain out of a protected path is three hops and leaves the
   origin on the second:

     GET /healthz                        -> 302 (same origin)
     /outpost.goauthentik.io/start?rd=.. -> 302 to auth.example.com
     /application/o/authorize/           -> 302 to /if/flow/... -> 200

   Two consequences kill the tempting signals:

     - The FINAL url is on auth.example.com and contains the outpost path only
       percent-encoded inside ?redirect_uri=, so sniffing xhr.responseURL for
       '/outpost.goauthentik.io/' never matches.
     - auth.example.com sends no Access-Control-Allow-Origin, so a same-origin
       XHR that follows the chain cross-origin is CORS-blocked: onload never
       fires, responseURL is '', and htmx gets a send error rather than login
       HTML it could swap in.

   So the probe uses fetch with `redirect: 'manual'`, which stops at the FIRST
   hop while it is still same-origin. `response.type === 'opaqueredirect'` then
   means "something redirected me", unambiguously and with no CORS involved,
   while a live session is a plain 200. A thrown promise is a real network
   fault and is treated as unknown, never as a lapse.

   This is the one file that reaches past the project's ES5-only house rule, for
   a runtime API rather than syntax: `redirect: 'manual'` has no XMLHttpRequest
   equivalent, and it is the only reliable signal here. The syntax stays ES5
   (var, function expressions, .then callbacks) and the guard simply disables
   itself where fetch is missing.

   /healthz is the probe because it is cheap, DB-free, and deliberately NOT in
   the outpost's skip_path_regex. Never probe /mcp or
   /.well-known/oauth-protected-resource -- those two ARE exempt and would
   answer 200 regardless of the browser session. */
(function () {
  'use strict';

  var OUTPOST = '/outpost.goauthentik.io/';
  var BANNER_ID = 'session-expired-banner';

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  /* Calls back with true only when the session is definitively gone. Anything
     unclear -- a network fault, a non-200, no fetch at all -- reports false, so
     a broken probe can never block a legitimate action. */
  function probeLapsed(done) {
    if (typeof fetch !== 'function') {
      done(false);
      return;
    }
    /* Cache-buster: the app sends no Cache-Control or Vary on any route. */
    fetch('/healthz?_=' + Date.now(), {
      redirect: 'manual',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' }
    }).then(function (res) {
      /* status 0 accompanies an opaqueredirect; check both, since a couple of
         engines report one without labelling the other. */
      done(res.type === 'opaqueredirect' || res.status === 0);
    })['catch'](function () {
      done(false);
    });
  }

  function showBanner() {
    var main = document.querySelector('main');
    if (!main || document.getElementById(BANNER_ID)) return;

    var banner = document.createElement('div');
    banner.id = BANNER_ID;
    banner.className = 'flash-error';
    banner.setAttribute('role', 'alert');
    banner.appendChild(
      document.createTextNode('Session expired — reload to sign in again. ')
    );

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Reload';
    btn.addEventListener('click', function () {
      location.reload();
    });
    banner.appendChild(btn);

    main.insertBefore(banner, main.firstChild);
    banner.scrollIntoView({ block: 'nearest' });
  }

  ready(function () {
    /* Plain POST forms only. The htmx forms carry no `method` attribute at all,
       so they fall through to the htmx hooks below. */
    document.addEventListener('submit', function (event) {
      /* First, and load-bearing. Inline onsubmit/onclick="return confirm(...)"
         handlers (image delete, k8s deletes) are registered at parse time and so
         run before this one. If the user cancelled the confirm the submit is
         already prevented, and we must not resurrect it. */
      if (event.defaultPrevented) return;

      var form = event.target;
      if (!form || !form.getAttribute) return;
      var method = form.getAttribute('method');
      if (!method || method.toLowerCase() !== 'post') return;

      event.preventDefault();
      probeLapsed(function (isLapsed) {
        if (isLapsed) showBanner();
        else form.submit();
      });
    });

    /* Mutating htmx requests (host-detail tags, notes, custom fields).

       The cross-origin block means these surface as a send error, not as a swap
       of login HTML -- so this is where the real handling lives. Re-probe rather
       than assume, so a genuine network blip does not get mislabelled as an
       expired login. */
    function onHtmxFailure() {
      probeLapsed(function (isLapsed) {
        if (isLapsed) showBanner();
      });
    }
    document.addEventListener('htmx:sendError', onHtmxFailure);
    document.addEventListener('htmx:responseError', onHtmxFailure);

    /* Belt and braces: if the login page is ever fronted same-origin (making it
       readable), cancel the swap so it cannot land inside a card. */
    document.addEventListener('htmx:beforeSwap', function (event) {
      var xhr = event.detail && event.detail.xhr;
      if (!xhr || !xhr.responseURL) return;
      if (xhr.responseURL.indexOf(OUTPOST) === -1) return;
      event.detail.shouldSwap = false;
      showBanner();
    });
  });
})();
