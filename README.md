# AutoLead Marketing Engine v2.0

**AI-powered lead generation and outreach — 100% self-hosted, zero monthly cost.**

AutoLead scrapes Google Maps for local business leads, generates personalised outreach messages via a local LLM (Ollama/llama3), and sends them automatically via Gmail or WhatsApp Desktop.

---

## Table of Contents

1. [One-Time Setup](#one-time-setup)
2. [Daily Usage](#daily-usage)
3. [Filling in company_dna.txt](#filling-in-company_dnatxt)
4. [Getting a Gmail App Password](#getting-a-gmail-app-password)
5. [Troubleshooting](#troubleshooting)

---

## One-Time Setup

Complete these steps **once** before your first launch.

### Step 1 — Install Python 3.11+

- **Windows / Mac / Linux:** Download from [python.org/downloads](https://python.org/downloads/)
- On Windows, check **"Add Python to PATH"** during installation.
- Verify: open a terminal and run `python --version` (should show 3.11 or higher).

### Step 2 — Install Node.js 18+

- Download from [nodejs.org](https://nodejs.org/) (LTS version recommended).
- Verify: `node --version` (should show v18 or higher).

### Step 3 — Install Ollama and pull llama3

Ollama runs the AI model locally — no API keys or fees required.

1. Download from [ollama.com/download](https://ollama.com/download) and install.
2. Open a terminal and run:
   ```
   ollama pull llama3
   ```
   This downloads the model (~4 GB). Do it once; it stays cached.
3. Verify Ollama is running: `ollama list` should show `llama3`.

> **Tip:** Ollama must be running in the background whenever AutoLead is active. On Windows it starts automatically with the system after installation.

### Step 4 — Edit `backend/.env`

On first launch, `start.bat` / `start.sh` will copy `.env.example` → `.env` and open it for you. Fill in your Gmail credentials:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop   ← 16-char Gmail App Password (no spaces)
SMTP_FROM_NAME=Your Name
SMTP_FROM_EMAIL=you@gmail.com
```

See [Getting a Gmail App Password](#getting-a-gmail-app-password) for how to generate this.

### Step 5 — Edit `backend/company_dna.txt`

This file tells the AI who you are. The LLM reads it to write personalised messages on your behalf.

See [Filling in company_dna.txt](#filling-in-company_dnatxt) for a complete example.

---

## Daily Usage

### Windows

Double-click **`start.bat`** — or run it from a terminal:

```
start.bat
```

The script will:
1. Check Python, Node, and Ollama are installed.
2. Create / activate the Python virtual environment (`venv/`).
3. Install or update all backend and frontend packages.
4. Launch the FastAPI backend on **http://localhost:8000**.
5. Launch the React frontend on **http://localhost:5173**.
6. Open your browser automatically.

### Mac / Linux

Make the script executable once:

```bash
chmod +x start.sh
```

Then launch:

```bash
./start.sh
```

Press **Ctrl+C** to stop all services cleanly.

### Using the Dashboard

1. Open **http://localhost:5173** in your browser.
2. Go to **Campaign** → enter a niche (e.g. `dental clinic`) and city.
3. Choose your send channel (Email / WhatsApp / Both) and daily cap.
4. Click **Start Campaign** — AutoLead scrapes leads, generates AI messages, and sends outreach automatically.
5. Monitor live progress in the **Logs** panel.
6. View all leads in the **Leads** tab; send manual follow-ups from there.

---

## Filling in company_dna.txt

Open `backend/company_dna.txt` and replace every placeholder with your real information. The more specific you are, the better the AI messages will be.

**Filled example:**

```
COMPANY_NAME=Bright Digital Agency
FOUNDER_NAME=Sarah Malik
INDUSTRY=Digital Marketing / Website Design
TAGLINE=We help local businesses get found online and grow revenue
WEBSITE=https://brightdigital.co
PHONE=+1-416-555-0192

## What We Do
We design modern, mobile-friendly websites and run Google/Facebook ad campaigns
for local service businesses. Our clients see an average of 2.5x ROI within 60 days
of launching with us.

## Our Unique Value Proposition
No long contracts — we work month-to-month. You own everything we build,
and we provide a free performance report every two weeks so you always know your numbers.

## Social Proof
We've partnered with 80+ businesses across Toronto and the GTA, generating over
$3.5M in new revenue through digital marketing campaigns since 2021.

## Target Niches
- Dental and medical clinics
- Restaurants and cafes
- Real estate agencies
- Law firms
- Gyms and fitness studios
- Home services (plumbers, HVAC, electricians)

## Tone
Friendly and Professional

## Call To Action
CTA=Book a free 30-minute growth call at https://brightdigital.co/book

## Pain Points You Solve
- Most local businesses are invisible on Google
- Websites built years ago look broken on phones
- Business owners have no time to manage social media
- Paid ads feel confusing without expert guidance
```

**Tips:**
- Be concrete with numbers (clients, revenue, timeframes).
- Keep the tone description to one line — the AI reads it literally.
- The CTA line is inserted at the end of every message — make it a clear, low-friction ask.

---

## Getting a Gmail App Password

Google requires an **App Password** (not your regular password) for automated email sending. This takes about 90 seconds.

**Prerequisites:** Your Google account must have 2-Step Verification enabled.

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
2. Sign in if prompted.
3. Under **"Select app"**, choose **Mail** (or type a custom name like `AutoLead`).
4. Under **"Select device"**, choose **Windows Computer** (or your OS).
5. Click **Generate**.
6. Google shows a 16-character password like `abcd efgh ijkl mnop`.
7. Copy it (spaces are optional — paste with or without).
8. Paste it into `backend/.env` as the value of `SMTP_PASSWORD`.

> **Security note:** App Passwords are specific to one app. If you ever stop using AutoLead, revoke it at the same URL. Never share your `.env` file.

---

## Troubleshooting

### Ollama not running / AI messages are blank

**Symptom:** Campaign runs but all AI messages say "Personalized message" or similar.

**Fix:**
1. Open a terminal and run `ollama serve` (or check if the Ollama tray icon is running).
2. Run `ollama list` — confirm `llama3` is listed.
3. If not listed: `ollama pull llama3` and wait for the download.
4. Restart AutoLead.

---

### Port already in use (8000 or 5173)

**Symptom:** `start.bat` / `start.sh` opens windows but the dashboard doesn't load.

**Fix — Windows:**
```
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

**Fix — Mac/Linux:**
```bash
lsof -i :8000 | grep LISTEN
kill -9 <PID>
```

Then re-run the launcher.

---

### Gmail authentication error (535 or 534)

**Symptom:** Emails fail with "Username and Password not accepted" or "Less secure app access".

**Fix:**
1. Confirm 2-Step Verification is ON for your Google account.
2. Generate a fresh App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Paste it (without spaces) into `backend/.env` → `SMTP_PASSWORD`.
4. Restart AutoLead.

> Regular Gmail passwords will not work — you must use an App Password.

---

### WhatsApp Desktop not opening / messages not sending

**Symptom:** WhatsApp automation starts but the window doesn't appear or messages don't send.

**Checklist:**
- WhatsApp Desktop is installed from [whatsapp.com/download](https://www.whatsapp.com/download) (not WhatsApp Web in a browser).
- You are logged in to WhatsApp Desktop.
- The recipient's phone number is saved in your contacts **or** the number is in international format (e.g. `+14165550192`).
- Do not move your mouse or click anything while the automation is running.
- If the send fails, move your mouse to the top-left corner of the screen to trigger the fail-safe and cancel.

---

### No leads scraped / 0 results found

**Symptom:** Campaign completes immediately with 0 leads.

**Possible causes:**
- The niche or city spelling doesn't match Google Maps results — try variations (`dental`, `dentist`, `dental clinic`).
- Google Maps temporarily rate-limiting — wait 10–15 minutes and try a smaller daily cap (5–10).
- Chromium driver issue — open a terminal, activate the venv, and run:
  ```
  python -m playwright install chromium
  ```

---

### `ModuleNotFoundError` on startup

**Symptom:** Backend window shows `ModuleNotFoundError: No module named 'xyz'`.

**Fix:**
```
# Windows:
venv\Scripts\activate.bat
pip install -r backend\requirements.txt

# Mac/Linux:
source venv/bin/activate
pip install -r backend/requirements.txt
```

Then restart AutoLead.

---

### Virtual environment activation fails (Windows)

**Symptom:** `start.bat` shows "Failed to activate virtual environment".

**Fix:** Delete the `venv\` folder and re-run `start.bat`:
```
rmdir /s /q venv
start.bat
```

The launcher will recreate it automatically.

---

### Frontend shows "Cannot connect to backend"

**Symptom:** Dashboard loads but shows connection errors or blank data.

**Fix:**
1. Check the backend window (taskbar, labelled "AutoLead-Backend") for error messages.
2. Visit [http://localhost:8000/docs](http://localhost:8000/docs) directly — if it loads, the backend is fine and it's a browser cache issue (hard-refresh with Ctrl+Shift+R).
3. If the backend window closed, re-run `start.bat` / `start.sh`.

---

### Still stuck?

1. Check `backend.log` (Mac/Linux) or the Backend terminal window (Windows) for the full error.
2. Search the error message on [github.com/anthropics/claude-code/issues](https://github.com/anthropics/claude-code/issues).
3. The API interactive docs at [http://localhost:8000/docs](http://localhost:8000/docs) let you test every endpoint directly in the browser.
