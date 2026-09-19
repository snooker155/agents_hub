Use this agent once positions exist in the pipeline and you want to know which are worth applying
to — and to get the application documents for the ones that are.

Good fits:
- "Score every new position in the pipeline against my CV"
- "How well do I fit `deepmind__research-scientist__202608`?"
- "Tailor my CV and write a cover letter for the top three"
- "Re-score this one, the ad was updated"

Poor fits:
- "Find me more Applied Scientist roles" → Job Scout
- "Send the application" — it writes documents, it never submits them

How to invoke:
- Name a `position_id`, a shortlist, or just ask it to review everything with `status: "new"`
- Output lands in `applications/<position_id>/`: `fit_report.md`, `CV__<Company>__<Role>.md`,
  `cover_letter__<Company>__<Role>.md`, and the updated pipeline view
- Read the `<!-- tailoring notes -->` block at the end of a tailored CV to see what was promoted,
  cut, and left for the cover letter to cover
