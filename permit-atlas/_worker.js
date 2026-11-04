// Cloudflare Pages advanced mode (_worker.js): runs on every request, even for
// dashboard "Upload assets" (Direct Upload) deployments, which do NOT compile a
// functions/ directory. Gates the site behind HTTP Basic Auth, then serves the
// static files via the ASSETS binding.
//
// Opt-in: auth is enforced only when BOTH secrets are set on the Pages project
// (Settings -> Variables and Secrets -> AUTH_USER, AUTH_PASS). With them unset the
// site serves normally, so you can instead protect it with Cloudflare Access.
export default {
  async fetch(request, env) {
    if (env.AUTH_USER && env.AUTH_PASS) {
      const expected = "Basic " + btoa(`${env.AUTH_USER}:${env.AUTH_PASS}`);
      const got = request.headers.get("Authorization") || "";
      if (got.length !== expected.length || got !== expected) {
        return new Response("Authentication required.", {
          status: 401,
          headers: { "WWW-Authenticate": 'Basic realm="Permit Atlas", charset="UTF-8"' },
        });
      }
    }
    return env.ASSETS.fetch(request);
  },
};
