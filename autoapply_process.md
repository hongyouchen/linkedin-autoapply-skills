# Autoapply Process (LinkedIn, AI-agent driven)

Canonical end-to-end checklist for an unattended agent running LinkedIn auto-apply passes on a user's behalf. Point any new agent session at this file (and the two it links to) before it starts applying — it consolidates every hard rule learned from past incidents (resume tailoring, apply-via-LinkedIn-not-direct-ATS, autofill-vs-resume-upload ordering, skip-if-applied, confirm-yes) into one exact sequence.

A new session with no prior context should read this file (and the linked files) in full before applying to anything, and follow it exactly — see [never_deviate_from_instructions.md](never_deviate_from_instructions.md): the user has explicitly said not to make unilateral calls about how deep to tailor, which apply path to use, or which steps to skip.

**Re-read this file (and the linked files) in full before *every single job application*, not just once at the start of a session.** This was required explicitly after a deviation (skipping straight to a form-autofill tool instead of the full step-by-step sequence — see steps 3–4 below) happened mid-session despite the files having been read at session start. Reading a checklist once and then acting from memory across many applications is exactly the failure mode that caused the deviation — memory of "I read this earlier" is not the same as actually consulting it at the moment of acting. Treat every application as its own fresh pass through this file, however repetitive that feels.

**Discovered: a third-party "Apply with Autofill" browser-extension button, clicked directly from the LinkedIn side panel, does NOT reliably trigger LinkedIn's own apply-tracking / "Did you finish applying?" prompt** — same underlying failure as going straight to a direct ATS URL (see [job_apply_via_linkedin.md](job_apply_via_linkedin.md)). After an application was genuinely submitted this way, the LinkedIn listing still showed no "Applied" status and no Yes/No confirmation prompt. Correct order going forward: click **LinkedIn's own "Apply" button first** (step 3) to trigger its tracking flow, and only use a third-party autofill tool *on the resulting external form*, not as the initial entry point from the LinkedIn panel. If that autofill button is the only visible option and LinkedIn's own Apply button isn't present or doesn't lead anywhere useful, flag this to the user rather than silently accepting the tracking gap.

## Skip taxonomy — final, radically simplified

This went through several rounds of correction (score-based skips → soft domain judgment → industry-mismatch nuance) before being cut down to one rule: **the only fit-judgment skip criterion is an explicit 7+ years YOE requirement stated in the JD.** If the JD states 7+ years, skip. If it states anything under 7, or states no YOE at all, apply.

Do not skip based on: title level (Principal/Staff/Director/VP/Head-of, Senior, etc.), out-of-scope-sounding function names, a named industry/domain vertical (fintech, payments, ads, security, healthcare, etc.), or a "this requires unfakeable expertise" judgment call. None of that decides fit anymore — YOE alone does. Never use a browser-extension match score (e.g. a "Jobright"-style fit score) as a factor either — it was never signal.

**Two things are not fit-judgment calls and still stand as hard rules regardless of YOE:**
- A posting from an agency/undisclosed employer, or one matching a known mass-job-posting scam pattern (the real hiring employer is never named): log/flag it, don't apply. This is about fraud/conflict risk, not fit.
- A role at an employer where the candidate already holds an active internal position (e.g. currently an intern there): skip/flag it as an internal-mobility situation, not a normal external application.

New-grad/future-start programs (a role the candidate can't actually start now) still don't make sense to apply to, but that's a logistics fact, not a fit judgment — flag it rather than silently skipping.

**Known technical wrinkle:** listings tagged as having off-platform response handling have intermittently failed to render their job description body on LinkedIn (confirmed via DOM inspection, sometimes persisting even after a full page reload), while native-Apply listings promoted directly by the hirer have rendered reliably. When a JD won't render even after a retry/reload, flag the listing as **blocked** — don't skip it (no real basis to) and don't apply blind (can't tailor honestly against unread content).

## Per-job sequence, in order, no steps skipped

1. **Check status first.** If the listing already shows "Applied" in the sidebar or job detail pane, skip it entirely — don't re-tailor, don't re-apply, don't touch it. Only act on "Viewed," "Saved," or no-status listings. (A prior session started re-processing jobs already marked Applied — don't repeat that.)

2. **Tailor the resume per-JD.** Follow [resume_ats_tailoring.md](resume_ats_tailoring.md) in full — real tailoring against that specific job's actual JD every time. Never reuse another company's already-tailored bullets as a template, even under time pressure or high volume. This is a hard line, not a style preference (see the incident described in that file).

3. **Apply via LinkedIn's own Apply button**, not by independently finding the company's direct ATS URL (Ashby/Greenhouse/Rippling/etc.) and applying there. See [job_apply_via_linkedin.md](job_apply_via_linkedin.md) for why — going direct breaks LinkedIn's own apply-tracking flow, which is what step 6 below depends on.

4. **Use a third-party "Apply with Autofill" browser tool when it's present on the listing** to speed through the external application form rather than manually typing every field. **Exact order matters, learned the hard way:** click Autofill *first*, then upload/replace the résumé field with the freshly tailored one from step 2 *after* Autofill finishes — if the tailored résumé is uploaded before running Autofill, Autofill silently overwrites it with whatever generic resume it has stored, discarding the tailored one. **Immediately before hitting submit, re-check the résumé field one more time** (visually confirm the filename shown is the tailored one, not the generic default) — this final check is mandatory even if the résumé was already swapped earlier in the flow, since some forms re-run autofill logic or reset on other field changes. Never trust a single earlier upload as sufficient.

5. **Close the redirect tab after submitting**, without resubmitting the application a second time.

6. **Go back to the LinkedIn listing and confirm "Yes" on the "Did you finish applying?" prompt.** This is mandatory, not optional — it's the only thing that actually marks the job "Applied" in the user's tracker; skipping it leaves the job stuck in "In progress" looking unapplied even though it was submitted. Only report something as "Applied" once this Yes click is actually confirmed — a successful ATS submission alone is not enough. If the first click doesn't visibly register (a toast notification can cover the Yes/No buttons right after clicking Apply), click Yes again and verify the listing now shows "Applied" before moving on. **If the "Did you finish applying?" prompt is not visible at all** on the original LinkedIn job posting tab: click that listing's LinkedIn Apply button once more (this reopens the application tab/flow), then switch away from that newly-opened tab back to the original LinkedIn posting tab — the "Did you finish applying?" prompt should now appear there, and click Yes. Don't guess at an alternative sequence — this exact fallback was specified explicitly after a session got this wrong.

7. **Keep a running tally** of every company applied to in the session, and flag anything unusual (no matching listing found, application form failed, a JD requirement that would require fabricating experience, autofill attaching the wrong resume) for the user to review — don't silently skip or guess on these, per [never_deviate_from_instructions.md](never_deviate_from_instructions.md).

## Sheet logging is strictly a fallback, never a substitute for applying

Log a job to an application-tracking sheet only when it is a genuine fit (real JD read, passes the skip taxonomy) **and** its application is not supported by the ATS platforms the agent can actually complete (Ashby, Greenhouse, and Rippling are all directly supported, alongside LinkedIn's own Apply flow) — e.g. a company's own career-site form that's inaccessible/unreadable, or an "Apply on company website" link that goes nowhere the tools can reach. If the ATS is actually reachable for a genuine fit, apply through it — never log it instead just because logging is faster. This was corrected explicitly after a session logged a genuine-fit job hosted on Rippling to the sheet instead of applying, before the supported-ATS list was expanded to explicitly include Rippling.

## Never report a pass as complete until every results page has been checked

Click through every page number shown at the bottom of the results list (1, 2, 3, ... Next) before giving any summary of applied/skipped/logged counts for a pass. A pass is not done just because the tight recent-postings window has been scrolled through once at the top of page 1 — the same window's results are paginated, and later pages can and do contain genuine unevaluated fits. This was learned the hard way after a session reported a pass complete having checked only page 1.

## A listing already tagged "Applied" gets skipped immediately during browsing

No re-evaluation, no re-reading its JD, no re-tailoring. This is the same rule as step 1 above, restated because it applies at the browsing stage, before a listing is ever opened for evaluation.

## Between 30-minute-window passes, also scan the past month

If a recurring pass is scoped to a tight "last N minutes" search window, strong-fit roles can scroll past before a pass ever sees them. When a recurring pass turns up little or nothing new in the tight window, use the same pass to also run a broader search (same keywords/location, but a much wider posting-age filter) and apply the full skip taxonomy to what surfaces there. Because a wide window will re-show the same listings pass after pass, dedupe primarily off each listing's own LinkedIn status badge ("Applied," "Viewed," "Saved" with a date) rather than trying to re-derive history from a local log alone — a listing already marked "Applied" is done regardless of which pass touched it. Log genuine-fit decisions from this broader scan the same way as the regular pass, clearly marked as a past-month scan so it's not confused with the tight window's own count.

## LinkedIn's own status badge is not fully trustworthy for past-month listings — always cross-check email before applying

Discovered during a past-month scan: LinkedIn showed two roles as "Saved"/no-status (implying not yet applied), but an email search for confirmation messages (ATS-sender domains, or just the company name + "application") turned up genuine "Thank you for applying" receipts — meaning both had already been fully applied to. Existing tailored-resume files for a company are another tell that a prior pass touched it, but check email to confirm whether that prior touch actually completed (vs. being abandoned, e.g. at a file-upload blocker) rather than assuming either way. Before applying to anything surfaced by a broader (non-tight-window) scan, search email for "{Company} application" or "thank you for applying" and treat a genuine confirmation email as authoritative over LinkedIn's displayed status badge.

## When the original LinkedIn posting can't be found

(e.g. it expired, or there are multiple similarly-titled listings from the same company and it's unclear which one was actually applied to): check email for the "Thank you for applying to {Company}" confirmation first — some ATS confirmation emails name the exact role in the body, which disambiguates immediately. If the email is generic (no role name or link), fall back to matching on which listing has clear signs of deliberate engagement (e.g. "Saved" status, posting recency consistent with the application batch) rather than guessing — and if still ambiguous, ask the user rather than picking silently.
