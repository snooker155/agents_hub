Use this agent to find out how strong the CV is for a kind of role — and what to change.

Good fits:
- "Score my CV for the Applied Scientist profile"
- "Score my CV against `deepmind__research-scientist__202608`"
- "Here's a role description [paste] — how do I measure up?"
- "What are the top three changes that would raise my score most?"
- "Build a role profile for AI Engineer from the postings we've collected"
- "Re-score me against the same profile — has last month's rewrite helped?"

Poor fits:
- "Rewrite my CV for this job" / "Write the cover letter" → Application Tailor
- "Should I apply to this one?" → Application Tailor (its `fit_verdict` answers that)
- "Find more roles like this" → Job Scout

How to invoke:
- Name a role profile id, a `position_id`, or paste the role description
- Reports land in `evaluations/<spec_id>__<date>.md`, one per evaluation, plus a view on the Views
  page; earlier reports are never overwritten, so score history stays readable
- Answer its "ask the candidate" questions and it will fold them into the next evaluation
