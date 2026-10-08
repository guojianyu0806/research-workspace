"""Opt-in model services with retrieved, version-bound source evidence.

This module owns no job queue and performs no request until an async model method
is called with a configured provider. Transport tests are not model quality tests.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import inspect
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .library import Library, LibraryError


PROMPT_VERSION = "research-evidence-v1"
PROMPT_DIRECTORY = Path(__file__).with_name("prompts")
MAX_DOCUMENTS = 20
MAX_EVIDENCE = 24
MAX_EVIDENCE_CHARS = 5000
MAX_BLOCK_CHARS = 16000
T = TypeVar("T", bound=BaseModel)


class ModelServiceError(RuntimeError):
    code = "model_error"
    retryable = False


class ModelNotConfigured(ModelServiceError):
    code = "model_not_configured"

    def __init__(self):
        super().__init__("尚未配置大模型服务；请配置模型地址、模型名称和API密钥后重试。未生成模拟回答。")


class ModelProviderError(ModelServiceError):
    code = "model_provider_error"

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class ModelOutputError(ModelServiceError):
    code = "model_output_invalid"


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = field(default="", repr=False)
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 45.0
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(base_url=os.getenv("RESEARCH_MODEL_BASE_URL", "").strip(),
                   model=os.getenv("RESEARCH_MODEL", "").strip(),
                   api_key=os.getenv("RESEARCH_MODEL_API_KEY", "").strip())

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip() and self.api_key.strip())

    def endpoint(self) -> str:
        if not self.configured:
            raise ModelNotConfigured()
        try:
            parsed = urlsplit(self.base_url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            raise ModelProviderError("模型地址无效，请检查配置。") from None
        if (not hostname or parsed.scheme not in {"http", "https"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme == "http" and hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ModelProviderError("模型地址须使用HTTPS；仅本机地址允许HTTP，且地址不能包含凭据或查询参数。")
        if port is not None and not 1 <= port <= 65535:
            raise ModelProviderError("模型服务端口无效。")
        if not 0 < self.timeout_seconds <= 300 or not 1 <= self.max_attempts <= 3:
            raise ModelProviderError("模型超时或重试配置超出允许范围。")
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=3000)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=6000)
    citations: list[Citation] = Field(min_length=1, max_length=12)


class AnswerPayload(StrictModel):
    status: Literal["answered", "insufficient_evidence"]
    claims: list[Claim] = Field(default_factory=list, max_length=30)
    message: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "answered" and not self.claims:
            raise ValueError("An answer needs cited claims")
        if self.status == "insufficient_evidence" and self.claims:
            raise ValueError("A refusal cannot contain asserted claims")
        return self


class TranslationPayload(StrictModel):
    translated_text: str = Field(min_length=1, max_length=64000)


class Section(StrictModel):
    heading: str = Field(min_length=1, max_length=200)
    claims: list[Claim] = Field(default_factory=list, max_length=15)
    # This field is explicitly hypothetical; it must not be rendered as evidence.
    proposed_work: str = Field(default="", max_length=5000)


class SynthesisPayload(StrictModel):
    status: Literal["generated", "insufficient_evidence"]
    sections: list[Section] = Field(default_factory=list, max_length=12)
    message: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "generated" and not self.sections:
            raise ValueError("A synthesis needs sections")
        if self.status == "insufficient_evidence" and self.sections:
            raise ValueError("A refusal cannot contain generated sections")
        return self


class Evidence(StrictModel):
    evidence_id: str
    document_id: str
    document_title: str
    filename: str
    run_id: str
    block_id: str
    page: int
    bbox: list[float] = Field(min_length=4, max_length=4)
    text: str
    score: float = 0.0
    truncated: bool = False


class ChatCompletionProvider:
    """A small chat/completions adapter; errors never echo credentials or bodies."""

    def __init__(self, config: ModelConfig | None = None, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.config = config if config is not None else ModelConfig.from_env()
        self._transport = transport
        self._sleep = sleep

    @property
    def configured(self) -> bool:
        return self.config.configured

    async def generate(self, prompt: str, payload: dict, output_model: type[T]) -> T:
        endpoint = self.config.endpoint()
        messages = [
            {"role": "system", "content": prompt + "\nReturn only a JSON object matching this schema:\n"
             + json.dumps(output_model.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        async with httpx.AsyncClient(transport=self._transport,
                                    timeout=httpx.Timeout(self.config.timeout_seconds),
                                    follow_redirects=False, trust_env=False) as client:
            for attempt in range(self.config.max_attempts):
                try:
                    response = await client.post(
                        endpoint, headers={"Authorization": f"Bearer {self.config.api_key}"},
                        json={"model": self.config.model, "messages": messages,
                              "temperature": 0, "response_format": {"type": "json_object"}},
                    )
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt + 1 == self.config.max_attempts:
                        raise ModelProviderError("模型请求超时或连接失败；未生成回答。", retryable=True) from None
                    await self._sleep(min(0.25 * (2 ** attempt), 2.0))
                    continue
                except httpx.HTTPError:
                    raise ModelProviderError("模型HTTP请求失败；请检查服务配置。") from None
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 == self.config.max_attempts:
                        raise ModelProviderError("模型服务限流或暂时不可用；请稍后重试。", retryable=True)
                    try:
                        delay = min(max(float(response.headers.get("Retry-After", "0.5")), 0), 3.0)
                        if not math.isfinite(delay):
                            delay = 0.5
                    except ValueError:
                        delay = 0.5
                    await self._sleep(delay)
                    continue
                if response.status_code in {401, 403}:
                    raise ModelProviderError("模型认证失败，请检查密钥与服务访问权限。")
                if response.status_code < 200 or response.status_code >= 300:
                    raise ModelProviderError(f"模型服务返回HTTP {response.status_code}；请检查模型和兼容接口配置。")
                try:
                    if len(response.content) > 2 * 1024 * 1024:
                        raise ValueError("Response too large")
                    envelope = response.json()
                    choice = envelope["choices"][0]
                    if choice.get("finish_reason") not in {None, "stop"}:
                        raise ValueError("Incomplete response")
                    content = choice["message"]["content"]
                    if not isinstance(content, str):
                        raise ValueError("Expected text content")
                    return output_model.model_validate_json(content)
                except (ValueError, TypeError, KeyError, IndexError, ValidationError):
                    if attempt + 1 == self.config.max_attempts:
                        raise ModelOutputError("模型输出未通过结构校验；未将其作为有效结果展示。") from None
                    # Do not retain a malformed output or copy its contents into logs.
                    messages = messages[:2] + [{"role": "user", "content":
                        "The previous result was incomplete or invalid JSON/schema. Return a complete JSON object matching the schema exactly."}]
        raise ModelOutputError("未获得有效模型结果。")


def _prompt(name: str) -> str:
    return (PROMPT_DIRECTORY / f"{name}.txt").read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


_STOP_WORDS = frozenset("the a an of to in on for and or is are was were what which how does do this that from with paper papers please compare 比较 对比 请问 什么 如何 论文 研究 的 了 是 和 与 在 对 请".split())


def tokenize(text: str) -> list[str]:
    """English/numeric words plus Chinese unigrams and bigrams; no remote model."""
    tokens = []
    for token in re.findall(r"[a-z0-9_]+(?:[.\-][a-z0-9_]+)*|[\u3400-\u9fff]+|[α-ω]+", text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            tokens.extend(character for character in token if character not in _STOP_WORDS)
            tokens.extend(token[index:index + 2] for index in range(len(token) - 1)
                          if token[index:index + 2] not in _STOP_WORDS)
        elif token not in _STOP_WORDS:
            tokens.append(token)
    return tokens


class IntelligenceService:
    def __init__(self, library: Library, provider: ChatCompletionProvider | None = None):
        self.library = library
        self.provider = provider if provider is not None else ChatCompletionProvider()

    def capabilities(self) -> dict:
        return {"configured": self.provider.configured, "prompt_version": PROMPT_VERSION,
                "retrieval": "bm25_lexical", "max_documents": MAX_DOCUMENTS,
                "external_freshness_verification": False}

    def _require_model(self) -> None:
        if not self.provider.configured:
            raise ModelNotConfigured()

    @staticmethod
    def _validate_documents(document_ids: list[str]) -> list[str]:
        if not isinstance(document_ids, list) or not document_ids:
            raise LibraryError("请至少选择一篇文献。")
        if any(not isinstance(value, str) or not value for value in document_ids):
            raise LibraryError("文献编号无效。")
        unique = list(dict.fromkeys(document_ids))
        if len(unique) > MAX_DOCUMENTS:
            raise LibraryError(f"单次最多选择{MAX_DOCUMENTS}篇文献。")
        return unique

    def _blocks(self, document_ids: list[str]) -> list[Evidence]:
        document_ids = self._validate_documents(document_ids)
        results = []
        with self.library._connection() as db:
            for document_id in document_ids:
                document = self.library._document(db, document_id)
                run = document["available_run"]
                if not run:
                    continue
                rows = db.execute(
                    """SELECT b.*,p.status FROM blocks b JOIN pages p
                       ON b.run_id=p.run_id AND b.page=p.page
                       WHERE b.run_id=? AND p.status='parsed' ORDER BY b.page,b.ordinal""", (run["id"],)
                )
                for row in rows:
                    results.append(Evidence(evidence_id=f"E_{row['id']}", document_id=document_id,
                        document_title=document["title"], filename=document["filename"],
                        run_id=run["id"], block_id=row["id"], page=row["page"],
                        bbox=json.loads(row["bbox"]), text=row["text"]))
        return results

    def retrieve(self, question: str, document_ids: list[str], *, limit: int = 12) -> list[Evidence]:
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise LibraryError("问题不能为空且不得超过4000个字符。")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVIDENCE:
            raise LibraryError(f"证据条数须介于1至{MAX_EVIDENCE}之间。")
        document_ids = self._validate_documents(document_ids)
        blocks = self._blocks(document_ids)
        terms = set(tokenize(question))
        if not terms or not blocks:
            return []
        counts = [Counter(tokenize(block.text)) for block in blocks]
        lengths = [sum(count.values()) for count in counts]
        average = max(sum(lengths) / len(lengths), 1)
        document_frequency = Counter(term for count in counts for term in terms if count[term])
        candidates = []
        for block, count, length in zip(blocks, counts, lengths):
            score = 0.0
            for term in terms:
                frequency = count[term]
                if not frequency:
                    continue
                inverse = math.log(1 + (len(blocks) - document_frequency[term] + 0.5)
                                   / (document_frequency[term] + 0.5))
                score += inverse * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average))
            if score > 0:
                candidates.append(block.model_copy(update={"score": round(score, 6)}))
        candidates.sort(key=lambda block: (-block.score, block.document_id, block.page, block.block_id))
        selected: list[Evidence] = []
        # Give each selected document a matching block before allocating extras.
        for document_id in document_ids:
            candidate = next((block for block in candidates if block.document_id == document_id), None)
            if candidate and len(selected) < limit:
                selected.append(candidate)
        selected_ids = {block.block_id for block in selected}
        for block in candidates:
            if len(selected) >= limit:
                break
            if block.block_id not in selected_ids:
                selected.append(block)
                selected_ids.add(block.block_id)
        return [block.model_copy(update={"text": block.text[:MAX_EVIDENCE_CHARS],
                     "truncated": len(block.text) > MAX_EVIDENCE_CHARS}) for block in selected]

    @staticmethod
    def _validate_claims(claims: list[Claim], evidence: list[Evidence]) -> set[str]:
        allowed = {block.evidence_id: block for block in evidence}
        used = set()
        for claim in claims:
            for citation in claim.citations:
                source = allowed.get(citation.evidence_id)
                if source is None:
                    raise ModelOutputError("模型引用了本次检索范围外的来源，结果已拒绝。")
                if not citation.quote.strip() or _normalized(citation.quote) not in _normalized(source.text):
                    raise ModelOutputError("模型引文与检索原文不一致，结果已拒绝。")
                used.add(citation.evidence_id)
        return used

    @staticmethod
    def _trace(step: str, detail: str) -> dict:
        return {"step": step, "detail": detail}

    async def answer(self, question: str, document_ids: list[str], *,
                     mode: Literal["qa", "compare"] = "qa") -> dict:
        self._require_model()
        if mode not in {"qa", "compare"}:
            raise LibraryError("问答模式无效。")
        document_ids = self._validate_documents(document_ids)
        if mode == "compare" and len(document_ids) < 2:
            raise LibraryError("跨文献比较需要至少两篇文献。")
        evidence = self.retrieve(question, document_ids, limit=max(12, len(document_ids)))
        represented = {block.document_id for block in evidence}
        missing = [value for value in document_ids if value not in represented]
        trace = [self._trace("retrieve", f"从{len(document_ids)}篇选定文献中按BM25检索到{len(evidence)}个文本块。")]
        if not evidence or (mode == "compare" and missing):
            return {"status": "insufficient_evidence", "answer": "现有可用文献中未找到足够证据，无法可靠回答。",
                    "claims": [], "evidence": [block.model_dump() for block in evidence],
                    "sources": [], "missing_document_ids": missing, "prompt_version": PROMPT_VERSION,
                    "trace": trace + [self._trace("decline", "没有足够的匹配原文；未调用模型编造回答。")],
                    "verification": "no_matching_evidence"}
        result = await self.provider.generate(_prompt("qa_v1"), {
            "task": mode, "question": question, "selected_document_ids": document_ids,
            "evidence": [block.model_dump() for block in evidence],
        }, AnswerPayload)
        used = self._validate_claims(result.claims, evidence)
        if mode == "compare" and result.status == "answered":
            cited_documents = {block.document_id for block in evidence if block.evidence_id in used}
            if cited_documents != set(document_ids):
                raise ModelOutputError("跨文献回答未提供所有选定文献的原文依据，结果已拒绝。")
        answer = "\n\n".join(claim.text + " " + " ".join(
            f"[{citation.evidence_id}]" for citation in claim.citations) for claim in result.claims)
        return {"status": result.status, "answer": answer or result.message or "证据不足，无法可靠回答。",
                "message": result.message, "claims": [claim.model_dump() for claim in result.claims],
                "evidence": [block.model_dump() for block in evidence],
                "sources": [block.model_dump() for block in evidence if block.evidence_id in used],
                "missing_document_ids": missing, "prompt_version": PROMPT_VERSION,
                "trace": trace + [self._trace("model", "已调用配置的模型生成结构化回答。"),
                    self._trace("validate", "已核验引文属于检索集且引文文本存在于对应来源。")],
                "verification": "citation_membership_and_quote_match; semantic_entailment_not_guaranteed"}
