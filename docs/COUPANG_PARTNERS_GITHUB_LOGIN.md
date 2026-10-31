# Coupang Partners quick-link — GitHub-only feasibility experiments

Updated 2026-11-01 KST. Experimental branch: `test/github-only-pipeline-20261031`, draft PR #1. Do not merge before additional verification.

## Goal

Use @jirum original Coupang affiliate URL -> one HTTP 302 hop (no product HTML) -> cleaned ordinary product URL retaining productId, itemId and vendorItemId -> paste into user's own signed-in Coupang Partners quick-link form -> produce account-specific new link. No Daangn publishing in this phase. The user's official Coupang Partners API credentials are not yet available.

## Actual live findings

- **Anonymous GitHub hosted Chromium** [run 41234764331](https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41234764331): `https://partners.coupang.com/` responded **HTTP 403**, page title `Access Denied`, not a login form. Did not enter credentials.
- **Ordinary Python HTTP plus Chromium direct hash path** [run 41234965441](https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41234965441): HTTP 403 by both. Tested candidate URL `https://partners.coupang.com/#affiliate/ws/link-to-any-page`. This older documented browser path is unverified as a current menu route because the 403 is from the server before the client-side hash fragment can route.
- The same second run passed **8/8 offline canonical-link preparation tests**, and used one-hop redirect on the two user-provided shortlinks: Whikare `https://link.coupang.com/a/iiPBYpVHae` -> `https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724`; Ottogi hotcake `https://link.coupang.com/a/iiCmQxWdUG` -> `https://www.coupang.com/vp/products/6221369038?itemId=12453073752`. No original publisher affiliate tracking params retained, no links generated.
- **Safe optional authorized session check** [run 41235105740](https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41235105740): 5/5 offline storage-state filter tests passed, live login job intentionally **SKIPPED** because the user has not added `COUPANG_PARTNERS_STORAGE_STATE_JSON` Secret and `GH_ONLY_PARTNERS_AUTH_TEST_ENABLED=true` Variable. This stage never clicks link creation, never logs session cookies, and cannot claim signed-in access.
- The above proves a GitHub-hosted anonymous network/access failure but does NOT conclusively prove whether adding a valid user-authorized session would alter the access result. Do not assume cookies automatically solve an edge 403; do not try stealth, proxy rotation, CAPTCHA bypass, or other evasions.

## Ready modules

- `gh_only/partners_anonymous_ui_probe.py` — read-only hosted browser homepage access test.
- `gh_only/partners_access_methods_probe.py` — ordinary HTTP + Chromium check for direct hash candidate.
- `gh_only/partners_link_prep.py` and `gh_only/test_partners_link_prep.py` — prepare clean exact product URLs for official simple link form, no publisher attribution.
- `gh_only/partners_authenticated_access_probe.py` + tests and `.github/workflows/partners-authenticated-probe.yml` — OPTIONAL read-only user-authorized session test. Only Coupang cookie and storage origins restored in memory, auth state never written in report/artifact.
- `.github/workflows/partners-self-hosted-vm-reachability.yml` — guarded push/manual test, requires user-installed trusted self-hosted GitHub Actions Linux runner labeled `coupang-partners` and owner-set `GH_ONLY_PARTNERS_SELFHOSTED_ENABLED=true`; before enabled this job SKIPS. No credentials, checks whether its network can reach the Partners homepage. NOT run on a VM.

## Option A — one opt-in GitHub-cloud authenticated access test

Risk: Playwright `storage_state` JSON contains a login-equivalent browser session. The repository is private, but only the owner should add the JSON directly to encrypted GitHub Actions Secrets; never post it in chat, a commit, run log, screenshots or issue comments. Restrict access to the private repository. A login session may expire or be challenged on a new IP. **This does not guarantee resolving the existing GitHub 403.**

Local Windows PowerShell, with Playwright installed:

```powershell
py -m playwright codegen --save-storage="$env:USERPROFILE\coupang_partners_session.json" "https://partners.coupang.com/#affiliate/ws/link-to-any-page"
```

Sign in interactively on the isolated browser, verify the actual Partners quick-link page is visible, then close the browser and recorder normally. If the isolated local browser is itself denied, STOP; do not try to bypass access controls. Verify file exists using `Test-Path "$env:USERPROFILE\coupang_partners_session.json"`; no need to display its contents. Add the JSON bytes to a new GitHub Actions Secret `COUPANG_PARTNERS_STORAGE_STATE_JSON` via GitHub Settings UI, and set a separate Actions Variable `GH_ONLY_PARTNERS_AUTH_TEST_ENABLED` to `true`. It is different from the Kakao secret and variable. The script only tests accessibility and never generates an affiliate link. After confirmed GitHub Secret upload, securely clean up the local session file. Do not ask for raw cookies or passwords in ChatGPT.

After the above user action, **edit only `.github/workflows/partners-authenticated-probe.yml` on the experiment branch** to trigger its `push` test path and inspect fixed-status report. Note: GitHub `workflow_dispatch` Run Workflow UI normally requires that workflow YAML also exist on the repository default branch; here we deliberately keep `main` unchanged. Expected states: `ACCESS_DENIED_EVEN_WITH_SESSION` (GitHub remains blocked), `QUICK_LINK_FORM_REACHABLE` (user's session works), `SESSION_EXPIRED_OR_REJECTED`, `PAGE_REACHED_BUT_UI_UNCONFIRMED` (requires non-sensitive UI selector inspection). Do not create links until signed-in UI is confirmed.

## Option B — GitHub orchestrates a trusted always-on VM (recommended fallback if cloud runner blocked)

GitHub's official self-hosted runner receives jobs by connecting outbound to GitHub, then executes work from the chosen VM. The user's PC can stay off while the VM runs. A normal browser session may be kept on the VM instead of uploading auth cookies to GitHub. This still requires a trusted running VM and authorized Coupang Partners login, and the VM's network may also receive 403.

Manual next step on VM: configure a GitHub Actions `self-hosted, linux, x64, coupang-partners` runner via repo Settings > Actions > Runners (official install commands are generated by GitHub and expire; don't store runner registration tokens in repo). Keep runner jobs restricted to this private trusted repository; do not run untrusted PR code or broad permissions. Before login/session work, set Actions repo Variable `GH_ONLY_PARTNERS_SELFHOSTED_ENABLED=true` **only after the runner is connected**, then edit `.github/workflows/partners-self-hosted-vm-reachability.yml` on the experiment branch to trigger the guarded push job (no main merge required). If ordinary network access works, next test a locally authenticated browser session in an isolated profile, then guarded quick-link form input and a single user-authorized link generation. If 403 persists, stop and use the official API after approved eligibility or human-assisted generation.

## Decision gate

1. GitHub-cloud auth test succeeds -> inspect actual quick-link form; test one clean product URL; verify generated `link.coupang.com` points to same product and item, manually verify own Partners account ownership; zero auto posts.
2. GitHub-cloud auth test still 403 -> do not repeat; check the existing VM via self-hosted runner's ordinary reachability test. If accessible, use GitHub for scheduling/orchestration and VM for authenticated partner UI.
3. Both blocked or partner policy disallows UI automation -> use authorized official Partners API after approval, or keep link generation human-in-the-loop.

## Safety and status

No Coupang Partners link generated yet, no credentials imported, no login attempted by our GitHub tests, no publishing/Telegram forward/30-minute DealSpot+Naver workflow changed. The original source affiliate link remains unchanged and is never advertised as the user's own affiliate link.

## 2026-11-01: All three ordinary GitHub-hosted OS networks rejected anonymously

- A single standard (no cookies, no proxy, no retry) HTTPS GET per OS was run with `gh_only/partners_github_runner_compare.py` and `.github/workflows/partners-os-compare.yml` on isolated experimental branch.
- **[Run 41239757269](https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41239757269)**: `windows-latest` **403**, `macos-latest` **403**. Earlier `ubuntu-latest` 403 also reproducible. Both CI jobs finished successfully as diagnostics; successful job status does NOT indicate site access.
- This eliminates a simple Windows/Linux/macOS runner switch as a useful unauthenticated fix. It does NOT prove that restored authenticated session also gets 403. The existing opt-in `partners-authenticated-probe.yml` requires user-created Secret and Variable; never assume they are installed or access their values.
- GitHub-hosted Ubuntu and Windows networks are Azure based; macOS runs in GitHub's macOS cloud. GitHub documentation explicitly warns shared hosted-runner IP addresses can be flagged by third-party threat intelligence and security products. Blocking is likely related to GitHub-hosted network reputation and/or upstream access-control; the exact Coupang decision criterion is not known.
- A separate **local Chrome v0.4 extension** has been user-verified to create/read a newly generated link on the already signed-in official Coupang Partners quick-link page. Thus official Partners manual creation works in user's local login session without API. That evidence does **not** prove the same local session is accepted by a remote GitHub-hosted runner, nor prove third-party UI automation is an officially supported API.
- The next decisive GitHub-hosted-only experiment is an explicitly opt-in single signed-in **read-only** page access check using user-provisioned `COUPANG_PARTNERS_STORAGE_STATE_JSON`, recognizing session sensitivity/expiry and not automatically retrying upon denial. If 403 persists, stop standard hosted-runner probes.
- For the user's off-PC aim, GitHub Actions can still run the **entire workflow orchestration** while one authenticated link-generation step executes through a trusted self-hosted GitHub Runner on a previously available always-on VM, if network and UI access permit. This is not the same as GitHub-owned hosted compute and requires the machine be online.
- Another valuable data point from friend who claims 'GitHub-only' success is the workflow's literal `runs-on` and whether a self-hosted runner, external app/bridge, or approved API is involved; do not assume GitHub-hosted runners alone made the personal affiliate link.
- No partner login credentials submitted in this task, no personal partner link generated on GitHub, no attempt to evade 403. Existing 30-minute workflows and publishing untouched.
