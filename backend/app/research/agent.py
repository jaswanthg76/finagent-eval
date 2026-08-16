import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings

ToolExecutor = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class AgentConfigurationError(RuntimeError):
    pass


class AgentGenerationError(RuntimeError):
    pass


class FilingSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=2_000)
    limit: int = Field(default=4, ge=1, le=4)


class FinancialEvidenceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concepts: list[str] = Field(min_length=1, max_length=6)
    limit_per_concept: int = Field(default=3, ge=1, le=4)

    @field_validator("concepts")
    @classmethod
    def normalized_concepts(cls, value: list[str]) -> list[str]:
        concepts = [concept.strip() for concept in value]
        if any(not concept for concept in concepts):
            raise ValueError("Financial concepts cannot be empty")
        if any(len(concept) > 200 for concept in concepts):
            raise ValueError("Financial concepts cannot exceed 200 characters")
        return list(dict.fromkeys(concepts))


TOOL_SCHEMAS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "search_filings",
            "description": (
                "Semantically search the selected company's SEC filing passages. Use this for "
                "drivers, risks, management explanations, strategy, and other narrative evidence."
            ),
            "parameters": FilingSearchArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_financial_evidence",
            "description": (
                "Retrieve evidence for financial concepts using natural financial terminology. "
                "The application selects normalized XBRL facts or SEC filing content. Use this for "
                "amounts, balances, operating metrics, segment or product revenue, trends, and "
                "period comparisons."
            ),
            "parameters": {
                **FinancialEvidenceArguments.model_json_schema(),
                "properties": {
                    "concepts": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 6,
                        "items": {"type": "string", "minLength": 1, "maxLength": 200},
                        "description": (
                            "Financial concepts needed to answer the question, such as revenue, "
                            "accounts receivable, operating cash flow, or Data Center revenue."
                        ),
                    },
                    "limit_per_concept": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 4,
                        "default": 3,
                    },
                },
            },
        },
    },
]


def validate_tool_arguments(name: str, arguments: dict[str, object]) -> dict[str, object]:
    if name == "search_filings":
        return FilingSearchArguments.model_validate(arguments).model_dump()
    if name == "get_financial_evidence":
        return FinancialEvidenceArguments.model_validate(arguments).model_dump()
    raise ValueError(f"Unknown research tool: {name}")


def _system_prompt(
    ticker: str,
    as_of_date: str | None,
    form: str | None,
) -> str:
    scope = [f"company={ticker}"]
    if as_of_date:
        scope.append(f"filed_on_or_before={as_of_date}")
    if form:
        scope.append(f"form={form}")
    return f"""You are a careful SEC financial research agent.
Scope: {', '.join(scope)}. The application enforces this scope; never ask tools for another company.

Determine what evidence is necessary to answer the question. Use get_financial_evidence for financial
concepts, amounts, balances, operating metrics, segment or product revenue, period comparisons,
growth rates, and other quantitative information. Request concepts using normal financial
terminology; you do not need to know the application's internal XBRL mappings. The application
determines whether evidence comes from normalized XBRL facts or SEC filing content. Use
search_filings directly for narrative evidence such as management explanations, risks, causes,
strategy, outlook, customer relationships, or qualitative disclosures. A question may require
multiple calls to both tools. Do not infer a financial value from narrative text when structured
metric evidence is available. Do not perform arithmetic yourself when deterministic_comparisons are
returned by a tool.
When explaining a metric comparison, use narrative evidence from the filing that contains the newest
metric evidence and verify that the narrative discusses the same period before calling it a driver.
Tool results use a shared filing-evidence budget. Make targeted requests, and if a result says the
budget is exhausted, answer from the returned evidence or state that the evidence is insufficient.

You must use at least one provided tool before answering. Base every factual claim only on returned
evidence. Cite filing evidence as [F1], [F2] and metric evidence as [M1], [M2], using the exact IDs in
tool results. Cite multiple items separately as [M1][M2], never as [M1, M2]. Never invent a value,
period, source, or citation. If evidence is insufficient, say so plainly. Describe metric periods by
their exact start/end dates and do not infer fiscal-year labels. Treat a movement as a driver only
when narrative evidence explicitly says it drove or caused the movement. Cite every factual
conclusion, including overall risk conclusions. Attribute a risk or view to management only when the
cited passage explicitly makes that statement; otherwise describe only what the passage says. Keep
For every number taken from filing evidence, cite the exact passage containing that number, not a
different passage about the same topic. Keep the final answer concise and decision-useful. Do not
include a separate sources section because the application renders sources independently.
"""


class GroqResearchAgent:
    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        if client is None:
            if settings.groq_api_key is None:
                raise AgentConfigurationError(
                    "GROQ_API_KEY is not configured. Add it to backend/.env and restart the API."
                )
            client = AsyncOpenAI(
                api_key=settings.groq_api_key.get_secret_value(),
                base_url="https://api.groq.com/openai/v1",
                timeout=httpx.Timeout(90.0),
            )
        self._client = client

    async def answer(
        self,
        *,
        ticker: str,
        question: str,
        as_of_date: str | None,
        form: str | None,
        execute_tool: ToolExecutor,
    ) -> tuple[str, list[dict[str, object]]]:
        executed_calls: list[dict[str, object]] = []
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _system_prompt(ticker, as_of_date, form),
            },
            {"role": "user", "content": question},
        ]

        try:
            for round_index in range(4):
                response = await self._client.chat.completions.create(
                    model=settings.ai_model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="required" if round_index == 0 else "auto",
                    temperature=0,
                    max_completion_tokens=700,
                )
                message = response.choices[0].message
                messages.append(message.model_dump(exclude_none=True))
                tool_calls = message.tool_calls or []
                if not tool_calls:
                    answer = (message.content or "").strip()
                    if not answer:
                        raise AgentGenerationError("Groq returned an empty research answer")
                    return answer, executed_calls

                for tool_call in tool_calls:
                    name = tool_call.function.name
                    try:
                        raw_arguments = json.loads(tool_call.function.arguments or "{}")
                        arguments = validate_tool_arguments(name, raw_arguments)
                        result = await execute_tool(name, arguments)
                        executed_calls.append({"name": name, "arguments": arguments})
                    except (json.JSONDecodeError, ValidationError, ValueError) as error:
                        result = {"error": f"Invalid tool call: {error}"}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result, default=str),
                        }
                    )

            final_response = await self._client.chat.completions.create(
                model=settings.ai_model,
                messages=messages,
                tool_choice="none",
                temperature=0,
                max_completion_tokens=700,
            )
        except RateLimitError as error:
            raise AgentGenerationError("Groq free-tier rate limit exceeded; try again later") from error
        except APIConnectionError as error:
            raise AgentGenerationError("Could not reach the Groq API") from error
        except APIStatusError as error:
            if error.status_code == 413:
                raise AgentGenerationError(
                    "Groq rejected the evidence batch as too large for the current free-plan limit"
                ) from error
            raise AgentGenerationError(f"Groq returned HTTP {error.status_code}") from error

        answer = (final_response.choices[0].message.content or "").strip()
        if not answer:
            raise AgentGenerationError("Groq returned an empty research answer")
        return answer, executed_calls
