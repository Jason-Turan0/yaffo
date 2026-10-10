# OAuth App Registrations

Phase 0 of [Automation Sync Adapters](automation-sync-adapters.md#phase-0-lead-time-items-start-now-in-parallel).
The OneDrive and Google Photos presets sign in with Yaffo's own registered
apps, so users never create one. Both registrations are **desktop (public)
clients**: the browser sign-in uses PKCE and a loopback return address
(`http://localhost:<port>` or `http://127.0.0.1:<port>`), and no secret that
matters ships in the app.

**The return address has no path.** Both providers ignore the port on a
loopback address, but Microsoft still matches the path against the
registered URI. With `http://localhost` registered,
`http://localhost:<port>/oauth/callback` is rejected (`invalid_request` on
`redirect_uri`). So the loopback listener answers on `/`, and Phase 2's
sign-in must do the same.

Google's verification for the Photos scopes can take weeks, which is why this
runs in parallel with Phase 1.

## Acceptance criteria

Phase 0 is done when all of these hold. **Status 2026-10-09: all met.**

1. **Microsoft:** `python -m scripts.oauth_probe microsoft --client-id <id>`
   prints `PASS` when signed in with a personal Microsoft account.
2. **Google:** `python -m scripts.oauth_probe google --client-id <id> --client-secret <secret>`
   prints `PASS` when signed in with a test-user Google account.
3. **Ready for Google verification:**
   - the branding is complete (home page, privacy policy, authorized
     domain);
   - `yaffo.app` is verified in Search Console;
   - the privacy policy is live.
   
   *Publishing and submitting are deferred to Phase 3* (decided 2026-10-09).
   Google's reviewer needs a demo video of the scopes in use, and the Google
   Photos target that uses them doesn't exist until Phase 3. Publishing
   early would also start the 100-user lifetime cap for no benefit. The
   one sensitive scope (`appendonly`) typically clears in days once a video
   is in. It's the restricted scopes that take weeks.
4. The client IDs (and Google's desktop client secret) are recorded where
   Phase 2 reads them (see [Where the IDs live](#where-the-ids-live)).

The probe (`scripts/oauth_probe.py`) does what the presets will do:
1. Opens the provider's sign-in page in the browser with PKCE (S256).
2. Waits on a loopback port for the authorization code.
3. Exchanges the code for tokens.
4. Makes one API read (`/me/drive/root/children` on Microsoft Graph,
   `/v1/albums` on the Photos Library API).
5. Exchanges the refresh token once, to prove offline access works.

It never prints or stores tokens. It's covered by
`tests/scripts/test_oauth_probe.py`, which uses a fake browser and token
endpoint.

## Microsoft Entra app (OneDrive)

**Prerequisite: an Entra tenant.** A personal Microsoft account signed in to
the Entra admin center lands in the "Microsoft Services" tenant, where it
can't register apps. Personal accounts no longer get a default directory
automatically. Create one by signing up for a free Azure account (or the
Microsoft 365 Developer Program). The registration lives in that tenant, but
it accepts personal accounts from anywhere.

Steps in the [Entra admin center](https://entra.microsoft.com) →
*App registrations* → *New registration*:

| Setting | Value | Why |
|---|---|---|
| Name | `Yaffo` | Shown on the consent screen |
| Supported account types | **Accounts in any organizational directory and personal Microsoft accounts** (the console now calls it *Any Entra ID Tenant + Personal Microsoft accounts*) | Covers OneDrive personal and work accounts. It **can't be changed later** without re-registering. |
| Redirect URI platform | **Mobile and desktop applications** | Makes it a public client. Don't use *Web*, which requires a secret. |
| Redirect URI | `http://localhost` | Microsoft accepts any port on `localhost` for this platform, so the loopback server can take a free port |

Then:
- **Authentication → Advanced settings → Allow public client flows: Yes.**
  This is needed for the device-code fallback ("Signing in on another
  device?").
- **API permissions:** Microsoft Graph, *delegated*: `Files.Read`,
  `Files.ReadWrite`, `offline_access`, and `User.Read` (added by default;
  keep it). All three can
  be consented to by the user, so no admin consent is needed. Phase 2's
  read-only source asks for `Files.Read` at runtime. The registration
  lists the union so the Phase 3 target needs no registration change.
- **Certificates & secrets:** none. Don't create a client secret.
- **Branding & properties:**
  - Home page URL and privacy statement URL.
  - **Publisher domain** `yaffo.app`. It's verified by serving
    `https://yaffo.app/.well-known/microsoft-identity-association.json`
    with the application ID. Without it, the consent screen shows
    the domain as unverified.
- **Publisher verification (optional, later):** this needs a Microsoft
  Partner Network (Partner Center) account. Personal accounts can consent
  without it; the screen just says "unverified". Work or school tenants
  whose admins allow consent only to verified publishers will block Yaffo
  until it's done. Not a Phase 0 blocker.

Record the **Application (client) ID**. Run acceptance check 1.

**Registered 2026-10-09:**
- Application (client) ID: `de78c8bb-c682-4f8c-853c-979edc53a42c`.
- It lives in the Default Directory tenant of turan.jason@gmail.com. That
  account already had a directory, so no Azure sign-up was needed. The app
  appears under *All applications*, not *Owned applications*.
- Public client flows are on, with the four permissions above.
- Acceptance check 1 passed with a personal Microsoft account.
- Branding: home page `https://yaffo.app/`, privacy statement
  `https://yaffo.app/privacy/`, and **publisher domain `yaffo.app`
  verified**.
- The Branding page warns: "End users cannot grant consent to newly
  registered multitenant apps without verified publishers." That applies to
  work and school tenants. Personal accounts consent fine (acceptance check
  1). OneDrive for Business users need publisher verification (Partner
  Center) or an admin's consent.

## Google Cloud project (Google Photos)

Since March 31, 2025 the Library API has no scope for reading a user's whole
library. It keeps three:

| Scope | Used for |
|---|---|
| `photoslibrary.appendonly` | Upload bytes, create media items and albums |
| `photoslibrary.readonly.appcreateddata` | List the albums and items Yaffo created (reconcile) |
| `photoslibrary.edit.appcreateddata` | Remove uploaded items from Yaffo-created albums (`batchRemoveMediaItems`) |

These are exactly what the Google Photos *target* needs, because it only
manages albums it created. A Google Photos *source* isn't possible with the
Library API any more, which matches the design (it isn't planned).

Steps in the [Google Cloud console](https://console.cloud.google.com):

1. **New project** `Yaffo` (registered: project ID `yaffo-511203`, with a
   billing account linked because the form requires one; the Photos Library
   API has no charges). **APIs & Services → Enable APIs** → *Photos Library
   API*.
2. **Google Auth Platform → Branding:**
   - App name `Yaffo`, user support email, and developer contact email.
   - App home page, privacy policy link, and **authorized domain**
     `yaffo.app`. The domain must be verified in Google Search Console by
     the same account.
   - A logo is optional. Uploading one adds brand verification, so skip it
     for now.
3. **Audience:** user type **External**. Status starts at **Testing**. Add
   test users, including your own account: in Testing, consent is refused
   to anyone not on the list.
   After saving, check the list shows the account ("1 user (1 test)"). An
   unsaved list fails sign-in with a bare Google error page showing only
   *Request details*.
4. **Data access:** add the three scopes above (paste them into *Manually add
   scopes*). The console classifies them as follows:
   - `appendonly` is **sensitive**;
   - the two `appcreateddata` scopes are **non-sensitive**;
   - none are restricted.
   
   So verification is the sensitive-scope review, with no third-party
   security assessment.
5. **Clients → Create client → Desktop app**, named `Yaffo desktop`.
   - Record the **client ID** and **client secret**. The console shows the
     secret only once, in the creation dialog. A desktop client
     accepts a loopback redirect on any port, with nothing to register.
   - The secret ships inside the app and isn't confidential, but Google's
     token endpoint expects it.

Run acceptance check 2 as a test user.

**Phase 3 readiness check (2026-10-10):** The public home page and privacy
policy both returned HTTP 200. The home page links to the privacy policy; the
policy describes Google Photos access, local token and ID storage, revocation,
and the Google API Services User Data Policy Limited Use commitment. The live
Google Photos target uploaded three photos, and a read of the app-created album
found all three remote IDs. The Google Auth Platform console still shows
**Testing**, one test user, the expected three Photos scopes, the `yaffo.app`
branding links and authorized domain, and no restricted scopes. The demo video
and verification submission remain.

**Registered 2026-10-09:**
- Project `yaffo-511203`, in Testing, with turan.jason@gmail.com as the
  test user.
- Desktop client *Yaffo desktop*.
- Acceptance check 2 passed.
- Branding: home page `https://yaffo.app/`, privacy policy
  `https://yaffo.app/privacy/`, authorized domain `yaffo.app`. The domain
  is verified in Search Console with a DNS TXT record.

**Testing-status limits** (these matter during Phase 3 development):
- refresh tokens expire after **7 days**, so expect to sign in again weekly;
- at most 100 test users.

### Verification

Publishing (*Audience → Publish app*) moves the app to *In production*.
Because sensitive scopes are present, Google then requires verification.
Until it passes:
- users see the "Google hasn't verified this app" screen;
- the project is capped at **100 users over its lifetime** (the cap never
  resets).

The submission needs:
- **Home page** on the verified domain. It must describe the app and link to
  the privacy policy, and it can't be behind a sign-in.
- **Privacy policy** on the same domain that discloses what is done with
  Google user data (see below).
- **Scope justification** for each scope (wording below).
- **Demo video** (unlisted YouTube) showing:
  - the consent screen with the app name and the client ID visible in the
    URL bar;
  - then the feature using each scope: creating the album, uploading,
    adding to and removing from the album.
  
  Record this after Phase 3's Google Photos target works, before submitting
  the verification request. [Google's verification requirements](https://support.google.com/cloud/answer/13464321)
  require a demo video with the end-to-end OAuth grant and scope use.

Suggested scope justifications:

- **appendonly:** "Yaffo is a desktop photo manager. When the user sets up
  a sync to Google Photos, Yaffo uploads the photos in the album or filter
  they chose, and creates the album that receives them."
- **readonly.appcreateddata:** "Yaffo lists the albums and photos it
  created to check that the album still matches the user's selection, and
  to avoid uploading duplicates."
- **edit.appcreateddata:** "Yaffo removes photos from the album it created
  when the user deletes them or takes them out of the selected set, keeping
  the Google Photos album in step with Yaffo."

The privacy policy must state at least:
- Yaffo runs on the user's computer. Google data (tokens, album and item
  IDs) is stored only there, and tokens are kept in the OS keychain.
- It's used only to provide the sync the user configured. It isn't
  transferred to anyone else, used for advertising, or read by humans.
- The Google API Services User Data Policy **Limited Use** statement, word
  for word as Google publishes it.
- How to revoke access: through the Google account's third-party
  connections page, or by removing the account in Yaffo.

## Where the IDs live

Desktop client IDs (and Google's desktop secret) aren't secrets. They're
visible to anyone who inspects a shipped app, and other open-source desktop
apps commit them. Phase 2 reads them from a module in the sync package,
committed to the repository, with these environment variables overriding it
for development builds:

- `YAFFO_OAUTH_MICROSOFT_CLIENT_ID`
- `YAFFO_OAUTH_GOOGLE_CLIENT_ID`
- `YAFFO_OAUTH_GOOGLE_CLIENT_SECRET`

Registered values:

| Variable | Value |
|---|---|
| `YAFFO_OAUTH_MICROSOFT_CLIENT_ID` | `de78c8bb-c682-4f8c-853c-979edc53a42c` |
| `YAFFO_OAUTH_GOOGLE_CLIENT_ID` | `150163256384-1mduv8hmpulmc4tmu4nhquu19rcsp5ff.apps.googleusercontent.com` (project `yaffo-511203`, client *Yaffo desktop*) |
| `YAFFO_OAUTH_GOOGLE_CLIENT_SECRET` | shipped with the desktop client in `yaffo/sync/oauth_clients.py`; this variable overrides it for development |

`scripts/oauth_probe.py` reads the same variables. Until Phase 2 adds the
module, the variables are the record.

## Hosting on yaffo.app

The docs site (MkDocs, deployed from master by `.github/workflows/docs.yml`)
is served at `https://yaffo.app` through a GitHub Pages custom domain, with
HTTPS enforced. The old `jason-turan0.github.io/yaffo` URLs redirect. It
provides:

- **Home page** (`docs/index.md`) and **privacy policy** (`docs/privacy.md`)
  for both providers' branding.
- **`/.well-known/microsoft-identity-association.json`**, the Microsoft
  publisher-domain file. MkDocs skips dot-directories, so `mkdocs.yml` has an
  `exclude_docs` override that publishes `.well-known/`.

DNS is the `yaffo-app` Cloud DNS zone in project
`gen-lang-client-0392476874`:
- apex A and AAAA records point to GitHub Pages;
- `www` is a CNAME to `jason-turan0.github.io`;
- a `google-site-verification` TXT record verifies the domain in Search
  Console.

Nothing here adds cost: GitHub Pages is free, and the zone already existed.
