"""Task 5: normalized execution result taxonomy + execution record.

Every execution normalizes into a result x verification pair; cost stays
UNKNOWN unless actually measured; Execution round-trips all new fields.
"""

import unittest

from agentos.adapters.base import AdapterResult
from agentos.models import (
    CostPolicy,
    Execution,
    ExecutionMode,
    ResultState,
    VerificationState,
    cost_status,
    normalize_result,
)


def _ok_result(**kwargs):
    kwargs.setdefault("ok", True)
    kwargs.setdefault("output", {"stdout": "done"})
    return AdapterResult(**kwargs)


class TestNormalizeResult(unittest.TestCase):
    def test_inspect_never_completed(self):
        result, verification = normalize_result(
            _ok_result(), ExecutionMode.INSPECT
        )
        self.assertNotEqual(result, ResultState.COMPLETED)
        self.assertEqual(verification, VerificationState.UNVERIFIED)

    def test_simulated_never_completed(self):
        result, _ = normalize_result(_ok_result(), ExecutionMode.SIMULATED)
        self.assertNotEqual(result, ResultState.COMPLETED)

    def test_live_ok_is_completed_verifying(self):
        result, verification = normalize_result(_ok_result(), ExecutionMode.LIVE)
        self.assertEqual(result, ResultState.COMPLETED)
        self.assertEqual(verification, VerificationState.VERIFYING)

    def test_live_failure(self):
        result, verification = normalize_result(
            AdapterResult(ok=False, output={}, error="boom"),
            ExecutionMode.LIVE,
        )
        self.assertEqual(result, ResultState.FAILED)
        self.assertEqual(verification, VerificationState.UNVERIFIED)

    def test_timed_out_is_interrupted(self):
        result, _ = normalize_result(
            AdapterResult(ok=False, output={"timed_out": True}, error="timed out"),
            ExecutionMode.LIVE,
        )
        self.assertEqual(result, ResultState.INTERRUPTED)

    def test_policy_block_is_refused(self):
        result, _ = normalize_result(
            AdapterResult(ok=False, output={}, error="policy_block: destructive"),
            ExecutionMode.LIVE,
        )
        self.assertEqual(result, ResultState.REFUSED)


class TestCostStatus(unittest.TestCase):
    def test_unmeasured_is_unknown(self):
        self.assertEqual(cost_status(), "UNKNOWN")
        self.assertEqual(cost_status(None), "UNKNOWN")
        self.assertEqual(cost_status("UNKNOWN"), "UNKNOWN")

    def test_cost_policy_values(self):
        self.assertEqual(
            {p.value for p in CostPolicy},
            {
                "FREE_FIRST",
                "LOW_COST",
                "BALANCED",
                "QUALITY_FIRST",
                "EXPLICIT_PROVIDER",
            },
        )


class TestExecutionRecord(unittest.TestCase):
    def test_round_trip_new_fields(self):
        execution = Execution(
            id="exec_1",
            objective_id="obj_1",
            capability_id="cap_1",
            operation="run",
            mode=ExecutionMode.LIVE.value,
            files_changed=["a.py", "b.py"],
            git_diff_summary="2 files changed",
            cost_status="UNKNOWN",
            cost_amount="UNKNOWN",
            parent_objective_id="obj_parent",
            authorization=[{"class": "network"}],
            result_summary="did the thing",
        )
        revived = Execution.from_dict(execution.to_dict())
        self.assertEqual(revived.mode, "live")
        self.assertEqual(revived.files_changed, ["a.py", "b.py"])
        self.assertEqual(revived.git_diff_summary, "2 files changed")
        self.assertEqual(revived.cost_status, "UNKNOWN")
        self.assertEqual(revived.cost_amount, "UNKNOWN")
        self.assertEqual(revived.parent_objective_id, "obj_parent")
        self.assertEqual(revived.authorization, [{"class": "network"}])
        self.assertEqual(revived.result_summary, "did the thing")

    def test_old_dict_loads_with_defaults(self):
        old = {
            "id": "exec_old",
            "objective_id": "obj_1",
            "capability_id": "cap_1",
            "operation": "run",
        }
        revived = Execution.from_dict(old)
        self.assertEqual(revived.cost_status, "UNKNOWN")
        self.assertEqual(revived.cost_amount, "UNKNOWN")
        self.assertEqual(revived.files_changed, [])
        self.assertIsNone(revived.parent_objective_id)
        # Defaults must also serialize so persistence never drops keys.
        self.assertIn("cost_status", revived.to_dict())

    def test_store_save_reload_round_trip(self):
        import tempfile

        from agentos.store import Store

        with tempfile.TemporaryDirectory() as home:
            store = Store(home + "/agentos.db")
            try:
                execution = Execution(
                    id="exec_store",
                    objective_id="obj_1",
                    capability_id="cap_1",
                    operation="run",
                    mode="live",
                    files_changed=["x.py"],
                    git_diff_summary="1 file changed",
                    cost_status="UNKNOWN",
                    cost_amount="UNKNOWN",
                    parent_objective_id="obj_p",
                    authorization=[{"class": "network"}],
                    result_summary="summary",
                )
                store.save_execution(execution)
                revived = store.get_execution("exec_store")
                assert revived is not None
                self.assertEqual(revived.files_changed, ["x.py"])
                self.assertEqual(revived.git_diff_summary, "1 file changed")
                self.assertEqual(revived.cost_status, "UNKNOWN")
                self.assertEqual(revived.parent_objective_id, "obj_p")
                self.assertEqual(revived.result_summary, "summary")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
