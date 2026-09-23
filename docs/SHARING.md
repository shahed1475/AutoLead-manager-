# Running HOM as a server and sharing it

HOM runs on this Linux machine (it needs the local AI model, the research
browser and WhatsApp desktop) and is shared through a Cloudflare Tunnel: an
HTTPS link, no router changes, and your IP address stays private.

## Everyday use

Click the **HOM** button on the desktop (or in the app menu), or run:

| Command | What it does |
|---|---|
| `./start.sh` | Start the app, open a share link, copy it to the clipboard, open the browser |
| `./start.sh local` | Start the app on this computer only |
| `./start.sh status` | Show whether it is running and the current share link |
| `./start.sh link` | Print and copy the current share link |
| `./start.sh stop` | Close the share link and stop the app |
| `./start.sh install-button` | (Re)create the desktop / app-menu button |

Right-click the button for Stop, Status, Copy share link and Start on this
computer only. After you pull new code, start with `HOM_REBUILD=1 ./start.sh`
so the app is rebuilt. Developer mode (venv + Vite dev server) is
`scripts/dev.sh`.

The app itself keeps running in Docker after the window closes and comes back
after a reboot. The share link also keeps running until `./start.sh stop` or a
reboot; after a reboot, click the button again for a new link.

## Security

- The first `./start.sh` asks you to create a password (10+ characters). No
  link is ever opened while the app has no password — otherwise the first
  person to reach it could set one and lock you out.
- Everyone with the link and password has full access, including sending
  email from your account. Share both only with people you trust. Change the
  password in Settings if someone should lose access.
- The instant link changes each time it is reopened, and anyone who has an old
  link can no longer use it.

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
   ```

5. `./start.sh stop && ./start.sh` — the launcher now uses your permanent link,
   and the live log feed streams.
