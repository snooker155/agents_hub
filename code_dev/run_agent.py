import argparse
from common.utils import ensure_dirs
from agents import pm, ba, sd, tl, dev_backend, dev_frontend, dev_ops, qa
from common.config import Settings, Paths
from common.tasks import load_tasks, save_tasks
from common.utils import load_json

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=["pm","ba","sd","tl","be","fe","ops","qa"])
    ap.add_argument("action", nargs="?")
    ap.add_argument("--desc", help="Изначальное описание для PM intake")
    ap.add_argument("--mode", choices=["full","simple"], default="simple")
    ap.add_argument("--task-id", help="Выполнить только одну задачу по ID (например, TASK-BE-3)")
    ap.add_argument("--task-title", help="Выполнить задачи по части названия (case-insensitive)")
    ap.add_argument("--run-checks", action="store_true", help="QA: выполнить 'сухую' проверку синтаксиса тестов")

    args = ap.parse_args()
    Settings.mode_full = (args.mode=="full")
    ensure_dirs()

    if args.agent=="pm" and args.action=="intake":
        assert args.desc, "--desc обязателен"
        pm.intake(args.desc, mode_full=Settings.mode_full)
    elif args.agent=="ba" and args.action=="generate":
        ba.generate_brd(mode_full=Settings.mode_full)
    elif args.agent=="sd" and args.action=="generate":
        sd.generate_all()
    elif args.agent=="tl" and args.action=="choose":
        print("stack:", tl.choose_stack())
    elif args.agent=="tl" and args.action=="split":
        tl.split_tasks()
    elif args.agent=="tl" and args.action=="scaffold":
        tl.scaffold_env()
    elif args.agent=="be":
        stack = load_json(Paths().plan + "/stack.json", default={"backend_lang":"python"})
        dev_backend.run_backend(stack["backend_lang"], task_id=args.task_id, title_query=args.task_title)
    elif args.agent=="fe":
        dev_frontend.run_frontend(task_id=args.task_id, title_query=args.task_title)
    elif args.agent=="ops":
        dev_ops.run_ops(task_id=args.task_id, title_query=args.task_title)
    elif args.agent=="qa":
        qa.run_qa(task_id=args.task_id, title_query=args.task_title, run_checks=bool(args.run_checks))
    # elif args.agent=="tl" and args.action=="review":
    #     tl.review_code()


    else:
        ap.error("неизвестная комбинация agent/action")

if __name__=="__main__":
    main()
