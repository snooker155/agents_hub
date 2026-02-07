import argparse
import os
import sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=["pm","ba","sd","tl","be","fe","ops","qa"])
    ap.add_argument("action", nargs="?")
    ap.add_argument("--desc", help="Изначальное описание для PM intake")
    ap.add_argument("--mode", choices=["full","simple"], default="simple")
    ap.add_argument("--task-id", help="Выполнить только одну задачу по ID (например, TASK-BE-3)")
    ap.add_argument("--task-title", help="Выполнить задачи по части названия (case-insensitive)")
    ap.add_argument("--run-checks", action="store_true", help="QA: выполнить 'сухую' проверку синтаксиса тестов")
    ap.add_argument("--workspace", help="Path to workspace")

    args = ap.parse_args()

    if args.workspace:
        os.environ["WORKSPACE_ROOT"] = args.workspace

    from common.utils import ensure_dirs
    from agents import pm, ba, sd, tl, dev_backend, dev_frontend, dev_ops, qa
    from common.config import Settings, Paths
    from common.utils import load_json

    Settings.mode_full = (args.mode=="full")
    ensure_dirs()

    ws = args.workspace or os.environ.get("WORKSPACE_ROOT", "./out")

    if args.agent=="pm":
        pm.intake(args.desc or "", workspace=ws)
    elif args.agent=="ba":
        ba.generate_brd(workspace=ws)
    elif args.agent=="sd":
        sd.generate_all(workspace=ws)
    elif args.agent=="tl":
        tl.split_tasks(workspace=ws)
    elif args.agent=="be":
        dev_backend.run_backend(workspace=ws, task_id=args.task_id)
    elif args.agent=="fe":
        dev_frontend.run_frontend(workspace=ws, task_id=args.task_id)
    elif args.agent=="ops":
        dev_ops.run_ops_agent(workspace=ws, task_id=args.task_id)
    elif args.agent=="qa":
        qa.run_qa_agent(workspace=ws, task_id=args.task_id)
    else:
        ap.error("неизвестная комбинация agent/action")

if __name__=="__main__":
    main()
