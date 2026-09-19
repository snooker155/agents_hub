Use this agent to design and maintain iteration loops (a flow repeated until it is good enough).

Good fits:
- "Keep re-running the article flow until every claim has a source, at most 5 passes"
- "Add a dedicated reviewer to this loop instead of letting the writer judge itself"
- "This loop always runs to its cap — what is wrong with its criterion?"
- "Cap the research loop at $2 and stop it once it scores 85"

Also good fits (the run tools refuse until you approve the cost):
- "Run it on the pricing article"
- "What did the last run score on each pass?"
- "Stop it, it is not converging"

Poor fits:
- Reading each iteration's full output — that is the Loops page
- Building the flow the loop repeats — that is the Flow Creator's job

How to invoke:
- Name the flow (or describe the work) and say what "good enough" means; the agent writes the criterion, picks the evaluator and sets the ceilings
- For changes, name the loop (or its id) and the exact edit
- The agent reports the resulting loop_id and the conditions under which the loop will stop
