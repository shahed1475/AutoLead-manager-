# Social media automation

Engage → **Social media**. Tabs: Create · Posts · Inbox · Accounts.

| Platform | Posting | Inbox (comments + DMs) |
|---|---|---|
| Facebook Page | Meta API | Meta API — automatic AI replies |
| Instagram (Business/Creator) | Meta API | Meta API — automatic AI replies |
| LinkedIn (profile or Company Page) | API **or** browser | not available (LinkedIn doesn't give apps access) |
| X | API **or** browser | not available (needs X's paid API tiers) |

Browser posting is **owner-only**. Client workspaces get everything else with their
own API keys; see "Client workspaces" below.

## Connect (Accounts tab)
Paste one Meta access token (System user or long-lived) with `pages_show_list,
pages_manage_posts, pages_read_engagement, instagram_basic, instagram_content_publish`.
HOM lists the Pages it can manage and each Page's linked Instagram account; each
Page's own token is stored encrypted (`social_accounts.token_enc`). *Check* re-tests it.

## Create → publish
1. Describe the post; **Write with AI** writes a Facebook and an Instagram version
   from your Company DNA and offer list (no invented prices/results). Edit freely.
2. Add a JPG/PNG (≤ 8 MB) — required for Instagram.
3. **Publish now**, **Schedule** (your local time), or **Save as draft**. Nothing is
   published without one of those — that's your approval.
4. HOM's n8n (*HOM · Social media publisher*) publishes due posts every minute.
   Each platform is published separately: one failing gives *partial* with the
   reason and a **Retry**.

Instagram fetches the image from a public address: HOM serves post images at
`<dashboard link>/api/social/media/<random 32-hex>.jpg`, so your dashboard link must
be online (`./start.sh`) when an Instagram post publishes. Facebook images are uploaded.

## LinkedIn / X — API
- **LinkedIn**: token from your own LinkedIn developer app with `openid profile
  w_member_social` (+ `w_organization_social` and the Community Management API for a
  Company Page — give its numeric id). Tokens last ~60 days. `linkedin.py` posts via
  `/rest/posts` (text in LinkedIn's "little text" format, `#tags` kept as hashtags;
  images via `/rest/images?action=initializeUpload`).
- **X**: OAuth 2.0 user token with `tweet.read tweet.write users.read media.write
  offline.access` + refresh token + Client ID (and secret for confidential apps).
  X tokens expire after ~2 h; `xapi.with_token` renews once on 401 and saves the new
  pair (encrypted). Posts are limited to 280 characters (checked before saving).

## LinkedIn / X — browser (no API)
HOM opens the site in its own headless Chromium, one saved profile per account in
`backend/data/social_browser/<id>/` (git-ignored; deleted on Disconnect). The **Sign in**
window shows it live (screenshots every ~1.2 s): click on the picture, type in the box
(hidden by default, for passwords), Enter/Tab/scroll. You sign in yourself — use email/
phone + password; "Continue with Google" is usually refused inside automated browsers.
HOM keeps only the site's cookies, never the password. Then **I'm signed in** verifies it.

Publishing opens the composer, types the text, attaches the image and presses Post.
Safety: a sign-in page, checkpoint, CAPTCHA or "confirm it's you" → HOM stops, marks the
account **Needs sign-in** and asks you to finish it in the window. No CAPTCHA solving,
stealth or proxies. At most 8 browser posts per account per 24 h. The live window can
only open that platform's https pages. Automated posting may break a site's rules — the
API is the safer option; sites also change their pages, so selectors have fallbacks.

## Inbox (Facebook + Instagram)
Extra Meta permissions: `pages_manage_engagement, pages_messaging,
instagram_manage_comments, instagram_manage_messages` (a missing one only disables that
part and is shown on the account). The n8n tick starts `inbox.sync_all()` in the
background (one at a time); each account is polled every 2 minutes. **Check now** forces it.
- First sync only marks the backlog as seen. Messages older than 12 h are only shown.
- Your own comments/messages are kept as history, never answered.
- **Answer automatically with AI** (default on, like WhatsApp) + *Also public comments*.
  DMs reuse the WhatsApp reply prompt (Company DNA + offer list, no prices, no invented
  facts, continue the conversation); public comment replies are 1–2 sentences, never
  mention prices and invite a DM. Several DMs in a row get one answer (the newest).
- Opt-out words → status *Asked to stop*, lead → **Do not contact**, never answered again
  (not even by hand). Do-not-contact leads are skipped. Per-person daily cap (default 6).
- Leads: everyone who sends a DM, and commenters who show interest (a question, price,
  "interested", "DM" …) → lead `Facebook · Name` / `Instagram · @name`, source
  FACEBOOK / INSTAGRAM, status REPLIED.
- You can open any conversation, **Write with AI**, edit and **Send**, or **Skip**.
  `inbox.send_reply()` is the one way an answer goes out (Meta: comment reply /
  `/{page}/messages` with `messaging_type=RESPONSE`, i.e. inside Meta's 24 h window).

## Client workspaces
Same page (Engage → Social media), same Create / Posts / Inbox, with the client's **own**
Meta / LinkedIn / X keys, stored encrypted in their own workspace database.
- **API only.** The browser transport is refused in the client edition: in the service
  (`connect_browser`, `browser_login`, `publish_target`) and in the router (403).
  The UI shows no "Browser (no API)" option. An automated website session can get a
  client's account restricted, and that risk isn't taken on their behalf.
- **No n8n in a workspace:** `service.tick_loop()` runs `tick()` every minute from the
  app's lifespan (next to the WhatsApp pacer). It publishes due posts and syncs the inbox.
- **Instagram images:** Instagram fetches the image without a cookie, so the client link
  has a route `/social-media/<port>/<32-hex>.jpg|png` (GET only, ports 7001–7099) to that
  workspace's `/api/social/media/`. The supervisor copies the current client link
  (`.run/portal-link.txt`) into `workspaces/ws-<id>/data/client-link.txt` and removes it
  when the link closes; `media_url()` builds `<client link>/social-media/<port>/<file>`.
- The inbox works as on the owner dashboard (automatic replies from the client's own
  Company DNA, opt-outs, per-person cap, leads in their own workspace).
- The egress guard is unchanged: Meta, LinkedIn and X are public addresses.

Code: `backend/social/` (meta.py = Graph API, linkedin.py, xapi.py, browser.py = the
browser transport, inbox.py = comments/DMs, service.py = accounts/compose/posts/
`publish_target` — the one way a post goes out), `routers/social.py`,
`pages/Social.jsx` (+ `components/social/SocialInbox.jsx`, `BrowserWindow.jsx`), `deploy/n8n/hom-social-publisher.json`. Tests: `tests/test_social.py`.
