Use this agent to find and track open positions in the `jobs` workspace.

Good fits:
- "Find Applied Scientist roles in Berlin and remote-EU, posted in the last two weeks"
- "Re-run the standing search and show me what's new"
- "Add Helsing, Aleph Alpha and Merantix career pages to the sources"
- "Which sources failed last run?"

Poor fits:
- "Is this role a good match for my CV?" → Application Tailor
- "Write a cover letter for this one" → Application Tailor
- Applying, scheduling interviews, or messaging recruiters — it never transacts

How to invoke:
- State what changed vs. the standing criteria; leave it out to run the defaults
- Results land in `positions/positions.json` plus one `positions/<position_id>.md` per posting,
  and in the "Job Pipeline" table view on the Views page
- Requires `WEB_SEARCH_PROVIDER` + `WEB_SEARCH_API_KEY` for `web_search`; the JSON/HTML sources
  in memory work with `fetch_url` alone
