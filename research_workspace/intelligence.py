"""Opt-in model services with retrieved, version-bound source evidence.

This module owns no job queue and performs no request until an async model method
is called with a configured provider. Transport tests are not model quality tests.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import hashlib
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
MAX_TRANSLATION_TEXT_CHARS = 200000
MAX_OUTPUT_TOKENS = 8192
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


class ArchitectureNode(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=120)


class ArchitectureEdge(StrictModel):
    source: str
    target: str


class Architecture(StrictModel):
    nodes: list[ArchitectureNode] = Field(min_length=1, max_length=15)
    edges: list[ArchitectureEdge] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid_endpoints(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or any(edge.source not in ids or edge.target not in ids for edge in self.edges):
            raise ValueError("Architecture IDs must be unique and edges must reference nodes")
        return self


class SynthesisPayload(StrictModel):
    status: Literal["generated", "insufficient_evidence"]
    sections: list[Section] = Field(default_factory=list, max_length=12)
    message: str = Field(default="", max_length=3000)
    architecture: Architecture | None = None

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "generated" and not self.sections:
            raise ValueError("A synthesis needs sections")
        if self.status == "insufficient_evidence" and (self.sections or self.architecture):
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
                              "temperature": 0, "max_tokens": MAX_OUTPUT_TOKENS,
                              "response_format": {"type": "json_object"}},
                    )
                except (httpx.ConnectTimeout, httpx.ConnectError):
                    if attempt + 1 == self.config.max_attempts:
                        raise ModelProviderError("模型连接失败；未生成回答。", retryable=True) from None
                    await self._sleep(min(0.25 * (2 ** attempt), 2.0))
                    continue
                except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError):
                    raise ModelProviderError("模型调用中断，服务端结果未知；未自动重试。再次提交可能产生重复费用。") from None
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
                        raise ModelOutputError("模型输出被截断或未通过结构校验；未将其作为完整有效结果展示。") from None
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

    def list_translation_blocks(self, document_id: str, *, run_id: str | None = None,
                                page: int | None = None, block_ids: list[str] | None = None) -> dict:
        """Return all blocks in the requested scope; callers can persist each item."""
        if block_ids is not None and (not isinstance(block_ids, list) or not block_ids
                                     or any(not isinstance(value, str) for value in block_ids)):
            raise LibraryError("局部翻译须提供有效的文本块编号列表。")
        with self.library._connection() as db:
            document = self.library._document(db, document_id)
            selected_run = run_id or (document["available_run"] or document["current_run"])["id"]
            run = self.library._run(db, selected_run)
            if run["document_id"] != document_id:
                raise LibraryError("解析记录不属于指定文献。")
            if page is not None:
                self.library._validate_page(page, document["page_count"])
            pages = {row["page"]: dict(row) for row in db.execute(
                "SELECT page,status,error FROM pages WHERE run_id=?", (selected_run,))}
            rows = db.execute("SELECT * FROM blocks WHERE run_id=? ORDER BY page,ordinal", (selected_run,))
            blocks = []
            for row in rows:
                if page is not None and row["page"] != page:
                    continue
                if block_ids is not None and row["id"] not in block_ids:
                    continue
                blocks.append(Evidence(evidence_id=f"E_{row['id']}", document_id=document_id,
                    document_title=document["title"], filename=document["filename"], run_id=selected_run,
                    block_id=row["id"], page=row["page"], bbox=json.loads(row["bbox"]), text=row["text"]).model_dump())
            if block_ids is not None and {block["block_id"] for block in blocks} != set(block_ids):
                raise LibraryError("所选文本块不存在，或不属于指定文献、解析记录和页面。")
            scope_pages = ([page] if page is not None else list(range(1, document["page_count"] + 1)))
            if block_ids is not None:
                scope_pages = sorted({block["page"] for block in blocks})
            missing = [{"page": number, "status": pages.get(number, {}).get("status", "pending")}
                       for number in scope_pages if pages.get(number, {}).get("status") != "parsed"]
        return {"document_id": document_id, "run_id": selected_run, "run_status": run["status"],
                "scope": "blocks" if block_ids is not None else ("page" if page else "document"),
                "total_pages": document["page_count"], "scope_pages": scope_pages,
                "parsed_pages": len(scope_pages) - len(missing), "missing_pages": missing,
                "total_blocks": len(blocks), "blocks": blocks}

    def _canonical_block(self, block: Evidence | dict) -> Evidence:
        value = block.model_dump() if isinstance(block, Evidence) else block
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str) for key in
                                              ("document_id", "run_id", "block_id")):
            raise LibraryError("翻译文本块缺少有效的文献、解析记录或文本块编号。")
        with self.library._connection() as db:
            document = self.library._document(db, value["document_id"])
            run = self.library._run(db, value["run_id"])
            if run["document_id"] != value["document_id"]:
                raise LibraryError("解析记录不属于指定文献。")
            row = db.execute("SELECT * FROM blocks WHERE id=? AND run_id=?",
                             (value["block_id"], value["run_id"])).fetchone()
            if row is None:
                raise LibraryError("所选原文文本块不存在。")
            return Evidence(evidence_id=f"E_{row['id']}", document_id=document["id"],
                document_title=document["title"], filename=document["filename"], run_id=run["id"],
                block_id=row["id"], page=row["page"], bbox=json.loads(row["bbox"]), text=row["text"])

    async def translate_text(self, text: str, *, target_language: Literal["zh", "en"] = "en",
                             progress: Callable[[dict], Any] | None = None) -> dict:
        self._require_model()
        if target_language not in {"zh", "en"}:
            raise LibraryError("当前翻译目标仅支持zh或en。")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TRANSLATION_TEXT_CHARS:
            raise LibraryError(f"翻译文本不能为空，单次不得超过{MAX_TRANSLATION_TEXT_CHARS}个字符；请分块处理更长文本。")
        protected, replacements = protect_text(text)
        chunks = split_protected_text(protected, max_chars=4000)
        outputs = []
        segments = []
        for index, chunk in enumerate(chunks):
            result = await self.provider.generate(_prompt("translation_v1"), {
                "target_language": target_language, "text": chunk,
                "segment": index + 1, "total_segments": len(chunks),
            }, TranslationPayload)
            validate_preserved_tokens(chunk, result.translated_text)
            outputs.append(result.translated_text)
            translated = restore_text(result.translated_text, replacements)
            segment = {"segment": index + 1, "total_segments": len(chunks),
                       "source_text": restore_text(chunk, replacements), "translated_text": translated}
            segments.append(segment)
            if progress is not None:
                value = progress(segment)
                if inspect.isawaitable(value):
                    await value
        translated_text = restore_text("\n".join(outputs), replacements)
        if Counter(re.findall(r"\d+(?:[.,]\d+)*", text)) != Counter(re.findall(r"\d+(?:[.,]\d+)*", translated_text)):
            raise ModelOutputError("翻译改变了原文数字，结果已拒绝。")
        return {"status": "completed", "source_text": text, "translated_text": translated_text,
                "target_language": target_language, "segments": segments, "prompt_version": PROMPT_VERSION,
                "trace": [self._trace("protect", "对可识别的代码、公式、引用标记及数字进行原样保留。"),
                    self._trace("translate", f"已调用模型翻译{len(chunks)}个片段，并核验保留标记与数字。")],
                "verification": "protected_spans_and_numeric_preservation; translation_quality_not_guaranteed"}

    async def translate_block(self, block: Evidence | dict, *,
                              target_language: Literal["zh", "en"] = "zh") -> dict:
        self._require_model()
        source = self._canonical_block(block)
        translated = await self.translate_text(source.text, target_language=target_language)
        return {**translated, "document_id": source.document_id, "run_id": source.run_id,
                "block_id": source.block_id, "evidence_id": source.evidence_id,
                "page": source.page, "bbox": source.bbox}

    async def translate(self, document_id: str, *, run_id: str | None = None, page: int | None = None,
                        block_ids: list[str] | None = None, target_language: Literal["zh", "en"] = "zh",
                        progress: Callable[[dict], Any] | None = None) -> dict:
        self._require_model()
        selection = self.list_translation_blocks(document_id, run_id=run_id, page=page, block_ids=block_ids)
        translated = []
        for block in selection["blocks"]:
            result = await self.translate_block(block, target_language=target_language)
            translated.append(result)
            if progress is not None:
                value = progress(result)
                if inspect.isawaitable(value):
                    await value
        return {**{key: value for key, value in selection.items() if key != "blocks"},
                "status": "partial" if selection["missing_pages"] else "completed",
                "target_language": target_language, "completed_blocks": len(translated),
                "blocks": translated, "prompt_version": PROMPT_VERSION,
                "trace": [self._trace("translate", f"已完成{len(translated)}/{selection['total_blocks']}个可用文本块；缺失页独立报告。")],
                "source_scope": "extracted_text_only; no_OCR_or_original_layout_PDF"}

    async def synthesize(self, kind: Literal["review", "future_work", "draft"], document_ids: list[str],
                         *, instruction: str = "") -> dict:
        self._require_model()
        if kind not in {"review", "future_work", "draft"}:
            raise LibraryError("学术生成任务类型无效。")
        if not isinstance(instruction, str) or len(instruction) > 10000:
            raise LibraryError("生成要求不得超过10000个字符。")
        if kind == "draft" and not instruction.strip():
            raise LibraryError("论文框架需要明确的研究idea或要求。")
        document_ids = self._validate_documents(document_ids)
        query = instruction[:3000] or ("future work limitations further research 未来 工作 局限 后续"
                                      if kind == "future_work" else "abstract introduction conclusion results contribution")
        evidence = self.retrieve(query, document_ids, limit=max(12, len(document_ids)))
        # Reviews may include a selected document's opening passage when none of
        # its blocks matched headings. This is disclosed as a bounded excerpt.
        if kind == "review":
            represented = {block.document_id for block in evidence}
            if represented != set(document_ids):
                candidates = self._blocks([document_id for document_id in document_ids if document_id not in represented])
                for document_id in document_ids:
                    candidate = next((block for block in candidates if block.document_id == document_id), None)
                    if candidate and len(evidence) < MAX_EVIDENCE:
                        evidence.append(candidate.model_copy(update={"text": candidate.text[:MAX_EVIDENCE_CHARS],
                            "truncated": len(candidate.text) > MAX_EVIDENCE_CHARS}))
        trace = [self._trace("retrieve", f"本次生成使用{len(evidence)}个选定文献片段，未自动核验外部最新研究。")]
        limitations = ["仅基于所选文献的有限检索片段，未验证覆盖全文。",
                       "引用校验验证来源归属与原文引句，不等于自动证明全部推论正确。"]
        if kind == "future_work":
            limitations.append("未进行外部最新文献检索；是否已解决及新颖性均为证据不足，需后续研究核验。")
        if not evidence:
            return {"kind": kind, "status": "insufficient_evidence", "content": "未找到足够的相关原文，无法可靠生成。",
                    "sections": [], "sources": [], "references": [], "architecture": None,
                    "limitations": limitations, "prompt_version": PROMPT_VERSION, "trace": trace,
                    "future_work_verification": "evidence_insufficient" if kind == "future_work" else None}
        result = await self.provider.generate(_prompt("synthesis_v1"), {
            "task": kind, "instruction": instruction, "evidence": [block.model_dump() for block in evidence],
            "external_freshness_verified": False,
        }, SynthesisPayload)
        claims = [claim for section in result.sections for claim in section.claims]
        used = self._validate_claims(claims, evidence)
        if result.status == "generated":
            if not claims:
                raise ModelOutputError("生成结果没有可核验的来源依据，已拒绝。")
            if kind == "draft" and not {"abstract", "introduction", "related work"}.issubset(
                    {_normalized(section.heading) for section in result.sections}):
                raise ModelOutputError("论文框架缺少规定章节，已拒绝。")
        cited_documents = {block.document_id for block in evidence if block.evidence_id in used}
        references = []
        for document_id in document_ids:
            if document_id not in cited_documents:
                continue
            document = self.library.get_document(document_id)
            references.append({"document_id": document_id, "title": document["title"],
                "filename": document["filename"], "sha256": document["sha256"],
                "bibliographic_status": "local_document_identity_only; authors_venue_year_not_verified"})
        content_parts = []
        for section in result.sections:
            paragraphs = ["## " + section.heading]
            paragraphs.extend(claim.text + " " + " ".join(f"[{citation.evidence_id}]" for citation in claim.citations)
                              for claim in section.claims)
            if section.proposed_work:
                paragraphs.append("待验证研究设想（不是已完成实验）：" + section.proposed_work)
            content_parts.append("\n\n".join(paragraphs))
        if references:
            content_parts.append("## References\n\n" + "\n".join(
                f"- {reference['title']}（本地文献：{reference['filename']}）" for reference in references))
        return {"kind": kind, "status": result.status, "content": "\n\n".join(content_parts) or result.message,
                "message": result.message, "sections": [section.model_dump() for section in result.sections],
                "sources": [block.model_dump() for block in evidence if block.evidence_id in used],
                "evidence": [block.model_dump() for block in evidence], "references": references,
                "architecture": result.architecture.model_dump() if result.architecture and kind == "draft" else None,
                "architecture_status": "proposed_design_not_experimental_evidence" if result.architecture and kind == "draft" else None,
                "limitations": limitations, "prompt_version": PROMPT_VERSION,
                "trace": trace + [self._trace("model", "已调用模型生成分节内容。"),
                    self._trace("validate", "已核验引文，并从实际本地文献记录生成来源清单。")],
                "future_work_verification": "evidence_insufficient" if kind == "future_work" else None,
                "search_time_range": None,
                "verification": "citation_membership_and_quote_match; bibliographic_details_not_verified"}


_PRESERVATION_MARKER = re.compile(r"⟪K[a-f0-9]{12}_\d+⟫")
_PROTECTED_SPAN = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|"
    r"\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|"
    r"^[^\n]*(?:=|[∑∏∫√≤≥±])[^\n]*$|"
    r"https?://[^\s)]+|\[[^\]\n]{1,300}\]|"
    r"\\[A-Za-z]+(?:\{[^{}]*\})*|[α-ωΑ-Ω]|"
    r"[+-]?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?(?:%|‰)?",
    re.MULTILINE,
)


def protect_text(text: str) -> tuple[str, dict[str, str]]:
    namespace = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    replacements = {}
    def replace(match):
        token = f"⟪K{namespace}_{len(replacements)}⟫"
        replacements[token] = match.group(0)
        return token
    if _PRESERVATION_MARKER.search(text):
        raise LibraryError("原文包含翻译保留标记格式，请先处理该内容以避免标记冲突。")
    return _PROTECTED_SPAN.sub(replace, text), replacements


def restore_text(text: str, replacements: dict[str, str]) -> str:
    return _PRESERVATION_MARKER.sub(lambda match: replacements[match.group(0)], text)


def validate_preserved_tokens(source: str, translated: str) -> None:
    if Counter(_PRESERVATION_MARKER.findall(source)) != Counter(_PRESERVATION_MARKER.findall(translated)):
        raise ModelOutputError("翻译丢失、重复或改变了受保护的代码、公式、引用或数字，结果已拒绝。")
    if re.search(r"\d", _PRESERVATION_MARKER.sub("", translated)):
        raise ModelOutputError("翻译新增了原文未提供的数字，结果已拒绝。")


def split_protected_text(text: str, *, max_chars: int = 4000) -> list[str]:
    chunks = []
    remaining = text
    while len(remaining) > max_chars:
        candidate = remaining[:max_chars]
        split = max(candidate.rfind("\n"), candidate.rfind(" "))
        if split < max_chars // 2:
            split = max_chars
        else:
            split += 1
        for marker in _PRESERVATION_MARKER.finditer(remaining):
            if marker.start() < split < marker.end():
                split = marker.start()
                break
            if marker.start() >= split:
                break
        chunks.append(remaining[:split])
        remaining = remaining[split:]
    if remaining:
        chunks.append(remaining)
    return chunks
