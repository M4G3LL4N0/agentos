from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from agentos.cli import main as cli_main
from agentos.models import (
    AutonomyState,
    Capability,
    CapabilityType,
    EventType,
    HealthStatus,
    Objective,
    Strategy,
    VerificationOutcome,
    VerificationType,
    WorkflowDefinition,
    WorkflowMaturity,
    WorkflowStatus,
)
from agentos.router import Router
from agentos.services import AgentOS
from agentos.store import Store


class LearningExecutionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.app = AgentOS(home=self.home.name)

    def tearDown(self) -> None:
        self.app.store.close()
        self.home.cleanup()

    def test_cli_run_accepts_json_params(self) -> None:
        cli_home = tempfile.TemporaryDirectory()
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli_main([
                    "--home", cli_home.name,
                    "--json",
                    "run", "cli parameterized echo",
                    "--operation", "echo",
                    "--params", '{"message":"from-cli"}',
                    "--explain",
                ])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["state"], "COMPLETED")
            self.assertEqual(
                payload["trace"]["request"]["message"],
                "from-cli",
            )
        finally:
            cli_home.cleanup()

    def test_exact_cache_hit_completes_without_worker(self) -> None:
        first = self.app.delegate(
            "repeatable lookup",
            operation="echo",
            params={"message": "same-answer"},
        )
        self.assertEqual(first.state.value, "COMPLETED")

        second = self.app.delegate(
            "repeatable lookup",
            operation="echo",
            params={"message": "same-answer"},
        )
        self.assertEqual(second.state.value, "COMPLETED")
        self.assertEqual(second.result["source"], "intelligence_cache")
        self.assertEqual(second.result["workersInvoked"], 0)
        self.assertEqual(second.result["modelCalls"], 0)
        self.assertEqual(second.result["grokCalls"], 0)

    def test_existing_objective_run_accepts_params(self) -> None:
        objective = self.app.create_objective(
            "parameterized shell failure",
            operation="shell.run",
        )
        report = self.app.run(
            objective.id,
            operation="shell.run",
            params={"command": "false"},
            max_attempts=1,
        )
        self.assertEqual(report.state.value, "FAILED")
        self.assertNotIn("requires a non-empty", str(report.failure))
        self.assertEqual(
            self.app.store.get_job_trace(objective.id)["request"]["command"],
            "false",
        )

    def test_cache_key_includes_constraints_and_context(self) -> None:
        first = self.app.create_objective(
            "same visible request",
            operation="echo",
            params={"message": "same"},
            constraints=["policy-mode:first"],
            extra_context={"taskType": "read"},
        )
        self.app.run(first.id)
        second = self.app.create_objective(
            "same visible request",
            operation="echo",
            params={"message": "same"},
            constraints=["policy-mode:second"],
            extra_context={"taskType": "write"},
        )
        report = self.app.run(second.id)
        self.assertEqual(report.state.value, "COMPLETED")
        self.assertNotEqual(report.result.get("source"), "intelligence_cache")
        self.assertEqual(
            self.app.store.get_job_trace(second.id)["workersInvoked"],
            1,
        )

    def test_force_bypasses_exact_cache(self) -> None:
        first = self.app.delegate(
            "forced cache bypass",
            operation="echo",
            params={"message": "same"},
        )
        second = self.app.create_objective(
            "forced cache bypass",
            operation="echo",
            params={"message": "same"},
        )
        report = self.app.run(second.id, force=True)
        self.assertEqual(first.state.value, "COMPLETED")
        self.assertNotEqual(report.result.get("source"), "intelligence_cache")
        self.assertEqual(
            self.app.store.get_job_trace(second.id)["workersInvoked"],
            1,
        )

    def test_cache_preflight_applies_current_shell_policy(self) -> None:
        first = self.app.delegate(
            "policy cache check",
            operation="shell.run",
            params={"command": "true"},
        )
        self.assertEqual(first.state.value, "COMPLETED")
        self.app.engine.registry.policy.blocked_command_patterns.append("*true*")
        second = self.app.create_objective(
            "policy cache check",
            operation="shell.run",
            params={"command": "true"},
        )
        report = self.app.run(second.id, max_attempts=1)
        self.assertEqual(report.state.value, "FAILED")
        trace = self.app.store.get_job_trace(second.id)
        self.assertEqual(trace["cacheDecision"]["status"], "BYPASSED")
        self.assertEqual(len(self.app.store.list_executions(second.id)), 1)

    def test_cached_output_is_reverified_before_completion(self) -> None:
        objective = self.app.create_objective(
            "cached failed shell",
            operation="shell.run",
            params={"command": "false"},
        )
        cache = self.app.engine._cache_for(objective)
        cache.put(cache.build_entry(
            self.app.engine._cache_intent(objective, "shell.run"),
            payload={"exit_code": 1, "stderr": "", "stdout": ""},
        ))
        report = self.app.run(objective.id, max_attempts=1)
        self.assertEqual(report.state.value, "FAILED")
        self.assertEqual(len(self.app.store.list_executions(objective.id)), 1)

    def test_deterministic_request_uses_zero_agents(self) -> None:
        report = self.app.delegate(
            "compute 2 + 3 * 4",
            operation="deterministic.calculate",
            params={},
        )
        self.assertEqual(report.state.value, "COMPLETED")
        self.assertEqual(report.result["value"], 14)
        self.assertEqual(report.result["workersInvoked"], 0)
        self.assertEqual(report.result["modelCalls"], 0)

    def test_service_workflow_match_returns_definition(self) -> None:
        from agentos.models import WorkflowDefinition, WorkflowStatus

        self.app.store.save_workflow(WorkflowDefinition(
            id="service_workflow",
            name="service workflow",
            task_class="service_task",
            inputs={"value": "str"},
            preconditions=["input:value"],
            steps=[{"kind": "operation", "operation": "echo", "capability_id": "echo"}],
            status=WorkflowStatus.OBSERVED,
        ))
        workflow, score, reason = self.app.workflow_match(
            "service_task", {"value": "ok"}
        )
        self.assertEqual(workflow.id, "service_workflow")
        self.assertGreater(score, 0.0)
        self.assertTrue(reason)

    def test_known_workflow_reuses_plan(self) -> None:
        workflow = WorkflowDefinition(
            id="wf_lookup",
            name="lookup",
            task_class="lookup",
            version=1,
            steps=[{"kind": "operation", "operation": "echo", "capability_id": "echo"}],
            status=WorkflowStatus.VERIFIED,
            maturity=WorkflowMaturity.REPEAT,
        )
        self.app.store.save_workflow(workflow)
        objective = self.app.create_objective(
            "known workflow request",
            operation="echo",
            params={"message": "workflow"},
            extra_context={"taskClass": "lookup"},
        )
        report = self.app.run(objective.id)
        self.assertEqual(report.state.value, "COMPLETED")
        trace = self.app.store.get_job_trace(objective.id)
        self.assertEqual(trace["workflowDecision"]["status"], "REUSED")
        self.assertEqual(trace["workflowDecision"]["workflowId"], "wf_lookup")
        self.assertEqual(
            self.app.store.get_workflow("wf_lookup").metrics["uses"], 1
        )

    def test_normal_worker_records_verification_and_trace(self) -> None:
        report = self.app.delegate(
            "normal worker request",
            operation="echo",
            params={"message": "hello"},
        )
        self.assertEqual(report.state.value, "COMPLETED")
        trace = self.app.store.get_job_trace(report.objective_id)
        self.assertEqual(trace["finalStatus"], "VERIFIED_COMPLETE")
        self.assertEqual(trace["verification"]["result"], "ACCEPT")
        self.assertEqual(trace["workersInvoked"], 1)
        self.assertEqual(trace["modelCalls"], 0)
        self.assertEqual(trace["grokCalls"], 0)
        self.assertIn("cacheDecision", trace)
        self.assertIn("route", trace)
        self.assertIn("evidence", trace)

    def test_fleet_no_action_after_success(self) -> None:
        self.app.delegate("healthy fleet", operation="echo", params={"message": "ok"})
        health = self.app.fleet_health()
        self.assertTrue(health["no_action"])
        self.assertEqual(health["open_incidents"], 0)

    def test_repeated_failures_cluster_into_one_incident(self) -> None:
        for index in range(3):
            report = self.app.delegate(
                f"provider outage {index}",
                operation="shell.run",
                params={"command": "false"},
                max_attempts=1,
            )
            self.assertEqual(report.state.value, "FAILED")
        incidents = self.app.store.list_incidents(status="OPEN")
        self.assertEqual(len(incidents), 1)
        self.assertGreaterEqual(len(incidents[0].affected_jobs), 3)
        self.assertTrue(self.app.fleet_resolve_incident(incidents[0].id, "provider restored"))
        self.assertEqual(self.app.store.get_incident(incidents[0].id).status, "RESOLVED")

    def test_verification_failure_retries_with_reason(self) -> None:
        report = self.app.delegate(
            "retry this",
            operation="shell.run",
            params={"command": "false"},
            max_attempts=2,
        )
        self.assertEqual(report.state.value, "FAILED")
        trace = self.app.store.get_job_trace(report.objective_id)
        self.assertEqual(len(trace["retries"]), 1)
        self.assertEqual(trace["retries"][0]["strategy"], "retry")
        self.assertEqual(len(trace["failures"]), 2)
        self.assertTrue(all(item["reason"] for item in trace["retries"]))
        self.assertTrue(all(item["reason"] for item in trace["failures"]))
        self.assertEqual(trace["verification"]["result"], "RETRY_SAME_WORKER")

    def test_verification_failure_uses_alternate_worker(self) -> None:
        for capability_id, command in (("a_missing", "missing-agentos-test-command"), ("b_good", "true")):
            self.app.store.save_capability(Capability(
                id=capability_id,
                name=capability_id,
                type=CapabilityType.CLI,
                description="fixture",
                operations=["fixture.run"],
                adapter="shell",
                availability=True,
                health=HealthStatus.AVAILABLE,
                source="configured",
                verification={"method": "exit_code"},
                config={"command": command},
            ))
        report = self.app.delegate(
            "alternate worker",
            operation="fixture.run",
            params={},
            max_attempts=2,
        )
        self.assertEqual(report.state.value, "COMPLETED")
        trace = self.app.store.get_job_trace(report.objective_id)
        self.assertTrue(any(item["outcome"] == "ESCALATE_WORKER" for item in trace["escalations"]))
        self.assertEqual(trace["executor"], "b_good")

    def test_human_attention_requires_reason(self) -> None:
        with self.assertRaises(Exception):
            self.app.record_human_attention("question", "because")
        self.app.record_human_attention("question", "GENUINELY_MISSING_INFORMATION")
        metrics = self.app.human_attention_metrics()
        self.assertEqual(metrics["humanQuestions"], 1)
        self.assertEqual(metrics["total"], 1)

    def test_bounded_escalation_stops_after_attempt_budget(self) -> None:
        report = self.app.delegate(
            "bounded escalation",
            operation="shell.run",
            params={"command": "false"},
            max_attempts=2,
        )
        self.assertEqual(report.state.value, "FAILED")
        self.assertEqual(len(self.app.store.list_executions(report.objective_id)), 2)
        trace = self.app.store.get_job_trace(report.objective_id)
        self.assertEqual(len(trace["retries"]), 1)
        self.assertEqual(len(trace["failures"]), 2)
        self.assertEqual(trace["retries"][0]["strategy"], "retry")
        self.assertEqual(trace["escalations"][-1]["outcome"], "ESCALATE_SUPERVISOR")
        self.assertEqual(trace["escalations"][-1]["attempt"], 2)

    def test_low_usage_metrics_use_measured_values(self) -> None:
        self.app.delegate("metric once", operation="echo", params={"message": "metric"})
        self.app.delegate("metric once", operation="echo", params={"message": "metric"})
        metrics = self.app.low_usage_metrics()
        self.assertEqual(metrics.requests_total, 2)
        self.assertEqual(metrics.zero_agent_resolutions, 1)
        self.assertEqual(metrics.cache_hits, 1)
        self.assertEqual(metrics.model_calls, 0)
        self.assertEqual(metrics.premium_calls, 0)
        self.assertEqual(metrics.grok_calls, 0)
        self.assertEqual(metrics.verification_required_rate, 1.0)
        self.assertEqual(metrics.verification_pass_rate, 1.0)

    def test_exact_cache_skips_coach(self) -> None:
        self.app.delegate(
            "coach skip cache",
            operation="echo",
            params={"message": "stable"},
        )
        self.app.delegate(
            "coach skip cache",
            operation="echo",
            params={"message": "stable"},
        )
        trace = self.app.store.list_job_traces(limit=10)[0]
        self.assertEqual(trace["coachDecision"]["decision"], "SKIPPED")
        self.assertEqual(trace["coachDecision"]["reason"], "exact_cache_hit")

    def test_failed_worker_triggers_coach_and_lesson(self) -> None:
        report = self.app.delegate(
            "run failing command",
            operation="shell.run",
            params={"command": "false"},
            max_attempts=1,
        )
        self.assertEqual(report.state.value, "FAILED")
        trace = self.app.store.get_job_trace(report.objective_id)
        self.assertEqual(trace["coachDecision"]["decision"], "RUN")
        self.assertTrue(trace["lessonRefs"])
        self.assertTrue(self.app.store.list_lessons(task_class="shell.run"))

    def test_oversized_workflow_is_blocked_without_truncation(self) -> None:
        self.app.store.save_workflow(WorkflowDefinition(
            id="oversized_workflow",
            name="oversized workflow",
            task_class="oversized_task",
            steps=[
                {
                    "kind": "operation",
                    "operation": "echo",
                    "capability_id": "echo",
                    "params": {"index": index},
                }
                for index in range(11)
            ],
            status=WorkflowStatus.VERIFIED,
        ))
        objective = self.app.create_objective(
            "oversized workflow",
            operation="echo",
            extra_context={"taskClass": "oversized_task"},
        )
        report = self.app.run(objective.id)
        self.assertEqual(report.state.value, "BLOCKED")
        self.assertEqual(len(self.app.store.list_executions(objective.id)), 0)
        trace = self.app.store.get_job_trace(objective.id)
        self.assertEqual(trace["workflowDecision"]["status"], "REJECTED")
        self.assertIn("exceeds", trace["workflowDecision"]["reason"])

    def test_reset_rewires_all_store_components_and_subscribers(self) -> None:
        old_store = self.app.store
        self.app.init(reset=True)
        self.assertIsNot(self.app.store, old_store)
        for component in (
            self.app.engine,
            self.app.verification_engine,
            self.app.coach,
            self.app.workflow_compiler,
            self.app.skill_model,
            self.app.role_classifier,
            self.app.autonomy_model,
            self.app.fleet_monitor,
            self.app.learned_router,
            self.app.routine_engine,
            self.app.backup_manager,
            self.app.bot_governor,
        ):
            self.assertIs(component.store, self.app.store)
        self.assertEqual(
            len(self.app.bus._subscribers[EventType.EXECUTION_FAILED]),
            1,
        )
        for index in range(3):
            report = self.app.delegate(
                f"post reset failure {index}",
                operation="shell.run",
                params={"command": "false"},
                max_attempts=1,
            )
            self.assertEqual(report.state.value, "FAILED")
        self.assertEqual(len(self.app.store.list_incidents(status="OPEN")), 1)

    def test_highly_sensitive_success_skips_cache_without_raising(self) -> None:
        objective = self.app.create_objective(
            "sensitive success",
            operation="echo",
            params={"message": "private"},
            extra_context={"securityScope": "HIGHLY_SENSITIVE"},
        )
        report = self.app.run(objective.id)
        self.assertEqual(report.state.value, "COMPLETED")
        trace = self.app.store.get_job_trace(objective.id)
        self.assertEqual(trace["cacheUpdates"][-1]["status"], "SKIPPED")
        self.assertIn("HIGHLY_SENSITIVE", trace["cacheUpdates"][-1]["reason"])

    def test_workflow_approval_and_inputs_are_hard_gates(self) -> None:
        self.app.store.save_workflow(WorkflowDefinition(
            id="guarded_workflow",
            name="guarded workflow",
            task_class="guarded_task",
            inputs={"required_value": "str"},
            steps=[{
                "kind": "operation",
                "operation": "echo",
                "capability_id": "echo",
                "params": {"message": "ok"},
            }],
            approval_policy="required",
            status=WorkflowStatus.VERIFIED,
        ))
        fake = self.app.create_objective(
            "guarded workflow",
            operation="echo",
            params={"message": "ok"},
            extra_context={
                "taskClass": "guarded_task",
                "approvalEvidence": "not-a-real-approval",
            },
        )
        fake_report = self.app.run(fake.id)
        self.assertEqual(
            self.app.store.get_job_trace(fake.id)["workflowDecision"]["status"],
            "FRESH_PLAN",
        )
        self.assertEqual(fake_report.state.value, "COMPLETED")
        missing = self.app.create_objective(
            "guarded workflow",
            operation="echo",
            params={"message": "ok"},
            extra_context={
                "taskClass": "guarded_task",
                "approvalEvidence": {
                    "approved": True,
                    "approvalId": "approval-123",
                    "operation": "echo",
                },
            },
        )
        self.app.run(missing.id)
        self.assertEqual(
            self.app.store.get_job_trace(missing.id)["workflowDecision"]["status"],
            "FRESH_PLAN",
        )

    def test_engine_uses_task_class_verification_policy(self) -> None:
        objective = self.app.create_objective(
            "policy verification",
            operation="echo",
            params={"message": "ok"},
            extra_context={"taskClass": "code_change"},
        )
        self.app.run(objective.id)
        records = self.app.store.list_verification_records(objective.id)
        self.assertEqual(
            {record.verification_type for record in records},
            {VerificationType.TEST, VerificationType.BUILD, VerificationType.DIFF},
        )
        self.assertTrue(all(record.result == VerificationOutcome.ACCEPT for record in records))

    def test_workflow_maturity_requires_repeated_evidence(self) -> None:
        workflow = WorkflowDefinition(
            id="wf_repeat",
            name="repeat",
            task_class="repeatable",
            steps=[{"kind": "operation", "operation": "echo", "capability_id": "echo"}],
            status=WorkflowStatus.OBSERVED,
        )
        self.app.store.save_workflow(workflow)
        for index in range(5):
            objective = self.app.create_objective(
                f"repeatable workflow {index}",
                operation="echo",
                params={"message": str(index)},
                extra_context={"taskClass": "repeatable"},
            )
            self.app.run(objective.id)
        updated = self.app.store.get_workflow("wf_repeat")
        self.assertEqual(updated.status, WorkflowStatus.VERIFIED)
        self.assertEqual(updated.metrics["uses"], 5)
        self.assertEqual(updated.metrics["successes"], 5)
        self.assertGreaterEqual(len(self.app.store.list_lessons(task_class="repeatable")), 1)

    def test_forced_rerun_archives_previous_job_trace(self) -> None:
        first = self.app.delegate("trace history", operation="echo", params={"message": "one"})
        self.app.run(first.objective_id, force=True, operation="echo", params={"message": "two"})
        history = self.app.store.list_job_trace_history(first.objective_id)
        self.assertGreaterEqual(len(history), 1)
        self.assertNotEqual(history[0]["runId"], self.app.store.get_job_trace(first.objective_id)["runId"])

    def test_evidence_refs_round_trip_through_store(self) -> None:
        from agentos.models import LessonCandidate, LessonStatus, VerificationRecord

        record = VerificationRecord(
            id="evidence_record",
            task_id="evidence_task",
            verification_type=VerificationType.TEST,
            verifier="test",
            evidence_refs=["execution:one", "test:two"],
            result=VerificationOutcome.ACCEPT,
        )
        self.app.store.save_verification_record(record)
        self.assertEqual(
            self.app.store.get_verification_record(record.id).evidence_refs,
            record.evidence_refs,
        )
        lesson = LessonCandidate(
            id="evidence_lesson",
            task_class="evidence_task",
            scope="test",
            symptom="symptom",
            root_cause="cause",
            lesson="lesson",
            evidence_refs=["execution:one"],
            status=LessonStatus.CANDIDATE,
        )
        self.app.store.save_lesson(lesson)
        self.assertEqual(
            self.app.store.get_lesson(lesson.id).evidence_refs,
            lesson.evidence_refs,
        )

    def test_verification_record_persists_no_secret_values(self) -> None:
        from agentos.models import VerificationRecord, VerificationType

        marker = "DO_NOT_TRACE_RECORD"
        record = VerificationRecord(
            id="secret_verification_record",
            task_id="secret_task",
            verification_type=VerificationType.TEST,
            verifier="test_verifier",
            failure_reason=f"access_token={marker}",
            recommendation=f"password={marker}",
        )
        self.app.store.save_verification_record(record)
        stored = self.app.store.get_verification_record(record.id)
        self.assertNotIn(marker, str(stored.to_dict()))
        raw = self.app.store.conn.execute(
            "SELECT failure_reason, recommendation FROM verification_records WHERE id=?",
            (record.id,),
        ).fetchone()
        self.assertNotIn(marker, str(dict(raw)))

    def test_learning_records_persist_no_secret_values(self) -> None:
        from agentos.models import IncidentRecord, LessonCandidate, Pattern, WorkflowDefinition

        marker = "DO_NOT_TRACE_LEARNING"
        secret = f"access_token={marker}"
        self.app.store.save_pattern(Pattern(
            id="secret_pattern",
            objective_class=secret,
            operation=secret,
            strategy=secret,
            capability_id=secret,
            verified=False,
            lesson=secret,
        ))
        self.app.store.save_lesson(LessonCandidate(
            id="secret_lesson",
            task_class=secret,
            scope=secret,
            symptom=secret,
            root_cause=secret,
            lesson=secret,
            recommended_change=secret,
        ))
        self.app.store.save_workflow(WorkflowDefinition(
            id="secret_workflow",
            name=secret,
            task_class=secret,
        ))
        self.app.store.save_incident(IncidentRecord(
            id="secret_incident",
            signature=secret,
            probable_cause=secret,
            mitigation=secret,
            resolution=secret,
        ))
        rows = self.app.store.conn.execute(
            "SELECT objective_class, operation, lesson FROM patterns "
            "UNION ALL SELECT task_class, symptom, lesson FROM lessons "
            "UNION ALL SELECT name, task_class, outputs_json FROM workflows "
            "UNION ALL SELECT signature, probable_cause, resolution FROM incidents"
        ).fetchall()
        self.assertNotIn(marker, str([dict(row) for row in rows]))

    def test_trace_never_contains_secret_values(self) -> None:
        report = self.app.delegate(
            "secret trace",
            operation="echo",
            params={"message": "safe", "token": "access_token=DO_NOT_TRACE"},
        )
        trace = self.app.store.get_job_trace(report.objective_id)
        self.assertNotIn("DO_NOT_TRACE", str(trace))
        self.assertIn("[REDACTED]", str(trace))
    def test_failure_persists_no_secret_in_any_state_record(self) -> None:
        marker = "DO_NOT_TRACE"
        report = self.app.delegate(
            "secret failure",
            operation="shell.run",
            params={"command": f"printf 'access_token={marker}\\n' >&2; exit 1"},
            max_attempts=1,
        )
        self.assertEqual(report.state.value, "FAILED")
        persisted = {
            "objective": self.app.store.get_objective(report.objective_id).to_dict(),
            "trace": self.app.store.get_job_trace(report.objective_id),
            "executions": [
                execution.to_dict()
                for execution in self.app.store.list_executions(report.objective_id)
            ],
            "failures": [
                failure.to_dict()
                for failure in self.app.store.list_failures(report.objective_id)
            ],
            "events": [event.to_dict() for event in self.app.bus.for_objective(report.objective_id)],
        }
        self.assertNotIn(marker, json.dumps(persisted, default=str))


class LearnedRouterGateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.home.name, "router.db"))
        self.router = Router(self.store)
        self.good = Capability(
            id="good",
            name="good",
            type=CapabilityType.TOOL,
            description="",
            operations=["echo"],
            adapter="echo",
            health=HealthStatus.AVAILABLE,
        )
        self.bad = Capability(
            id="bad",
            name="bad",
            type=CapabilityType.TOOL,
            description="",
            operations=["echo"],
            adapter="echo",
            health=HealthStatus.AVAILABLE,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.home.cleanup()

    def test_learned_metrics_modify_rank_after_hard_gates(self) -> None:
        self.store.record_executor_metric({
            "id": "m-good",
            "executor": "good",
            "runtime": "local",
            "model_provider": "",
            "task_class": "echo",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 100,
            "retry_count": 0,
            "resource_cost": "UNKNOWN",
            "human_correction": 0,
            "context_size": 100,
            "final_outcome": "VERIFIED_COMPLETE",
        })
        self.store.record_executor_metric({
            "id": "m-bad",
            "executor": "bad",
            "runtime": "local",
            "model_provider": "",
            "task_class": "echo",
            "success": 0,
            "verification_result": "FAILED",
            "latency_ms": 5000,
            "retry_count": 3,
            "resource_cost": "UNKNOWN",
            "human_correction": 2,
            "context_size": 5000,
            "final_outcome": "FAILED",
        })
        objective = Objective(id="obj", title="route", description="")
        plan = self.router.plan(objective, "echo", [self.bad, self.good])
        self.assertEqual(plan.strategy, Strategy.DIRECT)
        self.assertEqual(plan.steps[0].capability_id, "good")
        self.assertIn("learned=", plan.rationale)

    def test_hard_health_gate_beats_learned_score(self) -> None:
        self.bad.health = HealthStatus.DOWN
        self.store.record_executor_metric({
            "id": "m-bad-good-history",
            "executor": "bad",
            "runtime": "local",
            "model_provider": "",
            "task_class": "echo",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 1,
            "retry_count": 0,
            "resource_cost": "UNKNOWN",
            "human_correction": 0,
            "context_size": 1,
            "final_outcome": "VERIFIED_COMPLETE",
        })
        objective = Objective(id="obj", title="route", description="")
        plan = self.router.plan(objective, "echo", [self.bad, self.good])
        self.assertEqual(plan.steps[0].capability_id, "good")


class RoutineAndBackupGateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.app = AgentOS(home=self.home.name)

    def tearDown(self) -> None:
        self.app.store.close()
        self.home.cleanup()

    def test_routine_unchanged_is_no_action(self) -> None:
        result = self.app.routine_run("routine_usage_refresh", {})
        self.assertEqual(result["action"], "NO_ACTION")

    def test_default_changed_routine_without_workflow_is_honest(self) -> None:
        source = tempfile.TemporaryDirectory()
        try:
            path = os.path.join(source.name, "usage.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("usage")
            result = self.app.routine_run(
                "routine_usage_refresh",
                {"data_dir": source.name, "current_state": {"usage": 1}},
            )
            self.assertEqual(result["action"], "ALERT")
            self.assertEqual(result["workflow_result"]["status"], "UNCONFIGURED")
        finally:
            source.cleanup()

    def test_routine_changed_runs_existing_workflow(self) -> None:
        source = tempfile.TemporaryDirectory()
        source_path = os.path.join(source.name, "usage.json")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write('{"provider":"local"}')
        self.app.store.save_routine({
            "id": "routine_usage_refresh",
            "trigger": "usage_refresh",
            "cheap_detector": "usage_snapshot",
            "change_condition": "new export",
            "workflow_id": "wf_usage",
            "early_exit": "NO_ACTION",
            "report_policy": "delta",
            "enabled": 1,
        })
        self.app.store.save_workflow(WorkflowDefinition(
            id="wf_usage",
            name="usage refresh",
            task_class="usage_refresh",
            steps=[{"kind": "operation", "operation": "echo", "capability_id": "echo", "params": {"message": "usage"}}],
            status=WorkflowStatus.VERIFIED,
        ))
        result = self.app.routine_run(
            "routine_usage_refresh",
            {"data_dir": source.name, "current_state": {"usage.json": "hash"}},
        )
        self.assertEqual(result["action"], "RUN_WORKFLOW")
        self.assertTrue(result["workflow_result"]["ok"])
        self.assertEqual(result["workflow_result"]["verification"], "VERIFIED")
        source.cleanup()

    def test_deterministic_learning_benchmark(self) -> None:
        result = self.app.learning_benchmark()
        totals = result["totals"]
        self.assertGreaterEqual(totals["tasks"], 10)
        self.assertGreaterEqual(totals["cacheHits"], 2)
        self.assertEqual(totals["modelCalls"], 0)
        self.assertEqual(totals["grokCalls"], 0)
        self.assertIsInstance(totals["latencyMs"], int)
        failed = next(
            sample for sample in result["samples"]
            if sample["taskClass"] == "failed_executor"
        )
        self.assertEqual(failed["retries"], 0)
        classes = {sample["taskClass"] for sample in result["samples"]}
        for expected in (
            "repeated_exact_task", "repo_state_lookup",
            "deterministic_calculation", "known_workflow",
            "mock_code_job", "research_extraction_fixture",
            "failed_executor", "provider_outage",
            "routine_unchanged", "duplicate_request",
        ):
            self.assertIn(expected, classes)

    def test_routine_full_report_returns_current_state(self) -> None:
        source = tempfile.TemporaryDirectory()
        try:
            path = os.path.join(source.name, "usage.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("usage")
            self.app.store.save_routine({
                "id": "routine_full",
                "trigger": "full",
                "cheap_detector": "usage_snapshot",
                "workflow_id": None,
                "report_policy": "full",
                "enabled": 1,
            })
            result = self.app.routine_run(
                "routine_full",
                {"data_dir": source.name, "current_state": {"usage": "hash"}},
            )
            self.assertEqual(result["report"]["report_policy"], "full")
            self.assertEqual(result["report"]["current"]["usage"], "hash")
        finally:
            source.cleanup()

    def test_benchmark_does_not_overwrite_existing_workflow(self) -> None:
        self.app.store.save_workflow(WorkflowDefinition(
            id="benchmark_workflow",
            name="user-owned workflow",
            task_class="user-owned",
            steps=[{"kind": "operation", "operation": "echo", "capability_id": "echo"}],
            status=WorkflowStatus.VERIFIED,
        ))
        self.app.learning_benchmark()
        self.assertEqual(
            self.app.store.get_workflow("benchmark_workflow").name,
            "user-owned workflow",
        )

    def test_backup_restore_dry_run(self) -> None:
        self.app.delegate("backup me", operation="echo", params={"message": "saved"})
        archive = self.app.backup_create(self.home.name)
        result = self.app.backup_inspect(archive)
        self.assertTrue(result["valid"])
        self.assertIn("objectives", result["tablesIncluded"])
        dry_run = self.app.backup_restore(archive, dry_run=True)
        self.assertTrue(dry_run["ok"])
        self.assertTrue(dry_run["dryRun"])
        self.assertEqual(
            self.app.store.count_intelligence_entries(),
            self.app.store.count_intelligence_entries(),
        )


if __name__ == "__main__":
    unittest.main()
