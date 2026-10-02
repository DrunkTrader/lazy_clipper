import json
import sys
import types

import pytest

from backend.app.config import Settings
from backend.app.services.llm import LLMClient, LLMResponseError, MomentAnalyzer


class FakeResponse:
    def __init__(self, content):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]


class FakeCompletions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)


class FakeOpenAI:
    completions = None
    init_kwargs = None

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs
        self.chat = types.SimpleNamespace(completions=type(self).completions)


def install_fake_openai(monkeypatch, responses):
    completions = FakeCompletions(responses)
    FakeOpenAI.completions = completions
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    return completions


def settings():
    # Keep this unit test independent of a developer's local .env.
    return Settings(
        llm_base_url="http://gateway.test/v1",
        llm_api_key="secret",
        llm_model="model-a",
        llm_max_retries=2,
    )


def segment(start, end, text):
    return {"start": start, "end": end, "text": text}


def detection(title="A useful idea", start=0, end=1):
    return json.dumps({"candidates": [{
        "start_segment": start, "end_segment": end, "title": title,
        "description": "A complete idea.", "reason": "It has a setup and payoff.",
    }]})


def review(candidate_id=0, accepted=True, hook=8):
    body = {"candidate_id": candidate_id, "accepted": accepted, "reason": "Reviewed."}
    if accepted:
        body["scores"] = {name: hook for name in ("hook", "clarity", "standalone", "novelty", "emotional_interest", "payoff")}
    return json.dumps({"candidates": [body]})


def test_client_uses_only_provider_config_and_sends_timestamped_segments(monkeypatch):
    calls = install_fake_openai(monkeypatch, [detection(), review()])
    client = LLMClient(settings())
    analyzer = MomentAnalyzer(client._settings, client=client)
    result = analyzer.analyze([{"segments": [segment(10.25, 12.5, "First"), segment(12.5, 30, "Second")]}])

    assert result[0].start == 10.25
    assert result[0].end == 30
    detection_prompt = calls.calls[0]["messages"][1]["content"]
    assert "[0] 10.250-12.500: First" in detection_prompt
    assert "[1] 12.500-30.000: Second" in detection_prompt
    assert "response_format" not in calls.calls[0]
    assert FakeOpenAI.init_kwargs == {
        "api_key": "secret", "base_url": "http://gateway.test/v1", "timeout": 120.0, "max_retries": 2,
    }


def test_missing_candidate_array_is_not_an_empty_result(monkeypatch):
    install_fake_openai(monkeypatch, ['{}', '{"still": "not candidates"}'])
    with pytest.raises(LLMResponseError):
        LLMClient(settings()).detect([segment(0, 20, "Text")])


def test_provider_failure_is_reported_as_request_failure(monkeypatch):
    install_fake_openai(monkeypatch, [RuntimeError("HTTP 503 provider unavailable")])
    with pytest.raises(LLMResponseError, match="request failed.*503"):
        LLMClient(settings()).detect([segment(0, 20, "Text")])


def test_malformed_json_gets_one_bounded_correction(monkeypatch):
    calls = install_fake_openai(monkeypatch, ["not json", detection()])
    detected = LLMClient(settings()).detect([segment(0, 20, "Text")])
    assert detected.candidates[0].title == "A useful idea"
    assert len(calls.calls) == 2
    assert "valid JSON" in calls.calls[1]["messages"][-1]["content"]


def test_out_of_bounds_and_rejected_candidates_are_not_returned(monkeypatch):
    out_of_bounds = json.dumps({"candidates": [{
        "start_segment": 0, "end_segment": 9, "title": "bad", "description": "bad", "reason": "bad",
    }]})
    calls = install_fake_openai(monkeypatch, [out_of_bounds])
    result = MomentAnalyzer(settings(), client=LLMClient(settings())).analyze(
        [{"segments": [segment(0, 20, "Only source")]}]
    )
    assert result == []
    assert len(calls.calls) == 1  # invalid range is discarded before validation

    calls = install_fake_openai(monkeypatch, [detection(), review(accepted=False)])
    result = MomentAnalyzer(settings(), client=LLMClient(settings())).analyze(
        [{"segments": [segment(0, 20, "Only source"), segment(20, 30, "Payoff")]}]
    )
    assert result == []


def test_deterministic_ranking_and_deduplication(monkeypatch):
    calls = install_fake_openai(monkeypatch, [detection("low", 0, 1), review(hook=5), detection("high", 0, 1), review(hook=9)])
    chunks = [
        {"segments": [segment(0, 10, "Setup"), segment(10, 30, "Payoff")]},
        {"segments": [segment(5, 15, "Setup"), segment(15, 35, "Payoff")]},
    ]
    result = MomentAnalyzer(settings(), client=LLMClient(settings())).analyze(chunks)
    assert [item.title for item in result] == ["high"]
    assert result[0].start == 5
    assert len(calls.calls) == 4


def test_source_segments_keep_project_indexes(monkeypatch):
    install_fake_openai(monkeypatch, [detection(), review()])
    chunks = [{"segments": [
        {**segment(10, 20, "Setup"), "segment_index": 12},
        {**segment(20, 40, "Payoff"), "segment_index": 13},
    ]}]
    result = MomentAnalyzer(settings(), client=LLMClient(settings())).analyze(chunks)
    assert result[0].source_segments == [12, 13]
