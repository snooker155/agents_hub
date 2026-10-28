Use this agent for everything that runs later or on a schedule. It owns the **Plan** and **Deployments** pages, and the main agent hands it every scheduling request.

Good fits:
- "Remind me tomorrow at 9 to check the release"
- "Every weekday at 9:00 Berlin time, have the analyst summarise yesterday's sales"
- "Every Monday create a task for the analyst to update the forecast"
- "Run the nightly review flow every night at 2:00 and pause it after two failures"
- "What is scheduled, and did last night's deployment run?"

Poor fits:
- Tracker tasks (creating, splitting, ordering them): that is the Task Manager
- Doing the work itself: the job's agent, flow or loop does it

How to invoke:
- Say what should happen and when, in your own words; give a timezone when it matters.
- A loop is billed for every iteration, so it shows you the loop, the first run and the cost of one run and waits for your yes.
- An agent run makes no task: its answer is in a new conversation in the Chat list. Ask for a task when you want one on the tracker.
- It confirms each job with what it runs and when it runs next.
