import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.ai import AIClientConfigurationError, create_ai_client
from app.core.config import settings

ToolExecutor = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
_CITATION_PATTERN = re.compile(r"\[([FM]\d+)\]", re.IGNORECASE)
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_PATTERN = re.compile(
    r";\s+|,\s+(?=(?:as evidenced|with|while|whereas|because|driven|caused|and|but|which)\b)",
    re.IGNORECASE,
)
_COMPARISON_PATTERN = re.compile(
    r"\b(compared(?:\s+to|\s+with)?|versus|vs\.?|year[- ]over[- ]year|sequential(?:ly)?|"
    r"increase[ds]?|decrease[ds]?|grew|growth|declined?|fell|rose|change[ds]?)\b",
    re.IGNORECASE,
)
_INCREASE_PATTERN = re.compile(r"\b(increase[ds]?|grew|growth|rose|higher)\b", re.IGNORECASE)
_DECREASE_PATTERN = re.compile(r"\b(decrease[ds]?|declined?|fell|lower)\b", re.IGNORECASE)
_CAUSAL_PATTERN = re.compile(
    r"\b(because|caused|driven by|due to|attributable to|resulted from)\b",
    re.IGNORECASE,
)
_INSUFFICIENT_PATTERN = re.compile(
    r"\b(insufficient evidence|evidence (?:is|was) insufficient|cannot determine|"
    r"could not determine|not enough evidence)\b",
    re.IGNORECASE,
)
_MAX_REPAIR_ATTEMPTS = 2


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


def validate_research_answer(answer: str, available_evidence_ids: set[str]) -> list[str]:
    violations: list[str] = []
    sentences = [sentence.strip() for sentence in _SENTENCE_PATTERN.split(answer) if sentence.strip()]
    for index, sentence in enumerate(sentences, start=1):
        citation_ids = [match.upper() for match in _CITATION_PATTERN.findall(sentence)]
        unknown_ids = sorted(set(citation_ids) - available_evidence_ids)
        if unknown_ids:
            violations.append(
                f"Sentence {index} cites unavailable evidence: {', '.join(unknown_ids)}."
            )
        clauses = [clause.strip() for clause in _CLAUSE_PATTERN.split(sentence) if clause.strip()]
        for clause_index, clause in enumerate(clauses, start=1):
            if not _CITATION_PATTERN.search(clause) and not _INSUFFICIENT_PATTERN.search(clause):
                violations.append(
                    f"Sentence {index}, clause {clause_index} makes an uncited factual claim."
                )
        metric_ids = {item for item in citation_ids if item.startswith("M")}
        if metric_ids and _COMPARISON_PATTERN.search(sentence) and len(metric_ids) < 2:
            violations.append(
                f"Sentence {index} makes a metric comparison without two metric citations."
            )
        if (
            citation_ids
            and all(item.startswith("M") for item in citation_ids)
            and _CAUSAL_PATTERN.search(sentence)
        ):
            violations.append(
                f"Sentence {index} makes a causal claim using only metric evidence."
            )
        if _INCREASE_PATTERN.search(sentence) and _DECREASE_PATTERN.search(sentence):
            violations.append(f"Sentence {index} contains conflicting direction language.")
    return violations


def _collect_evidence_ids(value: object) -> set[str]:
    evidence_ids: set[str] = set()
    if isinstance(value, dict):
        evidence_id = value.get("evidence_id")
        if isinstance(evidence_id, str) and re.fullmatch(
            r"[FM]\d+", evidence_id, re.IGNORECASE
        ):
            evidence_ids.add(evidence_id.upper())
        for nested_value in value.values():
            evidence_ids.update(_collect_evidence_ids(nested_value))
    elif isinstance(value, list):
        for nested_value in value:
            evidence_ids.update(_collect_evidence_ids(nested_value))
    return evidence_ids


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
            try:
                client = create_ai_client()
            except AIClientConfigurationError as error:
                raise AgentConfigurationError(str(error)) from error
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
        available_evidence_ids: set[str] = set()
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
                        raise AgentGenerationError("The AI provider returned an empty research answer")
                    return await self._repair_answer(
                        answer=answer,
                        messages=messages,
                        available_evidence_ids=available_evidence_ids,
                    ), executed_calls

                for tool_call in tool_calls:
                    name = tool_call.function.name
                    try:
                        raw_arguments = json.loads(tool_call.function.arguments or "{}")
                        arguments = validate_tool_arguments(name, raw_arguments)
                        result = await execute_tool(name, arguments)
                        executed_calls.append({"name": name, "arguments": arguments})
                        available_evidence_ids.update(_collect_evidence_ids(result))
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
            raise AgentGenerationError(
                f"{settings.ai_provider.title()} rate limit exceeded; try again later"
            ) from error
        except APIConnectionError as error:
            raise AgentGenerationError(f"Could not reach the {settings.ai_provider} API") from error
        except APIStatusError as error:
            if error.status_code == 413:
                raise AgentGenerationError(
                    f"{settings.ai_provider.title()} rejected the evidence batch as too large"
                ) from error
            raise AgentGenerationError(
                f"{settings.ai_provider.title()} returned HTTP {error.status_code}"
            ) from error

        answer = (final_response.choices[0].message.content or "").strip()
        if not answer:
            raise AgentGenerationError("The AI provider returned an empty research answer")
        return await self._repair_answer(
            answer=answer,
            messages=[
                *messages,
                final_response.choices[0].message.model_dump(exclude_none=True),
            ],
            available_evidence_ids=available_evidence_ids,
        ), executed_calls

    async def _repair_answer(
        self,
        *,
        answer: str,
        messages: list[dict[str, Any]],
        available_evidence_ids: set[str],
    ) -> str:
        violations = validate_research_answer(answer, available_evidence_ids)
        if not violations:
            return answer

        repair_instruction = """Revise the research answer to fix every validation failure below.
Use only evidence already returned by the tools. Do not call tools, invent evidence, or add a sources
section. Remove any claim that cannot be supported. Preserve exact citation syntax and ensure every
factual sentence has citations. For metric comparisons, cite both metric periods. For causes or
management views, include filing evidence. Put citations directly after each factual clause instead
of relying on a citation at the end of a compound sentence. Resolve contradictory direction wording.

Validation failures:
"""
        repair_messages = list(messages)
        for _ in range(_MAX_REPAIR_ATTEMPTS):
            repair_prompt = repair_instruction + "\n".join(
                f"- {violation}" for violation in violations
            )
            response = await self._client.chat.completions.create(
                model=settings.ai_model,
                messages=[*repair_messages, {"role": "user", "content": repair_prompt}],
                tool_choice="none",
                temperature=0,
                max_completion_tokens=700,
            )
            repaired_answer = (response.choices[0].message.content or "").strip()
            violations = validate_research_answer(repaired_answer, available_evidence_ids)
            if repaired_answer and not violations:
                return repaired_answer
            repair_messages.extend(
                [
                    {"role": "user", "content": repair_prompt},
                    {"role": "assistant", "content": repaired_answer},
                ]
            )

        details = "; ".join(violations or ["The AI provider returned an empty repair"])
        raise AgentGenerationError(
            f"Research answer failed pre-save evidence validation after repair: {details}"
        )
