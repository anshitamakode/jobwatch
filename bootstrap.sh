#!/usr/bin/env bash
# Feeds a candidate list through `jobwatch add`, which verifies each feed
# before writing it to companies.yaml. Ones that don't resolve are skipped
# with a message -- that's expected, companies migrate ATSs constantly.
#
# These URLs are *starting guesses*, not gospel. The reliable way to add a
# company is: open a real job posting on their careers page, copy the URL
# from the address bar, and run `python -m jobwatch add "<that url>"`.

set -uo pipefail
cd "$(dirname "$0")"

CANDIDATES=(
  # --- Workday (verified from your browser) -------------------------------
  "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"

  # --- Workday (guesses, will be verified) --------------------------------
  "https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced"
  "https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site"
  "https://walmart.wd5.myworkdayjobs.com/en-US/WalmartExternal"
  "https://target.wd5.myworkdayjobs.com/en-US/targetcareers"
  "https://cisco.wd5.myworkdayjobs.com/en-US/at_cisco"
  "https://paypal.wd1.myworkdayjobs.com/en-US/jobs"
  "https://ebay.wd5.myworkdayjobs.com/en-US/apply"
  "https://dell.wd1.myworkdayjobs.com/en-US/External"

  # --- Greenhouse (guesses) -----------------------------------------------
  "https://boards.greenhouse.io/databricks"
  "https://boards.greenhouse.io/stripe"
  "https://boards.greenhouse.io/confluent"
  "https://boards.greenhouse.io/mongodb"
  "https://boards.greenhouse.io/postman"
  "https://boards.greenhouse.io/gitlab"
  "https://boards.greenhouse.io/airbnb"

  # --- Ashby / Lever (guesses) --------------------------------------------
  "https://jobs.ashbyhq.com/openai"
  "https://jobs.lever.co/dream11"
)

ok=0; fail=0
for url in "${CANDIDATES[@]}"; do
  echo "──────────────────────────────────────────────"
  if python3 -m jobwatch add "$url"; then ok=$((ok+1)); else fail=$((fail+1)); fi
  sleep 1
done

echo
echo "Done. $ok added/known, $fail skipped."
echo "Add more any time:  python3 -m jobwatch add \"<job url>\""
