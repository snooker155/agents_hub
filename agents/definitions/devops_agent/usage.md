Use this agent when an application is ready and needs deployment artefacts written.

Good fits:
- "Write a Dockerfile and GitHub Actions pipeline for this service"
- "Create Terraform IaC for the infra in the architecture doc"
- "Produce a deployment README with environment templates"

Poor fits:
- Implementing the application itself
- Running the deployment — this agent only authors the artefacts

How to invoke:
- Ensure the application source and architecture docs are present
- The agent writes everything under `ops/`; review and apply manually
