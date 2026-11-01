<<<<<<< HEAD
# QuickFix: Home help, made easy

A local-services marketplace for Pune: customers book repairs, cleaning and house help (one-time or recurring); professionals register, get approved by an admin, and accept jobs; admins run the operation.

Stack: Python 3.12, Flask (blueprints), SQLite, SQLAlchemy + Flask-Migrate, HTML/CSS/vanilla JS, pytest.

> **Honest status:** this is a working MVP, not a production-ready product. See [Known limitations](#known-limitations).

---

## 1. Run it on your Mac (copy-paste)

Check Python first. You need 3.10 or newer (3.12 recommended):

```bash
python3 --version
# If it's missing or older:  brew install python@3.12
```

Open a Terminal, go to the folder you unzipped, and open it in VS Code:

```bash
cd ~/Downloads/quickfix      # change to wherever you unzipped it
code .                       # opens VS Code (VS Code: Cmd+Shift+P -> "Shell Command: Install 'code' command" if this fails)
```

In VS Code open the terminal (Ctrl+`) and run, one block at a time:

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your settings file
cp .env.example .env

# 4. Create the database tables
flask --app run.py db upgrade

# 5. Load Pune localities + the service catalogue (no users)
flask --app run.py seed-catalogue

# 6. Create YOUR admin account (it asks for email, name, password)
flask --app run.py create-admin

# 7. (Optional, for demos) add clearly-labelled DEMO customers/professionals
flask --app run.py seed-demo

# 8. Start the server
python run.py
```

Open **http://127.0.0.1:5001**. (The port comes from `PORT` in `.env`.)

**Stop the server:** press `Ctrl+C` in the terminal.
**Next time:** `cd` to the folder, `source .venv/bin/activate`, `python run.py`.

### Demo accounts (only if you ran step 7. Development only)
| Role | Email | Password |
|---|---|---|
| Customer | demo.customer@example.com | `Demo@12345` |
| Approved professional | demo.pro1@example.com / demo.pro2@example.com | `Demo@12345` |
| Pending professional | demo.pending@example.com | `Demo@12345` |

`seed-demo` refuses to run when `QUICKFIX_ENV=production`. There is **no default admin**. You create it in step 6; admins can never be created from the website.

### Start from a clean database
```bash
rm instance/quickfix.db
flask --app run.py db upgrade
flask --app run.py seed-catalogue
flask --app run.py create-admin
```

### Run the tests
```bash
python -m pytest            # about 30 seconds
python -m pytest -q -k house_help     # just one area
```

### Port 5001 (or 5000) already in use
macOS uses port 5000 for AirPlay Receiver, which is why the default is 5001. To pick another port, edit `PORT=` in `.env`. To see what is using a port and stop it safely:
```bash
lsof -nP -iTCP:5001 -sTCP:LISTEN     # shows the process and its PID
kill <PID>                            # polite stop; only use `kill -9` if that fails
```

### Troubleshooting
| Problem | Fix |
|---|---|
| `command not found: flask` | Activate the venv: `source .venv/bin/activate` |
| `No such table` errors | Run `flask --app run.py db upgrade` |
| `Address already in use` | See the port section above |
| Logged out after restarting | Normal in development if `SECRET_KEY` changed. A key is auto-saved in `instance/.secret_key`; don't delete it |
| Locked database | Close other programs that have `instance/quickfix.db` open (e.g. DB browsers) |
| Page looks unstyled | Hard refresh (Cmd+Shift+R). Check the server log for 404s on `/static/css/app.css` |

---

## 2. How it works

```
app/
  __init__.py         app factory, security headers, error pages, SQLite write-lock hook
  models.py           all tables, constraints, indexes
  auth/               register/login/logout, role decorator (role comes from the DB, never the browser)
  main/               public pages, search, policies, support/complaints
  customer/           booking flows, my bookings, schedules, account, notifications
  professional/       dashboard, jobs, profile, availability, earnings
  admin/              approvals, users, services/pricing, localities, bookings, complaints, reviews, audit
  services/           business logic: pricing, matching, bookings (state machine), recurring, reviews, payments
  templates/ static/  UI (one design system in static/css/app.css)
migrations/           Alembic migrations (flask db upgrade)
tests/                66 automated tests
```

**Booking states:** pending → accepted → on_the_way → in_progress → completed; pending can become rejected; pending/accepted can become cancelled. Every change is validated on the server, timestamped, and stored in `booking_status_history`. Payment status (unpaid/paid/refunded/void) is separate.

**Matching:** a professional is eligible only if approved + account active + status *Available* + offers every requested service + serves the locality + inside working hours + no time off + no overlapping active job. Customers pick an eligible professional, or send an *open request* that any eligible professional can accept. If nobody is free, nearby times that do have someone are suggested.

**Double-booking protection (two layers):** the service layer re-checks overlaps inside the transaction while SQLite holds the write lock (`BEGIN IMMEDIATE`), and a partial unique index (`uq_pro_active_slot`) blocks identical active start times at the database level.

**Pricing (server-side only):** models are `fixed`, `visit_estimate` (visit fee + estimate, final set at completion), `hourly`, `duration` (base covers a minimum, extra time per hour) and `quote` (visit fee now, quote later). Admins edit all rates. Professionals may set their own rate within 50–150% of the catalogue rate. House-help combos bill at the highest hourly rate of the chosen tasks.

**Recurring house help:** a `recurring_schedules` rule plus real visit rows in `bookings`. `UNIQUE(schedule_id, original_start)` makes duplicate visits impossible, and cancelled/rescheduled visits are not regenerated. **There is no background scheduler.** Visits for the next 28 days are created when a schedule is created, whenever the customer opens it, from the admin dashboard button, and by `flask --app run.py generate-visits` (put that in a daily cron if you want). Each visit is shown as *Awaiting a professional* until someone accepts it.

**Payments:** cash/manual only. The professional (or admin) records "paid". `app/services/payments.py` documents where a real gateway would plug in. No online payment is simulated anywhere.

**Notifications:** in-app only (polled every 30 seconds). No email/SMS/WhatsApp/push/GPS.

---

## 3. Security summary
Password hashing (Werkzeug scrypt), server-side sessions with HttpOnly + SameSite cookies, CSRF on every POST, rate limits on login/register, role checks on every protected route, ownership checks (other customers' bookings return 404), suspended users lose their session immediately, safe `next` redirects, CSP that only allows same-origin scripts, `no-store` caching for logged-in pages, ORM-parameterised queries, audit log for admin actions, no secrets in git (`.env`, `instance/` ignored).

---

## 4. Deployment preparation (not production-ready yet)

| Topic | What to do |
|---|---|
| Environment | Set `QUICKFIX_ENV=production`, a strong `SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`), `SESSION_COOKIE_SECURE=1` |
| HTTPS | Terminate TLS in front (Caddy/nginx/host platform). Cookies are Secure only when `SESSION_COOKIE_SECURE=1` |
| Server | `pip install gunicorn` then `gunicorn "run:app"`. Do not use `python run.py` in production |
| Database | SQLite is fine for a demo. For production use PostgreSQL (`DATABASE_URL=...`). **Re-implement double-booking protection with row locks or an exclusion constraint on Postgres**. The `BEGIN IMMEDIATE` trick is SQLite-specific |
| Persistence | Put `instance/` (or your DB) on a persistent disk; platform filesystems are often ephemeral |
| Migrations | `flask --app run.py db migrate -m "msg"` then `db upgrade` on every deploy |
| Backups | SQLite: `sqlite3 instance/quickfix.db ".backup backup.db"`; restore by copying back while stopped. Postgres: `pg_dump`/`pg_restore` |
| Rate limiting | Use a shared store (`RATELIMIT_STORAGE_URI=redis://...`); in-memory limits are per process |
| Logging/monitoring | Logs go to stdout. Add Sentry (or similar) for error monitoring |
| Integrations needed | Email/SMS (e.g. an SMTP or SMS provider), payment gateway (gateway account + webhook), maps (only if you add geolocation) |
| Legal | Policies in `app/content.py` are drafts. Get legal review before launch |

---

## 5. Known limitations
- No email, SMS, WhatsApp, push or live GPS tracking. "On the way" is a manual status set by the professional.
- No online payments (needs a gateway account; nothing is simulated). Refunds are recorded manually.
- Professional verification is an admin review of the submitted profile. There is **no document upload** and no background check.
- No customer approval step for custom quotes / final amounts on estimate jobs.
- No background scheduler (see above). Schedules are limited to 90 days; renew by creating a new one.
- Customer data lives on the `users` + `addresses` tables (no separate customer-profile table).
- Late-cancellation fees are flagged, not charged.
- SQLite-only double-booking lock (see Deployment).
- Policies/FAQ text are drafts; contact details are intentionally blank until set (`SUPPORT_EMAIL`).
- Inline `style=` attributes are allowed by the CSP (scripts remain same-origin only).
- English only; no screen-reader or full keyboard audit has been done (labels, focus rings and contrast were designed in, but not formally audited).

---

## 6. Investor-demo walkthrough (about 8 minutes)
1. **Home page** (logged out): hero, locality + search, category cards, house-help section, honest "no reviews yet" panel. Search "fan" to see keyword search.
2. **Sign up as a customer.** Note the header switches to your name/My bookings with no reload.
3. **Book an Electrician** (use demo.customer or your new account): add an address, pick a date 2 days ahead at 10:00. The price breakdown and the list of *available* professionals appear live. Choose one and confirm. You get a reference (QF-XXXXXX) in *Pending*.
4. **Log in as demo.pro1@example.com** (private window): the request is on the dashboard. Open it, accept, then "I'm on the way", "Start job", "Mark completed" with a final amount, then "Mark received in cash".
5. **Back as the customer:** status history, the final amount, then leave a star review. Home page now shows the real review.
6. **House help:** book dishwashing + sweeping for 2 hours once. Then set up a *recurring* schedule (Mon/Thu for 2 weeks). Open the schedule: visits appear as *Awaiting a professional*. As the professional, open one visit and use "Accept all pending visits in this schedule". The customer's view flips to *Accepted*.
7. **Professional onboarding:** register a new professional, complete the profile (note the pending banner and that they don't appear in matching).
8. **Admin** (the account from `create-admin`): approve/reject with a reason, then show Overview metrics (real numbers), Services & pricing, Bookings, Complaints, Review moderation, and the Audit log.
9. **Close with the Known limitations list.** Say plainly what is not built (payments, notifications, verification documents).
=======
# quickfix
local services marketplace built with Flask
>>>>>>> 38e028a4deb1ebe697e78ac69c12d1fc35d16620
