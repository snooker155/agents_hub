Use this agent after the BRD exists, to translate requirements into a buildable design.

Good fits:
- "Design the system based on docs/BRD.md"
- "Update the architecture to support requirement N"

Poor fits:
- When the BRD is missing or incomplete — run the BA Agent first
- Implementation work — hand to the Developer Agent once the design is ready

How to invoke:
- Ensure `docs/BRD.md` exists in the workspace
- The agent will populate the `docs/SD_*` files; make sure they don't already conflict with manual work
