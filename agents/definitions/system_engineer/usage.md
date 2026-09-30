Runs as the second node ("patch") of the `system_maintenance` flow inside the system loop,
after the System Doctor has filed `[system]` tasks.

Good fits:
- A `[system]` task marked `code: yes` with evidence and a suggested fix

Poor fits:
- Anything that must reach the real repository or a remote: a human fetches and pushes
- Feature work: use a project workspace and the Software Engineering Agent
