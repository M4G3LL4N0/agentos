"""Tests for the Coach Layer - trigger detection, LessonCandidate generation, lesson store, promotion."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import (
    LessonCandidate,
    LessonStatus,
    VerificationOutcome,
)
from agentos.store import Store
from agentos.coach import (
    CoachTrigger,
    COACH_TRIGGERS,
    detect_coach_triggers,
    generate_lesson,
    Coach,
    _recommend_change,
)


class TestCoachTriggers(unittest.TestCase):
    def test_coach_triggers_defined(self):
        self.assertEqual(len(COACH_TRIGGERS), 11)
        self.assertIn("retries_exceeded", COACH_TRIGGERS)
        self.assertIn("worker_failed", COACH_TRIGGERS)
        self.assertIn("routing_poor", COACH_TRIGGERS)
        self.assertIn("context_excessive", COACH_TRIGGERS)
        self.assertIn("cache_miss_avoidable", COACH_TRIGGERS)
        self.assertIn("human_correction", COACH_TRIGGERS)
        self.assertIn("task_recurring", COACH_TRIGGERS)
        self.assertIn("verification_expensive", COACH_TRIGGERS)
        self.assertIn("workflow_repeated", COACH_TRIGGERS)
        self.assertIn("provider_underperformed", COACH_TRIGGERS)
        self.assertIn("provider_overperformed", COACH_TRIGGERS)

    def test_coach_trigger_dataclass(self):
        trigger = CoachTrigger(
            trigger_type="retries_exceeded",
            task_id="task_123",
            task_class="code_change",
            context={"attempts": 4},
            evidence_refs=["ref1"],
        )
        self.assertEqual(trigger.trigger_type, "retries_exceeded")
        self.assertEqual(trigger.task_id, "task_123")
        self.assertEqual(trigger.task_class, "code_change")


class TestDetectCoachTriggers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_retries_exceeded_trigger(self):
        history = [
            {"attempt": 1, "status": "RUNNING"},
            {"attempt": 2, "status": "RUNNING"},
            {"attempt": 3, "status": "RUNNING"},
            {"attempt": 4, "status": "FAILED", "evidence_refs": ["ref1"]},
        ]
        triggers = detect_coach_triggers("task_1", "code_change", history, self.store)
        trigger_types = [t.trigger_type for t in triggers]
        self.assertIn("retries_exceeded", trigger_types)
        t = next(t for t in triggers if t.trigger_type == "retries_exceeded")
        self.assertEqual(t.context["attempts"], 4)

    def test_worker_failed_trigger(self):
        history = [{"attempt": 1, "status": "FAILED", "failure_type": "timeout", "evidence_refs": ["ref1"]}]
        triggers = detect_coach_triggers("task_1", "code_change", history, self.store)
        trigger_types = [t.trigger_type for t in triggers]
        self.assertIn("worker_failed", trigger_types)

    def test_routing_poor_trigger(self):
        history = [
            {"attempt": 1, "status": "FAILED", "escalated": True},
            {"attempt": 2, "status": "FAILED", "escalated": True},
            {"attempt": 3, "status": "FAILED", "escalated": True},
        ]
        triggers = detect_coach_triggers("task_1", "code_change", history, self.store)
        trigger_types = [t.trigger_type for t in triggers]
        self.assertIn("routing_poor", trigger_types)

    def test_context_excessive_trigger(self):
        history = [{"attempt": 1, "status": "RUNNING", "context_size": 15000, "evidence_refs": []}]
        triggers = detect_coach_triggers("task_1", "code_change", history, self.store)
        trigger_types = [t.trigger_type for t in triggers]
        self.assertIn("context_excessive", trigger_types)

    def test_human_correction_trigger(self):
        history = [{"attempt": 1, "status": "FAILED", "human_correction": True, "correction_detail": "fixed path", "evidence_refs": []}]
        triggers = detect_coach_triggers("task_1", "code_change", history, self.store)
        trigger_types = [t.trigger_type for t in triggers]
        self.assertIn("human_correction", trigger_types)

    def test_verification_expensive_trigger(self):
        history = [{"attempt": 1, "status": "VERIFIED_COMPLETE", "verification_rounds": 4, "evidence_refs": []}]
        triggers = detect_coach_triggers("task_1", "code_change", history, self.store)
        trigger_types = [t.trigger_type for t in triggers]
        self.assertIn("verification_expensive", trigger_types)

    def test_provider_underperformed_trigger(self):
        history = [{"attempt": 1, "status": "FAILED", "provider": "grok", "provider_metrics": {"success_rate": 0.3}, "evidence_refs": []}]
        triggers = detect_coach_triggers("task_1", "code_change", history, self.store)
        trigger_types = [t.trigger_type for t in triggers]
        self.assertIn("provider_underperformed", trigger_types)


class TestGenerateLesson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_generate_lesson_retries_exceeded(self):
        trigger = CoachTrigger(
            trigger_type="retries_exceeded",
            task_id="task_1",
            task_class="code_change",
            context={"attempts": 4},
            evidence_refs=["ref1"],
        )
        history = [{"id": "exec1", "attempt": 4, "evidence_refs": ["ref1"]}]
        lesson = generate_lesson(trigger, history, self.store)

        self.assertIsInstance(lesson, LessonCandidate)
        self.assertEqual(lesson.task_class, "code_change")
        self.assertEqual(lesson.scope, "retries_exceeded")
        self.assertIn("Retry logic too aggressive", lesson.root_cause)
        self.assertEqual(lesson.status, LessonStatus.CANDIDATE)
        self.assertLessEqual(lesson.confidence, 0.5)

    def test_generate_lesson_human_correction_higher_confidence(self):
        trigger = CoachTrigger(
            trigger_type="human_correction",
            task_id="task_1",
            task_class="code_change",
            context={"correction": "fixed path"},
            evidence_refs=["ref1"],
        )
        history = [{"id": "exec1", "evidence_refs": ["ref1"]}]
        lesson = generate_lesson(trigger, history, self.store)
        self.assertGreaterEqual(lesson.confidence, 0.7)

    def test_generate_lesson_task_recurring_higher_confidence(self):
        trigger = CoachTrigger(
            trigger_type="task_recurring",
            task_id="task_1",
            task_class="code_change",
            context={"execution_count": 10},
            evidence_refs=["ref1"],
        )
        history = [{"id": "exec1", "evidence_refs": ["ref1"]}]
        lesson = generate_lesson(trigger, history, self.store)
        self.assertGreaterEqual(lesson.confidence, 0.6)


class TestCoach(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.coach = Coach(self.store, sampling_rate=1.0)  # Always sample for tests

    def tearDown(self):
        self.tmp.cleanup()

    def test_analyze_execution_creates_lessons(self):
        history = [
            {"id": "exec1", "attempt": 4, "status": "FAILED", "evidence_refs": ["ref1"]},
        ]
        lessons = self.coach.analyze_execution("task_1", "code_change", history)
        self.assertGreaterEqual(len(lessons), 1)
        for l in lessons:
            self.assertIsInstance(l, LessonCandidate)
            # Check lesson was persisted
            stored = self.store.get_lesson(l.id)
            self.assertIsNotNone(stored)

    def test_promote_lesson(self):
        # Create a lesson directly
        lesson = LessonCandidate(
            id="lesson_1",
            task_class="code_change",
            scope="retries_exceeded",
            symptom="retries_exceeded",
            root_cause="test",
            lesson="test lesson",
            status=LessonStatus.CANDIDATE,
        )
        self.store.save_lesson(lesson)

        # Promote it
        ok = self.coach.promote_lesson("lesson_1", LessonStatus.VALIDATED)
        self.assertTrue(ok)

        stored = self.store.get_lesson("lesson_1")
        self.assertEqual(stored.status, LessonStatus.VALIDATED)

    def test_get_lessons_for_task_class(self):
        lesson = LessonCandidate(
            id="lesson_1",
            task_class="code_change",
            scope="retries_exceeded",
            symptom="retries_exceeded",
            root_cause="test",
            lesson="test lesson",
        )
        self.store.save_lesson(lesson)

        lesson2 = LessonCandidate(
            id="lesson_2",
            task_class="research",
            scope="cache_miss",
            symptom="cache_miss",
            root_cause="test",
            lesson="test lesson",
        )
        self.store.save_lesson(lesson2)

        code_change_lessons = self.coach.get_lessons_for_task_class("code_change")
        self.assertEqual(len(code_change_lessons), 1)
        self.assertEqual(code_change_lessons[0].id, "lesson_1")

    def test_get_adopted_lessons(self):
        lesson = LessonCandidate(
            id="lesson_1",
            task_class="code_change",
            scope="retries_exceeded",
            symptom="retries_exceeded",
            root_cause="test",
            lesson="test lesson",
            status=LessonStatus.ADOPTED,
        )
        self.store.save_lesson(lesson)

        lesson2 = LessonCandidate(
            id="lesson_2",
            task_class="code_change",
            scope="cache_miss",
            symptom="cache_miss",
            root_cause="test",
            lesson="test lesson",
            status=LessonStatus.CANDIDATE,
        )
        self.store.save_lesson(lesson2)

        adopted = self.coach.get_adopted_lessons()
        self.assertEqual(len(adopted), 1)
        self.assertEqual(adopted[0].id, "lesson_1")


class TestRecommendChange(unittest.TestCase):
    def test_recommend_change_known_triggers(self):
        for trigger in COACH_TRIGGERS:
            rec = _recommend_change(trigger, [])
            self.assertIsInstance(rec, str)
            self.assertGreater(len(rec), 10)


class TestLessonCandidateModel(unittest.TestCase):
    def test_lesson_candidate_to_from_dict(self):
        lesson = LessonCandidate(
            id="l1",
            task_class="code_change",
            scope="retries_exceeded",
            symptom="retries_exceeded",
            root_cause="test",
            lesson="Add pre-check",
            evidence_refs=["ref1", "ref2"],
            confidence=0.8,
            recommended_change="Add pre-execution validation",
            status=LessonStatus.VALIDATED,
            times_observed=3,
            times_applied=1,
        )
        d = lesson.to_dict()
        self.assertEqual(d["status"], "VALIDATED")
        self.assertEqual(d["confidence"], 0.8)
        self.assertEqual(d["times_observed"], 3)

        restored = LessonCandidate.from_dict(d)
        self.assertEqual(restored.id, "l1")
        self.assertEqual(restored.status, LessonStatus.VALIDATED)
        self.assertEqual(restored.times_observed, 3)
        self.assertEqual(restored.evidence_refs, ["ref1", "ref2"])


if __name__ == "__main__":
    unittest.main()