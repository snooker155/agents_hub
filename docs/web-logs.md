# Web requests

Every `web_search` and `fetch_url` call an agent made, recorded with the text it
was actually handed. The **Web Requests** page is where you read them.

## The two questions it answers

**What did the tool actually give the agent?** Extraction is lossy on purpose:
HTML is stripped, JSON re-serialised, long text truncated. When an agent reasons
from a page and gets it wrong, the question is always what it read, not what the
page says in a browser. The entry holds the exact returned text, the status,
content type, redirect chain and timing.

**Was the response trying something?** Retrieved content is untrusted, and the
untrusted-content envelope around it is a label, not a detector. Each response
is scanned for the patterns worth a human look: injection phrasing, instructions
hidden from human readers but legible to a model, attempts to close the envelope
early, credential-shaped strings, and exfiltration-shaped requests. Flags carry
a severity, and text hidden from a reader is scored higher than the same text in
plain view.

## Refusals are logged too

An SSRF block, a domain-policy denial, or search results withheld because their
host is on the deny list all appear here. A tool that returned nothing is not
the same as a tool that was never called, and the log is where the difference is
visible.

## Reading a flag correctly

Flags are **signals, not verdicts**, and deliberately eager: an article *about*
prompt injection is flagged exactly like a page attempting it. Nothing here
blocks a call. Blocking lives elsewhere: the SSRF guard, the domain policy and
the [capability model](tools-and-capabilities.md).

## From an agent

`web_log_recent` gives the Service Agent the same list, which is how "what did
this run fetch" gets answered without leaving the conversation. Because a log
entry holds whatever the fetched page said, reading one counts as ingesting
untrusted content.

## Configuration

Recording is on by default (`WEB_LOG_ENABLED`), capped by entry count
(`WEB_LOG_MAX_ENTRIES`) and by stored response size (`WEB_LOG_BODY_CHARS`), in a
capped JSONL file under the state root.

## Gotchas

- The list omits response bodies; open an entry to read what the agent got.
- Turning the log off removes the only record of what retrieved content said.
- A high-severity flag on a page an agent summarised is worth reading before
  trusting the summary.

Related: [tools-and-capabilities](tools-and-capabilities.md), [service-health](service-health.md), [evals](evals.md).
