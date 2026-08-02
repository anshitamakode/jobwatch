# jobwatch

Polls company ATS feeds directly and pushes you a Telegram alert within minutes
of a new posting going live — hours before it syndicates to LinkedIn.

It also tells you **which of your two resumes to use**, based on what the
posting actually asks for.

```
🧠 Staff AI Engineer, Generative AI
NVIDIA · Bengaluru, India
Resume: Resume_Anshita_Makode_AI_ML.pdf · score 30
generative ai, llm, agentic, orchestration, gemini
Apply →
```

Supports **Workday, Greenhouse, Lever, Ashby, SmartRecruiters** — which covers
the large majority of tech roles you'd want.

---

## Setup (about 15 minutes)

### 1. Install

```bash
cd jobwatch
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python tests/test_smoke.py      # should print "All good."
```

### 2. Telegram

1. In Telegram, message **@BotFather** → `/newbot` → pick a name. Copy the token.
2. Message **@userinfobot** → it replies with your numeric chat ID.
3. Send your new bot any message (`hi`) — a bot can't message you first.

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="987654321"
python -m jobwatch test-notify
```

If the test alert lands on your phone, you're done with the hard part.

### 3. Add companies

```bash
./bootstrap.sh                                  # tries a candidate list
python -m jobwatch add "https://<any job url>"  # add one at a time
```

`add` figures out the ATS from the URL, **verifies the feed actually responds**,
prints the first few live jobs, then writes it to `companies.yaml`. If a
company has moved ATS, it fails loudly instead of silently watching nothing.

The reliable way to add anyone: open a real job posting on their careers page,
copy the URL from the address bar, paste it into `add`. Works even for
`careers.company.com` pages, as long as clicking a job redirects you to the
underlying ATS host.

### 4. First poll

```bash
python -m jobwatch poll
```

The first run is a **baseline** — it indexes every currently-open job without
alerting, so you don't get 800 notifications at once. Alerts begin on run two.

---

## Running it continuously

### Option A — GitHub Actions (recommended)

Free, runs whether or not your laptop is on.

1. Push this folder to a **private** GitHub repo.
2. Settings → Secrets and variables → Actions → add `TELEGRAM_BOT_TOKEN` and
   `TELEGRAM_CHAT_ID`.
3. Settings → Actions → General → Workflow permissions → **Read and write**.
4. Actions tab → *jobwatch* → *Run workflow* once, to lay down the baseline.

`.github/workflows/poll.yml` then runs every 15 minutes and commits the state
file back. GitHub queues scheduled jobs, so real spacing is usually 15–25
minutes — still far ahead of LinkedIn.

### Option B — cron on your machine

```cron
*/15 * * * * cd /path/to/jobwatch && .venv/bin/python -m jobwatch poll --quiet >> log.txt 2>&1
```

Put the two `export` lines in the crontab or a sourced env file.

---

## Tuning

Everything lives in `config.yaml`.

| Symptom | Fix |
|---|---|
| Too many irrelevant alerts | Raise `filters.min_score` from 12 toward 20 |
| Missing roles you'd want | Add terms to the relevant profile's `title_any` |
| A whole category of noise | Add a phrase to `filters.exclude_titles` |
| Wrong resume suggested | Move the deciding keyword into that profile's `boost` |

**Scoring:** title match = 10 base, each boost term in the title = +6, in the
body = +2, seniority word = +4. Highest-scoring profile picks the resume.

**Already configured for you:** Indian locations only; cloud/SRE/DevOps/platform
titles excluded per your preference; NVIDIA/Qualcomm-style hardware and silicon
roles filtered out; intern/manager/director levels dropped.

One caveat on the cloud exclusion — `platform engineer` is on the kill list,
which will also drop *ML Platform Engineer* roles. If you want those, delete
that one line and add `ml platform` to the `ai_ml` profile instead.

**Note on descriptions:** Greenhouse, Lever, and Ashby return full job text, so
scoring there is rich. Workday's list endpoint returns titles only, so those
postings score on title alone. That's why title keywords matter most.

---

## Commands

```bash
python -m jobwatch poll          # the real thing: fetch, diff, alert
python -m jobwatch check         # dry run — show current matches, touch no state
python -m jobwatch add <url>     # add a company, with verification
python -m jobwatch test-notify   # send one fake alert
```

`check` is the one to use while tuning filters. It never writes state, so you
can iterate on `config.yaml` freely without burning through jobs.

To re-alert on something you dismissed, delete its line from `state/seen.json`.

---

## Files

```
config.yaml       filters + the two resume profiles     ← you'll edit this
companies.yaml    who to watch                          ← managed by `add`
bootstrap.sh      bulk-add a candidate list
state/seen.json   de-dup memory (auto-created)
jobwatch/
  sources.py      one adapter per ATS
  discover.py     URL → config entry
  matcher.py      scoring + resume routing
  store.py        seen-job persistence
  notify.py       Telegram / email / console
  cli.py          command line
tests/            offline tests, no network needed
```

---

## Being a good citizen

These are the same public endpoints the companies' own career pages call from
your browser. Still: there's a 1-second pause between companies by default
(`politeness_seconds`), one request per company per cycle, and polling every 15
minutes rather than every 30 seconds. Don't crank the schedule to `* * * * *` —
it won't make you meaningfully faster and it will get you rate-limited.

This tool finds jobs and tells you which resume to use. It does not submit
anything. Pair it with Simplify or JobWizard for the form-filling.
