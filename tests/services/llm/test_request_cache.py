from deeptutor.services.llm.provider_core.openai_codex_provider import _prompt_cache_key
from deeptutor.services.llm.request_cache import compare_requests, fingerprint_request


def test_prefix_diagnostics_distinguish_append_history_tools_and_route_changes():
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}]
    route = {"model": "example"}
    previous = fingerprint_request(messages, [], route)
    current = fingerprint_request(
        [*messages, {"role": "assistant", "content": "answer"}], [], route
    )
    assert compare_requests(previous, current)["first_change"] == "append"
    assert compare_requests(previous, current)["shared_prefix_messages"] == 2
    assert "question" not in str(previous)
    assert (
        compare_requests(previous, fingerprint_request(messages[:1], [], route))["first_change"]
        == "history"
    )
    assert (
        compare_requests(previous, fingerprint_request(messages, [{"name": "new"}], route))[
            "first_change"
        ]
        == "tools"
    )
    assert (
        compare_requests(previous, fingerprint_request(messages, [], {"model": "other"}))[
            "first_change"
        ]
        == "route"
    )


def test_codex_cache_affinity_survives_new_turns_and_tool_messages():
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}]
    assert _prompt_cache_key(messages) == _prompt_cache_key(
        [*messages, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "next"}]
    )
    assert _prompt_cache_key(messages) != _prompt_cache_key(
        [{"role": "system", "content": "new rules"}]
    )


def test_image_fingerprint_hashes_bytes_but_never_tokenizes_base64(monkeypatch):
    from deeptutor.services.session import context_builder

    counted = []

    def count(text):
        counted.append(text)
        assert "base64" not in text
        return len(text) // 4

    monkeypatch.setattr(context_builder, "count_tokens", count)
    image = "data:image/png;base64," + "A" * 4_000_000
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": image}}]}]
    previous = fingerprint_request(messages, [], {"model": "vision"})
    current = fingerprint_request(
        [*messages, {"role": "user", "content": "Continue"}], [], {"model": "vision"}
    )
    assert compare_requests(previous, current)["first_change"] == "append"
    assert previous["messages"][0]["tokens"] < 2048
    changed = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image + "B"}}]}
    ]
    assert (
        fingerprint_request(changed, [], {"model": "vision"})["messages"][0]["hash"]
        != previous["messages"][0]["hash"]
    )
    assert sum(map(len, counted)) < 512


def test_claude_compat_marks_latest_tool_result_with_at_most_four_breakpoints():
    from copy import deepcopy

    from deeptutor.services.llm.provider_core.openai_compat_provider import OpenAICompatProvider

    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "question"},
        {"role": "tool", "tool_call_id": "a", "content": "large new evidence"},
    ]
    tools = [{"type": "function", "function": {"name": f"tool{i}"}} for i in range(25)]
    original = deepcopy((messages, tools))
    marked, schemas = OpenAICompatProvider._apply_cache_control(messages, tools)
    assert marked[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    count = sum("cache_control" in tool for tool in schemas)
    count += sum(
        "cache_control" in block
        for m in marked
        for block in m["content"]
        if isinstance(block, dict)
    )
    assert count <= 4
    assert (messages, tools) == original
