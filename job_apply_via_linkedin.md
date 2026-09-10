# Apply Via LinkedIn, Not Direct-to-ATS

When auto-applying to jobs sourced via LinkedIn, always click LinkedIn's own Apply button to reach the underlying ATS (Ashby, Greenhouse, etc.) rather than independently searching for and navigating straight to the company's ATS page.

Never bypass LinkedIn and go straight to a company's ATS (e.g. a direct Ashby/Greenhouse URL) when the job was sourced from a LinkedIn search, even if a web search can find the direct ATS URL faster.

**What happened:** During a large batch of ATS auto-applications, company names were found via a LinkedIn "past month PM jobs" search, then instead of clicking each posting's LinkedIn Apply button, a subagent was spawned to web-search each company for its direct ATS URL and apply there instead. This was faster to execute but skipped LinkedIn's own apply-tracking flow ("Did you finish applying?"), so none of those applications got marked as Applied in the user's LinkedIn job tracker. The user was frustrated that this shortcut was taken without being asked first.

**Why:** The user explicitly wants every application tracked in their LinkedIn job tracker so they don't waste time re-discovering or re-applying to jobs already covered. Clicking LinkedIn's Apply button and completing the application there (even though it redirects to Ashby/Greenhouse/etc. under the hood) is what triggers LinkedIn to record the application and later show the "Did you finish applying? Yes/No" confirmation prompt on that exact posting.

**How to apply:** Whenever a job to auto-apply for was discovered via LinkedIn (search results, "past month" browsing, etc.), navigate to the LinkedIn posting first and click its Apply button to open the underlying ATS application form, rather than searching for or constructing the ATS URL independently. Complete the application there, then return to the LinkedIn posting and confirm "Yes" on the "Did you finish applying?" prompt if it appears. Only fall back to going directly to the ATS site when the original LinkedIn posting URL is genuinely unrecoverable (e.g., lost to a context compaction) — treat that as an exception to flag, not a shortcut to take by default. Once corrected on this, do not revert to the direct-ATS shortcut for convenience or token savings — this is a hard rule, not a cost/benefit tradeoff to re-litigate each time.

See also [resume_ats_tailoring.md](resume_ats_tailoring.md) for the broader auto-apply workflow this fits into, and [never_deviate_from_instructions.md](never_deviate_from_instructions.md) for the general standing rule this incident falls under.
