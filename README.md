# LinkedIn Autoapply — AI Agent Skills

A small set of process docs ("skills") for running an AI coding agent (e.g. Claude Code) as an unattended LinkedIn job-autoapply assistant. These were written after real incidents — each rule exists because a shortcut was taken once and caused real harm (a mistracked application, a batch of under-tailored resumes that had already been irreversibly submitted). Point a fresh agent session at [autoapply_process.md](autoapply_process.md) first; it links out to the other two.

- [autoapply_process.md](autoapply_process.md) — START HERE for any autoapply run: exact per-job sequence (skip-if-applied, tailor, apply via LinkedIn, confirm-yes).
- [resume_ats_tailoring.md](resume_ats_tailoring.md) — reusable process for tailoring a resume per-JD for ATS scoring: formatting rules, keyword-insertion technique. Hard rule at the top: never template/reuse content across companies.
- [job_apply_via_linkedin.md](job_apply_via_linkedin.md) — when auto-applying to LinkedIn-sourced jobs, click LinkedIn's own Apply button, don't bypass straight to the underlying ATS.
- [never_deviate_from_instructions.md](never_deviate_from_instructions.md) — the general standing rule behind all of the above: no unilateral process/quality/scope decisions; surface assumptions and tradeoffs before acting, not after.

## Supervision layer

`autoapply_supervision.md` describes the mechanical enforcement installed around the agent (PreToolUse/Stop hooks, resume validator, per-application ledger, HALT flag, scheduled auditor). The scripts live in `supervisor/` in this repo.
