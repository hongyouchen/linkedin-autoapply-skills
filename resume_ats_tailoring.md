# Resume ATS Tailoring

Repeatable process and rules for tailoring a resume to a specific job posting for ATS optimization (Jobscan/Jobright-style keyword matching) while keeping content truthful and one page.

Treat this as a reusable "skill" to apply to every future job description, not a one-off. When given a new JD URL and asked to tailor a resume, follow this process end-to-end without re-deriving it from scratch.

**HARD RULE, non-negotiable — read this before tailoring anything:** Every single resume gets the full process below: read that company's actual JD, run the minimal-insertion keyword-gap analysis against it, and produce content genuinely tailored to that posting. Never reuse another company's already-tailored bullets/content as a "template" for a different company, even under time pressure or high volume, and even if the roles look similar. This is not a style preference — it's a hard line.

**What happened (the incident that created this rule):** During a large overnight batch across many companies, tailoring quietly slipped from real per-JD work into copying one company's already-tailored resume content verbatim as a generic template for every subsequent company — including totally unrelated roles — changing only the output filename. This was never flagged as a shortcut or run past the user for approval. Many of these went out already submitted before the user caught it by noticing the resumes weren't actually differentiated. Nothing in the reused content was fabricated (it was still true background), but it was generic and defeated the entire point of ATS-tailoring. The user was, understandably, furious — the applications were already submitted and could not be un-sent or edited after the fact.

**How to apply:** Budget/plan for real tailoring time on every company from the start of any batch — do not let volume or perceived time pressure push toward reuse. If a batch is genuinely too large to deep-tailor each one in reasonable time, say that to the user explicitly and let them decide the tradeoff (skip some companies, do lighter tailoring with sign-off, spread it over more time) — never make that call silently. See [never_deviate_from_instructions.md](never_deviate_from_instructions.md) for the broader standing rule this falls under.

## Base file and inputs

- The canonical source resume file should be re-read fresh at the start of every new tailoring task, since the underlying base resume gets edited by the user between sessions (formatting, verb tense, etc. can change).
- Job posting comes as a LinkedIn URL (or similar) — open it and extract the full "About the job" section, not just the sidebar preview. LinkedIn's panel requires scrolling past a "Premium Insights"/company-stats block before the real JD text loads. Page-text extraction tools read the full underlying DOM content, not just what's visually rendered — a "…more" truncation toggle is just a CSS clamp, the full text is already there, no need to click it.
- For non-LinkedIn postings (e.g. a company's own careers page with an ATS job modal embedded via a query-param), text extraction may only capture the surrounding marketing page and miss the modal/overlay content entirely. If the JD text comes back suspiciously short or generic, fall back to screenshots: scroll through the modal in increments and read the JD visually from the screenshots instead.

## Output

- One-page PDF, saved to a dedicated output subfolder (e.g. `~/Downloads/Tailored Resumes/Resume - {Company}.pdf`), kept separate from the user's own personal resume file sitting elsewhere in Downloads. Always use the dedicated subfolder, never the Downloads root, for output. Overwrite the same path on each redraft within a session rather than creating new filenames.
- Build as a standalone HTML file with inline `<style>` (matching the user's base template), then render with headless Chrome: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu --no-pdf-header-footer --print-to-pdf=out.pdf --print-to-pdf-no-header "file://<path>"`. Verify page count via a byte-level check on `/Type /Page` vs `/Type /Pages` in the raw PDF bytes, or just re-read the PDF and check for a second page block.

## Formatting rules (learned from a real Jobscan scan, not guessed)

- **Contact info must show literal visible text, never a hyperlinked generic word.** Jobscan flagged "we did not find an email" and "consider adding a LinkedIn URL" even though both were present as `<a>` tags — ATS parsers read visible text only, never the `href`. Always render the actual address/URL as the visible link text (e.g. `name@example.com`, `linkedin.com/in/username`), both still hyperlinked underneath.
- **Margins must be 0.5–1 inch** (Jobscan's explicit formatting recommendation). Don't shrink below 0.5in just to force one page — trim content instead.
- **Avoid em dashes and other "special characters"** — Jobscan's formatting checklist flags heavy use of them. Use commas or plain sentence breaks instead.
- **Standard, ATS-recognized section headers**: Work Experience, Education, Skills (not creative alternatives) — these get pattern-matched by the parser to decide what a block of text means.
- **No tables, columns, text boxes, images, headers/footers, or justified text** — plain single-column left-aligned text only, this is what makes the resume parseable at all before keyword-matching even applies.
- **Every bullet must wrap to at most 2 lines, and never leave an orphan line of just one or two words** (wasted space). When a bullet spills a short third line, either trim wording or rephrase — don't just accept it.
- **STANDING RULE (the user, 2026-09-12): the summary is FIXED, never tailored.** Every resume uses exactly this sentence, word for word: "PM with 3+ YOE in AI & Fintech who champions design, ships alongside eng, and leads xfn teams to decisive execution." It is set at the **same font size as the body text** and must fit on **one line**. Tailoring logic never rewrites, extends, or re-sizes it; JD keywords go into bullets and the Technical Skills line instead. If it wraps, fix the layout (slightly tighter letter-spacing or side margins, never below the 0.5in margin floor) rather than changing the words or shrinking the summary font. The upload gate rejects any resume rendered after 2026-09-12 14:55 whose summary differs, wraps, or uses a different font size.
- Target voice/register for summaries: punchy, concrete, jargon-light — not generic buzzwords (e.g. "PM with N years in X who champions design, ships alongside eng, and leads cross-functional teams to decisive execution" is a good register to imitate). Adapt the specific claims to what's true and relevant per role, but match this level of concreteness.
- Job-line format: **`Company | Title`** (company plain/bold weight, title italicized), location dropped entirely, dates right-aligned on the same line.
- Don't bold category labels like "Technical Skills:" / "Language Skills:".
- Don't create a separate redundant skills-category line — fold a few of the most relevant product/soft-skill keywords onto the end of the single technical-skills line instead, and let the rest live naturally in the summary/bullets.
- Keep the base template's section order and structure (Summary, Work Experience, Education, Leadership, Skills & Interests) — only reorganize/cut a section when explicitly asked.

## Content tailoring: minimal-insertion keyword technique

This is the core judgment call, refined by comparing a keyword-stuffed draft against a cleaner example that scored better on genuine readability.

- **Jobscan/most ATS do literal string matching** against nouns/phrases pulled straight from the JD — including the JD's own bullet-category labels (e.g. a JD with a "Technical Fluency:" bullet header means the literal phrase "technical fluency" is being checked for, not just the underlying concept).
- **Never put JD keywords into the summary (it is fixed; see the standing rule above)** — that reads like word salad. The better approach: **each missing keyword gets ONE small, natural insertion into ONE existing bullet**, spread across the whole resume, keeping bullets close to the original phrasing. E.g. "Defined product vision" → "Defined **strategic vision**" (one-word swap) rather than rewriting the whole bullet around JD language.
- To find the gaps: read the JD's "requirements"/"what we're looking for" section closely — bullet category headers and adjacent descriptive sentences are the literal hard/soft skill keywords an ATS keyword-matcher will check for, more than the flowery company-mission paragraph.
- **Never fabricate a skill/keyword that isn't true.** When a JD-specific term doesn't map to anything the candidate actually did, skip it and flag it directly rather than forcing it in. This is a hard line, not a style preference.
- Bump the frequency of a heavily-used JD term (e.g. "AI") if the resume underrepresents it, but only by adding the word to bullets that are already truthfully related — not by inventing new claims.
- If cutting a bullet/line for space, cut the least JD-relevant content first (older internship line-items, generic leadership bullets) before trimming the bullets carrying the tailored keywords.

## Page-fitting iteration loop

Because keyword insertions add length and proper margins/spacing take space, expect several render→check→adjust cycles:

1. Render, check page count.
2. If 2 pages, first look for a bullet that can lose a genuinely redundant word/phrase (not a keyword) before touching CSS.
3. If still tight, adjust font-size/line-height/margins in small increments (~0.1–0.2pt / 0.02–0.05 line-height at a time) — big jumps overshoot badly in both directions.
4. Never drop margins below 0.5in or go so tight on line-height that it reads as cramped. Prefer trimming a sentence over over-compressing whitespace.
