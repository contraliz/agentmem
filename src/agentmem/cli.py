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
    p=sub.add_parser("knowledge",help="manage project-scoped Markdown knowledge pages"); p.add_argument("action",choices=["init","list","read","create","update","history"]); p.add_argument("project_id"); p.add_argument("--id",default=""); p.add_argument("--title",default=""); p.add_argument("--content",default=""); p.add_argument("--expected-sha256",default=""); p.add_argument("--history-id",default=""); p.add_argument("--limit",type=int,default=50); p.add_argument("--offset",type=int,default=0); p.add_argument("--max-chars",type=int,default=12000)
    p=sub.add_parser("task"); p.add_argument("action",choices=["create","list","show","update"]); p.add_argument("project_id"); p.add_argument("--id",default=""); p.add_argument("--title",default=""); p.add_argument("--goal",default=""); p.add_argument("--acceptance",action="append",default=None); p.add_argument("--depends-on",action="append",default=None); p.add_argument("--authorized-path",action="append",default=None); p.add_argument("--blocked-reason",default=None); p.add_argument("--priority",type=int,default=None); p.add_argument("--status",default="all"); p.add_argument("--approval-proposal-id",default="")
    p.add_argument("--component",action="append",default=None); p.add_argument("--requirement-id",action="append",default=None)
    p=sub.add_parser("task-context",help="show task packet including applicable requirements"); p.add_argument("project_id"); p.add_argument("--id",required=True); p.add_argument("--max-chars",type=int,default=12000); p.add_argument("--requirement-offset",type=int,default=0); p.add_argument("--requirements-limit",type=int,default=12)
    p=sub.add_parser("requirement",help="manage requirements and applicability"); p.add_argument("action",choices=["create","list","show","history","update","fulfill","withdraw","supersede","sync"]); p.add_argument("project_id"); p.add_argument("--id",default=""); p.add_argument("--title",default=None); p.add_argument("--statement",default=None); p.add_argument("--kind",default=None); p.add_argument("--lifecycle",default=None); p.add_argument("--fulfillment",default=None); p.add_argument("--quote",default=None); p.add_argument("--interpretation",default=None); p.add_argument("--scope-json",default=None); p.add_argument("--rationale",default=None); p.add_argument("--criterion",action="append",default=None); p.add_argument("--evidence",action="append",default=None); p.add_argument("--source-type",default=None); p.add_argument("--source-ref",default=None); p.add_argument("--source-date",default=None); p.add_argument("--supersedes-id",default=None); p.add_argument("--expected-revision",type=int,default=None); p.add_argument("--idempotency-key",default=""); p.add_argument("--change-note",default=""); p.add_argument("--force",action="store_true")
    p=sub.add_parser("decision",help="manage project decisions"); p.add_argument("action",choices=["create","list","show","history","update","supersede","withdraw","sync"]); p.add_argument("project_id"); p.add_argument("--id",default=""); p.add_argument("--title",default=None); p.add_argument("--choice",default=None); p.add_argument("--rationale",default=None); p.add_argument("--alternative",action="append",default=None); p.add_argument("--scope-json",default=None); p.add_argument("--status",default=None); p.add_argument("--expected-revision",type=int,default=None); p.add_argument("--source-type",default=""); p.add_argument("--source-ref",default=""); p.add_argument("--source-date",default=""); p.add_argument("--quote",default=""); p.add_argument("--revisit-when",default=""); p.add_argument("--requirement-id",action="append",default=None); p.add_argument("--supersedes-id",default=""); p.add_argument("--idempotency-key",default=""); p.add_argument("--change-note",default=""); p.add_argument("--force",action="store_true")
    p=sub.add_parser("proposal",help="create proposals and record explicit version-pinned approval events"); p.add_argument("action",choices=["create","list","show","status","update","approve","reject","withdraw","history","approval-history","sync"]); p.add_argument("project_id"); p.add_argument("--id",default=""); p.add_argument("--title",default=""); p.add_argument("--content",default=""); p.add_argument("--targets-json",default=None); p.add_argument("--approval-required",dest="approval_required",action="store_true",default=None); p.add_argument("--no-approval-required",dest="approval_required",action="store_false"); p.add_argument("--expected-revision",type=int,default=None); p.add_argument("--quote",default=""); p.add_argument("--source-type",default=""); p.add_argument("--source-ref",default=""); p.add_argument("--source-date",default="unknown"); p.add_argument("--recorded-by",default=""); p.add_argument("--created-by",default="agent"); p.add_argument("--event-id",default=""); p.add_argument("--idempotency-key",default=""); p.add_argument("--change-note",default=""); p.add_argument("--limit",type=int,default=50); p.add_argument("--offset",type=int,default=0)
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
        elif args.command=="knowledge":
            if args.action=="init": result=store.initialize_knowledge(args.project_id)
            elif args.action=="list": result=store.list_knowledge_pages(args.project_id,args.limit,args.offset)
            elif args.action=="read": result=store.read_knowledge_page(args.project_id,args.id,args.max_chars,args.history_id)
            elif args.action=="create": result=store.create_knowledge_page(args.project_id,args.id,args.title,args.content)
            elif args.action=="update": result=store.update_knowledge_page(args.project_id,args.id,args.content,args.expected_sha256)
            else: result=store.knowledge_page_history(args.project_id,args.id,args.limit)
        elif args.command=="task":
            if args.action=="create": result=store.create_task(args.project_id,args.title,args.goal,args.acceptance or [],args.priority or 3,args.depends_on,args.authorized_path,args.blocked_reason or "",args.component,args.requirement_id,args.approval_proposal_id)
            elif args.action=="list": result={"tasks":store.list_tasks(args.project_id,args.status)}
            elif args.action=="show": result=store.get_task(args.project_id,args.id)
            else: result=store.update_task(args.project_id,args.id,args.title or None,args.goal or None,args.acceptance,args.priority,args.authorized_path,args.depends_on,args.blocked_reason,args.component,args.requirement_id,args.approval_proposal_id or None)
        elif args.command=="task-context": result=store.task_context(args.project_id,args.id,args.max_chars,args.requirement_offset,args.requirements_limit)
        elif args.command=="requirement":
            scope=json.loads(args.scope_json) if args.scope_json else None
            if args.action=="create": result=store.create_requirement(args.project_id,args.title or "",args.statement or "",kind=args.kind or "behavior",lifecycle=args.lifecycle or "current",fulfillment=args.fulfillment or "unknown",original_quote=args.quote or "",interpretation=args.interpretation or "",scope=scope,rationale=args.rationale or "",criteria=args.criterion,fulfillment_evidence=args.evidence,source_type=args.source_type or "",source_ref=args.source_ref or "",source_date=args.source_date or "",supersedes_id=args.supersedes_id or "",idempotency_key=args.idempotency_key,change_note=args.change_note)
            elif args.action=="list": result={"requirements":store.list_requirements(args.project_id,args.lifecycle or "all")}
            elif args.action=="show": result=store.get_requirement(args.project_id,args.id)
            elif args.action=="history": result={"history":store.requirement_history(args.project_id,args.id)}
            elif args.action=="sync": result=store.sync_records_projection(args.project_id,"requirements",args.force)
            else:
                patch={"source_type":args.source_type,"source_ref":args.source_ref,"source_date":args.source_date}
                if args.action=="fulfill": patch.update(fulfillment=args.fulfillment or "fulfilled",fulfillment_evidence=args.evidence or [])
                elif args.action=="withdraw": patch["lifecycle"]="withdrawn"
                elif args.action=="supersede":
                    replacement=store.get_requirement(args.project_id,args.supersedes_id or "")
                    if replacement["supersedes_id"]!=args.id or replacement["lifecycle"]!="current": raise ValueError("Create a current replacement requirement first with --supersedes-id pointing to the old ID")
                    patch["lifecycle"]="superseded"
                else:
                    for key,value in (("title",args.title),("statement",args.statement),("kind",args.kind),("lifecycle",args.lifecycle),("fulfillment",args.fulfillment),("original_quote",args.quote),("interpretation",args.interpretation),("scope",scope),("rationale",args.rationale),("criteria",args.criterion),("fulfillment_evidence",args.evidence),("supersedes_id",args.supersedes_id)):
                        if value is not None: patch[key]=value
                result=store.update_requirement(args.project_id,args.id,expected_revision=args.expected_revision if args.expected_revision is not None else -1,idempotency_key=args.idempotency_key,change_note=args.change_note,**{k:v for k,v in patch.items() if v is not None and v!=""})
        elif args.command=="decision":
            scope=json.loads(args.scope_json) if args.scope_json else None
            if args.action=="create": result=store.create_decision(args.project_id,args.title or "",args.choice or "",rationale=args.rationale or "",alternatives=args.alternative,scope=scope,status=args.status or "current",source_type=args.source_type,source_ref=args.source_ref,source_date=args.source_date,original_quote=args.quote,revisit_when=args.revisit_when,requirement_ids=args.requirement_id,supersedes_id=args.supersedes_id,idempotency_key=args.idempotency_key,change_note=args.change_note)
            elif args.action=="list": result={"decisions":store.list_decisions(args.project_id,args.status or "all")}
            elif args.action=="show": result=store.get_decision(args.project_id,args.id)
            elif args.action=="history": result={"history":store.decision_history(args.project_id,args.id)}
            elif args.action=="sync": result=store.sync_records_projection(args.project_id,"decisions",args.force)
            else:
                if args.action=="supersede":
                    replacement=store.get_decision(args.project_id,args.supersedes_id)
                    if replacement["supersedes_id"]!=args.id or replacement["status"]!="current": raise ValueError("Create a current replacement decision first with --supersedes-id pointing to the old ID")
                changes={k:v for k,v in {"title":args.title,"choice":args.choice,"rationale":args.rationale,"alternatives":args.alternative,"scope":scope,"status":"superseded" if args.action=="supersede" else "withdrawn" if args.action=="withdraw" else args.status,"source_type":args.source_type or None,"source_ref":args.source_ref or None,"source_date":args.source_date or None,"original_quote":args.quote or None,"revisit_when":args.revisit_when or None,"supersedes_id":args.supersedes_id if args.action=="update" and args.supersedes_id else None}.items() if v is not None}
                result=store.update_decision(args.project_id,args.id,expected_revision=args.expected_revision if args.expected_revision is not None else -1,idempotency_key=args.idempotency_key,change_note=args.change_note,requirement_ids=args.requirement_id,**changes)
        elif args.command=="proposal":
            targets=json.loads(args.targets_json) if args.targets_json is not None else None
            if args.action=="create": result=store.create_proposal(args.project_id,args.title,args.content,targets=targets or [],approval_required=bool(args.approval_required),source_type=args.source_type,source_ref=args.source_ref,source_date=args.source_date,created_by=args.created_by,idempotency_key=args.idempotency_key)
            elif args.action=="list": result=store.list_proposals(args.project_id,args.limit,args.offset)
            elif args.action=="show": result=store.get_proposal(args.project_id,args.id)
            elif args.action=="status": result=store.proposal_status(args.project_id,args.id)
            elif args.action=="history": result=store.proposal_history(args.project_id,args.id,args.limit)
            elif args.action=="approval-history": result=store.approval_history(args.project_id,args.id,args.limit)
            elif args.action=="sync": result=store.sync_records_projection(args.project_id,"approvals")
            elif args.action=="update":
                if args.expected_revision is None: raise ValueError("expected_revision is required")
                result=store.update_proposal(args.project_id,args.id,expected_revision=args.expected_revision,idempotency_key=args.idempotency_key,title=args.title or None,content=args.content or None,targets=targets,approval_required=args.approval_required,source_type=args.source_type,source_ref=args.source_ref,source_date=args.source_date,change_note=args.change_note)
            elif args.action in {"approve","reject"}:
                if args.expected_revision is None: raise ValueError("expected_revision is required")
                fn=store.approve_proposal if args.action=="approve" else store.reject_proposal
                result=fn(args.project_id,args.id,expected_revision=args.expected_revision,original_quote=args.quote,source_type=args.source_type,source_ref=args.source_ref,source_date=args.source_date,recorded_by=args.recorded_by,idempotency_key=args.idempotency_key)
            else:
                if args.expected_revision is None: raise ValueError("expected_revision is required")
                result=store.withdraw_proposal_approval(args.project_id,args.id,args.event_id,expected_revision=args.expected_revision,original_quote=args.quote,source_type=args.source_type,source_ref=args.source_ref,source_date=args.source_date,recorded_by=args.recorded_by,idempotency_key=args.idempotency_key)
        else: result=store.export_project(args.project_id)
        emit(result)
    except (ValueError,OSError) as exc:
        print(f"agentmem: {exc}",file=sys.stderr); raise SystemExit(2)


if __name__ == "__main__":
    main()
