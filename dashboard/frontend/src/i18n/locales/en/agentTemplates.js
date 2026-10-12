export default {
  optionLabel: 'Start from a template',
  optionDesc: 'Pick a ready agent: an SRE agent, an AI teammate, a support triage agent, a researcher and more. Rename it and adjust it before creating.',
  stepTemplate: 'Template',
  pickHint: 'Each template comes with a tuned prompt, a safe tool set and a default outcome rubric. You can change all of it before creating.',
  loading: 'Loading templates…',
  loadFailed: 'Could not load the templates',
  needs: 'Needs',
  worksWith: 'Works with',
  connected: 'connected',
  notConnected: 'not connected',
  tryAsking: 'Try asking',
  adjust: 'Adjust the prompt and tools',
  prompt: 'Prompt',
  tools: 'Tools',
  template: 'Template',
  rubric: 'Default outcome',
  created: '{{name}} created from the template',
  createFailed: 'Could not create the agent',
  categories: {
    Engineering: 'Engineering',
    Productivity: 'Productivity',
    Data: 'Data',
    Finance: 'Finance',
    Legal: 'Legal',
    Support: 'Support',
    Research: 'Research',
    HR: 'HR',
    Marketing: 'Marketing',
    Sales: 'Sales',
  },
  items: {
    sre_agent: {
      name: 'SRE agent',
      description: 'Investigates an incident from logs, manifests and runbooks, names the root cause with evidence, and proposes the fix as a pull request a person approves.',
    },
    ai_teammate: {
      name: 'AI teammate',
      description: 'Joins the team chat, answers from the team\'s own docs and notes with sources, picks up small tasks and remembers decisions.',
    },
    github_issue_agent: {
      name: 'GitHub issue agent',
      description: 'Takes an issue synced from GitHub or GitLab, finds the cause in the code, writes a focused fix with a test and opens a pull request.',
    },
    data_agent: {
      name: 'Data agent',
      description: 'Answers business questions from the connected databases and spreadsheets, shows the query behind every number, and draws charts.',
    },
    invoice_agent: {
      name: 'Invoice agent',
      description: 'Extracts vendor, dates, line items, tax and totals from invoices, checks arithmetic, duplicates and purchase orders, and prepares records for approval.',
    },
    contract_review_agent: {
      name: 'Contract review agent',
      description: 'Reviews a contract against the team\'s playbook: key terms, risky or missing clauses by severity, proposed redlines and a short memo.',
    },
    support_triage_agent: {
      name: 'Support triage agent',
      description: 'Classifies tickets by area, urgency and sentiment, finds the answer in the knowledge base, drafts a reply for a person to approve and turns bugs into tasks.',
    },
    research_agent: {
      name: 'Research agent',
      description: 'Researches a question on the web and in your files and memory, compares the sources and writes a cited brief with confidence levels and open questions.',
    },
    meeting_notes_agent: {
      name: 'Meeting notes agent',
      description: 'Turns a transcript or rough notes into a summary, decisions, action items with owners and dates and open questions, and creates the tasks.',
    },
    recruiting_screener_agent: {
      name: 'Recruiting screener',
      description: 'Screens CVs against a role\'s must-haves and nice-to-haves with evidence, flags gaps, ignores protected characteristics and writes a shortlist with interview questions.',
    },
    content_writer_agent: {
      name: 'Content writer',
      description: 'Drafts blog posts, newsletters and social posts in your brand voice from a brief: outline first, facts checked against your sources, headline variants.',
    },
    lead_research_agent: {
      name: 'Lead research agent',
      description: 'Researches an account or person from public sources and your CRM, writes an account brief and a personalised first outreach draft. Never sends.',
    },
  },
};
