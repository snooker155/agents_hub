import argparse
import os
import sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", help="Agent ID from definitions (e.g. pm_agent, swe_agent)")
    ap.add_argument("action", nargs="?", help="Optional action/instruction")
    ap.add_argument("--desc", help="Initial description for the task")
    ap.add_argument("--mode", choices=["full","simple"], default="simple")
    ap.add_argument("--task-id", help="Optional task ID to associate with")
    ap.add_argument("--workspace", help="Path to workspace")
    ap.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args()

    if args.workspace:
        os.environ["WORKSPACE_ROOT"] = args.workspace

    from common.utils import ensure_dirs
    from agents.factory import create_agent
    from common.config import settings

    ensure_dirs()

    ws = args.workspace or os.environ.get("WORKSPACE_ROOT", "./out")

    # Map legacy names to factory agent IDs if needed
    agent_map = {
        "pm": "pm_agent",
        "ba": "ba_agent",
        "sd": "sd_agent",
        "tl": "tl_agent",
        "be": "dev_agent",
        "fe": "dev_agent",
        "ops": "devops_agent",
        "qa": "qa_agent",
        "decomposer": "decomposer",
        "swe": "swe_agent",
        "swe-fs": "swe_agent"
    }

    agent_id = agent_map.get(args.agent, args.agent)

    # Apply per-agent model overrides injected by factory_runner via environment variables
    agent_overrides: dict = {}
    if os.environ.get("AGENT_PROVIDER"):
        agent_overrides["provider"] = os.environ["AGENT_PROVIDER"]
    if os.environ.get("AGENT_MODEL"):
        agent_overrides["model"] = os.environ["AGENT_MODEL"]
    if os.environ.get("AGENT_BASE_URL"):
        agent_overrides["base_url"] = os.environ["AGENT_BASE_URL"]
    if os.environ.get("AGENT_API_KEY"):
        agent_overrides["api_key"] = os.environ["AGENT_API_KEY"]
    if os.environ.get("AGENT_TEMPERATURE"):
        agent_overrides["temperature"] = float(os.environ["AGENT_TEMPERATURE"])
    if os.environ.get("AGENT_MAX_TOKENS"):
        agent_overrides["max_tokens"] = int(os.environ["AGENT_MAX_TOKENS"])

    print(f"Creating agent: {agent_id} in workspace: {ws}" + (f" (provider: {agent_overrides['provider']}" + (f", model: {agent_overrides['model']}" if 'model' in agent_overrides else "") + ")" if 'provider' in agent_overrides else ""))
    agent = create_agent(agent_id, workspace=ws, verbose=args.verbose, **agent_overrides)

    instruction = args.action or args.desc or f"Process task {args.task_id or ''}"

    print(f"Running agent with instruction: {instruction}")
    result = agent.run(instruction)

    if result.ok:
        print("Agent output:")
        print(result.agent_output)
    else:
        print(f"Agent failed: {result.error}")
        sys.exit(1)

if __name__=="__main__":
    main()
