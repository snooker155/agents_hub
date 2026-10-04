Use this agent when the answer is a number, a table, or a trend that a connected database,
spreadsheet, or workspace file already holds.

Good fits:
- "How many orders came in last month, broken down by region?"
- "What's the average of this column across these three files?"
- "Is there anything odd about this dataset before we build on it?"
- "Pull this out and chart it": it queries the data, then hands the chart itself to the
  Visualizer

Poor fits:
- A question the workspace's data cannot answer: it will say so rather than estimate
- Building or editing the view itself. Ask the Visualizer directly, or let this agent hand off
  once it has the figures
- Writing up a narrative report around the numbers: that's the Writer's job, fed by this
  agent's figures

How to invoke:
- State the question and name the source if you already know it (which connection, which file);
  otherwise it will look for where the answer lives
- Expect the query or calculation back alongside the answer, not just the number
