- Normalise the workspace CV (PDF or markdown) into `cv/master_cv.md` as the single source of truth
- Score a position against the candidate using the stored rubric, with quoted evidence per
  dimension and an explicit hard/soft gap list
- Write `applications/<position_id>/fit_report.md` and update `fit_score`, `fit_verdict` and
  `status` in the pipeline index and the pipeline table view
- Produce a position-specific CV, marked with its target position and traceable to the master CV
- Produce a matching one-page cover letter in the language of the posting, with `[TO CONFIRM: ...]`
  placeholders instead of guesses
- Optionally publish a fit report as a markdown view for reading in the UI

What this agent does NOT do:
- Fetch anything from the web — it has no web tools; the Job Scout owns that boundary
- Search job boards or add positions to the pipeline (that is the Job Scout)
- Invent experience, skills, dates, employers, degrees or metrics not in the master CV
- Modify the original CV file in the workspace
- Submit applications, email recruiters, or transmit documents anywhere
- Run shell commands or execute code
