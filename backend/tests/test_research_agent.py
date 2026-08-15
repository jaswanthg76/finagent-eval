import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.research.agent import GroqResearchAgent, validate_tool_arguments


class FakeMessage:
    def __init__(self, *, content: str | None = None, tool_calls: list[object] | None = None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, *, exclude_none: bool = False) -> dict[str, object]:
        message: dict[str, object] = {"role": "assistant"}
        if self.content is not None or not exclude_none:
            message["content"] = self.content
        if self.tool_calls is not None or not exclude_none:
            message["tool_calls"] = self.tool_calls
        return message


def completion(message: FakeMessage) -> object:
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_research_tool_arguments_are_strictly_validated() -> None:
    assert validate_tool_arguments("search_filings", {"query": "revenue drivers"}) == {
        "query": "revenue drivers",
        "limit": 4,
    }
    with pytest.raises(ValidationError):
        validate_tool_arguments("search_filings", {"query": "risk", "ticker": "AMD"})
    with pytest.raises(ValidationError):
        validate_tool_arguments("get_financial_metrics", {"metric_names": ["Made Up Metric"]})


async def test_groq_agent_executes_validated_tool_then_returns_answer() -> None:
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="get_financial_metrics",
            arguments=json.dumps({"metric_names": ["Revenue"], "limit_per_metric": 2}),
        ),
    )
    create = AsyncMock(
        side_effect=[
            completion(FakeMessage(tool_calls=[tool_call])),
            completion(FakeMessage(content="Revenue increased [M1].", tool_calls=[])),
        ]
    )
    client = Mock()
    client.chat.completions.create = create
    execute_tool = AsyncMock(return_value={"metric_evidence": [{"evidence_id": "M1"}]})

    answer, calls = await GroqResearchAgent(client=client).answer(
        ticker="NVDA",
        question="How did revenue change?",
        as_of_date=None,
        form="10-Q",
        required_metric_names=[],
        execute_tool=execute_tool,
    )

    assert answer == "Revenue increased [M1]."
    assert calls == [
        {
            "name": "get_financial_metrics",
            "arguments": {"metric_names": ["Revenue"], "limit_per_metric": 2},
        }
    ]
    execute_tool.assert_awaited_once_with(
        "get_financial_metrics",
        {"metric_names": ["Revenue"], "limit_per_metric": 2},
    )
    assert create.await_args_list[0].kwargs["tool_choice"] == "required"
    assert create.await_args_list[1].kwargs["tool_choice"] == "auto"


async def test_groq_agent_preloads_explicit_metrics_before_model_call() -> None:
    create = AsyncMock(
        return_value=completion(FakeMessage(content="Revenue was $10 [M1].", tool_calls=[]))
    )
    client = Mock()
    client.chat.completions.create = create
    execute_tool = AsyncMock(return_value={"metric_evidence": [{"evidence_id": "M1"}]})

    answer, calls = await GroqResearchAgent(client=client).answer(
        ticker="NVDA",
        question="What was revenue?",
        as_of_date=None,
        form=None,
        required_metric_names=["Revenue"],
        execute_tool=execute_tool,
    )

    assert answer == "Revenue was $10 [M1]."
    assert calls[0]["name"] == "get_financial_metrics"
    execute_tool.assert_awaited_once_with(
        "get_financial_metrics",
        {"metric_names": ["Revenue"], "limit_per_metric": 2},
    )
    assert create.await_args.kwargs["tool_choice"] == "auto"
