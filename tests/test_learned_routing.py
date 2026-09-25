"""Tests for Learned Routing - executor metrics, historical signals, configurable weights."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.store import Store
from agentos.learned_routing import (
    ExecutorProfile,
    DEFAULT_ROUTING_WEIGHTS,
    build_executor_profiles,
    score_executor,
    explain_routing,
    LearnedRouter,
)


class TestExecutorProfile(unittest.TestCase):
    def test_executor_profile_creation(self):
        profile = ExecutorProfile(
            executor="worker_1",
            runtime="local",
            model_provider="openai",
            task_class="code_change",
            total_executions=10,
            success_count=9,
            verification_passes=8,
            total_latency_ms=50000,
            total_retries=2,
            human_corrections=1,
            avg_context_size=500,
        )
        self.assertEqual(profile.executor, "worker_1")
        self.assertEqual(profile.pass_rate, 0.9)
        self.assertEqual(profile.retry_rate, 0.2)
        self.assertEqual(profile.verification_pass_rate, 0.8)
        self.assertEqual(profile.human_correction_rate, 0.1)

    def test_executor_profile_zero_executions(self):
        profile = ExecutorProfile(
            executor="worker_1",
            runtime="local",
            model_provider="openai",
            task_class="code_change",
            total_executions=0,
        )
        self.assertEqual(profile.pass_rate, 0.0)
        self.assertEqual(profile.retry_rate, 0.0)
        self.assertEqual(profile.verification_pass_rate, 0.0)
        self.assertEqual(profile.human_correction_rate, 0.0)

    def test_executor_profile_to_dict(self):
        profile = ExecutorProfile(
            executor="worker_1",
            runtime="local",
            model_provider="openai",
            task_class="code_change",
            total_executions=10,
            success_count=9,
        )
        d = profile.to_dict()
        self.assertEqual(d["executor"], "worker_1")
        self.assertEqual(d["total_executions"], 10)
        self.assertEqual(d["success_count"], 9)


class TestBuildExecutorProfiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_executor_profiles_empty(self):
        profiles = build_executor_profiles(self.store)
        self.assertEqual(profiles, [])

    def test_build_executor_profiles_from_metrics(self):
        # Add metrics for multiple executors
        self.store.record_executor_metric({
            "id": "m1",
            "executor": "worker_1",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "code_change",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 1000,
            "retry_count": 0,
            "human_correction": 0,
            "context_size": 500,
        })
        self.store.record_executor_metric({
            "id": "m2",
            "executor": "worker_1",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "code_change",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 1200,
            "retry_count": 1,
            "human_correction": 0,
            "context_size": 600,
        })
        self.store.record_executor_metric({
            "id": "m3",
            "executor": "worker_2",
            "runtime": "browser",
            "model_provider": "grok",
            "task_class": "web_research",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 5000,
            "retry_count": 0,
            "human_correction": 0,
            "context_size": 2000,
        })

        profiles = build_executor_profiles(self.store)
        self.assertEqual(len(profiles), 2)

        # Check worker_1 profile
        w1 = next(p for p in profiles if p.executor == "worker_1")
        self.assertEqual(w1.task_class, "code_change")
        self.assertEqual(w1.total_executions, 2)
        self.assertEqual(w1.success_count, 2)
        self.assertEqual(w1.verification_passes, 2)
        self.assertEqual(w1.pass_rate, 1.0)
        self.assertEqual(w1.retry_rate, 0.5)  # 1 retry / 2 executions
        self.assertEqual(w1.verification_pass_rate, 1.0)

        # Check worker_2 profile
        w2 = next(p for p in profiles if p.executor == "worker_2")
        self.assertEqual(w2.task_class, "web_research")
        self.assertEqual(w2.runtime, "browser")
        self.assertEqual(w2.model_provider, "grok")

    def test_build_executor_profiles_filter_by_task_class(self):
        self.store.record_executor_metric({
            "id": "m1",
            "executor": "worker_1",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "code_change",
            "success": 1,
        })
        self.store.record_executor_metric({
            "id": "m2",
            "executor": "worker_1",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "web_research",
            "success": 1,
        })

        profiles = build_executor_profiles(self.store, task_class="code_change")
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].task_class, "code_change")


class TestScoreExecutor(unittest.TestCase):
    def setUp(self):
        self.profile = ExecutorProfile(
            executor="worker_1",
            runtime="local",
            model_provider="openai",
            task_class="code_change",
            total_executions=100,
            success_count=95,
            verification_passes=90,
            total_latency_ms=500000,
            total_retries=10,
            human_corrections=5,
            avg_context_size=500,
        )

    def test_score_executor_positive(self):
        score = score_executor(self.profile, DEFAULT_ROUTING_WEIGHTS)
        self.assertGreater(score, 0.0)

    def test_score_executor_weights_applied(self):
        # High weight on capability_fit (pass_rate)
        weights = {"capability_fit": 10.0, "quality": 0.0, "health": 0.0,
                   "scarcity": 0.0, "cost": 0.0, "latency": 0.0,
                   "historical_pass_rate": 0.0, "retry_rate": 0.0,
                   "verification_success": 0.0, "human_correction": 0.0,
                   "context_cost": 0.0}
        score = score_executor(self.profile, weights)
        # pass_rate = 0.95, so score should be ~9.5
        self.assertAlmostEqual(score, 9.5, places=1)

    def test_score_executor_penalty_retry_rate(self):
        # Test penalty in isolation with only negative weights
        weights = {
            "retry_rate": -10.0,
            "capability_fit": 0.0,
            "quality": 0.0,
            "health": 0.0,
            "scarcity": 0.0,
            "cost": 0.0,
            "latency": 0.0,
            "historical_pass_rate": 0.0,
            "verification_success": 0.0,
            "human_correction": 0.0,
            "context_cost": 0.0,
        }
        score = score_executor(self.profile, weights)
        # retry_rate = 0.1, penalty = -1.0
        self.assertAlmostEqual(score, -1.0, places=1)

    def test_score_executor_penalty_human_correction(self):
        weights = {
            "human_correction": -10.0,
            "capability_fit": 0.0,
            "quality": 0.0,
            "health": 0.0,
            "scarcity": 0.0,
            "cost": 0.0,
            "latency": 0.0,
            "historical_pass_rate": 0.0,
            "retry_rate": 0.0,
            "verification_success": 0.0,
            "context_cost": 0.0,
        }
        score = score_executor(self.profile, weights)
        # human_correction_rate = 0.05, penalty = -0.5
        self.assertAlmostEqual(score, -0.5, places=1)


class TestExplainRouting(unittest.TestCase):
    def setUp(self):
        self.profile = ExecutorProfile(
            executor="worker_1",
            runtime="local",
            model_provider="openai",
            task_class="code_change",
            total_executions=100,
            success_count=95,
            verification_passes=90,
            total_latency_ms=500000,
            total_retries=10,
            human_corrections=5,
            avg_context_size=500,
        )

    def test_explain_routing_structure(self):
        explanation = explain_routing(self.profile, DEFAULT_ROUTING_WEIGHTS)
        self.assertEqual(explanation["executor"], "worker_1")
        self.assertEqual(explanation["task_class"], "code_change")
        self.assertIn("total_score", explanation)
        self.assertIn("factors", explanation)
        self.assertIn("weights_used", explanation)

        # Check all factors present
        expected_factors = [
            "capability_fit", "quality", "health", "scarcity", "cost",
            "latency", "historical_pass_rate", "retry_rate_penalty",
            "verification_success", "human_correction_penalty", "context_cost_penalty"
        ]
        for factor in expected_factors:
            self.assertIn(factor, explanation["factors"])

        # Check factor structure
        for factor_name, factor_data in explanation["factors"].items():
            self.assertIn("weight", factor_data)
            self.assertIn("value", factor_data)
            self.assertIn("contribution", factor_data)


class TestLearnedRouter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.router = LearnedRouter(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_best_executor_no_profiles(self):
        executor, info = self.router.get_best_executor("code_change", ["worker_1", "worker_2"])
        self.assertIsNone(executor)
        self.assertEqual(info["reason"], "no candidate profiles")

    def test_get_best_executor_with_profiles(self):
        # Add metrics
        self.store.record_executor_metric({
            "id": "m1",
            "executor": "worker_1",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "code_change",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 1000,
            "retry_count": 0,
            "human_correction": 0,
            "context_size": 500,
        })
        self.store.record_executor_metric({
            "id": "m2",
            "executor": "worker_2",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "code_change",
            "success": 1,
            "verification_result": "UNVERIFIED",
            "latency_ms": 2000,
            "retry_count": 2,
            "human_correction": 1,
            "context_size": 1000,
        })

        executor, info = self.router.get_best_executor("code_change", ["worker_1", "worker_2"])
        self.assertEqual(executor, "worker_1")  # Should prefer worker_1 (better metrics)
        self.assertIn("selected", info)
        self.assertIn("score", info)
        self.assertIn("explanation", info)
        self.assertIn("candidates_evaluated", info)
        self.assertEqual(info["candidates_evaluated"], 2)
        self.assertEqual(len(info["all_scores"]), 2)

    def test_routing_decision_round_trips_through_store(self):
        self.router.record_routing_decision(
            "job_1",
            "code_change",
            "worker_1",
            {"score": 0.9, "reason": "measured"},
        )
        explanation = self.router.get_routing_explanation("job_1")
        self.assertIsNotNone(explanation)
        self.assertEqual(explanation["chosen"], "worker_1")
        self.assertEqual(explanation["decision"]["score"], 0.9)

    def test_get_best_executor_filters_candidates(self):
        self.store.record_executor_metric({
            "id": "m1",
            "executor": "worker_1",
            "task_class": "code_change",
            "success": 1,
        })
        self.store.record_executor_metric({
            "id": "m2",
            "executor": "worker_2",
            "task_class": "code_change",
            "success": 1,
        })
        self.store.record_executor_metric({
            "id": "m3",
            "executor": "worker_3",
            "task_class": "code_change",
            "success": 1,
        })

        executor, info = self.router.get_best_executor("code_change", ["worker_1", "worker_3"])
        self.assertIn(executor, ["worker_1", "worker_3"])
        self.assertEqual(info["candidates_evaluated"], 2)


if __name__ == "__main__":
    unittest.main()