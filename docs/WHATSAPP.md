# WhatsApp Campaigns

Engage → **WhatsApp** in your dashboard (owner only — client workspaces keep
WhatsApp off). Free and self-hosted: no paid WhatsApp API.

## How it works

| Part | What it does |
|---|---|
| **WAHA** (`hom-wa-waha`, 127.0.0.1:3100) | WhatsApp Web engine: links your WhatsApp by QR code, reports incoming messages, sends text, gives the live screen for the monitor |
| **n8n** (`hom-wa-n8n`, 127.0.0.1:5679) | HOM's own n8n. *HOM · WhatsApp inbound* forwards incoming messages to HOM; *HOM · WhatsApp campaign pacer* asks HOM every minute to send the next message |
| **HOM (Python)** | campaigns, pacing, reply detection, opt-outs, AI replies, the monitor. Every message goes out through `whatsapp_sender.send_whatsapp`, the one WhatsApp send path |

`./start.sh` starts and stops both containers with HOM (`deploy/whatsapp-compose.yml`).
Secrets live in `whatsapp/config/secrets.env` (created by start.sh, git-ignored);
the WhatsApp login is kept in `whatsapp/sessions/`.

## Using it

1. **Monitor → Connect WhatsApp**, then on your phone: WhatsApp → Settings →
   Linked devices → Link a device → scan the QR code. The monitor then shows your
   WhatsApp live, plus everything HOM does ("What's happening").
2. **Campaigns → New campaign → Who gets it**:
   - **From your leads** (automatic): HOT/WARM/COLD, city, niche.
   - **Upload a file** (manual): Excel (.xlsx) or CSV with a Phone / Mobile / WhatsApp
     column (name, company, city, niche optional; any common header spelling works).
     A default country code (e.g. +880) is added to local numbers. HOM checks the
     file first — ready / invalid / duplicates / opted out / already talking — and
     shows a preview. Contacts become leads (source WHATSAPP_IMPORT, the person in
     `contact_name`); people marked Do not contact are never added.
   Write one message with `{business_name}` `{first_name}` `{city}` `{niche}` (or,
   for leads, each lead's approved WhatsApp draft from AI Lab), then **Start** it.
3. Messages go out **one at a time**: only in sending hours, with a pause of
   90–240 s between messages and at most 30 a day (change in **Settings**).
4. **Every message is answered automatically** (owner's choice; switch with the
   *Auto-replies* button). The AI writes from your Company DNA, knows whether they
   wrote first or are answering your campaign, and replies after 20–60 s.
   Settings → *Who gets automatic replies*: **Everyone who messages you** (default;
   new contacts are saved as leads "WhatsApp · Name", source WHATSAPP) or **Only
   leads you messaged**. Never in groups, status updates or channels; never to
   messages older than 30 minutes (e.g. delivered after a reconnect); several
   messages in a row get one reply to the latest; at most 20 automatic replies per
   chat per day (stops two bots answering each other forever). People who ask to
   stop are marked *Do not contact* and never answered.

## Be careful with your number

WhatsApp doesn't allow unofficial automation and can block numbers. Use a separate
business number, start with a low daily limit, and only message businesses that
would reasonably want to hear from you. HOM doesn't try to disguise automation.
