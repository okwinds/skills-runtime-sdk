from __future__ import annotations

import time

import pytest

from skills_runtime.core.loop_controller import LoopController


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(
    max_steps: int = 10,
    max_wall_time_sec: float | None = None,
    started_monotonic: float | None = None,
    cancel_checker=None,
) -> LoopController:
    return LoopController(
        max_steps=max_steps,
        max_wall_time_sec=max_wall_time_sec,
        started_monotonic=started_monotonic if started_monotonic is not None else time.monotonic(),
        cancel_checker=cancel_checker,
    )


# ---------------------------------------------------------------------------
# try_consume_tool_step — step counting and max_steps budget
# ---------------------------------------------------------------------------

class TestTryConsumeToolStep:
    def test_first_step_succeeds(self) -> None:
        c = _make(max_steps=1)
        assert c.try_consume_tool_step() is True

    def test_step_at_limit_is_rejected(self) -> None:
        c = _make(max_steps=1)
        c.try_consume_tool_step()
        assert c.try_consume_tool_step() is False

    def test_steps_counted_correctly(self) -> None:
        c = _make(max_steps=3)
        assert c.try_consume_tool_step() is True   # 1
        assert c.try_consume_tool_step() is True   # 2
        assert c.try_consume_tool_step() is True   # 3
        assert c.try_consume_tool_step() is False  # over budget

    def test_zero_max_steps_rejects_immediately(self) -> None:
        c = _make(max_steps=0)
        assert c.try_consume_tool_step() is False

    def test_rejected_step_does_not_increment_counter(self) -> None:
        c = _make(max_steps=2)
        c.try_consume_tool_step()
        c.try_consume_tool_step()
        # Two rejections — internal counter must stay at 2
        c.try_consume_tool_step()
        c.try_consume_tool_step()
        # A new controller with max_steps=4 should still allow 4 steps
        c2 = _make(max_steps=4)
        results = [c2.try_consume_tool_step() for _ in range(5)]
        assert results == [True, True, True, True, False]


# ---------------------------------------------------------------------------
# wall_time_exceeded — timeout
# ---------------------------------------------------------------------------

class TestWallTimeExceeded:
    def test_no_limit_never_exceeds(self) -> None:
        c = _make(max_wall_time_sec=None)
        assert c.wall_time_exceeded() is False

    def test_future_deadline_not_exceeded(self) -> None:
        c = _make(max_wall_time_sec=9999.0)
        assert c.wall_time_exceeded() is False

    def test_past_deadline_exceeded(self) -> None:
        # started 10 s ago, budget is 1 s → already exceeded
        c = _make(max_wall_time_sec=1.0, started_monotonic=time.monotonic() - 10.0)
        assert c.wall_time_exceeded() is True

    def test_exactly_at_boundary_not_exceeded(self) -> None:
        # elapsed ≈ 0, budget = 0.001 s → not yet exceeded
        c = _make(max_wall_time_sec=0.001)
        assert c.wall_time_exceeded() is False


# ---------------------------------------------------------------------------
# is_cancelled — cancel_checker
# ---------------------------------------------------------------------------

class TestIsCancelled:
    def test_no_checker_returns_false(self) -> None:
        c = _make(cancel_checker=None)
        assert c.is_cancelled() is False

    def test_checker_returns_true(self) -> None:
        c = _make(cancel_checker=lambda: True)
        assert c.is_cancelled() is True

    def test_checker_returns_false(self) -> None:
        c = _make(cancel_checker=lambda: False)
        assert c.is_cancelled() is False

    def test_checker_exception_fail_open(self) -> None:
        def boom():
            raise RuntimeError("network error")

        c = _make(cancel_checker=boom)
        # fail-open: exception → False (do not cancel)
        assert c.is_cancelled() is False

    def test_checker_called_each_time(self) -> None:
        calls = []

        def checker():
            calls.append(1)
            return len(calls) >= 3

        c = _make(cancel_checker=checker)
        assert c.is_cancelled() is False  # call 1
        assert c.is_cancelled() is False  # call 2
        assert c.is_cancelled() is True   # call 3
        assert len(calls) == 3


# ---------------------------------------------------------------------------
# record_denied_approval + should_abort_due_to_repeated_denial
# ---------------------------------------------------------------------------

class TestDeniedApprovals:
    def test_first_denial_returns_one(self) -> None:
        c = _make()
        assert c.record_denied_approval("key_a") == 1

    def test_second_denial_returns_two(self) -> None:
        c = _make()
        c.record_denied_approval("key_a")
        assert c.record_denied_approval("key_a") == 2

    def test_different_keys_are_independent(self) -> None:
        c = _make()
        c.record_denied_approval("key_a")
        c.record_denied_approval("key_a")
        assert c.record_denied_approval("key_b") == 1

    def test_should_abort_below_threshold(self) -> None:
        c = _make()
        c.record_denied_approval("key_a")  # count = 1
        assert c.should_abort_due_to_repeated_denial(approval_key="key_a", threshold=2) is False

    def test_should_abort_at_threshold(self) -> None:
        c = _make()
        c.record_denied_approval("key_a")
        c.record_denied_approval("key_a")  # count = 2
        assert c.should_abort_due_to_repeated_denial(approval_key="key_a", threshold=2) is True

    def test_should_abort_above_threshold(self) -> None:
        c = _make()
        for _ in range(5):
            c.record_denied_approval("key_a")
        assert c.should_abort_due_to_repeated_denial(approval_key="key_a", threshold=2) is True

    def test_should_abort_unknown_key_returns_false(self) -> None:
        c = _make()
        assert c.should_abort_due_to_repeated_denial(approval_key="never_seen") is False

    def test_default_threshold_is_two(self) -> None:
        c = _make()
        c.record_denied_approval("k")
        assert c.should_abort_due_to_repeated_denial(approval_key="k") is False
        c.record_denied_approval("k")
        assert c.should_abort_due_to_repeated_denial(approval_key="k") is True

    def test_independent_default_dict_across_instances(self) -> None:
        c1 = _make()
        c2 = _make()
        c1.record_denied_approval("k")
        c1.record_denied_approval("k")
        assert c2.denied_approvals_by_key == {}

    def test_none_approval_key_normalised_to_empty_string(self) -> None:
        c = _make()
        c.record_denied_approval(None)  # type: ignore[arg-type]
        assert c.denied_approvals_by_key.get("") == 1


# ---------------------------------------------------------------------------
# next_turn_id / next_step_id — ID generation
# ---------------------------------------------------------------------------

class TestIdGeneration:
    def test_turn_ids_sequential(self) -> None:
        c = _make()
        assert c.next_turn_id() == "turn_1"
        assert c.next_turn_id() == "turn_2"
        assert c.next_turn_id() == "turn_3"

    def test_step_ids_sequential(self) -> None:
        c = _make()
        assert c.next_step_id() == "step_1"
        assert c.next_step_id() == "step_2"

    def test_turn_and_step_counters_are_independent(self) -> None:
        c = _make()
        c.next_turn_id()
        c.next_turn_id()
        assert c.next_step_id() == "step_1"

    def test_fresh_instance_starts_at_one(self) -> None:
        c = _make()
        assert c.next_turn_id() == "turn_1"
        assert c.next_step_id() == "step_1"


# ---------------------------------------------------------------------------
# Normal state — nothing triggered → should_stop=False
# ---------------------------------------------------------------------------

class TestNormalState:
    def test_no_stop_conditions_active(self) -> None:
        c = _make(max_steps=100, max_wall_time_sec=9999.0, cancel_checker=lambda: False)
        assert c.wall_time_exceeded() is False
        assert c.is_cancelled() is False
        assert c.try_consume_tool_step() is True
        assert c.should_abort_due_to_repeated_denial(approval_key="x") is False
