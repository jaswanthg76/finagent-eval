import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings
from app.research.hybrid import METRIC_TERMS

ToolExecutor = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
ALLOWED_METRICS = tuple(metric_name for metric_name, _ in METRIC_TERMS)


class AgentConfigurationError(RuntimeError):
    pass


class AgentGenerationError(RuntimeError):
    pass


class FilingSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=2_000)
    limit: int = Field(default=4, ge=1, le=4)


class MetricLookupArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_names: list[str] = Field(min_length=1, max_length=6)
    limit_per_metric: int = Field(default=3, ge=1, le=4)

    @field_validator("metric_names")
    @classmethod
    def known_metric_names(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(ALLOWED_METRICS))
        if unknown:
            raise ValueError(f"Unknown metric names: {', '.join(unknown)}")
        return list(dict.fromkeys(value))


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
            "name": "get_financial_metrics",
            "description": (
                "Retrieve exact normalized SEC XBRL facts for the selected company. Use this for "
                "amounts, periods, trends, and comparisons."
            ),
            "parameters": {
                **MetricLookupArguments.model_json_schema(),
                "properties": {
                    "metric_names": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 6,
                        "items": {"type": "string", "enum": list(ALLOWED_METRICS)},
                        "description": "Canonical normalized metric names to retrieve.",
                    },
                    "limit_per_metric": {
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
    if name == "get_financial_metrics":
        return MetricLookupArguments.model_validate(arguments).model_dump()
    raise ValueError(f"Unknown research tool: {name}")


def _system_prompt(
    ticker: str,
    as_of_date: str | None,
    form: str | None,
    required_metric_names: list[str],
) -> str:
    scope = [f"company={ticker}"]
    if as_of_date:
        scope.append(f"filed_on_or_before={as_of_date}")
    if form:
        scope.append(f"form={form}")
    required_metrics = ", ".join(required_metric_names) or "none"
    return f"""You are a careful SEC financial research agent.
Scope: {', '.join(scope)}. The application enforces this scope; never ask tools for another company.
Metrics explicitly requested by the question: {required_metrics}.

You must use at least one provided tool before answering. Use filing search for qualitative claims
and metric lookup for exact figures. You may use both. Base every factual claim only on returned
evidence. Cite filing evidence as [F1], [F2] and metric evidence as [M1], [M2], using the exact IDs
in tool results. Cite multiple items separately as [M1][M2], never as [M1, M2]. Never invent a
value, period, source, or citation. If evidence is insufficient, say
so plainly. Use deterministic_comparisons for arithmetic; do not recalculate them. Describe metric
periods by their exact start/end dates and do not infer fiscal-year labels. Treat a movement as a
driver only when narrative evidence explicitly says it drove or caused the movement. Keep the final
answer concise and decision-useful. Do not include a separate sources section because the
application renders sources independently.
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
        required_metric_names: list[str],
        execute_tool: ToolExecutor,
    ) -> tuple[str, list[dict[str, object]]]:
        executed_calls: list[dict[str, object]] = []
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _system_prompt(ticker, as_of_date, form, required_metric_names),
            },
            {"role": "user", "content": question},
        ]
        if required_metric_names:
            preload_arguments: dict[str, object] = {
                "metric_names": required_metric_names,
                "limit_per_metric": 3,
            }
            preloaded_metrics = await execute_tool(
                "get_financial_metrics",
                preload_arguments,
            )
            executed_calls.append(
                {"name": "get_financial_metrics", "arguments": preload_arguments}
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The application preloaded the required structured metric evidence below. "
                        "Use its evidence IDs in citations. Call search_filings if the question asks "
                        "for causes, risks, explanations, or other qualitative context.\n"
                        + json.dumps(preloaded_metrics, default=str)
                    ),
                }
            )
        available_tools = (
            [tool for tool in TOOL_SCHEMAS if tool["function"]["name"] == "search_filings"]
            if required_metric_names
            else TOOL_SCHEMAS
        )

        try:
            for round_index in range(4):
                response = await self._client.chat.completions.create(
                    model=settings.ai_model,
                    messages=messages,
                    tools=available_tools,
                    tool_choice=(
                        "auto" if round_index > 0 or required_metric_names else "required"
                    ),
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
