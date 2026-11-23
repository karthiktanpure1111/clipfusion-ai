# ClipFusion AI — External Integrations Guide

ClipFusion only talks to platforms through their **official APIs** and **official OAuth**.
It never asks for a password, never scrapes, and never downloads videos from YouTube,
Twitch, TikTok, Instagram or Facebook pages. This guide explains what an operator must set
up, what each end user must have, and how the app behaves while something is missing.

The live status of every integration is available to the UI at
`GET /api/v1/accounts/requirements` (configured?, missing env vars, redirect URIs, scopes,
eligibility, review requirements, docs links).

---

## 1. Redirect URIs

All OAuth callbacks go to the backend:

```
{BACKEND_PUBLIC_URL}{API_PREFIX}/accounts/callback/{provider}
```

| provider path | used for | register it in |
|---|---|---|
| `google` | YouTube (and optional Google Drive import) | Google Cloud Console → Credentials → OAuth client → *Authorized redirect URIs* |
| `instagram` | Instagram API with Instagram Login | Meta app → Instagram → API setup with Instagram login → Business login settings → *OAuth redirect URIs* |
| `instagram` (Unified Login) | Instagram API with Unified Login | Meta app → Instagram → API setup with Unified login → *Redirect URL* (HTTPS; set `INSTAGRAM_REDIRECT_URI` when it differs) |
| `facebook` | Instagram API with Facebook Login | Meta app → Facebook Login (for Business) → Settings → *Valid OAuth Redirect URIs* |

Example for local development: `http://localhost:8010/api/v1/accounts/callback/google` (the ClipFusion API moved from port 8000 to 8010; update the redirect URI in the Google Cloud console if you registered the old one).
Meta requires HTTPS redirect URIs outside of `localhost`. After the callback the browser is
sent to `{FRONTEND_URL}/app/accounts?connected={platform}` or `?error=<message>`.

---

## 2. YouTube (Google Cloud)

### 2.1 Project and APIs
1. Create a project at <https://console.cloud.google.com/>.
2. *APIs & Services → Library*: enable **YouTube Data API v3**, **YouTube Analytics API**
   and (only if you want Drive imports) **Google Drive API**.
3. *Credentials → Create credentials → OAuth client ID*, application type **Web
   application**, add the `google` redirect URI from §1.
4. Optional: *Create credentials → API key*, restrict it to *YouTube Data API v3*. It is used
   for public metadata lookups of pasted YouTube links when the user has not connected a
   channel.

Environment variables:

| variable | required | meaning |
|---|---|---|
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | yes, for connecting YouTube | OAuth web client |
| `YOUTUBE_API_KEY` | optional | public metadata lookups without OAuth |
| `YOUTUBE_DAILY_QUOTA` | default `10000` | your project's daily unit quota |
| `YOUTUBE_UPLOAD_QUOTA_COST` | default `1600` | units charged per `videos.insert` |
| `YOUTUBE_UPLOAD_CHUNK_BYTES` | default 8 MiB | resumable chunk size (rounded to 256 KiB multiples) |

### 2.2 OAuth consent screen and scopes
Configure *Google Auth Platform* (branding, audience = External, data access). ClipFusion
requests (with `access_type=offline`, `prompt=consent`, `include_granted_scopes=true`, PKCE S256):

| scope | why | Google classification |
|---|---|---|
| `https://www.googleapis.com/auth/youtube.upload` | upload Shorts | sensitive |
| `https://www.googleapis.com/auth/youtube.readonly` | channel identity, video metadata, processing status, statistics | sensitive |
| `https://www.googleapis.com/auth/yt-analytics.readonly` | real audience retention for the owner's own videos | sensitive |
| `openid email profile` | identify the Google account | non-sensitive |
| `https://www.googleapis.com/auth/drive.readonly` (only when the user asks for Drive import) | download a video file the user selects | **restricted** |

Users can untick individual permissions on Google's consent screen. ClipFusion stores the
scopes Google actually granted; a connection without `youtube.upload` is refused with an
explanation, and features needing a missing scope report it (e.g. retention → `missing_scope`).

**While the app is unverified / in "Testing":**
* only the test users you list (max. 100) can connect, and they see the "Google hasn't
  verified this app" screen;
* refresh tokens of External apps in *Testing* status expire after **7 days** — users must
  reconnect weekly. ClipFusion detects this (`invalid_grant`), marks the account *revoked*
  and sends an "account issue" notification.

**Verification:** to go to *In production* for more users you must pass Google's OAuth app
verification for the sensitive scopes (privacy policy, homepage, demo video of the scope use).
`drive.readonly` is a restricted scope and additionally requires a security assessment —
leave Drive import disabled if you do not need it.

### 2.3 YouTube API Services audit, quota, private lock
* Default quota is **10,000 units/day per Google Cloud project**, reset at midnight Pacific
  Time. Reads cost 1 unit (`videos.list`, `channels.list`); an upload (`videos.insert`)
  costs about **1,600 units** — i.e. roughly six uploads per day on the default quota.
  ClipFusion records usage in `api_quota_usage` (per account) and refuses an upload when
  the project-wide total for the Pacific day would exceed `YOUTUBE_DAILY_QUOTA`; the queue
  item is rescheduled for the next quota reset instead of failing.
* More quota requires the **YouTube API Services audit and quota extension form**
  (compliance review against the YouTube API Services Terms and Developer Policies).
* **Videos uploaded through `videos.insert` from unverified API projects created after
  28 July 2020 are restricted to private viewing** until the project passes that audit.
  ClipFusion detects when YouTube returns `privacyStatus=private` although public/unlisted
  was requested and writes that explanation into the publishing history.

### 2.4 Upload behaviour
* Resumable upload protocol: `POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`
  → session URI (stored on the queue item) → `PUT` chunks with `Content-Range`, `308` +
  `Range` acknowledgements; after an interruption the next attempt asks
  `Content-Range: bytes */{size}` and resumes from the persisted offset.
* Metadata: title ≤ 100 chars, description ≤ 5,000 chars (no `<` `>`), tags ≤ 500 chars in
  total, category 22, `privacyStatus` from the clip/schedule, `selfDeclaredMadeForKids`
  (COPPA declaration, editable per clip) and `containsSyntheticMedia=false` (clips are cut
  from the user's own footage; users are responsible for disclosure if they add synthetic
  content).
* After the upload the item is `processing_remote`; the poller (`videos.list
  part=status,processingDetails`, every 120 s) marks it `published` when YouTube reports
  `processed`, or `failed` with YouTube's rejection/failure reason.
* **Shorts eligibility is decided by YouTube**: vertical or square videos up to 3 minutes
  are classified as Shorts. ClipFusion exports 1080×1920 clips; it cannot force the
  Shorts shelf.

### 2.5 Retention analytics
Real audience retention (`audienceWatchRatio`, `relativeRetentionPerformance` by
`elapsedVideoTimeRatio`) comes from the YouTube Analytics API and is **only available for
videos on the user's own connected channel**. It is shown separately from — and never mixed
with — the "AI highlight score (estimate)".

---

## 3. Instagram (Meta)

Only **Instagram professional accounts (Business or Creator)** can publish through the API.
Personal accounts are refused with instructions to switch account type
(Instagram → Settings → Account type and tools).

Choose one flow with `INSTAGRAM_AUTH_FLOW`:

### 3.0 `unified_login` — Instagram API with Unified Login (Meta's recommended setup)

New Meta apps may only offer **API setup with Unified login** (Instagram → API setup with Unified login).
It signs in with Instagram *or* Facebook, needs **no Facebook Page**, and uses a *Facebook Login for
Business* configuration instead of scopes.

* `INSTAGRAM_AUTH_FLOW=unified_login`
* `META_APP_ID` / `META_APP_SECRET`: the app ID at the top of the dashboard and *App settings → Basic →
  App secret* (not an Instagram app ID/secret).
* `INSTAGRAM_CONFIG_ID`: the *Configuration ID* under "1. Configure your app". The configuration must
  include `instagram_basic` and `instagram_content_publish` (the default configuration does).
* Redirect URL: Meta requires HTTPS. For a local install set
  `INSTAGRAM_REDIRECT_URI=https://localhost:8011/api/v1/accounts/callback/instagram`, register that URL,
  and `start.ps1` runs `backend/scripts/https_callback.py`, a local HTTPS forwarder (self-signed
  certificate in `backend/data/certs`; the browser asks once to proceed) that passes the redirect to the API.
* Login dialog: `facebook.com/{version}/dialog/oauth?client_id&config_id&redirect_uri&response_type=code`
  (no `scope`); the code is exchanged at `graph.facebook.com/{version}/oauth/access_token` for a business
  integration system-user token. Accounts come from `/me/assigned_instagram_accounts`; every call goes to
  `graph.facebook.com` (`META_GRAPH_VERSION`, e.g. `v27.0`). Tokens without an expiry are used as is;
  an expiring one asks you to reconnect.
* Development mode: the Instagram account needs a role on the app (e.g. *Instagram Tester*, accepted in
  Instagram). App Review is only needed to connect accounts you don't own or manage.

### 3.1 `instagram_login` (default) — Instagram API with Instagram Login
* Create a Meta app (type *Business*) at <https://developers.facebook.com/>, add the
  **Instagram** product → *API setup with Instagram login*.
* Set `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET` (the *Instagram* app id/secret shown in
  that product, not the Facebook app secret).
* Permissions requested: `instagram_business_basic`, `instagram_business_content_publish`.
* No Facebook Page is needed.
* Tokens: the code is exchanged at `https://api.instagram.com/oauth/access_token`
  (short-lived, 1 h), then for a **long-lived token (60 days)** via
  `graph.instagram.com/access_token?grant_type=ig_exchange_token`. ClipFusion refreshes it
  (`graph.instagram.com/refresh_access_token`) once it is at least 24 h old; an expired
  token cannot be refreshed and the user is asked to reconnect.
* API host: `graph.instagram.com/{META_GRAPH_VERSION}`.

### 3.2 `facebook_login` — Instagram API with Facebook Login
* Meta app with **Facebook Login (for Business)**; set `META_APP_ID` and `META_APP_SECRET`.
* Permissions: `instagram_basic`, `instagram_content_publish`, `pages_show_list`,
  `pages_read_engagement`, `business_management`.
* The Instagram professional account must be **linked to a Facebook Page**, and the user
  must select that Page in the login dialog. ClipFusion lists `/me/accounts` and connects
  every `instagram_business_account` found.
* Tokens: the short-lived user token is exchanged for a **long-lived user token (~60 days)**
  via `fb_exchange_token`. These cannot be refreshed server-side; ClipFusion marks the
  account *expired* shortly before expiry and asks the user to reconnect.
* API host: `graph.facebook.com/{META_GRAPH_VERSION}`.

### 3.3 App Review, access levels, Business Verification
* In development mode / Standard Access only people with a role on the app (admins,
  developers, testers — Instagram testers must accept the invite) can connect.
* To let any professional account connect you need **App Review** for the permissions above
  and **Advanced Access**; Meta may require **Business Verification** of the app owner.
  Prepare a screencast showing the connect flow, the clip review screen and a publication.
* Reach/views **insights** need the additional `instagram_business_manage_insights`
  (Instagram Login) or `instagram_manage_insights` (Facebook Login) permission, which
  ClipFusion does not request by default. Without it analytics show only like/comment counts
  (each unsupported metric is reported, never estimated).

### 3.4 Publishing limits and Reels specs
* Meta limits API-published posts per account in a **rolling 24-hour window**. ClipFusion
  reads the live limit before every publication
  (`/{ig_user_id}/content_publishing_limit?fields=config,quota_usage`) and additionally
  caps itself at `INSTAGRAM_DAILY_PUBLISH_LIMIT` (default 50) per account per UTC day.
  When a limit is reached the queue item is rescheduled, not failed.
* Reels requirements (Meta "Reels specifications"): MP4 or MOV container (moov atom at the
  front, no edit lists), H.264 or HEVC video, progressive scan, closed GOP, 4:2:0, 23–60 fps,
  max 1920 px wide, 9:16 recommended, AAC audio (≤ 48 kHz, mono/stereo), 3 s – 15 min,
  ≤ 300 MB. ClipFusion's exports (1080×1920 H.264 yuv420p + AAC, faststart) meet these.
* Caption: ≤ 2,200 characters and ≤ 30 hashtags (ClipFusion trims hashtags to fit).

### 3.5 Publishing flow (resumable)
1. `POST /{ig_user_id}/media` with `media_type=REELS`, `upload_type=resumable`, `caption`,
   `share_to_feed` → container id (stored on the queue item).
2. Upload the file to `https://rupload.facebook.com/ig-api-upload/{version}/{container_id}`
   with headers `Authorization: OAuth {token}`, `offset: 0`, `file_size: {bytes}`.
3. Poll `GET /{container_id}?fields=status_code,status` (`IN_PROGRESS`, `FINISHED`, `ERROR`,
   `EXPIRED`, `PUBLISHED`). The worker waits up to 60 s inline, then the poller (every
   120 s) takes over.
4. `POST /{ig_user_id}/media_publish` with `creation_id` → media id;
   `GET /{media_id}?fields=permalink,shortcode`.
Containers that are not published within 24 h expire; ClipFusion then re-uploads.

---

## 4. Cloud import (direct links and Google Drive)

* **Direct links**: https only, port 443, no credentials in the URL; every resolved IP
  (and the connected peer address) must be public — private, loopback, link-local
  (incl. `169.254.169.254`), multicast and reserved ranges are refused; at most 5 redirects,
  each re-validated; size capped by `MAX_UPLOAD_BYTES`; HTML pages are refused.
* **Platform pages are refused** (YouTube, youtu.be, googlevideo.com, Twitch, TikTok,
  Instagram, Facebook, Kick, X/Twitter, Vimeo, Dailymotion, Reddit, …): such videos must be
  uploaded as original files by their rights holder. A YouTube link can still be pasted on
  the YouTube tab to fetch official metadata/analytics before attaching the original file.
* **Google Drive**: requires a YouTube/Google connection made with "include Drive"
  (`drive.readonly`). ClipFusion reads `files.get` metadata (name, mimeType, size), refuses
  non-video MIME types and streams `alt=media`.

---

## 5. Tokens and security

* OAuth tokens are encrypted at rest with Fernet (`TOKEN_ENCRYPTION_KEYS`, comma-separated
  for rotation) and are never returned by the API or written to logs (secret query
  parameters are redacted).
* OAuth `state` is an HMAC-signed payload (user id, provider, nonce, PKCE verifier, 10-minute
  expiry). The callback must also come from the browser that started the flow (an httpOnly
  nonce cookie scoped to the callback path, or the same signed-in session).
* Access tokens are refreshed automatically: Google when they expire within 5 minutes
  (and every 30 min by the `refresh_account_tokens` task), Instagram Login once a day.
  `invalid_grant` / OAuth error 190 mark the account *revoked/expired*, record the provider's
  message and send one "account issue" notification. Pending publications of such accounts
  fail with a clear "reconnect" message instead of retrying forever.
* Disconnecting an account revokes the Google grant (best effort,
  `https://oauth2.googleapis.com/revoke`), cancels its pending publications (kept in the
  history) and deletes the stored tokens. Meta grants can be removed by the user in
  Instagram/Facebook settings → Apps and websites.

---

## 6. Behaviour when credentials or approvals are missing

| situation | behaviour |
|---|---|
| `GOOGLE_CLIENT_ID/SECRET` unset | Connect YouTube → HTTP 503 `integration_not_configured` naming the variables; `/accounts/requirements` lists them |
| Meta app credentials unset | same for Instagram (`INSTAGRAM_APP_ID/SECRET` or `META_APP_ID/SECRET` depending on the flow) |
| no connected channel and no `YOUTUBE_API_KEY` | YouTube link metadata → 503 explaining both options |
| retention requested for a video the user does not own / scope missing | 403 `not_owner` / `missing_scope` with explanation; nothing is estimated in its place |
| project quota exhausted | uploads are rescheduled to the next Pacific-midnight reset |
| unverified Google project | uploads end up private; history explains the audit requirement |
| Meta app without Advanced Access | only app role holders can connect; others get Meta's error message |
| account token revoked/expired | account marked, notification sent, publications fail with "reconnect" |

Publishing is never simulated: a queue item is only `published` when the platform returned a
real video/media id.

---

## 7. Scheduler and workers (reference)

| task | period | purpose |
|---|---|---|
| `dispatch_due_publications` | 60 s | claim due items (`FOR UPDATE SKIP LOCKED` on PostgreSQL, conditional update on SQLite), skip paused schedules/users, ensure the clip render is current (requests a render; refuses while framing warnings are unacknowledged), dispatch `publish_queue_item` |
| `publish_queue_item` | job | YouTube/Instagram flow; item-level retries after 2, 10, 30, 120 min (`max_attempts` per item) |
| `poll_remote_publications` | 120 s | YouTube processing status / Instagram container status |
| `refresh_account_tokens` | 1800 s | keep tokens fresh, detect revoked grants |
| `refresh_platform_analytics` | 21600 s | real statistics (YouTube `videos.list statistics`, Instagram insights) for publications of the last 90 days |

Duplicate prevention: one queue row per clip and account (`idempotency_key =
"{clip_id}:{account_id}"`) plus a check of `publishing_history` for an earlier successful
publication.
