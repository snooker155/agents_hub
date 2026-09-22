# Contributing to Agents Hub

This guide covers the main contribution patterns for backend, frontend, CLI and tests. For the overall structure, see [ARCHITECTURE.md](./ARCHITECTURE.md).

## Setting up

Install Python 3.11+, Node.js 22+, and Git. Then run the installer:

```bash
git clone <your-fork> agents_hub
cd agents_hub
./install.sh                    # venv, service, dashboard, the `ah` command
```

See [SETUP.md](./SETUP.md) for the step by step walkthrough with all provider options.

**Run the backend:**

```bash
source .venv/bin/activate       # or on Windows: .venv\Scripts\activate
python -m uvicorn dashboard.backend.main:app --reload
```

The API is at `http://localhost:8000`. Docs are at `http://localhost:8000/docs`.

**Run the frontend:**

```bash
cd dashboard/frontend
npm install                     # once per machine
npm run dev
```

The dashboard is at `http://localhost:5173`.

**Run the CLI:**

```bash
source .venv/bin/activate
ah up                           # or run commands directly: ah agent list
```

**Run tests:**

```bash
python -m pytest tests/ -q      # backend + CLI tests
cd dashboard/frontend
npx vitest run                  # frontend tests
```

**Run linters:**

```bash
ruff check .                    # backend code
cd dashboard/frontend
npx eslint .                    # frontend code
```

## Adding an agent tool

A tool is a LangChain `@tool` function in `tools/`. It shows up in four places, and the tests check that they agree: the function, the catalog entry the dashboard reads, the capability grant, and the agent's tool list. Read `tools/calculator.py` for the smallest complete example and `tools/capabilities.py` for the security model.

**1. Write the tool in `tools/`**

Declare the tool with a Pydantic `args_schema` so arguments are typed and self describing:

```python
# tools/calculator.py (excerpt)
from langchain_core.tools import tool
from pydantic import BaseModel, Field

class CalculatorInput(BaseModel):
    expression: str = Field(..., description="Expression to evaluate")

@tool("calculator", args_schema=CalculatorInput)
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression."""
    ...
```

Return a JSON string or plain text. Raise nothing at the agent: return an error message the model can read.

**2. Make it selectable in `agents/agent_factory.py`**

An agent receives only the tools its record names. The factory builds the `available` list near `agents/agent_factory.py:556` from every tool module and then picks by name, so import your tool there and add it to that list. Without this step the id is silently ignored.

**3. Tool reaches the catalog automatically**

The tool catalog is generated from tools themselves, not hand-typed in parallel. The `spec_from_tool()` function in `tools/registry.py` reads each tool's name, docstring, and `args_schema` to build a `ToolSpec`, so the catalog stays accurate without duplication. A small table in `_CATALOG_OVERRIDES` carries only the few things a schema cannot express: friendlier display names and workspace requirements for tools whose schema has no field for them.

**4. Classify it in `tools/capabilities.py`**

Add the tool id to `CAPABILITY_GRANTS` with the capabilities it has (`INGESTS_UNTRUSTED`, `READS_PRIVATE`, `CAN_EXFILTRATE`), or to `REVIEWED_NO_GRANT` when you have checked that it has none. An unknown id grants nothing but is logged as unreviewed. `BLOCKED_COMBINATIONS` is what stops an agent from holding all three at once; `agents/registry.py:add_agent` enforces it at save time.

**5. Give it to an agent**

Seed agents live in `bootstrap/agents.json` with their prompts in `agents/definitions/<id>/`. Add the id to the `tools` list of the agent that should have it, or pick it in the dashboard's agent editor.

**6. Test it**

`tests/test_capabilities.py` covers grants and blocked combinations, `tests/test_entity_tools.py` covers tools that read and write hub entities. Add a direct unit test of the function itself next to those.

**Entity management tools**

Six modules own entity-specific management tools (flow, loop, team, scenario, world, project). Rather than each hand-rolling try/except, json_ok/json_err wrapping, and the `@tool` decorator, they use the factory in `tools/_crud.py` to avoid duplication:

```python
# tools/flow_management.py (excerpt)
from tools._crud import EntityToolSpec, ToolDef, build_entity_tools

def _create_flow(name: str, blueprint: str) -> str:
    """Create a flow from a name and blueprint."""
    ...

_SPEC = EntityToolSpec(
    singular="flow",
    plural="flows",
    create=ToolDef("create_flow_tool", CreateFlowInput, _create_flow, "Failed to create flow"),
    get=ToolDef("get_flow_tool", GetFlowInput, _get_flow, "Failed to get flow"),
    modify=ToolDef("modify_flow_tool", ModifyFlowInput, _modify_flow, "Failed to modify flow"),
)
_TOOLS = tools_by_id(build_entity_tools(_SPEC))
create_flow_tool = _TOOLS["create_flow_tool"]
```

The factory provides the error handling, json_ok/json_err wrapping, and the `@tool` decorator. Handlers are plain functions with the body and docstring the old decorated function had, minus the outer try/except the factory now provides.

## Adding an API route

Routes live in `dashboard/backend/routes/` and are mounted in `dashboard/backend/main.py`. See `routes/health.py` for a small complete example.

**1. Create a router in `dashboard/backend/routes/`**

```python
# dashboard/backend/routes/health.py (excerpt)
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["health"])

@router.get("/health")
async def health(request: Request):
    return snapshot(request.app.state)
```

**2. Mount it in `dashboard/backend/main.py`**

Add an import at the top:

```python
from routes import health
```

Then include the router:

```python
app.include_router(health.router)
```

**3. Test it**

Use FastAPI's TestClient in `tests/`. Create a test file or extend an existing one:

```python
from fastapi.testclient import TestClient
from dashboard.backend.main import app

client = TestClient(app)
response = client.get("/api/health")
assert response.status_code == 200
```

## Adding a dashboard page

Pages are React components in `dashboard/frontend/src/pages/` and are routed in `dashboard/frontend/src/App.jsx`. Pages are lazy-loaded on demand so bundle size stays manageable, and each route gets its own error boundary.

**1. Create a page component**

```jsx
// dashboard/frontend/src/pages/ExamplePage.jsx
import { useI18n } from '../i18n';

export default function ExamplePage() {
  const { t } = useI18n();
  return <div>{t('examplePage.title')}</div>;
}
```

**2. Add a lazy route in `dashboard/frontend/src/App.jsx`**

Declare the page as a lazy import and wrap it with `guard()`, which adds an error boundary and suspense loader per route:

```jsx
const ExamplePage = lazy(() => import('./pages/ExamplePage'));
// ... in Routes:
<Route path="/example" element={guard(<ExamplePage />)} />
```

The `guard()` function handles `RouteBoundary` with a per-route error boundary, keyed on pathname so a caught error resets on navigation.

**3. Add the route title in `src/components/routeTitles.js`**

`ROUTE_TITLES` is an ordered list of `{match, titleKey}` entries; the first regex that matches the pathname names the page for the document title and the page chat header. The title itself is a locale string under `layout.titles`:

```javascript
// src/components/routeTitles.js (excerpt)
export const ROUTE_TITLES = [
  { match: /^\/example$/, titleKey: 'layout.titles.example' },
  ...
];
```

**4. Add a sidebar entry**

Edit the navigation component that reads from locale `nav` namespace. The sidebar menu is built from `dashboard/frontend/src/i18n/locales/en/nav.js` and other language files.

**5. Never hardcode colors**

Colors come from generated `src/theme.css`. To add a new brand color:

1. Edit the brand ramp in `dashboard/frontend/scripts/gen-theme.mjs`
2. Run `node scripts/gen-theme.mjs`
3. Use Tailwind utilities in your component; they will reference the generated colors

Read `scripts/gen-theme.mjs` to see how the deep navy brand replaces indigo/violet/purple/fuchsia.

## Adding a locale string

Locale files live in `dashboard/frontend/src/i18n/locales/<lang>/`. The system auto-discovers them by filename and language code.

**1. Pick a namespace or create one**

Namespaces are files like `nav.js`, `common.js`, `agentDetails.js`. They organize related strings by page or feature. To add a new page, create `dashboard/frontend/src/i18n/locales/en/examplePage.js`:

```javascript
// dashboard/frontend/src/i18n/locales/en/examplePage.js
export default {
  title: 'Example Page',
  description: 'This is an example',
  actions: {
    create: 'Create',
    delete: 'Delete',
  },
};
```

**2. Add the same keys to other languages**

Create `dashboard/frontend/src/i18n/locales/ru/examplePage.js` and `dashboard/frontend/src/i18n/locales/de/examplePage.js` with translations for the same keys.

**3. Use the string in a component**

```jsx
import { useTranslation } from '../i18n';

export default function ExamplePage() {
  const { t } = useTranslation();
  return <h1>{t('examplePage.title')}</h1>;
}
```

The `t()` function walks the dot path through the namespace object. If a key is missing in the current language, it falls back to English, and if that fails too, it returns the key itself.

## Git conventions

Branch `dev` for work. The main branch is `main`, used for releases. Small focused commits with imperative subject lines:

```bash
git log --oneline -15   # see the house style
```

Recent examples:

```
f2894d6 Document external connections
c7a5fb3 Add a Connect group to the sidebar
5259c9c Connections in the UI: a list, a page, and where a run came from
d4381c9 Connectors get their own page, out of Settings
```

Rules:

- Subject line: imperative mood, capitalised first word, no period, under 70 characters
- Body: explain why, not what. What is in the diff
- One focused change per commit. Split large work across multiple commits
- Test your changes: `pytest tests/ -q` and `cd dashboard/frontend && npx vitest run` both pass
- No force push to main. If a PR needs a rebase, the author does it on their branch

Run `git gc` occasionally because the repository is large (currently `.git` is about 40MB).

## Where to document

**`ARCHITECTURE.md`:** The structure of the codebase. Runtime layers, folder structure, important files, storage model. Update it when you add a new subsystem or reorganize code.

**`docs/`:** User-facing guides and concepts. Served in the app under **Docs**, readable by agents through the `read_doc` tool. Examples: `docs/agents.md`, `docs/flows.md`, `docs/tools-and-capabilities.md`. Update these when user-visible behavior changes.

**`README.md`:** The landing page. Keep it short: what the product is, how to install, and links to ARCHITECTURE and docs. Update only for major announcements.

Keep these three in step. When you add a feature, start with a user-facing doc in `docs/`, then update ARCHITECTURE.md if the implementation touches structure.
