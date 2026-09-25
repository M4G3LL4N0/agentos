"""AgentOS command center: ``agentos``.

Usable by humans and by future automation (``--json`` for structured output).
The CLI is thin: every command delegates to the service layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from agentos import __version__
from agentos.engine import AgentOSError
from agentos.models import ObjectiveState
from agentos.services import AgentOS

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _use_color() -> bool:
    return sys.stdout.isatty()


def _paint(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if _use_color() else text


def state_painted(state: str) -> str:
    if state in ("COMPLETED", "VERIFIED", "OK"):
        return _paint(state, GREEN)
    if state in ("FAILED", "DOWN"):
        return _paint(state, RED)
    if state in ("RUNNING", "VERIFYING", "BLOCKED"):
        return _paint(state, YELLOW)
    return _paint(state, CYAN)


def emit_json(payload: Any) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return 0


def emit_error(message: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, indent=2))
    else:
        print(_paint(f"error: {message}", RED), file=sys.stderr)
    return 1


def _parse_json_object(text: str | None, flag: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentOSError(f"{flag} must be valid JSON: {exc}")
    if not isinstance(value, dict):
        raise AgentOSError(f"{flag} must be a JSON object")
    return value


def _parse_json_list(text: str | None, flag: str) -> list[str]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentOSError(f"{flag} must be valid JSON: {exc}")
    if not isinstance(value, list):
        raise AgentOSError(f"{flag} must be a JSON list")
    return [str(item) for item in value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentos", description="AgentOS: universal agent execution control plane"
    )
    parser.add_argument("--version", action="version", version=f"agentos {__version__}")
    parser.add_argument(
        "--home",
        default=None,
        help="AgentOS home directory (default: $AGENTOS_HOME or ~/.agentos)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON output"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="initialize AgentOS home and state")
    p_init.add_argument("--reset", action="store_true", help="wipe existing state")

    sub.add_parser("status", help="show engine status summary")

    p_obj = sub.add_parser("objective", help="manage objectives")
    obj_sub = p_obj.add_subparsers(dest="objective_command", required=True)
    p_create = obj_sub.add_parser("create", help="create an objective")
    p_create.add_argument("title", help="objective title")
    p_create.add_argument("--description", default="", help="objective description")
    p_create.add_argument(
        "--priority", default="MEDIUM", help="LOW, MEDIUM, HIGH or CRITICAL"
    )
    p_create.add_argument("--operation", default=None, help="required operation")
    p_create.add_argument("--params", default=None, help="JSON object of params")
    p_create.add_argument("--constraints", default=None, help="JSON list of constraints")
    p_create.add_argument("--parent", default=None, help="parent objective id")
    p_create.add_argument("--context", default=None, help="JSON object of context")
    p_list = obj_sub.add_parser("list", help="list objectives")
    p_list.add_argument("--status", default=None, help="filter by state")
    p_show = obj_sub.add_parser("show", help="show one objective")
    p_show.add_argument("objective_id", help="objective id")
    p_cancel = obj_sub.add_parser("cancel", help="cancel an objective")
    p_cancel.add_argument("objective_id", help="objective id")

    sub.add_parser("objectives", help="list objectives (alias)").add_argument(
        "--status", default=None, help="filter by state"
    )

    p_cap = sub.add_parser("capabilities", help="manage capabilities")
    cap_sub = p_cap.add_subparsers(dest="capabilities_command", required=False)
    cap_sub.add_parser("list", help="list known capabilities").add_argument(
        "--refresh", action="store_true", help="re-probe capability health"
    )
    p_add = cap_sub.add_parser("add", help="register a configured local CLI command")
    p_add.add_argument("capability_id", help="new capability id (e.g. lint)")
    p_add.add_argument("--name", default=None, help="display name")
    # NOTE: dest is shell_command because --command would clobber the
    # top-level subparser dest ("command").
    p_add.add_argument(
        "--command",
        dest="shell_command",
        required=True,
        help="shell command to execute",
    )
    p_add.add_argument("--description", default="", help="description")
    p_add.add_argument(
        "--expect", default=None, help="substring that verified output must contain"
    )
    p_rm = cap_sub.add_parser("remove", help="remove a configured capability")
    p_rm.add_argument("capability_id", help="capability id")
    p_inspect = cap_sub.add_parser(
        "inspect", help="show capability readiness and evidence ledger"
    )
    p_inspect.add_argument(
        "--readiness", action="store_true", help="show readiness table"
    )
    p_inspect.add_argument(
        "capability_id", nargs="?", default=None, help="optional capability id"
    )

    sub.add_parser("agents", help="list agent/model capabilities")

    p_run = sub.add_parser("run", help="execute an objective through the engine")
    p_run.add_argument("objective_id", help="objective id or new objective text")
    p_run.add_argument("--operation", default=None, help="override operation")
    p_run.add_argument("--params", default=None, help="operation parameters as JSON object")
    p_run.add_argument("--max-attempts", type=int, default=None)
    p_run.add_argument("--timeout", type=float, default=None)
    p_run.add_argument("--force", action="store_true", help="re-run terminal objective")
    p_run.add_argument("--explain", action="store_true", help="include the job trace")

    p_verify = sub.add_parser("verify", help="re-verify the latest execution")
    p_verify.add_argument("objective_id", help="objective id")

    p_project = sub.add_parser("project", help="project-level operations")
    proj_sub = p_project.add_subparsers(dest="project_command", required=True)
    p_pverify = proj_sub.add_parser(
        "verify", help="run the staged verification pipeline against a project"
    )
    p_pverify.add_argument("path", help="project directory to verify")
    p_pverify.add_argument(
        "--objective", default=None, help="objective id to attach the result to"
    )
    p_pverify.add_argument(
        "--stages",
        default=None,
        help="comma-separated stage subset (default: all stages)",
    )

    p_recover = sub.add_parser("recover", help="recover a failed/blocked objective")
    p_recover.add_argument("objective_id", help="objective id")
    p_recover.add_argument("--max-attempts", type=int, default=None)
    p_recover.add_argument("--operation", default=None)

    p_events = sub.add_parser("events", help="show recorded events")
    p_events.add_argument("--objective", default=None, help="filter by objective id")
    p_events.add_argument("--limit", type=int, default=50)

    p_inspect = sub.add_parser("inspect", help="full detail for an objective")
    p_inspect.add_argument("objective_id", help="objective id")

    sub.add_parser("doctor", help="run health checks")

    p_node = sub.add_parser(
        "node", help="show federation node state and per-cell routing gates"
    )
    p_node.add_argument(
        "--refresh",
        action="store_true",
        help="reload config and re-probe cells before reporting",
    )

    p_delegate = sub.add_parser(
        "delegate", help="express an objective; AgentOS orchestrates it"
    )
    p_delegate.add_argument("title", help="objective title")
    p_delegate.add_argument("--description", default="", help="objective description")
    p_delegate.add_argument("--priority", default="MEDIUM")
    p_delegate.add_argument("--operation", default=None)
    p_delegate.add_argument("--params", default=None, help="JSON object of params")
    p_delegate.add_argument("--constraints", default=None, help="JSON list")
    p_delegate.add_argument("--items", default=None, help="JSON list for parallel work")
    p_delegate.add_argument("--strategy", default=None, help="requested strategy")
    p_delegate.add_argument("--max-attempts", type=int, default=None)

    p_serve = sub.add_parser("serve", help="start the lightweight HTTP API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)

    p_mcp = sub.add_parser("mcp", help="MCP candidate lifecycle (or serve stdio)")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_command", required=False)
    p_mdiscover = mcp_sub.add_parser(
        "discover", help="search the configured MCP registry (read-only)"
    )
    p_mdiscover.add_argument("query", help="registry search text")
    p_mdiscover.add_argument("--limit", type=int, default=25)
    mcp_sub.add_parser("candidates", help="list MCP candidates by lifecycle state")
    p_minspect = mcp_sub.add_parser("inspect", help="mark a candidate INSPECTED")
    p_minspect.add_argument("candidate_id", help="candidate id (name@version)")
    p_mtest = mcp_sub.add_parser(
        "test", help="run bounded static certification (no install, no run)"
    )
    p_mtest.add_argument("candidate_id", help="candidate id (name@version)")
    p_mapprove = mcp_sub.add_parser(
        "approve", help="approve a TESTED candidate (explicit evidence required)"
    )
    p_mapprove.add_argument("candidate_id", help="candidate id (name@version)")
    p_mapprove.add_argument(
        "--evidence", default=None, help="JSON list of approval evidence"
    )
    p_mreject = mcp_sub.add_parser("reject", help="reject a candidate")
    p_mreject.add_argument("candidate_id", help="candidate id (name@version)")
    p_mreject.add_argument("--reason", default="", help="rejection reason")
    p_mrevoke = mcp_sub.add_parser("revoke", help="revoke an APPROVED candidate")
    p_mrevoke.add_argument("candidate_id", help="candidate id (name@version)")
    p_mrevoke.add_argument("--reason", default="", help="revocation reason")

    p_eco = sub.add_parser(
        "ecosystem", help="ecosystem scout: candidate discovery + certification"
    )
    eco_sub = p_eco.add_subparsers(dest="ecosystem_command", required=False)
    p_ediscover = eco_sub.add_parser(
        "discover", help="ingest ONE ecosystem source as inert metadata"
    )
    p_ediscover.add_argument("category", help="source category (registry|federation|worker|sheet)")
    p_ediscover.add_argument("name", help="source/provider name")
    p_ediscover.add_argument("source", help="source pointer (sheet path, peer, registry)")
    p_ediscover.add_argument("--version", default="", help="source version")
    eco_sub.add_parser("candidates", help="list ecosystem candidates by lifecycle state")
    p_einspect = eco_sub.add_parser("inspect", help="mark a candidate INSPECTED")
    p_einspect.add_argument("candidate_id", help="candidate id (category/name@version)")
    p_etest = eco_sub.add_parser("test", help="run bounded static certification")
    p_etest.add_argument("candidate_id", help="candidate id (category/name@version)")
    p_eapprove = eco_sub.add_parser(
        "approve", help="approve a TESTED candidate (explicit evidence required)"
    )
    p_eapprove.add_argument("candidate_id", help="candidate id (category/name@version)")
    p_eapprove.add_argument("--evidence", action="append", default=None)
    p_ereject = eco_sub.add_parser("reject", help="reject a candidate")
    p_ereject.add_argument("candidate_id", help="candidate id (category/name@version)")
    p_ereject.add_argument("--reason", default="", help="rejection reason")

    p_wf = sub.add_parser(
        "workflow", help="reusable workflow catalog (usage-led, never re-plan)"
    )
    wf_sub = p_wf.add_subparsers(dest="workflow_command", required=False)
    p_wreg = wf_sub.add_parser("register", help="register an inert reusable workflow")
    p_wreg.add_argument("name", help="workflow name")
    p_wreg.add_argument("--steps", nargs="*", default=None, help="ordered reusable steps")
    p_wreg.add_argument("--permissions", action="append", default=None, help="requested permissions")
    p_wreg.add_argument("--schedule-hint", default="", dest="schedule_hint", help="schedule hint")
    wf_sub.add_parser("catalog", help="list the reusable workflow catalog")
    p_wuse = wf_sub.add_parser("use", help="record one reuse event (never auto-upgrades)")
    p_wuse.add_argument("workflow_id", help="workflow id (workflow:name)")
    p_wuse.add_argument("--not-useful", action="store_true", default=False, help="record the reuse as NOT useful")
    p_wstate = wf_sub.add_parser("state", help="show or transition a workflow state")
    p_wstate.add_argument("workflow_id", help="workflow id (workflow:name)")
    p_wstate.add_argument("--to", default=None, dest="to_state", help="legal target state")

    p_eff = sub.add_parser(
        "efficiency", help="low-usage efficiency kernel (triage/team/cache/delta)"
    )
    eff_sub = p_eff.add_subparsers(dest="efficiency_command", required=False)
    p_etri = eff_sub.add_parser("triage", help="cheap rules-first triage decision")
    p_etri.add_argument("objective", help="objective text")
    p_etri.add_argument("--capability", default="", dest="target_capability")
    p_etri.add_argument("--exact-hit", action="store_true", default=False)
    p_etri.add_argument("--artifact-hit", action="store_true", default=False)
    p_etri.add_argument("--known-workflow", action="store_true", default=False)
    p_eteam = eff_sub.add_parser("team", help="smallest capable team")
    p_eteam.add_argument("--quality-floor", default="", dest="quality_floor")
    p_eteam.add_argument("--risk", default="low")
    p_eteam.add_argument("--decomposable", action="store_true", default=False)
    p_eteam.add_argument("--subtasks", type=int, default=1)
    p_eteam.add_argument("--verification-burden", action="store_true", default=False)
    p_eteam.add_argument("--security", default="internal")
    p_effp = eff_sub.add_parser("fingerprint", help="TaskFingerprint + dedupe")
    p_effp.add_argument("intent", help="task intent")
    p_effp.add_argument("--output-schema", default="", dest="output_schema")
    p_effp.add_argument("--constraint", action="append", default=None)
    p_effp.add_argument("--role", default="")
    p_edel = eff_sub.add_parser("delta", help="DeltaContext: baseline diff + task")
    p_edel.add_argument("--baseline", default="{}", help="JSON label->hash baseline")
    p_edel.add_argument("--current", default="{}", help="JSON label->hash current state")
    p_edel.add_argument("--task", default="", help="current task")
    p_edel.add_argument("--baseline-ref", default="baseline@1", dest="baseline_ref")
    p_edel.add_argument("--cache-scope", default="", dest="cache_scope")
    p_ecget = eff_sub.add_parser("cache-get", help="intelligence cache lookup")
    p_ecget.add_argument("intent", help="normalized intent")
    p_ecget.add_argument("--output-schema", default="", dest="output_schema")
    p_ecget.add_argument("--semantic", action="store_true", default=False)
    p_ecput = eff_sub.add_parser("cache-put", help="store an intelligence cache entry")
    p_ecput.add_argument("intent", help="normalized intent")
    p_ecput.add_argument("--category", default="exact")
    p_ecput.add_argument("--payload", default="{}", help="JSON payload")
    p_ecput.add_argument("--artifact-ref", default="", dest="artifact_ref")
    p_ecput.add_argument("--security-scope", default="INTERNAL", dest="security_scope")
    p_ecput.add_argument("--provider", default="")
    p_einv = eff_sub.add_parser("invalidate", help="narrow dependency invalidation")
    p_einv.add_argument("--deps", default="{}", help="JSON dependency label->hash")
    p_einv.add_argument("--also-exact", action="store_true", default=False)
    eff_sub.add_parser("benchmark", help="run deterministic local learning benchmark")
    eff_sub.add_parser("stats", help="efficiency kernel + cache telemetry")

    # Learning execution system commands (prefixed to avoid conflicts)
    p_lverify = sub.add_parser("lverify", help="verification engine (learning)")
    lverify_sub = p_lverify.add_subparsers(dest="lverify_command", required=False)
    p_lverify_start = lverify_sub.add_parser("start", help="start verification for a task")
    p_lverify_start.add_argument("task_id")
    p_lverify_start.add_argument("task_class")
    p_lverify_start.add_argument("--result", default="{}")
    p_lverify_run = lverify_sub.add_parser("run", help="record an external verifier result")
    p_lverify_run.add_argument("record_id")
    p_lverify_run.add_argument("--success", action="store_true")
    p_lverify_run.add_argument("--confidence", type=float, default=0.0)
    p_lverify_run.add_argument("--failure-reason", default="")
    p_lverify_evaluate = lverify_sub.add_parser("evaluate", help="evaluate stored verification outcomes")
    p_lverify_evaluate.add_argument("task_id")
    p_lverify_evaluate.add_argument("--attempt", type=int, default=1)

    p_coach = sub.add_parser("coach", help="coach layer - lesson generation")
    coach_sub = p_coach.add_subparsers(dest="coach_command", required=False)
    p_coach_analyze = coach_sub.add_parser("analyze", help="analyze execution for lessons")
    p_coach_analyze.add_argument("task_id")
    p_coach_analyze.add_argument("task_class")
    p_coach_analyze.add_argument("--history", default="[]")
    p_coach_promote = coach_sub.add_parser("promote", help="promote lesson status")
    p_coach_promote.add_argument("lesson_id")
    p_coach_promote.add_argument("status")

    p_wfc = sub.add_parser("workflow-compiler", help="workflow compiler")
    wfc_sub = p_wfc.add_subparsers(dest="wfc_command", required=False)
    p_wfc_compile = wfc_sub.add_parser("compile", help="compile workflow from executions")
    p_wfc_compile.add_argument("task_class")
    p_wfc_promote = wfc_sub.add_parser("promote", help="promote workflow status")
    p_wfc_promote.add_argument("workflow_id")
    p_wfc_promote.add_argument("status")
    p_wfc_advance = wfc_sub.add_parser("advance", help="advance workflow maturity")
    p_wfc_advance.add_argument("workflow_id")
    p_wfc_record = wfc_sub.add_parser("record-use", help="record workflow use")
    p_wfc_record.add_argument("workflow_id")
    p_wfc_record.add_argument("--success", action="store_true")
    p_wfc_match = wfc_sub.add_parser("match", help="match workflow for task")
    p_wfc_match.add_argument("task_class")
    p_wfc_match.add_argument("--inputs", default="{}")
    p_wfc_stats = wfc_sub.add_parser("stats", help="workflow compilation stats")
    p_wfc_stats.add_argument("--task-class", default=None)

    p_skill = sub.add_parser("skill", help="skill model")
    skill_sub = p_skill.add_subparsers(dest="skill_command", required=False)
    p_skill_execute = skill_sub.add_parser("execute", help="execute a skill")
    p_skill_execute.add_argument("skill_id")
    p_skill_execute.add_argument("--inputs", default="{}")
    p_skill_recommend = skill_sub.add_parser("recommend", help="recommend skill for task")
    p_skill_recommend.add_argument("task_class")
    p_skill_recommend.add_argument("--context", default="{}")
    skill_sub.add_parser("list", help="list skills")

    p_role = sub.add_parser("role", help="role classifier")
    role_sub = p_role.add_subparsers(dest="role_command", required=False)
    p_role_classify = role_sub.add_parser("classify", help="classify role persistence")
    p_role_classify.add_argument("role_id")

    p_autonomy = sub.add_parser("autonomy", help="autonomy model")
    autonomy_sub = p_autonomy.add_subparsers(dest="autonomy_command", required=False)
    for name in ("success", "failure", "security", "correction", "scope", "can-act", "promote"):
        command = autonomy_sub.add_parser(name)
        command.add_argument("entity_type")
        command.add_argument("entity_id")
        if name == "can-act":
            command.add_argument("--scope", default="default")
        if name == "promote":
            command.add_argument("target")

    p_fleet = sub.add_parser("fleet", help="fleet monitor")
    fleet_sub = p_fleet.add_subparsers(dest="fleet_command", required=False)
    p_fleet_signal = fleet_sub.add_parser("signal", help="process fleet signal")
    p_fleet_signal.add_argument("signal", help="signal JSON object")
    fleet_sub.add_parser("health", help="get fleet health")
    fleet_sub.add_parser("incidents", help="list active incidents")
    p_fleet_resolve = fleet_sub.add_parser("resolve", help="resolve incident")
    p_fleet_resolve.add_argument("incident_id")
    p_fleet_resolve.add_argument("resolution")
    p_fleet_resolve.add_argument("--lesson-ref", default=None)

    p_routing = sub.add_parser("routing", help="learned routing")
    routing_sub = p_routing.add_subparsers(dest="routing_command", required=False)
    p_routing_best = routing_sub.add_parser("best", help="get best executor")
    p_routing_best.add_argument("task_class")
    p_routing_best.add_argument("--candidates", default="[]")
    p_routing_explain = routing_sub.add_parser("explain", help="explain routing decision")
    p_routing_explain.add_argument("job_id")

    p_routine = sub.add_parser("routine", help="routine engine")
    routine_sub = p_routine.add_subparsers(dest="routine_command", required=False)
    p_routine_run = routine_sub.add_parser("run", help="run a routine")
    p_routine_run.add_argument("routine_id")
    p_routine_run.add_argument("--params", default="{}")
    routine_sub.add_parser("list", help="list routines")
    p_routine_enable = routine_sub.add_parser("enable", help="enable routine")
    p_routine_enable.add_argument("routine_id")
    p_routine_enable.add_argument("--disabled", action="store_true")
    p_routine_disable = routine_sub.add_parser("disable", help="disable routine")
    p_routine_disable.add_argument("routine_id")

    p_backup = sub.add_parser("backup", help="backup & reconstruction")
    backup_sub = p_backup.add_subparsers(dest="backup_command", required=False)
    p_backup_create = backup_sub.add_parser("create", help="create backup")
    p_backup_create.add_argument("--output-dir")
    p_backup_inspect = backup_sub.add_parser("inspect", help="inspect backup archive")
    p_backup_inspect.add_argument("archive")
    p_backup_verify = backup_sub.add_parser("verify", help="verify backup archive")
    p_backup_verify.add_argument("archive")
    p_backup_restore = backup_sub.add_parser("restore", help="restore from backup")
    p_backup_restore.add_argument("archive")
    p_backup_restore.add_argument("--dry-run", action="store_true")
    p_backup_restore.add_argument("--no-verify", action="store_true")
    p_backup_reconstruct = backup_sub.add_parser("reconstruct", help="reconstruct workforce from backup")
    p_backup_reconstruct.add_argument("backup_dir")

    p_bot = sub.add_parser("bot", help="persistent bot governor")
    bot_sub = p_bot.add_subparsers(dest="bot_command", required=False)
    p_bot_evaluate = bot_sub.add_parser("evaluate", help="evaluate virtual role for materialization")
    p_bot_evaluate.add_argument("role_id")
    p_bot_approve = bot_sub.add_parser("approve", help="approve/reject materialization")
    p_bot_approve.add_argument("role_id")
    p_bot_approve.add_argument("--approved", action="store_true")
    p_bot_approve.add_argument("--approver", default="")
    bot_sub.add_parser("pending", help="list pending approvals")

    p_gaps = sub.add_parser("gaps", help="show capability-gap analyses")
    p_gaps.add_argument("--objective", default=None)

    p_learn = sub.add_parser("learn", help="show learned execution patterns")
    p_learn.add_argument("--class", dest="obj_class", default=None)

    p_plans = sub.add_parser("plans", help="show execution plans")
    p_plans.add_argument("--objective", default=None)

    p_quality = sub.add_parser("quality", help="show quality evaluations")
    p_quality.add_argument("objective_id", help="objective id")

    p_policy = sub.add_parser("policy", help="view or change execution policy")
    policy_sub = p_policy.add_subparsers(dest="policy_command", required=False)
    policy_sub.add_parser("show", help="show the execution policy")
    p_grant = policy_sub.add_parser("grant", help="grant an approval class")
    p_grant.add_argument("operation_class", help="e.g. destructive")
    p_revoke = policy_sub.add_parser("revoke", help="revoke an approval class")
    p_revoke.add_argument("operation_class", help="e.g. destructive")

    p_exec = sub.add_parser("execution", help="show one execution record")
    exec_sub = p_exec.add_subparsers(dest="execution_command", required=True)

    p_fed = sub.add_parser(
        "federate", help="provider-neutral federated routing across executor cells"
    )
    fed_sub = p_fed.add_subparsers(dest="federate_command", required=True)
    fed_sub.add_parser("status", help="federation status (cells, jobs, telemetry)")
    p_fexec = fed_sub.add_parser("executors", help="list executor cells")
    p_fexec.add_argument(
        "--refresh", action="store_true", help="re-probe health and posture"
    )
    p_fcaps = fed_sub.add_parser(
        "capabilities", help="discoverable capability catalog (native + probed)"
    )
    p_fcaps.add_argument(
        "--refresh", action="store_true", help="re-probe capability health"
    )
    p_fmatrix = fed_sub.add_parser(
        "matrix", help="executor x capability routing matrix with honest health/tier"
    )
    p_fmatrix.add_argument(
        "--refresh", action="store_true", help="re-probe health and cells"
    )
    p_fdistill = fed_sub.add_parser(
        "distill", help="deterministic distilled context of registry + federation"
    )
    p_fdistill.add_argument(
        "--refresh", action="store_true", help="re-probe before distilling"
    )
    p_fjobs = fed_sub.add_parser("jobs", help="list federation jobs")
    p_fjobs.add_argument("--status", default=None, help="filter by job status")
    p_fjobs.add_argument("--limit", type=int, default=100)
    p_froute = fed_sub.add_parser(
        "route", help="route a job envelope (pure decision, nothing executed)"
    )
    p_froute.add_argument("--job", required=True, help="JSON job envelope")
    p_froute.add_argument(
        "--explain",
        action="store_true",
        help="explain the routing decision (task class, candidates, why)",
    )
    fed_sub.add_parser("doctor", help="federation health (PASS/WARN/UNCONFIGURED/...)")
    fed_sub.add_parser("cells", help="list executor cells with federation detail")
    fed_sub.add_parser("health", help="cell health rollup")
    p_fsubmit = fed_sub.add_parser(
        "submit", help="persist + route a job; SIMULATED by default"
    )
    p_fsubmit.add_argument("--job", required=True, help="JSON job envelope")
    p_fpacket = fed_sub.add_parser(
        "packet", help="parse a grokbot-office TASK/RESULT packet line"
    )
    p_fpacket.add_argument("line", help="packet line")
    fed_sub.add_parser(
        "telemetry", help="measured federation telemetry (costs only when real)"
    )

    p_gov = sub.add_parser(
        "governor", help="resource governor: weights, routing, outcomes, verification"
    )
    gov_sub = p_gov.add_subparsers(dest="governor_command", required=True)
    gov_sub.add_parser("weights", help="effective governor weights (defaults or config)")
    p_groute = gov_sub.add_parser(
        "route", help="governor-route a job envelope (records a PENDING outcome)"
    )
    p_groute.add_argument("--job", required=True, help="JSON job envelope")
    p_goutcomes = gov_sub.add_parser("outcomes", help="routing outcomes + verified-rate summary")
    p_goutcomes.add_argument("--job", default=None, dest="job_id")
    p_goutcomes.add_argument("--limit", type=int, default=100)
    p_gverify = gov_sub.add_parser(
        "verify", help="record an outcome (VERIFIED or FAILED) for a routed job"
    )
    p_gverify.add_argument("--job", required=True, dest="job_id")
    p_gverify.add_argument("--status", required=True, help="VERIFIED or FAILED")
    p_gverify.add_argument("--evidence", action="append", default=None)

    p_sup = sub.add_parser(
        "supervisor", help="supervisor operating model: honest chain status"
    )
    p_sup_sub = p_sup.add_subparsers(dest="supervisor_command", required=True)
    p_sup_sub.add_parser("status", help="supervisor model + chain layer status")

    p_exec_show = exec_sub.add_parser("show", help="show one execution")
    p_exec_show.add_argument("execution_id", help="execution id")

    p_execs = sub.add_parser("executions", help="list execution records")
    p_execs.add_argument("list_command", nargs="?", default="list")
    p_execs.add_argument("--objective", default=None, help="filter by objective id")
    p_execs.add_argument("--limit", type=int, default=50)

    p_route = sub.add_parser(
        "route", help="route a job envelope with an explanation"
    )
    p_route.add_argument("--job", required=True, help="JSON job envelope")
    p_route.add_argument(
        "--explain",
        action="store_true",
        default=True,
        help="explain the routing decision (default on)",
    )

    p_capability = sub.add_parser(
        "capability", help="unified capability graph: find and explain"
    )
    cap_single_sub = p_capability.add_subparsers(
        dest="capability_command", required=True
    )
    p_cap_find = cap_single_sub.add_parser(
        "find", help="rank executor candidates for a need"
    )
    p_cap_find.add_argument("need", help="free-text need, e.g. 'inspect this repo'")
    p_cap_find.add_argument("--limit", type=int, default=10)
    p_cap_explain = cap_single_sub.add_parser(
        "explain", help="explain one capability in full"
    )
    p_cap_explain.add_argument("capability_id", help="capability id")

    p_a2a = sub.add_parser("a2a", help="A2A v1.0 remote interop")
    a2a_sub = p_a2a.add_subparsers(dest="a2a_command", required=True)
    a2a_sub.add_parser("peers", help="list registered remote A2A peers")
    a2a_sub.add_parser("card", help="show AgentOS's minimal read-only agent card")
    p_a2a_test = a2a_sub.add_parser(
        "test", help="end-to-end interop probe against a peer URL"
    )
    p_a2a_test.add_argument("url", help="peer base URL (loopback for tests)")
    p_a2a_test.add_argument("--skill", default="a2a.test")
    p_a2a_register = a2a_sub.add_parser(
        "register", help="register a remote peer as an ExecutorCell"
    )
    p_a2a_register.add_argument("url", help="peer base URL")
    p_a2a_register.add_argument(
        "--evidence", default=None, help="JSON list of approval evidence"
    )

    p_durable = sub.add_parser("durable", help="durable execution backend status")
    durable_sub = p_durable.add_subparsers(dest="durable_command", required=False)
    durable_sub.add_parser("status", help="native backend + Temporal evaluation")

    p_bench = sub.add_parser("benchmark", help="real executor benchmarks")
    bench_sub = p_bench.add_subparsers(dest="benchmark_command", required=True)
    p_bench_run = bench_sub.add_parser("run", help="run one benchmark")
    p_bench_run.add_argument("--executor", default="local-worker")
    p_bench_run.add_argument("--workload", default="A")
    p_bench_report = bench_sub.add_parser("report", help="measured benchmark report")
    p_bench_report.add_argument("--executor", default=None)
    p_bench_report.add_argument("--workload", default=None)

    p_usage = sub.add_parser("usage", help="usage and scarcity status")
    usage_sub = p_usage.add_subparsers(dest="usage_command", required=False)
    usage_sub.add_parser("status", help="per-executor scarcity profiles + snapshot count")
    usage_sub.add_parser("snapshots", help="listed imported usage snapshots (raw data)")
    p_usnap = usage_sub.add_parser(
        "resolve", help="resolve winning usage snapshot by source precedence"
    )
    p_usnap.add_argument("--provider", default=None, help="filter by provider")
    p_usnap.add_argument("--account", default=None, help="filter by account cell")
    p_uimport = usage_sub.add_parser(
        "import", help="import ONE usage snapshot (validated; JSON object)"
    )
    p_uimport.add_argument("--snapshot", required=True, help="usage snapshot JSON")
    usage_sub.add_parser(
        "refresh",
        help="refresh from an operator-configured local export file (no network)",
    )
    p_uadd = usage_sub.add_parser(
        "add", help="add one usage-ledger entry (RECORD behavior)"
    )
    p_uadd.add_argument("--provider", required=True, help="provider name")
    p_uadd.add_argument("--category", default="manual", help="ledger category")
    p_uadd.add_argument("--account", default="", help="account cell label")
    p_uadd.add_argument("--amount", type=float, default=None, help="amount")
    p_uadd.add_argument("--unit", default="", help="amount unit")
    p_uadd.add_argument("--used-pct", type=float, default=None, dest="used_pct")
    p_uadd.add_argument("--refills-at", default=None, dest="refills_at")
    p_uadd.add_argument("--note", default="")
    p_uledger = usage_sub.add_parser("ledger", help="usage-ledger records + summary")
    p_uledger.add_argument("--provider", default=None)
    p_uledger.add_argument("--category", default=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(args.json)
    try:
        with AgentOS(home=args.home) as app:
            return dispatch(app, args, as_json)
    except AgentOSError as exc:
        return emit_error(str(exc), as_json)
    except KeyboardInterrupt:
        return emit_error("interrupted", as_json)


def dispatch(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    cmd = args.command
    if cmd == "init":
        return cmd_init(app, args, as_json)
    if cmd == "status":
        return cmd_status(app, args, as_json)
    if cmd in ("objective", "objectives"):
        return cmd_objective(app, args, as_json)
    if cmd == "capabilities":
        return cmd_capabilities(app, args, as_json)
    if cmd == "agents":
        return cmd_agents(app, args, as_json)
    if cmd == "run":
        return cmd_run(app, args, as_json)
    if cmd == "verify":
        return cmd_verify(app, args, as_json)
    if cmd == "project":
        return cmd_project(app, args, as_json)
    if cmd == "recover":
        return cmd_recover(app, args, as_json)
    if cmd == "events":
        return cmd_events(app, args, as_json)
    if cmd == "inspect":
        return cmd_inspect(app, args, as_json)
    if cmd == "doctor":
        return cmd_doctor(app, args, as_json)
    if cmd == "node":
        return cmd_node(app, args, as_json)
    if cmd == "delegate":
        return cmd_delegate(app, args, as_json)
    if cmd == "serve":
        return cmd_serve(app, args, as_json)
    if cmd == "mcp":
        return cmd_mcp(app, args, as_json)
    if cmd == "ecosystem":
        return cmd_ecosystem(app, args, as_json)
    if cmd == "workflow":
        return cmd_workflow(app, args, as_json)
    if cmd == "efficiency":
        return cmd_efficiency(app, args, as_json)
    if cmd == "lverify":
        return cmd_lverify(app, args, as_json)
    if cmd == "coach":
        return cmd_coach(app, args, as_json)
    if cmd == "workflow-compiler":
        return cmd_workflow_compiler(app, args, as_json)
    if cmd == "skill":
        return cmd_skill(app, args, as_json)
    if cmd == "role":
        return cmd_role(app, args, as_json)
    if cmd == "autonomy":
        return cmd_autonomy(app, args, as_json)
    if cmd == "fleet":
        return cmd_fleet(app, args, as_json)
    if cmd == "routing":
        return cmd_routing(app, args, as_json)
    if cmd == "routine":
        return cmd_routine(app, args, as_json)
    if cmd == "backup":
        return cmd_backup(app, args, as_json)
    if cmd == "bot":
        return cmd_bot(app, args, as_json)
    if cmd == "gaps":
        return cmd_gaps(app, args, as_json)
    if cmd == "learn":
        return cmd_learn(app, args, as_json)
    if cmd == "plans":
        return cmd_plans(app, args, as_json)
    if cmd == "quality":
        return cmd_quality(app, args, as_json)
    if cmd == "policy":
        return cmd_policy(app, args, as_json)
    if cmd == "federate":
        return cmd_federate(app, args, as_json)
    if cmd == "route":
        return cmd_route(app, args, as_json)
    if cmd == "capability":
        return cmd_capability(app, args, as_json)
    if cmd == "a2a":
        return cmd_a2a(app, args, as_json)
    if cmd == "durable":
        return cmd_durable(app, args, as_json)
    if cmd == "benchmark":
        return cmd_benchmark(app, args, as_json)
    if cmd == "usage":
        return cmd_usage(app, args, as_json)
    if cmd == "governor":
        return cmd_governor(app, args, as_json)
    if cmd == "supervisor":
        return cmd_supervisor(app, args, as_json)
    if cmd in ("execution", "executions"):
        return cmd_execution(app, args, as_json)
    raise AgentOSError(f"unknown command {cmd!r}")


# ------------------------------------------------------------------
# commands
# ------------------------------------------------------------------
def cmd_init(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    result = app.init(reset=args.reset)
    if as_json:
        return emit_json({"ok": True, **result})
    print(_paint("AgentOS initialized", BOLD))
    print(f"  home : {result['home']}")
    print(f"  db   : {result['db']}")
    print(f"  capabilities: {', '.join(result['capabilities'])}")
    return 0


def cmd_status(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    status = app.status()
    dashboard = app.dashboard()
    if as_json:
        return emit_json({"ok": True, **status, "dashboard": dashboard})
    print(_paint("AgentOS status", BOLD))
    print(f"  home : {status['home']}")
    counts = status["counts"]
    print(
        "  objectives: "
        f"{counts['objectives']}  executions: {counts['executions']}  "
        f"events: {counts['events']}  failures: {counts['failures']}"
    )
    print(
        f"  capabilities: {status['capabilities']} "
        f"({status['capabilities_healthy']} healthy)"
    )
    by_state = status["objectives_by_state"]
    if by_state:
        print("  by state:")
        for state in sorted(by_state):
            print(f"    {state_painted(state):<28} {by_state[state]}")
    else:
        print("  no objectives yet — create one with: agentos objective create \"...\"")
    active = dashboard["active_objectives"]
    print(f"  active: {len(active)} running")
    for item in active[:5]:
        print(f"    {item['id']}  {item['title'][:60]}")
    stuck = dashboard["stuck"]
    print(f"  stuck (failed/blocked): {len(stuck)}")
    for item in stuck[:5]:
        print(f"    {state_painted(item['status'])} {item['id']}  {item['title'][:50]}")
    current = dashboard["current_strategy"]
    if current:
        print(f"  strategy: {current['strategy']} — {current['rationale'][:100]}")
    pending = dashboard["pending_approvals"]
    if pending:
        print(f"  approvals pending: {', '.join(pending)}")
    recent = dashboard["recent_events"]
    if recent:
        print("  recent:")
        for event in recent[-5:]:
            print(f"    {event['at'][:19]}  {event['type']}")
    return 0


def cmd_objective(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "objective_command", None) or "list"
    if sub == "create":
        params = _parse_json_object(args.params, "--params")
        context = _parse_json_object(args.context, "--context")
        constraints = _parse_json_list(args.constraints, "--constraints")
        objective = app.create_objective(
            args.title,
            description=args.description,
            priority=args.priority,
            operation=args.operation,
            params=params,
            constraints=constraints,
            parent_id=args.parent,
            extra_context=context,
        )
        if as_json:
            return emit_json({"ok": True, "objective": objective.to_dict()})
        print(f"created {objective.id} [{state_painted(objective.status.value)}]")
        print(f"  {objective.title}")
        return 0
    if sub == "list" or args.command == "objectives":
        objectives = app.list_objectives(status=args.status)
        if as_json:
            return emit_json(
                {"ok": True, "objectives": [o.to_dict() for o in objectives]}
            )
        if not objectives:
            print("no objectives found")
            return 0
        for objective in objectives:
            print(
                f"{objective.id}  [{state_painted(objective.status.value)}]  "
                f"{objective.priority.value:<8} {objective.title}"
            )
        return 0
    if sub == "show":
        objective = app.get_objective(args.objective_id)
        if as_json:
            return emit_json({"ok": True, "objective": objective.to_dict()})
        print(_paint(objective.id, BOLD) + f" [{state_painted(objective.status.value)}]")
        print(f"  title   : {objective.title}")
        print(f"  priority: {objective.priority.value}")
        print(f"  verify  : {objective.verification_status.value}")
        if objective.description:
            print(f"  desc    : {objective.description}")
        if objective.strategy:
            print(f"  strategy: {objective.strategy}")
        if objective.next_action:
            print(f"  next    : {objective.next_action}")
        return 0
    if sub == "cancel":
        objective = app.cancel_objective(args.objective_id)
        if as_json:
            return emit_json({"ok": True, "objective": objective.to_dict()})
        print(f"{objective.id} [{state_painted(objective.status.value)}]")
        return 0
    raise AgentOSError(f"unknown objective subcommand {sub!r}")


def cmd_capabilities(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "capabilities_command", None) or "list"
    if sub == "list":
        capabilities = app.capabilities(refresh=bool(getattr(args, "refresh", False)))
        if as_json:
            return emit_json(
                {"ok": True, "capabilities": [c.to_dict() for c in capabilities]}
            )
        if not capabilities:
            print("no capabilities registered — run: agentos init")
            return 0
        for capability in capabilities:
            print(
                f"{capability.id}  [{state_painted(capability.health.value)}]  "
                f"{capability.type.value:<10} {capability.name}"
            )
            print(f"    ops: {', '.join(capability.operations)}")
            print(f"    adapter: {capability.adapter}  source: {capability.source}")
        return 0
    if sub == "add":
        capability = app.add_configured_cli(
            capability_id=args.capability_id,
            name=args.name or args.capability_id,
            command=args.shell_command,
            description=args.description,
            verify_expected=args.expect,
        )
        if as_json:
            return emit_json({"ok": True, "capability": capability.to_dict()})
        print(f"registered {capability.id} (operation: {capability.operations[0]})")
        return 0
    if sub == "remove":
        removed = app.remove_capability(args.capability_id)
        if as_json:
            return emit_json({"ok": True, "removed": removed})
        print(
            f"removed {args.capability_id}"
            if removed
            else f"no such capability: {args.capability_id}"
        )
        return 0 if removed else 1
    if sub == "inspect":
        return cmd_capabilities_inspect(app, args, as_json)
    raise AgentOSError(f"unknown capabilities subcommand {sub!r}")


def cmd_capabilities_inspect(
    app: AgentOS, args: argparse.Namespace, as_json: bool
) -> int:
    """Readiness + evidence ledger view. Formatting only; logic in registry."""
    capabilities = app.capabilities()
    if getattr(args, "capability_id", None):
        matches = [c for c in capabilities if c.id == args.capability_id]
        if not matches:
            raise AgentOSError(f"unknown capability: {args.capability_id}")
        capabilities = matches
    if as_json:
        return emit_json(
            {"ok": True, "capabilities": [c.to_dict() for c in capabilities]}
        )
    if not capabilities:
        print("no capabilities registered — run: agentos init")
        return 0
    print(f"{'capability':<14}{'readiness':<24}{'health':<12}exec ok/fail")
    for capability in capabilities:
        print(
            f"{capability.id:<14}"
            f"{state_painted(capability.readiness):<24}"
            f"{state_painted(capability.health.value):<12}"
            f"{capability.execution_count} "
            f"{capability.success_count}/{capability.failure_count}"
            + (
                f"  last_error={capability.last_error[:80]}"
                if capability.last_error
                else ""
            )
        )
    return 0


def cmd_agents(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    agents = app.agents()
    if as_json:
        return emit_json({"ok": True, "agents": [a.to_dict() for a in agents]})
    if not agents:
        print("no agent/model capabilities registered yet")
        print("future adapters (Grok/xAI, OpenCode, MCP, ...) will appear here")
        return 0
    for agent in agents:
        print(f"{agent.id}  [{state_painted(agent.health.value)}]  {agent.name}")
    return 0


def cmd_run(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    params = None
    if getattr(args, "params", None) is not None:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise AgentOSError("--params must be a JSON object") from exc
        if not isinstance(params, dict):
            raise AgentOSError("--params must be a JSON object")
    objective = app.store.get_objective(args.objective_id)
    if objective is None and args.objective_id.startswith("obj_"):
        raise AgentOSError(f"unknown objective: {args.objective_id}")
    if objective is None:
        report = app.delegate(
            args.objective_id,
            operation=args.operation,
            params=params,
            max_attempts=args.max_attempts,
            timeout_seconds=args.timeout,
        )
    else:
        report = app.run(
            args.objective_id,
            operation=args.operation,
            params=params,
            max_attempts=args.max_attempts,
            timeout_seconds=args.timeout,
            force=args.force,
        )
    trace = app.store.get_job_trace(report.objective_id) if getattr(args, "explain", False) else None
    if as_json:
        payload = {"ok": report.state == ObjectiveState.COMPLETED, **report.to_dict()}
        if trace is not None:
            payload["trace"] = trace
        print(json.dumps(payload, indent=2, default=str))
        return 0 if report.state == ObjectiveState.COMPLETED else 1
    print(f"objective {report.objective_id} -> {state_painted(report.state.value)}")
    if report.capability_id:
        print(f"  capability: {report.capability_id}  attempt: {report.attempt}")
    if report.execution_id:
        print(f"  execution : {report.execution_id}")
    if report.state == ObjectiveState.COMPLETED and report.result:
        print("  result:")
        for key, value in report.result.items():
            text = str(value)
            if len(text) > 500:
                text = text[:500] + "…"
            print(f"    {key}: {text}")
    if report.next_action:
        print(f"  next: {report.next_action}")
    if report.failure:
        print(f"  failure: {report.failure.get('type')}: {report.failure.get('detail')}")
    if trace is not None:
        print(f"  trace: {json.dumps(trace, sort_keys=True, default=str)}")
    return 0 if report.state == ObjectiveState.COMPLETED else 1


def cmd_verify(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    verification = app.verify(args.objective_id)
    if as_json:
        print(json.dumps({"ok": verification.verified, **verification.to_dict()}, indent=2, default=str))
        return 0 if verification.verified else 1
    print(
        f"verification {verification.id} [{state_painted('VERIFIED' if verification.verified else 'FAILED')}]"
    )
    print(f"  method: {verification.method}")
    stage_items = [
        item
        for item in verification.evidence
        if item.kind.startswith("stage:") and item.kind.count(":") == 1
    ]
    for item in verification.evidence:
        if item in stage_items:
            continue
        print(f"  - [{item.kind}] {item.detail}")
    if stage_items:
        print("  stages:")
        for item in stage_items:
            print(f"    - [{item.kind.split(':', 1)[1].upper()}] {item.detail}")
    return 0 if verification.verified else 1


def cmd_project(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Project-level operations. Formatting only; logic in services."""
    sub = getattr(args, "project_command", None)
    if sub == "verify":
        stages = (
            [s.strip() for s in str(args.stages).split(",") if s.strip()]
            if args.stages
            else None
        )
        verification = app.verify_project(
            args.path, stages=stages, objective_id=args.objective
        )
        if as_json:
            print(json.dumps({"ok": verification.verified, **verification.to_dict()}, indent=2, default=str))
            return 0 if verification.verified else 1
        print(
            f"project verification {verification.id} "
            f"[{state_painted('VERIFIED' if verification.verified else 'FAILED')}]"
        )
        print(f"  method: {verification.method}")
        for item in verification.evidence:
            if item.kind.startswith("stage:") and item.kind.count(":") == 1:
                print(f"  - [{item.kind.split(':', 1)[1].upper()}] {item.detail}")
            elif item.kind in ("policy_block", "pipeline"):
                print(f"  - [{item.kind}] {item.detail}")
        return 0 if verification.verified else 1
    raise AgentOSError(f"unknown project subcommand {sub!r}")


def cmd_recover(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    report = app.recover(
        args.objective_id,
        max_attempts=args.max_attempts,
        operation=args.operation,
    )
    if as_json:
        print(json.dumps({"ok": report.state == ObjectiveState.COMPLETED, **report.to_dict()}, indent=2, default=str))
        return 0 if report.state == ObjectiveState.COMPLETED else 1
    print(f"recovery {report.objective_id} -> {state_painted(report.state.value)}")
    if report.next_action:
        print(f"  next: {report.next_action}")
    return 0 if report.state == ObjectiveState.COMPLETED else 1


def cmd_events(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    events = app.events(objective_id=args.objective, limit=args.limit)
    if as_json:
        return emit_json({"ok": True, "events": events})
    if not events:
        print("no events recorded")
        return 0
    for event in events:
        scope = event.get("objective_id") or "-"
        print(f"{event['created_at']}  {event['event_type']:<24} {scope}")
    return 0


def cmd_inspect(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    detail = app.inspect(args.objective_id)
    if as_json:
        return emit_json({"ok": True, **detail})
    objective = detail["objective"]
    print(_paint(objective["id"], BOLD) + f" [{state_painted(objective['status'])}]")
    print(f"  title   : {objective['title']}")
    print(f"  verify  : {objective['verification_status']}")
    if objective.get("strategy"):
        print(f"  strategy: {objective['strategy']}")
    if objective.get("next_action"):
        print(f"  next    : {objective['next_action']}")
    print(f"  executions  : {len(detail['executions'])}")
    for execution in detail["executions"]:
        print(
            f"    {execution['id']} [{state_painted(execution['status'])}] "
            f"{execution['capability_id']}:{execution['operation']} "
            f"attempt={execution['attempt']}"
        )
    print(f"  verifications: {len(detail['verifications'])}")
    for verification in detail["verifications"]:
        mark = "pass" if verification["verified"] else "FAIL"
        print(f"    {verification['id']} [{mark}] method={verification['method']}")
    print(f"  failures    : {len(detail['failures'])}")
    for failure in detail["failures"]:
        print(f"    [{failure['failure_type']}] attempt={failure['attempt']}: {failure['detail'][:200]}")
    print(f"  evidence    : {len(objective.get('evidence', []))}")
    for item in objective.get("evidence", []):
        print(f"    - [{item['kind']}] {item['detail'][:200]}")
    print(f"  events      : {len(detail['events'])}")
    return 0


def cmd_doctor(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    result = app.doctor()
    if as_json:
        return emit_json({"ok": result["ok"], **result})
    print(_paint("AgentOS doctor", BOLD))
    for check in result["checks"]:
        mark = "PASS" if check["ok"] else "FAIL"
        print(f"  [{state_painted(mark)}] {check['name']}: {check['detail']}")
    return 0 if result["ok"] else 1


def cmd_node(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    result = app.federation_nodes(refresh=getattr(args, "refresh", False))
    if as_json:
        return emit_json({"ok": True, **result})
    node = result["node"]
    print(_paint("federation node", BOLD))
    print(
        f"  state: [{state_painted(node['state'])}]  "
        f"source={node['source']}  manual={node['manual']}"
    )
    if node.get("detail"):
        print(f"  detail: {node['detail']}")
    for record in result["nodes"]:
        mark = "ELIGIBLE" if record["eligibility"] == "ELIGIBLE" else "INELIGIBLE"
        print(
            f"  {record['node']:<16} [{state_painted(mark)}]  "
            f"tier={record['tier']:<22} online={record['requires_online']} "
            f"persistent_remote={record['persistent_remote']}"
        )
        print(f"    gate: {record['gate']}")
    return 0


def cmd_delegate(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    params = _parse_json_object(args.params, "--params")
    constraints = _parse_json_list(args.constraints, "--constraints")
    extra: dict[str, Any] = {}
    if args.items:
        try:
            items = json.loads(args.items)
        except json.JSONDecodeError as exc:
            raise AgentOSError(f"--items must be valid JSON: {exc}")
        if not isinstance(items, list):
            raise AgentOSError("--items must be a JSON list")
        extra["items"] = items
    if args.strategy:
        extra["strategy"] = args.strategy
    report = app.delegate(
        args.title,
        description=args.description,
        priority=args.priority,
        operation=args.operation,
        params=params or None,
        constraints=constraints or None,
        max_attempts=args.max_attempts,
    ) if not extra else _delegate_with_context(
        app, args, params, constraints, extra
    )
    if as_json:
        print(json.dumps({"ok": report.state == ObjectiveState.COMPLETED, **report.to_dict()}, indent=2, default=str))
        return 0 if report.state == ObjectiveState.COMPLETED else 1
    print(f"delegated {report.objective_id} -> {state_painted(report.state.value)}")
    if report.next_action:
        print(f"  next: {report.next_action}")
    if report.failure:
        print(f"  failure: {report.failure.get('type')}: {report.failure.get('detail')}")
    return 0 if report.state == ObjectiveState.COMPLETED else 1


def _delegate_with_context(app, args, params, constraints, extra):
    objective = app.create_objective(
        args.title,
        description=args.description,
        priority=args.priority,
        operation=args.operation,
        params=params or None,
        constraints=constraints or None,
        extra_context=extra,
    )
    return app.run(objective.id, max_attempts=args.max_attempts)


def cmd_serve(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    from agentos.api import create_server

    server = create_server(app, host=args.host, port=args.port)
    address = f"http://{args.host}:{server.server_address[1]}"
    if as_json:
        print(json.dumps({"ok": True, "endpoint": address}, indent=2))
    else:
        print(_paint(f"AgentOS API serving on {address}", BOLD))
        print("  endpoints: /health /status /capabilities /objectives /events")
        print("             /plans /patterns /gaps /delegate  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_mcp(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "mcp_command", None)
    if sub is None:
        from agentos import mcp_server

        return mcp_server.serve_stdio(app)
    if sub == "discover":
        result = app.mcp_discover(args.query, limit=args.limit)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"registry: {result['status']} — {result['detail']}")
        for candidate in result["candidates"]:
            print(f"  {candidate['id']}  [{candidate['state']}]  {candidate['name']}")
        return 0
    if sub == "candidates":
        candidates = app.mcp_candidates()
        if as_json:
            return emit_json({"ok": True, "candidates": candidates})
        if not candidates:
            print("no MCP candidates recorded")
            return 0
        for candidate in candidates:
            print(
                f"{candidate['id']}  [{candidate['state']}]  "
                f"risk={candidate['risk_class']}  {candidate['name']}"
            )
        return 0
    if sub == "inspect":
        result = app.mcp_inspect(args.candidate_id)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"{args.candidate_id} -> {result['candidate']['state']}")
        return 0
    if sub == "test":
        result = app.mcp_test(args.candidate_id)
        if as_json:
            return emit_json({"ok": True, **result})
        test_result = result["candidate"]["test_result"] or {}
        print(
            f"{args.candidate_id} -> {result['candidate']['state']} "
            f"passed={test_result.get('passed')}"
        )
        for finding in test_result.get("findings", []):
            print(f"  - {finding[:160]}")
        return 0
    if sub == "approve":
        evidence = _parse_json_list(args.evidence, "--evidence")
        result = app.mcp_approve(args.candidate_id, evidence=evidence or None)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"{args.candidate_id} -> {result['candidate']['state']}")
        return 0
    if sub == "reject":
        result = app.mcp_reject(args.candidate_id, reason=args.reason)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"{args.candidate_id} -> {result['candidate']['state']}")
        return 0
    if sub == "revoke":
        result = app.mcp_revoke(args.candidate_id, reason=args.reason)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"{args.candidate_id} -> {result['candidate']['state']}")
        return 0
    raise AgentOSError(f"unknown mcp subcommand {sub!r}")


def cmd_gaps(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    gaps = app.gaps(args.objective)
    if as_json:
        return emit_json({"ok": True, "gaps": gaps})
    if not gaps:
        print("no capability gaps recorded")
        return 0
    for gap in gaps:
        print(_paint(gap["id"], BOLD) + f"  op={gap['operation']}")
        print(f"  required: {gap['required_capability']}")
        print(f"  why     : {gap['why_required'][:160]}")
        if gap["alternatives"]:
            print(f"  alt     : {', '.join(gap['alternatives'])}")
        print(f"  missing : {gap['missing_interface'][:160]}")
    return 0


def cmd_learn(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    patterns = app.patterns(args.obj_class)
    if as_json:
        return emit_json({"ok": True, "patterns": patterns})
    if not patterns:
        print("no learned patterns yet — run objectives to teach the router")
        return 0
    for pattern in patterns[:30]:
        mark = "PASS" if pattern["verified"] else "FAIL"
        print(
            f"[{state_painted(mark)}] {pattern['objective_class']} "
            f"via {pattern['capability_id']} ({pattern['strategy']})"
        )
        print(f"    {pattern['lesson'][:140]}")
    return 0


def cmd_plans(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    plans = app.plans(args.objective)
    if as_json:
        return emit_json({"ok": True, "plans": plans})
    if not plans:
        print("no plans recorded")
        return 0
    for plan in plans:
        print(_paint(plan["id"], BOLD) + f"  [{plan['strategy']}]")
        print(f"  rationale: {plan['rationale'][:200]}")
        for step in plan["steps"]:
            print(
                f"    - {step['kind']}:{step['operation']} "
                f"-> {step['capability_id'] or 'none'}"
                + (f" [{step['role']}]" if step.get("role") else "")
            )
    return 0


def cmd_quality(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    evaluations = app.quality(args.objective_id)
    if as_json:
        return emit_json({"ok": True, "quality": evaluations})
    if not evaluations:
        print("no quality evaluations recorded")
        return 0
    for evaluation in evaluations:
        print(
            f"{evaluation['id']} [{state_painted(evaluation['grade'].upper())}] "
            f"score={evaluation['score']} evaluator={evaluation['evaluator']}"
        )
        for finding in evaluation["findings"]:
            print(f"  - {finding[:160]}")
    return 0


def cmd_policy(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "policy_command", None) or "show"
    if sub == "show":
        policy = app.policy_view()
        if as_json:
            return emit_json({"ok": True, "policy": policy})
        print(_paint("execution policy", BOLD))
        print(f"  destructive allowed : {policy['allow_destructive']}")
        print(f"  network allowed     : {policy['allow_network']}")
        print(f"  approvals required  : {', '.join(policy['approval_required_classes'])}")
        print(f"  approvals granted   : {', '.join(policy['approvals_granted']) or 'none'}")
        print(f"  write roots         : {', '.join(policy['allowed_write_roots']) or 'unrestricted'}")
        print(f"  blocked patterns    : {len(policy['blocked_command_patterns'])}")
        return 0
    if sub == "grant":
        policy = app.grant_approval(args.operation_class)
        if as_json:
            return emit_json({"ok": True, "policy": policy})
        print(f"granted approval class: {args.operation_class}")
        return 0
    if sub == "revoke":
        policy = app.revoke_approval(args.operation_class)
        if as_json:
            return emit_json({"ok": True, "policy": policy})
        print(f"revoked approval class: {args.operation_class}")
        return 0
    raise AgentOSError(f"unknown policy subcommand {sub!r}")


def cmd_federate(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Federation status / executors / capabilities / matrix / distill /
    jobs / route / submit / packet."""
    sub = getattr(args, "federate_command", None)
    if sub == "status":
        status = app.federation_status()
        if as_json:
            return emit_json({"ok": True, **status})
        print(_paint("federation status", BOLD))
        for cell_id in status["executors"]:
            print(f"  executor: {cell_id}")
        print(f"  cells by tier : {status['cells_by_tier']}")
        print(f"  cells by health: {status['cells_by_health']}")
        print(f"  jobs: {status['jobs']}  results: {status['results']}")
        grok = status["grokbot_office"]
        if grok:
            print(
                f"  grokbot-office: [{state_painted(grok['health'])}] "
                f"{grok['band']} tier={grok['tier']} usable={grok['usable']}"
            )
        tel = status["telemetry"]
        print(
            f"  telemetry: measured_cost_samples={tel['measured_cost_samples']} "
            f"total={tel['measured_cost_total']}"
        )
        return 0
    if sub == "executors":
        cells = app.federation_executors(refresh=args.refresh)
        if as_json:
            return emit_json({"ok": True, "executors": cells})
        if not cells:
            print("no executor cells registered")
            return 0
        for cell in cells:
            print(
                f"{cell['id']}  [{state_painted(cell['health'])}]  "
                f"{cell['tier_name']}  provider={cell['provider']}"
            )
            print(
                f"    usable={cell['usable']} band={cell['band']} "
                f"premium={cell['premium']} data_max={cell['data_class_max']}"
            )
            if cell["supported_operations"]:
                print(f"    ops: {', '.join(cell['supported_operations'][:8])}")
        return 0
    if sub == "capabilities":
        capabilities = app.federation_capabilities(refresh=args.refresh)
        if as_json:
            return emit_json({"ok": True, "capabilities": capabilities})
        if not capabilities:
            print("no capabilities registered — run: agentos init")
            return 0
        for capability in capabilities:
            print(
                f"{capability['id']}  [{state_painted(capability['health'])}]  "
                f"{capability['type']:<12} {capability['name']}"
            )
            print(
                f"    adapter={capability['adapter']} source={capability['source']} "
                f"usable={capability['usable']} readiness={capability['readiness']}"
            )
            if capability["operations"]:
                print(f"    ops: {', '.join(capability['operations'][:8])}")
        return 0
    if sub == "matrix":
        matrix = app.federation_matrix(refresh=args.refresh)
        if as_json:
            return emit_json({"ok": True, **matrix})
        print(_paint("executor x capability routing matrix", BOLD))
        current_executor = None
        for row in matrix["rows"]:
            if row["executor"] != current_executor:
                current_executor = row["executor"]
                print(
                    f"  {current_executor}  "
                    f"[{state_painted(row['health'])}]  "
                    f"{row['tier_name']}  avail={row['availability']}"
                )
            print(f"    {row['capability']:<24} usable={row['usable']}")
        return 0
    if sub == "distill":
        distilled = app.federation_distill(refresh=args.refresh)
        if as_json:
            return emit_json({"ok": True, **distilled})
        print(_paint("distilled federation context", BOLD))
        print(
            f"  entries: {distilled['entries']}  sources: "
            f"{', '.join(distilled['sources'])}  facts: {distilled['fact_count']}"
        )
        print(f"  note: {distilled['note']}")
        payload = distilled["distilled"]
        if payload["facts"]:
            print("  facts:")
            for fact in payload["facts"][:10]:
                sources = " ".join(f"<{r}>" for r in fact.get("refs") or [])
                print(f"    - {fact['fact']} {sources}".rstrip())
        if payload["uncertainties"]:
            print("  uncertainties:")
            for uncertainty in payload["uncertainties"][:5]:
                print(f"    - {uncertainty}")
        if payload["contradictions"]:
            print(f"  contradictions: {len(payload['contradictions'])}")
        return 0
    if sub == "jobs":
        jobs = app.federation_jobs(status=args.status, limit=args.limit)
        if as_json:
            return emit_json({"ok": True, "jobs": jobs})
        if not jobs:
            print("no federation jobs recorded")
            return 0
        for job in jobs:
            envelope = job["envelope"]
            decision = job["decision"]
            chosen = decision.get("chosen") or "-"
            print(
                f"{job['id']}  [{state_painted(job['status'])}]  "
                f"{envelope.get('targetCapability')} -> {chosen}"
            )
        return 0
    if sub == "route":
        job = _parse_json_object(args.job, "--job")
        if getattr(args, "explain", False):
            result = app.federate_route_explain(job)
        else:
            result = app.federate_route(job)
        return emit_json({"ok": True, **result})
    if sub == "doctor":
        result = app.federate_doctor()
        if as_json:
            return emit_json({"ok": result["ok"], **result})
        print(_paint("federation doctor", BOLD))
        for check in result["checks"]:
            print(f"  [{check['category']}] {check['name']}: {check['detail']}")
        return 0 if result["ok"] else 1
    if sub == "cells":
        cells = app.federate_cells()
        if as_json:
            return emit_json({"ok": True, "cells": cells})
        for cell in cells:
            print(
                f"{cell['id']}  [{state_painted(cell['health'])}]  "
                f"provider={cell['provider']} trust={cell.get('trust', 'UNTRUSTED')}"
            )
        return 0
    if sub == "health":
        result = app.federate_health()
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"cells: {result['cells']}  usable: {result['usable']}")
        for health, count in sorted(result["by_health"].items()):
            print(f"  {health}: {count}")
        return 0
    if sub == "submit":
        job = _parse_json_object(args.job, "--job")
        return emit_json({"ok": True, **app.federate_submit(job)})
    if sub == "packet":
        return emit_json({"ok": True, **app.federation_packet(args.line)})
    if sub == "telemetry":
        return emit_json({"ok": True, **app.federation_telemetry()})
    raise AgentOSError(f"unknown federate subcommand {sub!r}")


def cmd_route(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Top-level explainable routing: task class, candidates, choice, why."""
    job = _parse_json_object(args.job, "--job")
    result = app.federate_route_explain(job)
    if as_json:
        return emit_json({"ok": True, **result})
    print(_paint("route explanation", BOLD))
    print(f"  task class : {result['task_class']}")
    print(f"  requires   : {', '.join(result['required_capabilities'])}")
    print(
        f"  quality    : {result['quality_floor']}  "
        f"data: {result['data_class']}  node: {result['node_state']}"
    )
    print("  candidates:")
    for candidate in result["candidates"]:
        mark = "eligible" if candidate["eligible"] else "INELIGIBLE"
        print(
            f"    - {candidate['executor']}  score={candidate.get('score', 0)}  "
            f"{mark}"
        )
    print(f"  chosen     : {result['chosen']}")
    print(f"  why        : {result['why'][:300]}")
    print(f"  fallback   : {result['fallback']}")
    print(f"  premium/scarce required: {result['premium_scarce_required']}")
    print(f"  approval required      : {result['approval_required']}")
    return 0


def cmd_capability(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "capability_command", None)
    if sub == "find":
        result = app.capability_find(args.need, limit=args.limit)
        if as_json:
            return emit_json({"ok": True, **result})
        if not result["candidates"]:
            print(f"no executor candidate matches {args.need!r}")
            return 0
        for candidate in result["candidates"]:
            print(
                f"{candidate['executor']}  [{state_painted(candidate['health'])}]  "
                f"usable={candidate['usable']} {candidate['local_remote']} "
                f"cost={candidate['cost_scarcity']}"
            )
        return 0
    if sub == "explain":
        result = app.capability_explain(args.capability_id)
        if as_json:
            return emit_json({"ok": True, **result})
        capability = result["capability"]
        print(_paint(capability["id"], BOLD) + f"  [{capability['health']}]")
        print(f"  operations: {', '.join(capability['operations'][:8])}")
        print(f"  approval  : {result['approval']}")
        print(f"  history   : {result['history']}")
        cell = result["executor_cell"]
        if cell:
            print(f"  cell      : {cell['id']} tier={cell['tier']}")
        return 0
    raise AgentOSError(f"unknown capability subcommand {sub!r}")


def cmd_a2a(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "a2a_command", None)
    if sub == "peers":
        peers = app.a2a_peers()
        if as_json:
            return emit_json({"ok": True, "peers": peers})
        if not peers:
            print("no remote A2A peers registered")
            return 0
        for peer in peers:
            print(f"{peer['id']}  [{state_painted(peer['health'])}]")
        return 0
    if sub == "card":
        card = app.a2a_card()
        if as_json:
            return emit_json({"ok": True, "card": card})
        print(_paint("AgentOS agent card (minimal, read-only)", BOLD))
        print(f"  name: {card['name']}  version: {card['version']}")
        for skill in card["skills"]:
            print(f"  skill: {skill['id']} — {skill['description'][:100]}")
        return 0
    if sub == "test":
        result = app.a2a_test(args.url, skill=args.skill)
        if as_json:
            return emit_json({**result})
        print("A2A interop: " + ("OK" if result["ok"] else "FAILED"))
        for step in result["steps"]:
            print(f"  {step}")
        return 0 if result["ok"] else 1
    if sub == "register":
        evidence = _parse_json_list(args.evidence, "--evidence")
        result = app.a2a_register_peer(args.url, evidence=evidence or None)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"registered {result['cell']['id']} (UNTRUSTED, HS-capped)")
        return 0
    raise AgentOSError(f"unknown a2a subcommand {sub!r}")


def cmd_durable(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    result = app.durable_status()
    if as_json:
        return emit_json({"ok": True, **result})
    print(_paint("durable execution", BOLD))
    print(f"  backend: {result['backend']}  durable jobs: {result['durable_jobs']}")
    print(f"  smoke  : {'OK' if result['smoke']['ok'] else 'FAILED'}")
    temporal = result["temporal"]
    print(f"  temporal: {temporal['status']}")
    return 0


def cmd_benchmark(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "benchmark_command", None)
    if sub == "run":
        result = app.benchmark_run(args.executor, args.workload)
        if as_json:
            return emit_json({"ok": True, **result})
        record = result["record"]
        print(
            f"{record['executor']}/{record['workload']} -> {record['status']} "
            f"latency={record['latency_ms']}ms quality={record['quality_signal']}"
        )
        return 0
    if sub == "report":
        result = app.benchmark_report(
            executor=args.executor, workload=args.workload
        )
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"benchmark runs: {result['runs']}")
        for executor, stats in result["by_executor"].items():
            print(
                f"  {executor}: runs={stats['runs']} "
                f"success_rate={stats['success_rate']} "
                f"avg_latency_ms={stats['avg_latency_ms']}"
            )
        return 0
    raise AgentOSError(f"unknown benchmark subcommand {sub!r}")


def cmd_usage(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "usage_command", None) or "status"
    if sub == "status":
        result = app.usage_status()
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("usage + scarcity", BOLD))
        print(f"  reset mode: {result['reset_mode']}")
        for cell_id, profile in sorted(result["cells"].items()):
            print(
                f"  {cell_id}: quota={profile['quotaScarcity']} "
                f"used={profile['usedPct']} remaining={profile['remainingPct']}"
            )
        return 0
    if sub == "snapshots":
        result = app.usage_snapshots()
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("usage snapshots", BOLD))
        if not result["snapshots"]:
            print("  no usage snapshots imported (data-only; nothing fabricated)")
        for record in result["snapshots"]:
            snap = record["snapshot"]
            print(
                f"  {record['key']}: source={snap.get('source', '')} "
                f"used={snap.get('usedPct')}% asOf={snap.get('asOf')} "
                f"(updated {record['updated_at']})"
            )
        return 0
    if sub == "resolve":
        result = app.usage_resolve(
            provider=args.provider, account_cell=args.account
        )
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("usage resolution", BOLD))
        resolution = result["usage_resolution"]
        print(
            f"  source={resolution['source']} freshness={resolution['freshness']} "
            f"is_live={resolution['is_live']}"
        )
        if resolution["snapshot"]:
            snap = resolution["snapshot"]
            print(
                f"  provider={snap.get('provider')} account={snap.get('accountCell')} "
                f"used={snap.get('usedPct')}% remaining={snap.get('remainingPct')}%"
            )
        else:
            print("  snapshot: none")
        for warning in resolution["warnings"]:
            print(f"  warning: {warning}")
        return 0
    if sub == "import":
        try:
            snapshot = json.loads(args.snapshot)
        except json.JSONDecodeError as exc:
            raise AgentOSError(f"invalid snapshot JSON: {exc}")
        result = app.usage_import(snapshot)
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("usage snapshot imported", BOLD))
        snap = result["imported"]
        print(
            f"  provider={snap['provider']} account={snap['accountCell']} "
            f"source={snap['source']} used={snap['usedPct']}%"
        )
        return 0
    if sub == "refresh":
        result = app.usage_refresh()
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("usage refresh", BOLD))
        if result.get("ok"):
            print(f"  refreshed from {result['path']}")
        else:
            print(f"  NOT refreshed: {result.get('error', 'no data_dir configured')}")
        return 0
    if sub == "add":
        result = app.usage_add(
            provider=args.provider,
            category=args.category,
            account_cell=args.account,
            amount=args.amount,
            unit=args.unit,
            used_pct=args.used_pct,
            refills_at=args.refills_at,
            note=args.note,
        )
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("usage ledger entry", BOLD))
        print(
            f"  id={result['entry']['id']} provider={result['entry']['provider']} "
            f"amount={result['entry']['amount']}{result['entry']['unit']}"
        )
        return 0
    if sub == "ledger":
        result = app.usage_ledger(
            provider=args.provider, category=args.category
        )
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("usage ledger", BOLD))
        if not result["entries"]:
            print("  no usage-ledger entries recorded")
        for record in result["entries"]:
            entry = record["entry"]
            print(
                f"  {record['id']}: {entry['provider']}/{entry['category']} "
                f"amount={entry['amount']}{entry['unit']} at {record['created_at']}"
            )
        summary = result["summary"]
        print(
            f"  summary: {summary['entries']} entries, "
            f"amount_total={summary['amount_total']}{' '.join(summary['units_seen'])}"
        )
        return 0
    raise AgentOSError(f"unknown usage subcommand {sub!r}")


def cmd_governor(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "governor_command", None)
    if sub == "weights":
        result = app.governor_weights()
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("governor weights", BOLD))
        print(f"  source: {result['source']}")
        for factor, weight in sorted(result["weights"].items()):
            print(f"  {factor}: {weight}")
        return 0
    if sub == "route":
        try:
            job = json.loads(args.job)
        except json.JSONDecodeError as exc:
            raise AgentOSError(f"invalid job JSON: {exc}")
        result = app.governor_route(job)
        if as_json:
            return emit_json({"ok": True, **result})
        routed = result["governed_route"]
        print(_paint("governor route", BOLD))
        print(
            f"  job={routed['jobId']} target={routed['targetCapability']} "
            f"choice={routed['governed_choice']}"
        )
        print(f"  rationale: {routed['governed_rationale']}")
        print(f"  outcome: {result['outcome']['outcome']} ({result['outcome']['id']})")
        return 0
    if sub == "outcomes":
        result = app.governor_outcomes(job_id=args.job_id, limit=args.limit)
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint(f"routing outcomes (filter job={args.job_id})", BOLD))
        if not result["outcomes"]:
            print("  no routing outcomes recorded")
        for record in result["outcomes"]:
            print(
                f"  {record['id']}: job={record['job_id']} "
                f"chosen={record['chosen']} outcome={record['outcome']} "
                f"at {record['created_at']}"
            )
        if result["summary"]:
            print(_paint("verified-rate summary (learned, only from recorded rows)", BOLD))
            for executor, counts in sorted(result["summary"].items()):
                rate = (
                    f"{100.0 * counts['verified'] / counts['runs']:.0f}%"
                    if counts["runs"]
                    else "n/a"
                )
                print(
                    f"  {executor}: runs={counts['runs']} verified={counts['verified']} "
                    f"failed={counts['failed']} verified-rate={rate}"
                )
        return 0
    if sub == "verify":
        result = app.governor_verify(
            args.job_id, args.status, evidence=args.evidence
        )
        if as_json:
            return emit_json({"ok": True, **result})
        print(
            f"  recorded outcome={result['outcome']['outcome']} for "
            f"job={result['outcome']['job_id']}"
        )
        return 0
    raise AgentOSError(f"unknown governor subcommand {sub!r}")


def cmd_supervisor(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    sub = getattr(args, "supervisor_command", None)
    if sub == "status":
        result = app.supervisor_status()
        if as_json:
            return emit_json({"ok": True, **result})
        print(_paint("supervisor operating model", BOLD))
        chain = " -> ".join(result["model"]["chain"])
        print(f"  chain: {chain}")
        print(f"  verified_live: {result['verified_live']}")
        print(_paint("chain layer status", BOLD))
        for layer, status_word in result["chain_layer_status"].items():
            print(f"  {layer}: {status_word}")
        print(_paint("separation", BOLD))
        for cell in result["cells"]:
            separation = cell["separation"]
            print(
                f"  {cell['id']}: health={cell['health']} "
                f"identity_persistent={separation['identityPersistent']} "
                f"separation={separation['separation']}"
            )
        return 0
    raise AgentOSError(f"unknown supervisor subcommand {sub!r}")


def cmd_execution(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Show one execution / list executions. Formatting only; logic in services."""
    sub = getattr(args, "execution_command", None)
    if sub == "show" or args.command == "execution":
        execution = app.get_execution(args.execution_id)
        if as_json:
            return emit_json({"ok": True, "execution": execution.to_dict()})
        print(_paint(execution.id, BOLD) + f" [{state_painted(execution.status.value)}]")
        print(f"  objective : {execution.objective_id}")
        print(f"  capability: {execution.capability_id}:{execution.operation}")
        print(f"  mode      : {execution.mode}  attempt={execution.attempt}")
        print(f"  cost      : {execution.cost_status} ({execution.cost_amount})")
        if execution.files_changed:
            print(f"  changed   : {', '.join(execution.files_changed[:10])}")
        if execution.git_diff_summary:
            print(f"  diff      : {execution.git_diff_summary[:200]}")
        if execution.result_summary:
            print(f"  summary   : {execution.result_summary[:200]}")
        return 0
    if args.command == "executions":
        if getattr(args, "list_command", "list") != "list":
            raise AgentOSError(
                f"unknown executions subcommand {args.list_command!r}"
            )
        executions = app.list_executions(
            objective_id=args.objective, limit=args.limit
        )
        if as_json:
            return emit_json(
                {"ok": True, "executions": [e.to_dict() for e in executions]}
            )
        if not executions:
            print("no executions recorded")
            return 0
        for execution in executions:
            print(
                f"{execution.id}  [{state_painted(execution.status.value)}]  "
                f"{execution.capability_id}:{execution.operation}  "
                f"mode={execution.mode} cost={execution.cost_status}"
            )
        return 0
    raise AgentOSError(f"unknown execution subcommand {sub!r}")


def cmd_ecosystem(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Ecosystem scout: candidate discovery + certification (from services)."""
    sub = getattr(args, "ecosystem_command", None)
    if sub == "discover":
        result = app.ecosystem_discover(
            category=args.category,
            name=args.name,
            source=args.source,
            version=getattr(args, "version", ""),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        candidate = result["candidate"]
        print(
            f"ecosystem: stored {candidate['id']}  state={candidate['state']}  "
            f"capabilities={len(candidate.get('capabilities', []))}"
        )
        return 0
    if sub == "candidates":
        candidates = app.ecosystem_candidates()
        if as_json:
            return emit_json({"ok": True, "candidates": candidates})
        if not candidates:
            print("no ecosystem candidates recorded")
            return 0
        for candidate in candidates:
            print(
                f"{candidate['id']}  [{candidate['state']}]  "
                f"source={candidate.get('source', '')}"
            )
        return 0
    if sub == "inspect":
        result = app.ecosystem_inspect(args.candidate_id)
        if as_json:
            return emit_json({"ok": True, **result})
        candidate = result["candidate"]
        print(f"ecosystem: {args.candidate_id} -> state='{candidate['state']}'")
        return 0
    if sub == "test":
        result = app.ecosystem_test(args.candidate_id)
        if as_json:
            return emit_json({"ok": True, **result})
        candidate = result["candidate"]
        cert = candidate.get("test_result") or {}
        print(
            f"ecosystem: {args.candidate_id} -> certification="
            f"passed={cert.get('passed')} (static-metadata-only)"
        )
        return 0
    if sub == "approve":
        result = app.ecosystem_approve(
            args.candidate_id, evidence=getattr(args, "evidence", None)
        )
        if as_json:
            return emit_json({"ok": True, **result})
        candidate = result["candidate"]
        print(f"ecosystem: {candidate['id']} -> approved (evidence recorded)")
        return 0
    if sub == "reject":
        result = app.ecosystem_reject(
            args.candidate_id, reason=getattr(args, "reason", "")
        )
        if as_json:
            return emit_json({"ok": True, **result})
        candidate = result["candidate"]
        print(f"ecosystem: {candidate['id']} -> rejected")
        return 0
    print("ecosystem: no subcommand given (discover|candidates|inspect|test|approve|reject)")
    return 2


def cmd_workflow(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Reusable workflow catalog (usage-led, never auto-upgrades)."""
    sub = getattr(args, "workflow_command", None)
    if sub == "register":
        result = app.workflow_register(
            name=args.name,
            steps=getattr(args, "steps", None) or None,
            requested_permissions=getattr(args, "permissions", None) or None,
            schedule_hint=getattr(args, "schedule_hint", ""),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        workflow = result["workflow"]
        print(
            f"workflow: registered {workflow['id']}  state='{workflow['state']}'"
        )
        return 0
    if sub == "catalog":
        workflows = app.workflow_catalog()
        if as_json:
            return emit_json({"ok": True, "workflows": workflows})
        if not workflows:
            print("no reusable workflows recorded")
            return 0
        for workflow in workflows:
            print(
                f"{workflow['id']}  [{workflow['state']}]  "
                f"reuses={workflow.get('reuse_count', 0)}"
            )
        return 0
    if sub == "use":
        result = app.workflow_use(
            workflow_id=args.workflow_id,
            found_useful=not bool(getattr(args, "not_useful", False)),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"workflow: recorded reuse of {args.workflow_id}")
        return 0
    if sub == "state":
        result = app.workflow_state(
            workflow_id=args.workflow_id, to_state=getattr(args, "to_state", "") or ""
        )
        if as_json:
            return emit_json({"ok": True, **result})
        workflow = result["workflow"]
        print(f"workflow: {args.workflow_id} state -> '{workflow['state']}'")
        return 0
    print("workflow: no subcommand given (register|catalog|use|state)")
    return 2


def cmd_efficiency(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Low-usage efficiency kernel (triage/team/fingerprint/delta/cache)."""
    sub = getattr(args, "efficiency_command", None)
    if sub == "triage":
        result = app.efficiency_triage(
            args.objective,
            target_capability=getattr(args, "target_capability", ""),
            exact_cache_hit=bool(getattr(args, "exact_hit", False)),
            artifact_hit=bool(getattr(args, "artifact_hit", False)),
            known_workflow=bool(getattr(args, "known_workflow", False)),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        decision = result["decision"]
        print(
            f"triage: {decision['resolution']}  reason={decision['reason']}  "
            f"route_matched={decision['route_matched']}"
        )
        return 0
    if sub == "team":
        result = app.efficiency_team(
            quality_floor=getattr(args, "quality_floor", ""),
            risk=str(getattr(args, "risk", "low")),
            decomposable=bool(getattr(args, "decomposable", False)),
            subtasks=int(getattr(args, "subtasks", 1) or 1),
            verification_burden=bool(getattr(args, "verification_burden", False)),
            security=str(getattr(args, "security", "internal")),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        plan = result["teamPlan"]
        print(
            f"team: size={plan['size']}  roles={','.join(plan['roles'])}  "
            f"rationale={plan['rationale']}"
        )
        return 0
    if sub == "fingerprint":
        result = app.efficiency_fingerprint(
            {
                "intent": args.intent,
                "output_schema": getattr(args, "output_schema", ""),
                "constraints": getattr(args, "constraint", None) or [],
                "role": getattr(args, "role", ""),
            }
        )
        if as_json:
            return emit_json({"ok": True, **result})
        print(
            f"fingerprint: {result['fingerprint']}  "
            f"duplicate={result.get('duplicate', False)}"
        )
        return 0
    if sub == "delta":
        result = app.efficiency_delta(
            json.loads(getattr(args, "baseline", "{}")),
            json.loads(getattr(args, "current", "{}")),
            getattr(args, "task", ""),
            baseline_ref=getattr(args, "baseline_ref", "baseline@1"),
            cache_scope=getattr(args, "cache_scope", ""),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        delta = result["delta"]
        print(
            f"delta: changed={len(delta.get('changed', []))}  "
            f"verified={len(delta.get('verified', []))}  "
            f"intent={delta.get('task_intent', '')[:60]}"
        )
        return 0
    if sub == "cache-get":
        result = app.efficiency_cache_get(
            args.intent,
            output_schema=getattr(args, "output_schema", ""),
            semantic=bool(getattr(args, "semantic", False)),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        cache = result["cache"]
        if cache.get("hit"):
            print(
                f"cache: HIT {cache['cacheKey'][:16]}...  level={cache.get('level', '')}  "
                f"sourceRefs={len(cache.get('sourceRefs', []))}"
            )
        else:
            print(f"cache: miss -> {cache.get('reason', '')}")
        return 0
    if sub == "cache-put":
        result = app.efficiency_cache_put(
            args.intent,
            category=getattr(args, "category", "exact"),
            payload=json.loads(getattr(args, "payload", "{}")),
            artifact_ref=getattr(args, "artifact_ref", ""),
            security_scope=getattr(args, "security_scope", "INTERNAL"),
            provider=getattr(args, "provider", ""),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        entry = result["cache"]
        print(
            f"cache: stored {entry['id'][:16]}...  type={entry['type']}  "
            f"scope={entry['securityScope']}"
        )
        return 0
    if sub == "invalidate":
        result = app.efficiency_invalidate(
            json.loads(getattr(args, "deps", "{}")),
            also_exact=bool(getattr(args, "also_exact", False)),
        )
        if as_json:
            return emit_json({"ok": True, **result})
        invalidated = result.get("invalidated") or result.get("entries") or []
        reason = result.get("reason", f"{len(invalidated)} stale entries")
        print(f"invalidate: {len(invalidated)} entries -> {reason}")
        return 0
    if sub == "benchmark":
        result = app.learning_benchmark()
        if as_json:
            return emit_json({"ok": True, **result})
        print(json.dumps(result["totals"], sort_keys=True))
        return 0
    if sub == "stats":
        result = app.efficiency_stats()
        if as_json:
            return emit_json({"ok": True, **result})
        stats = result["efficiency"]
        print(
            f"efficiency: entries={stats['entries']}  "
            f"exactHits={stats['exactHits']}  semanticHits={stats['semanticHits']}  "
            f"modelCallsAvoided={stats['modelCallsAvoided']}  "
            f"hitRate={stats['hitRate']:.3f}"
        )
        return 0
    print(
        "efficiency: no subcommand given "
        "(triage|team|fingerprint|delta|cache-get|cache-put|invalidate|benchmark|stats)"
    )
    return 2


def cmd_lverify(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Verification engine (learning)."""
    sub = getattr(args, "lverify_command", None)
    if sub == "start":
        try:
            result = json.loads(getattr(args, "result", "{}"))
        except json.JSONDecodeError as exc:
            raise AgentOSError("--result must be a JSON object") from exc
        if not isinstance(result, dict):
            raise AgentOSError("--result must be a JSON object")
        records = app.verification_start(args.task_id, args.task_class, result)
        if as_json:
            return emit_json({"ok": True, "records": [r.to_dict() for r in records]})
        print(f"Started {len(records)} verifications for task {args.task_id}")
        return 0
    if sub == "run":
        record = app.verification_record_result(
            args.record_id,
            bool(args.success),
            float(args.confidence),
            args.failure_reason,
        )
        if as_json:
            return emit_json({"ok": True, "record": record.to_dict()})
        print(f"Verification record {record.id}: {record.result.value}")
        return 0 if record.result.value == "ACCEPT" else 1
    if sub == "evaluate":
        outcome, reason = app.verification_evaluate_stored(args.task_id, args.attempt)
        if as_json:
            return emit_json({"ok": outcome == "ACCEPT", "outcome": outcome, "reason": reason})
        print(f"Verification outcome: {outcome}")
        return 0 if outcome == "ACCEPT" else 1
    print("verify: no subcommand given (start|evaluate)")
    return 2


def cmd_coach(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Coach layer - lesson generation."""
    sub = getattr(args, "coach_command", None)
    if sub == "analyze":
        try:
            history = json.loads(getattr(args, "history", "[]"))
        except json.JSONDecodeError as exc:
            raise AgentOSError("--history must be a JSON array") from exc
        if not isinstance(history, list):
            raise AgentOSError("--history must be a JSON array")
        lessons = app.coach_analyze(args.task_id, args.task_class, history)
        if as_json:
            return emit_json({"ok": True, "lessons": [l.to_dict() for l in lessons]})
        for l in lessons:
            print(f"Lesson: {l.id} - {l.lesson} (confidence: {l.confidence:.2f})")
        return 0
    if sub == "promote":
        lesson_id = getattr(args, "lesson_id", "")
        status = getattr(args, "status", "VALIDATED")
        from agentos.models import LessonStatus
        ok = app.lesson_promote(lesson_id, LessonStatus(status))
        if as_json:
            return emit_json({"ok": ok})
        print(f"Lesson {lesson_id} promoted: {ok}")
        return 0
    print("coach: no subcommand given (analyze|promote)")
    return 2


def cmd_workflow_compiler(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Workflow compiler."""
    sub = getattr(args, "wfc_command", None)
    if sub == "compile":
        task_class = getattr(args, "task_class", "")
        wf = app.workflow_compile(task_class, [])
        if as_json:
            return emit_json({"ok": True, "workflow": wf.to_dict() if wf else None})
        print(f"Compiled workflow: {wf.id if wf else 'None'}")
        return 0
    if sub == "promote":
        workflow_id = getattr(args, "workflow_id", "")
        status = getattr(args, "status", "VERIFIED")
        from agentos.models import WorkflowStatus
        ok = app.workflow_promote(workflow_id, WorkflowStatus(status))
        if as_json:
            return emit_json({"ok": ok})
        print(f"Workflow {workflow_id} promoted: {ok}")
        return 0
    if sub == "advance":
        workflow_id = getattr(args, "workflow_id", "")
        ok = app.workflow_advance_maturity(workflow_id)
        if as_json:
            return emit_json({"ok": ok})
        print(f"Workflow {workflow_id} maturity advanced: {ok}")
        return 0
    if sub == "record-use":
        workflow_id = getattr(args, "workflow_id", "")
        success = getattr(args, "success", True)
        app.workflow_record_use(workflow_id, success)
        if as_json:
            return emit_json({"ok": True})
        print(f"Recorded use for {workflow_id}: success={success}")
        return 0
    if sub == "match":
        task_class = getattr(args, "task_class", "")
        inputs = json.loads(getattr(args, "inputs", "{}"))
        wf, score, reason = app.workflow_match(task_class, inputs)
        if as_json:
            return emit_json({"ok": True, "workflow": wf.to_dict() if wf else None,
                              "score": score, "reason": reason})
        print(f"Match: {wf.id if wf else 'None'} (score={score:.2f}) - {reason}")
        return 0
    if sub == "stats":
        task_class = getattr(args, "task_class", None)
        stats = app.workflow_stats(task_class)
        if as_json:
            return emit_json({"ok": True, **stats})
        print(f"Workflow stats: {stats}")
        return 0
    print("workflow-compiler: no subcommand given")
    return 2


def cmd_skill(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Skill model."""
    sub = getattr(args, "skill_command", None)
    if sub == "execute":
        skill_id = getattr(args, "skill_id", "")
        inputs = json.loads(getattr(args, "inputs", "{}"))
        result = app.skill_execute(skill_id, inputs)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"Skill result: {result}")
        return 0
    if sub == "recommend":
        task_class = getattr(args, "task_class", "")
        context = json.loads(getattr(args, "context", "{}"))
        skill = app.skill_recommend(task_class, context)
        if as_json:
            return emit_json({"ok": True, "skill": skill.to_dict() if skill else None})
        print(f"Recommended skill: {skill.id if skill else 'None'}")
        return 0
    if sub == "list":
        skills = app.skill_list()
        if as_json:
            return emit_json({"ok": True, "skills": [s.to_dict() for s in skills]})
        for s in skills:
            print(f"  {s.id}: {s.objective}")
        return 0
    print("skill: no subcommand given (execute|recommend|list)")
    return 2


def cmd_role(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Role classifier."""
    sub = getattr(args, "role_command", None)
    if sub == "classify":
        role_id = getattr(args, "role_id", "")
        decision, factors = app.role_classify(role_id)
        if as_json:
            return emit_json({"ok": True, "decision": decision, "factors": factors})
        print(f"Role {role_id}: {decision}")
        for k, v in factors.items():
            print(f"  {k}: {v}")
        return 0
    print("role: no subcommand given (classify)")
    return 2


def cmd_autonomy(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Autonomy model."""
    sub = getattr(args, "autonomy_command", None)
    if sub == "success":
        entity_type = getattr(args, "entity_type", "")
        entity_id = getattr(args, "entity_id", "")
        passed = getattr(args, "passed", True)
        record = app.autonomy_record_success(entity_type, entity_id, passed)
        if as_json:
            return emit_json({"ok": True, **record.to_dict()})
        print(f"Recorded success for {entity_type}:{entity_id}")
        return 0
    if sub == "failure":
        entity_type = getattr(args, "entity_type", "")
        entity_id = getattr(args, "entity_id", "")
        record = app.autonomy_record_failure(entity_type, entity_id)
        if as_json:
            return emit_json({"ok": True, **record.to_dict()})
        print(f"Recorded failure for {entity_type}:{entity_id}")
        return 0
    if sub == "security":
        entity_type = getattr(args, "entity_type", "")
        entity_id = getattr(args, "entity_id", "")
        record = app.autonomy_record_security(entity_type, entity_id)
        if as_json:
            return emit_json({"ok": True, **record.to_dict()})
        print(f"Recorded security incident for {entity_type}:{entity_id}")
        return 0
    if sub == "correction":
        entity_type = getattr(args, "entity_type", "")
        entity_id = getattr(args, "entity_id", "")
        record = app.autonomy_record_correction(entity_type, entity_id)
        if as_json:
            return emit_json({"ok": True, **record.to_dict()})
        print(f"Recorded human correction for {entity_type}:{entity_id}")
        return 0
    if sub == "scope":
        entity_type = getattr(args, "entity_type", "")
        entity_id = getattr(args, "entity_id", "")
        record = app.autonomy_record_scope_violation(entity_type, entity_id)
        if as_json:
            return emit_json({"ok": True, **record.to_dict()})
        print(f"Recorded scope violation for {entity_type}:{entity_id}")
        return 0
    if sub == "can-act":
        entity_type = getattr(args, "entity_type", "")
        entity_id = getattr(args, "entity_id", "")
        scope = getattr(args, "scope", "default")
        can, reason = app.autonomy_can_act(entity_type, entity_id, scope)
        if as_json:
            return emit_json({"ok": True, "can_act": can, "reason": reason})
        print(f"Can act: {can} ({reason})")
        return 0
    if sub == "promote":
        entity_type = getattr(args, "entity_type", "")
        entity_id = getattr(args, "entity_id", "")
        target = getattr(args, "target", "LIMITED_AUTONOMY")
        result = app.autonomy_promote(entity_type, entity_id, target)
        if as_json:
            return emit_json({"ok": True, "result": result})
        print(f"Promotion result: {result}")
        return 0
    print("autonomy: no subcommand given")
    return 2


def cmd_fleet(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Fleet monitor."""
    sub = getattr(args, "fleet_command", None)
    if sub == "signal":
        try:
            signal = json.loads(args.signal)
        except json.JSONDecodeError as exc:
            raise AgentOSError("signal must be a JSON object") from exc
        if not isinstance(signal, dict):
            raise AgentOSError("signal must be a JSON object")
        actions = app.fleet_process_signal(signal)
        if as_json:
            return emit_json({"ok": True, "actions": actions})
        for a in actions:
            print(f"  {a.get('action')}: {a}")
        return 0
    if sub == "health":
        health = app.fleet_health()
        if as_json:
            return emit_json({"ok": True, **health})
        print(f"Fleet health: {health}")
        return 0
    if sub == "incidents":
        incidents = app.fleet_incidents()
        if as_json:
            return emit_json({"ok": True, "incidents": incidents})
        for i in incidents:
            print(f"  {i['id']}: {i['probable_cause']} ({i['status']})")
        return 0
    if sub == "resolve":
        incident_id = getattr(args, "incident_id", "")
        resolution = getattr(args, "resolution", "")
        lesson_ref = getattr(args, "lesson_ref", None)
        ok = app.fleet_resolve_incident(incident_id, resolution, lesson_ref)
        if as_json:
            return emit_json({"ok": ok})
        print(f"Incident {incident_id} resolved: {ok}")
        return 0
    print("fleet: no subcommand given")
    return 2


def cmd_routing(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Learned routing."""
    sub = getattr(args, "routing_command", None)
    if sub == "best":
        try:
            candidates = json.loads(getattr(args, "candidates", "[]"))
        except json.JSONDecodeError as exc:
            raise AgentOSError("--candidates must be a JSON array") from exc
        if not isinstance(candidates, list):
            raise AgentOSError("--candidates must be a JSON array")
        executor, info = app.routing_best_executor(args.task_class, candidates)
        if as_json:
            return emit_json({"ok": True, "executor": executor, "info": info})
        print(f"Best executor: {executor}")
        if executor:
            exp = info.get("explanation", {})
            print(f"  Score: {info.get('score', 0):.3f}")
            for k, v in exp.get("factors", {}).items():
                print(f"  {k}: {v.get('contribution', 0):.3f}")
        return 0
    if sub == "explain":
        job_id = getattr(args, "job_id", "")
        exp = app.routing_explain(job_id)
        if as_json:
            return emit_json({"ok": True, "explanation": exp})
        print(f"Routing explanation for {job_id}: {exp}")
        return 0
    print("routing: no subcommand given (best|explain)")
    return 2


def cmd_routine(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Routine engine."""
    sub = getattr(args, "routine_command", None)
    if sub == "run":
        routine_id = getattr(args, "routine_id", "")
        params = json.loads(getattr(args, "params", "{}"))
        result = app.routine_run(routine_id, params)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"Routine {routine_id}: {result.get('action', 'NO_ACTION')}")
        return 0
    if sub == "list":
        routines = app.routine_list()
        if as_json:
            return emit_json({"ok": True, "routines": routines})
        for r in routines:
            print(f"  {r['id']}: {r['trigger']} -> {r['early_exit']}")
        return 0
    if sub == "enable":
        routine_id = getattr(args, "routine_id", "")
        enabled = not bool(getattr(args, "disabled", False))
        ok = app.routine_enable(routine_id, enabled)
        if as_json:
            return emit_json({"ok": ok})
        print(f"Routine {routine_id} enabled: {ok}")
        return 0
    if sub == "disable":
        routine_id = getattr(args, "routine_id", "")
        ok = app.routine_enable(routine_id, False)
        if as_json:
            return emit_json({"ok": ok})
        print(f"Routine {routine_id} disabled: {ok}")
        return 0
    print("routine: no subcommand given (run|list|enable|disable)")
    return 2


def cmd_backup(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Backup & reconstruction."""
    sub = getattr(args, "backup_command", None)
    if sub == "create":
        output_dir = getattr(args, "output_dir", None)
        archive = app.backup_create(output_dir)
        if as_json:
            return emit_json({"ok": True, "archive": archive})
        print(f"Backup created: {archive}")
        return 0
    if sub in ("inspect", "verify"):
        result = app.backup_inspect(args.archive)
        if as_json:
            return emit_json({"ok": bool(result["valid"]), **result})
        print(
            f"Backup valid={result['valid']} tables={len(result['tablesIncluded'])} "
            f"secretLeaks={result['secretLeaks']}"
        )
        return 0 if result["valid"] else 1
    if sub == "restore":
        dry_run = bool(getattr(args, "dry_run", False))
        verify = not bool(getattr(args, "no_verify", False))
        manifest = app.backup_restore(args.archive, verify=verify, dry_run=dry_run)
        if as_json:
            return emit_json({"ok": True, "manifest": manifest})
        if dry_run:
            print(
                f"Restore dry-run valid={manifest.get('ok')} "
                f"secretLeaks={manifest.get('secretLeaks', [])}"
            )
        else:
            print(f"Restored: {manifest.get('id', 'unknown')}")
        return 0
    if sub == "reconstruct":
        backup_dir = getattr(args, "backup_dir", "")
        result = app.backup_reconstruct_workforce(backup_dir)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"Reconstructed: {len(result.get('skills', []))} skills, "
              f"{len(result.get('workflows', []))} workflows, "
              f"{len(result.get('autonomy', []))} autonomy records")
        return 0
    print("backup: no subcommand given (create|inspect|verify|restore|reconstruct)")
    return 2


def cmd_bot(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    """Persistent bot governor."""
    sub = getattr(args, "bot_command", None)
    if sub == "evaluate":
        role_id = getattr(args, "role_id", "")
        result = app.bot_evaluate(role_id)
        if as_json:
            return emit_json({"ok": True, **result})
        print(f"Role {role_id}: {result['decision']} (cost: {result['estimated_cost']})")
        print(f"  Factors: {result['factors']}")
        return 0
    if sub == "approve":
        result = app.bot_approve(args.role_id, bool(args.approved), args.approver)
        if as_json:
            return emit_json({"ok": True, "result": result})
        print(f"Materialization {'approved' if args.approved else 'rejected'} for {args.role_id}")
        return 0
    if sub == "pending":
        pending = app.bot_pending()
        if as_json:
            return emit_json({"ok": True, "pending": pending})
        for p in pending:
            print(f"  {p['role_id']}: {p['decision']}")
        return 0
    print("bot: no subcommand given (evaluate|approve|pending)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())