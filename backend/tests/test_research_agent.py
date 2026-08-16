import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.research.agent import (
    AgentGenerationError,
    GroqResearchAgent,
    validate_research_answer,
    validate_tool_arguments,
)


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
    assert validate_tool_arguments(
        "get_financial_evidence",
        {"concepts": ["Data Center revenue", "accounts receivable"]},
    ) == {
        "concepts": ["Data Center revenue", "accounts receivable"],
        "limit_per_concept": 3,
    }
    with pytest.raises(ValidationError):
        validate_tool_arguments("get_financial_evidence", {"concepts": ["  "]})


async def test_groq_agent_executes_validated_tool_then_returns_answer() -> None:
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="get_financial_evidence",
            arguments=json.dumps({"concepts": ["Revenue"], "limit_per_concept": 2}),
        ),
    )
    create = AsyncMock(
        side_effect=[
            completion(FakeMessage(tool_calls=[tool_call])),
            completion(FakeMessage(content="Revenue increased [M1][M2].", tool_calls=[])),
        ]
    )
    client = Mock()
    client.chat.completions.create = create
    execute_tool = AsyncMock(
        return_value={
            "structured_evidence": [{"evidence_id": "M1"}, {"evidence_id": "M2"}]
        }
    )

    answer, calls = await GroqResearchAgent(client=client).answer(
        ticker="NVDA",
        question="How did revenue change?",
        as_of_date=None,
        form="10-Q",
        execute_tool=execute_tool,
    )

    assert answer == "Revenue increased [M1][M2]."
    assert calls == [
        {
            "name": "get_financial_evidence",
            "arguments": {"concepts": ["Revenue"], "limit_per_concept": 2},
        }
    ]
    execute_tool.assert_awaited_once_with(
        "get_financial_evidence",
        {"concepts": ["Revenue"], "limit_per_concept": 2},
    )
    assert create.await_args_list[0].kwargs["tool_choice"] == "required"
    assert create.await_args_list[1].kwargs["tool_choice"] == "auto"


def test_answer_validation_rejects_known_reliability_failures() -> None:
    answer = (
        "Customer concentration risk is increasing. "
        "Cash flow increased because revenue grew [M1]. "
        "Receivables increased with a 4% decrease [M2][M3]."
    )

    violations = validate_research_answer(answer, {"M1", "M2", "M3"})

    assert "Sentence 1, clause 1 makes an uncited factual claim." in violations
    assert "Sentence 2 makes a metric comparison without two metric citations." in violations
    assert "Sentence 2 makes a causal claim using only metric evidence." in violations
    assert "Sentence 3 contains conflicting direction language." in violations


async def test_groq_agent_repairs_invalid_answer_before_returning() -> None:
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="get_financial_evidence",
            arguments=json.dumps({"concepts": ["Revenue"], "limit_per_concept": 2}),
        ),
    )
    create = AsyncMock(
        side_effect=[
            completion(FakeMessage(tool_calls=[tool_call])),
            completion(FakeMessage(content="Revenue increased [M1].", tool_calls=[])),
            completion(FakeMessage(content="Revenue increased [M1][M2].")),
        ]
    )
    client = Mock()
    client.chat.completions.create = create
    execute_tool = AsyncMock(
        return_value={
            "structured_evidence": [{"evidence_id": "M1"}, {"evidence_id": "M2"}]
        }
    )

    answer, _ = await GroqResearchAgent(client=client).answer(
        ticker="NVDA",
        question="How did revenue change?",
        as_of_date=None,
        form=None,
        execute_tool=execute_tool,
    )

    assert answer == "Revenue increased [M1][M2]."
    assert create.await_args_list[2].kwargs["tool_choice"] == "none"


def test_answer_validation_requires_citations_on_each_factual_clause() -> None:
    violations = validate_research_answer(
        "Risk is increasing, as evidenced by customer A representing 16% [F1].",
        {"F1"},
    )

    assert violations == ["Sentence 1, clause 1 makes an uncited factual claim."]


async def test_groq_agent_rejects_answer_when_repair_is_still_invalid() -> None:
    agent = GroqResearchAgent(client=Mock())
    agent._client.chat.completions.create = AsyncMock(
        side_effect=[
            completion(FakeMessage(content="Risk is increasing.")),
            completion(FakeMessage(content="Risk is increasing.")),
        ]
    )

    with pytest.raises(AgentGenerationError, match="failed pre-save evidence validation"):
        await agent._repair_answer(
            answer="Risk is increasing.",
            messages=[],
            available_evidence_ids={"F1"},
        )


async def test_groq_agent_keeps_both_tools_available_after_first_call() -> None:
    metric_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="get_financial_evidence",
            arguments=json.dumps({"concepts": ["Revenue"], "limit_per_concept": 2}),
        ),
    )
    filing_call = SimpleNamespace(
        id="call_2",
        function=SimpleNamespace(
            name="search_filings",
            arguments=json.dumps({"query": "revenue drivers", "limit": 3}),
        ),
    )
    create = AsyncMock(
        side_effect=[
            completion(FakeMessage(tool_calls=[metric_call])),
            completion(FakeMessage(tool_calls=[filing_call])),
            completion(
                FakeMessage(
                    content="Revenue changed because of demand [M1][M2][F1].",
                    tool_calls=[],
                )
            ),
        ]
    )
    client = Mock()
    client.chat.completions.create = create
    execute_tool = AsyncMock(
        side_effect=[
            {
                "structured_evidence": [
                    {"evidence_id": "M1"},
                    {"evidence_id": "M2"},
                ]
            },
            {"filing_evidence": [{"evidence_id": "F1"}]},
        ]
    )

    answer, calls = await GroqResearchAgent(client=client).answer(
        ticker="NVDA",
        question="How did revenue change, and why?",
        as_of_date=None,
        form=None,
        execute_tool=execute_tool,
    )

    assert answer == "Revenue changed because of demand [M1][M2][F1]."
    assert [call["name"] for call in calls] == [
        "get_financial_evidence",
        "search_filings",
    ]
    for request in create.await_args_list[:2]:
        assert {tool["function"]["name"] for tool in request.kwargs["tools"]} == {
            "get_financial_evidence",
            "search_filings",
        }
    assert create.await_args_list[0].kwargs["tool_choice"] == "required"
    assert create.await_args_list[1].kwargs["tool_choice"] == "auto"
