"""Codex per-turn token usage projection regression tests."""

from types import SimpleNamespace

from src.runtimes.codex.runtime import _usage_payload_from_token_usage


def _breakdown(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_output_tokens: int,
    total_tokens: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        total_tokens=total_tokens,
    )


def test_should_project_last_turn_usage_without_recounting_thread_total() -> None:
    usage = SimpleNamespace(
        total=_breakdown(
            input_tokens=100_000,
            cached_input_tokens=80_000,
            output_tokens=10_000,
            reasoning_output_tokens=4_000,
            total_tokens=110_000,
        ),
        last=_breakdown(
            input_tokens=12_000,
            cached_input_tokens=9_000,
            output_tokens=700,
            reasoning_output_tokens=300,
            total_tokens=13_000,
        ),
    )

    payload = _usage_payload_from_token_usage(usage, "gpt-5.6-luna")

    assert payload == {
        "input_tokens": 3_000,
        "output_tokens": 1_000,
        "cache_read_tokens": 9_000,
        "cache_write_tokens": 0,
        "model_usage": {
            "gpt-5.6-luna": {
                "input_tokens": 3_000,
                "output_tokens": 1_000,
                "cache_read_tokens": 9_000,
                "cache_write_tokens": 0,
                "reasoning_output_tokens": 300,
                "total_tokens": 13_000,
            }
        },
    }


def test_should_clamp_uncached_input_when_provider_reports_inconsistent_cache() -> None:
    usage = SimpleNamespace(
        total=_breakdown(
            input_tokens=1,
            cached_input_tokens=2,
            output_tokens=0,
            reasoning_output_tokens=0,
            total_tokens=2,
        ),
        last=_breakdown(
            input_tokens=1,
            cached_input_tokens=2,
            output_tokens=0,
            reasoning_output_tokens=0,
            total_tokens=2,
        ),
    )

    payload = _usage_payload_from_token_usage(usage, "gpt-5.6-luna")

    assert payload["input_tokens"] == 0
    assert payload["cache_read_tokens"] == 2


def test_should_ignore_notification_without_last_turn_usage() -> None:
    usage = SimpleNamespace(last=None)

    assert _usage_payload_from_token_usage(usage, "gpt-5.6-luna") is None
