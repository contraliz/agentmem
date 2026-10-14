from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .store import MemoryStore


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser=argparse.ArgumentParser(prog="agentmem",description="Project-scoped memory and task CLI")
    parser.add_argument("--data-dir",default=os.environ.get("AGENTMEM_DATA_DIR"))
    parser.add_argument("--memory-root",default=os.environ.get("AGENTMEM_MEMORY_ROOT"))
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("project",help="register/list projects"); p.add_argument("action",choices=["list","register","resolve"]); p.add_argument("--id",default=""); p.add_argument("--root",default=""); p.add_argument("--cwd",default=""); p.add_argument("--description",default=""); p.add_argument("--memory-root",default=None)
    p=sub.add_parser("attach"); p.add_argument("project_id"); p.add_argument("--max-chars",type=int,default=6500)
    p=sub.add_parser("index"); p.add_argument("project_id"); p.add_argument("--max-files",type=int,default=5000)
    p=sub.add_parser("check"); p.add_argument("project_id"); p.add_argument("--max-files",type=int,default=5000)
    p=sub.add_parser("search"); p.add_argument("project_id"); p.add_argument("query"); p.add_argument("--limit",type=int,default=5)
    p=sub.add_parser("task"); p.add_argument("action",choices=["create","list","show","update"]); p.add_argument("project_id"); p.add_argument("--id",default=""); p.add_argument("--title",default=""); p.add_argument("--goal",default=""); p.add_argument("--acceptance",action="append",default=None); p.add_argument("--depends-on",action="append",default=None); p.add_argument("--authorized-path",action="append",default=None); p.add_argument("--blocked-reason",default=None); p.add_argument("--priority",type=int,default=None); p.add_argument("--status",default="all")
    p=sub.add_parser("export"); p.add_argument("project_id")
    args=parser.parse_args(); store=MemoryStore(args.data_dir,args.memory_root)
    try:
        if args.command=="project":
            if args.action=="list": result={"projects":store.list_projects()}
            elif args.action=="register": result=store.register_project(args.id,args.root,args.description,args.memory_root)
            else: result=store.resolve_project(args.id or None,args.cwd or None)
        elif args.command=="attach": result=store.attach(args.project_id,args.max_chars)
        elif args.command=="index": result=store.index_project(args.project_id,args.max_files)
        elif args.command=="check": result=store.check_project_changes(args.project_id,args.max_files)
        elif args.command=="search": result=store.search(args.project_id,args.query,args.limit)
        elif args.command=="task":
            if args.action=="create": result=store.create_task(args.project_id,args.title,args.goal,args.acceptance or [],args.priority or 3,args.depends_on,args.authorized_path,args.blocked_reason or "")
            elif args.action=="list": result={"tasks":store.list_tasks(args.project_id,args.status)}
            elif args.action=="show": result=store.get_task(args.project_id,args.id)
            else: result=store.update_task(args.project_id,args.id,args.title or None,args.goal or None,args.acceptance,args.priority,args.authorized_path,args.depends_on,args.blocked_reason)
        else: result=store.export_project(args.project_id)
        emit(result)
    except (ValueError,OSError) as exc:
        print(f"agentmem: {exc}",file=sys.stderr); raise SystemExit(2)


if __name__ == "__main__":
    main()
