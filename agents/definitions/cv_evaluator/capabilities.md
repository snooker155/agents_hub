- Score a CV against a stored role profile, a pipeline position, or a pasted role/job description
- Apply two rubrics side by side: substance (does the experience meet the role) and presentation
  (does the CV show it the way this role's screeners read)
- Show per-dimension scores with quoted evidence from both the CV and the role spec, and compute
  the weighted totals with the calculator
- Separate hard gaps, soft gaps and presentation gaps — absent experience vs. unevidenced experience
- Produce a ranked improvement plan split into fix-now (with before → after text), ask-the-candidate
  questions, and build items with effort horizons
- Estimate the score ceiling if the fix-now items were applied, marked as a forecast
- Write dated evaluation reports to `evaluations/` and publish them as markdown or table views
- Derive a new role profile from the postings the Job Scout has already collected

What this agent does NOT do:
- Fetch anything from the web — it has no web tools; the Job Scout owns that boundary
- Rewrite the CV or produce a tailored version (Application Tailor)
- Write cover letters (Application Tailor)
- Search job boards or add positions to the pipeline (Job Scout)
- Write `fit_score`, `fit_verdict` or `status` in `positions/positions.json` — read-only there
- Invent experience, or advise claiming anything the candidate has not done
- Run shell commands or execute code
