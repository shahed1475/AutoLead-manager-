# Running HOM as a server and sharing it

HOM runs on this Linux machine (it needs the local AI model, the research
browser and WhatsApp desktop) and is shared through a Cloudflare Tunnel: an
HTTPS link, no router changes, and your IP address stays private.

## Everyday use

Click the **HOM** button on the desktop (or in the app menu), or run:

| Command | What it does |
|---|---|
| `./start.sh` | Start the app, open your dashboard link and the client link, open the browser |
| `./start.sh local` | Start the app on this computer only |
| `./start.sh status` | Show whether it is running and both current links |
| `./start.sh link` | Print and copy your dashboard link |
| `./start.sh client-link` | Print and copy the link you give to clients |
| `./start.sh stop` | Close both links and stop the app |
| `./start.sh install-button` | (Re)create the desktop / app-menu button |

Right-click the button for Stop, Status, Copy client link, Copy dashboard link and Start on this
computer only. After you pull new code, start with `HOM_REBUILD=1 ./start.sh`
so the app is rebuilt. Developer mode (venv + Vite dev server) is
`scripts/dev.sh`.

If the internet is down, `./start.sh` can't rebuild and starts the version
already built on this computer instead.

The app itself keeps running in Docker after the window closes and comes back
after a reboot. The share link also keeps running until `./start.sh stop` or a
reboot; after a reboot, click the button again for a new link.

## Two links: yours and your clients'

| Link | Who | What is on it |
|---|---|---|
| **Your dashboard** (port 5173) | only you — needs your password | everything, incl. the Clients page |
| **Client link** (port 5174) | clients | sign-in, then **their own private HOM** |

**Every client gets their own workspace**: the same app as yours, without the
Clients page, owner settings (App Lock, AI model, Cloud LLM, WhatsApp) and
WhatsApp sending. Each workspace has its own containers, network, database and
sign-in key, so clients never see your data or each other's. A client signs in
on the client link with their Gmail (6-digit code) and lands in their own
dashboard; their outreach goes from the email account they connect themselves.
A workspace can reach the internet (search, websites, email) and your local AI
model — never this computer's other services or your home network.

Workspaces are created automatically at sign-up, up to the limit you set
(Clients → Portal → Workspaces; ~1 GB memory each). Beyond it clients wait in
line. On the Clients tab you can create, pause, resume and delete a workspace
(deleted data is moved to `workspaces/_deleted/`, never erased) and block a
client.

**Your release flow:** build and test new features in your own dashboard (it
always runs your latest code) → commit → Clients → **Publish to clients**. The
workspace service builds that *commit* (never uncommitted changes; `.env` files
and your company profile are stripped) and moves every workspace to it. Client
workspaces only start after your first publish.

The workspace service (`scripts/hom_supervisor.py`) starts and stops with
`./start.sh`; `./start.sh status` shows it. Its files: `workspaces/` (client
data), `.run/workspaces/status.json`, `.run/supervisor.log`, `.run/release/`.

**Sign-in codes are emailed from your account** — Clients → Portal → Sign-in
email (add a Gmail with an app password, then "Send test").

## Use HOM on a phone or as a desktop app

HOM is an installable app (a PWA): the same app on every device, with its own
icon, full screen, and no browser bar. Open the link (or, on this computer,
http://localhost:5173), log in, then:

| Device | How to install |
|---|---|
| Android phone (Chrome) | Tap **More → Install app** in HOM, or Chrome's menu → *Install app* |
| iPhone / iPad (Safari) | Tap **More → Install app** in HOM for the steps: Share → *Add to Home Screen* → Add |
| Computer (Chrome / Edge) | Click **Install app** in HOM's sidebar, or the install icon in the address bar |

On phones HOM has a bottom tab bar (Overview, Find, Leads, Inbox, More).
Nothing personal is stored on the device: leads, messages and settings always
come live from this computer. If it can't be reached, the app shows a
"Can't reach HOM right now" page instead of an error.

An installed app is tied to its link. The free instant link changes every time
it is reopened, so a phone app installed from an old link stops working — open
the new link and install again, or set up a permanent link (below) so installs
keep working. On this computer, install from http://localhost:5173 — that
address never changes.

## Security

- The first `./start.sh` asks you to create a password (10+ characters). No
  link is ever opened while the app has no password — otherwise the first
  person to reach it could set one and lock you out.
- Everyone with the link and password has full access, including sending
  email from your account. Share both only with people you trust. Change the
  password in Settings if someone should lose access.
- The instant links change each time they are reopened, and anyone who has an
  old link can no longer use it. Send clients the new client link after a
  restart (or use a permanent domain, below).
- Anyone can sign up on the client link. Sign-in codes are limited (one a
  minute and five an hour per address, 200 a day in total), expire after 10
  minutes and lock after 5 wrong tries; a client can have 10 open requests.

## Instant link limits

The free instant link (`https://<words>.trycloudflare.com`) needs no account,
but the Outreach campaign page's live log feed does not stream through it
(everything else works), and it is meant for sharing, not heavy traffic.

## Permanent link on your own domain

1. Create a free Cloudflare account and add your domain to it.
2. In the Cloudflare dashboard: Zero Trust → Networks → Tunnels → Create a
   tunnel (Cloudflared). Copy the tunnel token it shows.
3. Add a public hostname, e.g. `app.yourdomain.com`, pointing to
   `http://localhost:5173`.
4. Create `deploy/tunnel.env` (it is git-ignored — never commit it):

   ```
   TUNNEL_TOKEN=<the token from step 2>
   PUBLIC_URL=https://app.yourdomain.com
   # optional: a second public hostname on the same tunnel,
   # e.g. clients.yourdomain.com -> http://localhost:5174
   PORTAL_PUBLIC_URL=https://clients.yourdomain.com
   ```

5. `./start.sh stop && ./start.sh` — the launcher now uses your permanent link,
   and the live log feed streams.
