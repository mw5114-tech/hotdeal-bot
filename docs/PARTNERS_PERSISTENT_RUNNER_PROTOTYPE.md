# GitHub Actions partner link worker: executable experimental plan

Updated: 2026-11-01. Experimental branch ONLY, draft PR #1; never enable publishing.

## Proven facts
- User's regular Windows Chrome + helper extension v0.4: official quick-link UI successfully generated and extracted a new link.
- GitHub-hosted Ubuntu, Windows and macOS: anonymous site access 403.
- Hosted Ubuntu + real user-supplied Playwright authenticated state, run 41239608848: HTTP 403 and no quick-link form; despite having a storage state the server did not provide an authenticated page. Root cause is not proven.
- New worker fixture CI run 41244865358: 9/9 offline tests passed, actual link generation skipped. No external runner currently connected.

## Why copying Chrome or keeping its tab open on GitHub does not solve this
Windows Chrome protects cookies with App-Bound, machine-bound encryption. Copying a browser profile folder to a GitHub/Linux VM cannot faithfully clone its login. A Playwright state import only restores supported session values, and our real hosted restored-state run returned HTTP 403. GitHub-hosted Actions jobs use newly provisioned execution environments, not a continuously running signed-in browser tab.

## Implemented files
- gh_only/partners_persistent_worker.py: CLI supporting fixture, setup-profile, read-only, generate-one. Normal Chrome Stable with a persistent DEDICATED profile on a trusted always-on Linux host; no cookies copied to GitHub.
- gh_only/test_partners_persistent_worker.py: 9 deterministic unit tests.
- .github/workflows/partners-persistent-worker.yml: GitHub-hosted tests plus disabled opt-in self-hosted read-only/one-link jobs.
- Generated link validation: the new link must have the requested productId, itemId and vendorItemId in the first redirect. In ambiguous/uncertain cases it does not return any claimed valid link and never clicks twice.
- No automated posting, recurring cron, captcha handling, bypass, login credential collection, private endpoints, or session export.

## How to use a non-ephemeral machine if one is available
A trusted Linux x64 machine MUST be running whenever GitHub schedules work; an arbitrary cloud VM might still receive 403. User's PC can be off, provided that remote machine itself can open the site.

1. On the trusted Linux machine, install Chrome Stable, Python 3.11+, Playwright Python, and xvfb-run. Access an interactive Linux desktop (physical or remote desktop) on that machine for a one-time normal login.
2. From a private repo checkout on that machine, launch the dedicated profile (choose a private absolute path):
   python3 -m gh_only.partners_persistent_worker --mode setup-profile --profile-dir /home/ubuntu/.local/share/hotdeal-partners-profile
   Log in on the Chrome window using the official UI. Only when the quick-link form appears, press Enter to finish. Stop if HTTP 403.
3. On the SAME host, perform read-only check:
   xvfb-run -a python3 -m gh_only.partners_persistent_worker --mode read-only --profile-dir /home/ubuntu/.local/share/hotdeal-partners-profile
   It must produce SIGNED_IN_QUICK_LINK_FORM_VISIBLE before any generation attempt.
4. Register a GitHub self-hosted runner on that machine from repository Settings > Actions > Runners, with custom label coupang-partners. Protect runner access; never commit the registration token.
5. Set repository variable PARTNERS_PROFILE_DIR to the absolute profile path and GH_ONLY_PARTNERS_SELFHOSTED_ENABLED=true ONLY after verified access. The guarded branch-only read-only CI job can be tested by editing its workflow file on the experimental branch.
6. ONE actual link is further gated by GH_ONLY_PARTNERS_GENERATE_ENABLED=true, plus explicit manual dispatch (the workflow must exist on the default branch for the Run Workflow button), OR on the trusted host run the following authorized single click:
   GH_ONLY_PARTNERS_GENERATE_ENABLED=true xvfb-run -a python3 -m gh_only.partners_persistent_worker --mode generate-one --product-url 'https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724' --profile-dir /home/ubuntu/.local/share/hotdeal-partners-profile
   Do not run generation until the read-only test succeeds and a human confirms that the logged-in Partners account belongs to the user.
7. Output partners_worker_result.json may report NEW_LINK_EXACT_OPTION_VERIFIED (matching product option) or a clear blocked/uncertain status. The code does NOT independently verify the affiliate account owner; it must be checked in the official UI. The result is never auto posted.

## What cannot be truthfully claimed
- No PC-off real Coupang link creation has been demonstrated yet. No always-on authorized host was supplied, installed or tested. The new GitHub-hosted CI success is only a fixture.
- An external GitHub runner still needs a powered-on machine. Cloud hosts may also be blocked. There is no guaranteed free method to clone a logged-in local Windows Chrome into GitHub and automatically defeat a website HTTP 403.
- For GitHub's own cloud to create account-specific links without a website, the viable supported path is approved Coupang Partners Deeplink API credentials.
- Existing main and 30-minute DealSpot/Naver workflows were NOT modified.

Reference: https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41244865358
