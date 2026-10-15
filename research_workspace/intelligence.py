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
import unicodedata
from typing import Any, Awaitable, Callable, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .library import Library, LibraryError


PROMPT_VERSION = "research-evidence-v5"
MAX_ISOLATED_CLAIMS = 24
PROMPT_DIRECTORY = Path(__file__).with_name("prompts")
MAX_DOCUMENTS = 20
MAX_EVIDENCE = 24
MAX_EVIDENCE_CHARS = 5000
MAX_BLOCK_CHARS = 16000
MAX_TRANSLATION_TEXT_CHARS = 200000
MAX_OUTPUT_TOKENS = 8192
RETRIEVAL_PROMPT_VERSION = "retrieval-query-v2"
MAX_EXPANSION_TERMS = 6
MAX_EXPANSION_TOKENS = 512
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


class RetrievalTerm(StrictModel):
    source_phrase: str = Field(min_length=1, max_length=80)
    term: str = Field(min_length=1, max_length=80)


class RetrievalQueryPayload(StrictModel):
    terms: list[RetrievalTerm] = Field(default_factory=list, max_length=MAX_EXPANSION_TERMS)


class Citation(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(default="", max_length=3000)
    quote_id: str | None = Field(default=None, pattern=r"^Q[1-9][0-9]*$")


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


class ClaimSupportPayload(StrictModel):
    verdict: Literal["supported", "unsupported"]
    reason: str = Field(default="", max_length=600)


class CoveragePayload(StrictModel):
    unverified_statements: list[str] = Field(max_length=12)
    missing_aspects: list[str] = Field(max_length=12)


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
        # Query expansion is optional retrieval help, so it never buys a repair
        # or retry request. All existing answer/translation budgets stay intact.
        query_expansion = output_model is RetrievalQueryPayload
        attempts = 1 if query_expansion else self.config.max_attempts
        max_tokens = MAX_EXPANSION_TOKENS if query_expansion else MAX_OUTPUT_TOKENS
        if output_model is ClaimSupportPayload:
            max_tokens = 1024
        elif output_model is CoveragePayload:
            max_tokens = 2048
        messages = [
            {"role": "system", "content": prompt + "\nReturn only a JSON object matching this schema:\n"
             + json.dumps(output_model.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        async with httpx.AsyncClient(transport=self._transport,
                                    timeout=httpx.Timeout(self.config.timeout_seconds),
                                    follow_redirects=False, trust_env=False) as client:
            for attempt in range(attempts):
                request_body = {"model": self.config.model, "messages": messages,
                                "temperature": 0, "max_tokens": max_tokens,
                                "response_format": {"type": "json_object"}}
                # The official endpoint enables thinking by default. These bounded
                # structured tasks use the documented non-thinking mode explicitly.
                if urlsplit(endpoint).hostname == 'api.deepseek.com':
                    request_body['thinking'] = {'type': 'disabled'}
                issue = "response_envelope"
                try:
                    response = await client.post(
                        endpoint, headers={"Authorization": f"Bearer {self.config.api_key}"},
                        json=request_body,
                    )
                except (httpx.ConnectTimeout, httpx.ConnectError):
                    if attempt + 1 == attempts:
                        raise ModelProviderError("模型连接失败；未生成回答。", retryable=True) from None
                    await self._sleep(min(0.25 * (2 ** attempt), 2.0))
                    continue
                except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError):
                    raise ModelProviderError("模型调用中断，服务端结果未知；未自动重试。再次提交可能产生重复费用。") from None
                except httpx.HTTPError:
                    raise ModelProviderError("模型HTTP请求失败；请检查服务配置。") from None
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 == attempts:
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
                        issue = "output_truncated" if choice.get("finish_reason") == "length" else "output_not_completed"
                        raise ValueError("Incomplete response")
                    content = choice["message"]["content"]
                    if not isinstance(content, str):
                        raise ValueError("Expected text content")
                    issue = "json_or_schema"
                    return output_model.model_validate_json(content)
                except (ValueError, TypeError, KeyError, IndexError, ValidationError) as error:
                    if isinstance(error, ValidationError):
                        # Only schema field names/types, never the offending values,
                        # model response, provider body, or custom validator message.
                        known_fields = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:',
                            json.dumps(output_model.model_json_schema())))
                        def safe_location(location):
                            return ".".join(str(part) if isinstance(part, int) or part in known_fields else "?"
                                            for part in location) or "root"
                        issue = "; ".join(safe_location(item["loc"]) + ":" + item["type"]
                                          for item in error.errors(include_input=False, include_context=False)[:6])
                    if attempt + 1 == attempts:
                        reason = "模型输出被截断" if issue == "output_truncated" else "模型输出未通过完整性或结构校验"
                        raise ModelOutputError(f"{reason}（{issue}）；未展示无效结果。") from None
                    # Do not retain a malformed output or copy its contents into logs.
                    messages = messages[:2] + [{"role": "user", "content":
                        "The previous result failed validation: " + issue + ". Return a complete, concise JSON object matching the schema exactly. "
                        "Use fewer claims. When citation_spans is supplied, choose evidence_id and quote_id and leave quote empty; never transcribe quotes. "
                        "proposed_work is a string, not a list; citations is a list of objects. "
                        "Architecture edges contain source and target only. Do not repeat the schema or include markdown fences."}]
        raise ModelOutputError("未获得有效模型结果。")


def _prompt(name: str) -> str:
    return (PROMPT_DIRECTORY / f"{name}.txt").read_text(encoding="utf-8")


def _quote_normalized(text: str) -> str:
    # PDF printing ligatures are typography; superscripts/subscripts are facts.
    # Full NFKC would incorrectly make x² and x₂ identical, so do not use it
    # for quote membership or the exact-quotation semantic-review shortcut.
    text = text.translate(str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}))
    return " ".join(text.split())


def _normalized(text: str) -> str:
    return _quote_normalized(text).casefold()


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
                "retrieval": "bilingual_query_expansion_bm25_rrf", "max_documents": MAX_DOCUMENTS,
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

    def _retrieval_inputs(self, question: str, document_ids: list[str], limit: int) -> list[str]:
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise LibraryError("问题不能为空且不得超过4000个字符。")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVIDENCE:
            raise LibraryError(f"证据条数须介于1至{MAX_EVIDENCE}之间。")
        return self._validate_documents(document_ids)

    @staticmethod
    def _search_terms(text: str) -> list[str]:
        """Normalize PDF typography for matching only; never change quoted text."""
        text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
        text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
        # Compounds must match both 'encoder-decoder' and 'encoder / decoder'.
        text = re.sub(r"(?<=[A-Za-z])[-–](?=[A-Za-z])", " ", text)
        terms = []
        for term in tokenize(text):
            # Conservative English plural variants; do not stem numeric indices
            # or mathematical names such as loss, analysis, corpus, or beta_1.
            if re.fullmatch(r"[a-z]+", term) and len(term) > 4:
                if term.endswith("ies"):
                    term = term[:-3] + "y"
                elif term.endswith("s") and not term.endswith(("ss", "us", "is")):
                    term = term[:-1]
            terms.append(term)
        return terms

    @staticmethod
    def _evidence_quality(block: Evidence, question: str, reference_blocks=()) -> float:
        """Prefer explanatory passages without deleting legitimate short evidence."""
        text = " ".join(block.text.split())
        words = IntelligenceService._search_terms(text)
        if not words:
            return 0.0
        if block.block_id in reference_blocks and not re.search(
                r"参考文献|引用文献|书目|\b(?:references|bibliography|cited works)\b", question, re.I):
            return 0.08
        bibliographic = re.search(r"作者|机构|单位|隶属|\bauthors?\b|\baffiliations?\b", question, re.I)
        numeric_question = re.search(
            r"公式|方程|参数|数值|得分|分数|准确率|表格|[=α-ω]|\b(?:equation|formula|parameter|value|score|bleu|accuracy|table)\b",
            question, re.I)
        numeric_source = re.search(r"\d|[=α-ω]", text, re.I)
        if bibliographic:
            return 1.0
        if re.match(r"^(?:former|latter|layer|word|tion|ture)\b", text) and len(words) < 55:
            return 0.35  # A page/column continuation cannot identify its subject.
        if re.match(r"^(?:figure|fig\.?|table|图|表)\s*\d+\s*[:.：]", text, re.I):
            return 0.25 if len(words) < 18 else 0.85
        if re.match(r"^(?:this|the|following) section (?:describes|discusses|presents|provides)\b", text, re.I) and len(words) < 18:
            return 0.35  # A section signpost is not the promised explanation.
        if numeric_question and numeric_source and not re.fullmatch(r"\d+(?:\.\d+)*\s+[A-Za-z -]+", text):
            return 1.0
        if len(set(words)) <= 2 and len(words) <= 8:
            return 0.06  # Repeated model names and isolated figure labels.
        if len(words) < 8 and not re.search(r"[.!?。！？]$", text):
            return 0.3  # Section headings can help, but do not explain the section.
        return 1.0

    @staticmethod
    def _lexical_index(blocks: list[Evidence]) -> tuple:
        counts = [Counter(IntelligenceService._search_terms(block.text)) for block in blocks]
        lengths = [sum(count.values()) for count in counts]
        average = max(sum(lengths) / len(lengths), 1) if lengths else 1
        # Parsed bibliography fragments often match many technical search words.
        # Mark the section without removing it: explicit reference questions can
        # still retrieve it, and appendix headings end the down-weighted region.
        in_references, reference_blocks = {}, set()
        for block in blocks:
            text = " ".join(block.text.split())
            if re.fullmatch(r"(?:\d+\s+)?(?:references|bibliography|参考文献)", text, re.I):
                in_references[block.document_id] = True
            elif len(text) < 120 and re.match(r"^(?:Appendix\b|Appendices\b|[A-Z](?:\.\d+)*\s)", text):
                in_references[block.document_id] = False
            if in_references.get(block.document_id):
                reference_blocks.add(block.block_id)
        return counts, lengths, average, reference_blocks

    @staticmethod
    def _rank_lexical(question: str, blocks: list[Evidence], index: tuple) -> list[Evidence]:
        terms = set(IntelligenceService._search_terms(question))
        objective_search = "objective" in terms or "训练目标" in question
        objective_terms = {"objective", "loss", "prediction", "predict", "predicted", "probability", "likelihood"}
        if objective_search:
            # Task-neutral search synonyms, not asserted training facts.
            terms.update(objective_terms)
        if not terms or not blocks:
            return []
        counts, lengths, average, reference_blocks = index
        document_frequency = Counter(term for count in counts for term in terms if count[term])
        candidates = []
        for block, count, length in zip(blocks, counts, lengths):
            score = 0.0
            matches = 0
            for term in terms:
                frequency = count[term]
                if not frequency:
                    continue
                matches += 1
                inverse = math.log(1 + (len(blocks) - document_frequency[term] + 0.5)
                                   / (document_frequency[term] + 0.5))
                score += inverse * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average))
            score *= math.sqrt(matches / max(sum(bool(document_frequency[term]) for term in terms), 1))
            score *= IntelligenceService._evidence_quality(block, question, reference_blocks)
            if score > 0:
                candidates.append(block.model_copy(update={"score": round(score, 6)}))
        candidates.sort(key=lambda block: (
            IntelligenceService._evidence_quality(block, question, reference_blocks) < 0.5,
            objective_search and not objective_terms.intersection(IntelligenceService._search_terms(block.text)),
            -block.score, block.document_id, block.page, block.block_id))
        return candidates

    @staticmethod
    def _fair_blocks(candidates: list[Evidence], document_ids: list[str], limit: int) -> list[Evidence]:
        # Deduplicate repeated extraction labels within a paper, never across
        # papers: identical statements in two sources still need both identities.
        queues = {document_id: [] for document_id in document_ids}
        seen = set()
        for block in candidates:
            key = (block.document_id, _normalized(unicodedata.normalize("NFKC", block.text)))
            if block.document_id in queues and key not in seen:
                queues[block.document_id].append(block)
                seen.add(key)
        selected: list[Evidence] = []
        # Allocate each successive slot fairly, rather than guaranteeing only
        # one hit and letting a name-heavy paper consume every remaining slot.
        while len(selected) < limit and any(queues.values()):
            for document_id in document_ids:
                if queues[document_id] and len(selected) < limit:
                    selected.append(queues[document_id].pop(0))
        return selected

    @staticmethod
    def _heading(block: Evidence) -> tuple[str, bool, str] | None:
        lines = [line.strip() for line in block.text.splitlines() if line.strip()]
        if not lines:
            return None
        compact = " ".join(lines)
        if len(compact) < 120 and re.match(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)\s+[A-Z]", compact) and not re.search(r"[.!?]$", compact):
            return re.sub(r"^\S+\s+", "", compact), True, ""
        first = lines[0]
        if first.endswith(":") and len(lines) > 1:
            first += " " + lines[1]
        enumerated = re.match(r"^([A-Z][A-Za-z -]{0,20})\s*#?\d+\s*[:.]", first)
        if enumerated:
            return first, False, enumerated[1].strip().casefold()
        words = re.findall(r"[A-Za-z][A-Za-z/-]*", first)
        if (len(lines) > 1 and 2 <= len(words) <= 6 and len(first) < 70
                and not re.search(r"[.!?,;]", first)
                and sum(word[0].isupper() for word in words) >= len(words) - 1):
            return first, False, ""
        return None

    @staticmethod
    def _reading_order(source: list[Evidence]) -> list[Evidence]:
        """Read down each column before moving right; never alter source text."""
        two_column = set()
        for page in {item.page for item in source}:
            bodies = [item for item in source if item.page == page and len(item.text) > 150
                      and item.bbox[2] - item.bbox[0] < 0.55]
            if any(item.bbox[0] < 0.4 for item in bodies) and any(item.bbox[0] > 0.45 for item in bodies):
                two_column.add(page)
        return sorted(source, key=lambda item: (
            item.page, int(item.bbox[0] > 0.45) if item.page in two_column else 0,
            item.bbox[1], item.bbox[0]))

    def _section_candidates(self, query: str, blocks: list[Evidence], index: tuple,
                            document_id: str) -> list[Evidence]:
        """Retrieve bounded canonical paragraphs under matching headings."""
        source = self._reading_order([item for item in blocks if item.document_id == document_id])
        query_terms = set(self._search_terms(query))
        if not query_terms.intersection({"architecture", "encoder", "decoder", "objective", "training", "method",
                                         "algorithm", "procedure", "mechanism", "framework", "design", "implementation"}):
            return []
        headings = []
        for position, item in enumerate(source):
            heading = self._heading(item)
            if not heading or item.block_id in index[3]:
                continue
            title, standalone, enumeration = heading
            terms = set(self._search_terms(title))
            overlap = len(query_terms & terms)
            if overlap:
                score = overlap / math.sqrt(max(len(terms), 1)) + (0.15 if standalone else 0)
                headings.append((score, position, heading))
        if not headings:
            return []
        headings.sort(key=lambda item: (-item[0], source[item[1]].page, item[1]))
        _, start, (_, standalone, enumeration) = headings[0]
        window = []
        for position in range(start, min(start + 24, len(source))):
            item = source[position]
            if item.page > source[start].page + 1:
                break
            heading = self._heading(item)
            if position > start and heading and heading[1]:
                break
            if (position > start and not standalone and heading and not enumeration
                    and not heading[2]):
                break
            window.append(item)
        # An enumerated heading can be one of several sibling objectives/steps.
        # Preserve each sibling's best paragraph instead of only the first one.
        groups, current = [], []
        for item in window:
            heading = self._heading(item)
            if heading and heading[2]:
                if current:
                    groups.append(current)
                current = []
            if self._evidence_quality(item, query, index[3]) >= 0.85 and len(self._search_terms(item.text)) >= 8:
                current.append(item)
        if current:
            groups.append(current)
        ranked = self._rank_lexical(query, blocks, index)
        ranks = {item.block_id: position for position, item in enumerate(ranked)}
        # Prefer substantive child parts, then use the parent introduction.
        child_groups = [group for group in groups if any((self._heading(item) or (None, None, ""))[2] for item in group)]
        ordered = child_groups + [group for group in groups if group not in child_groups]
        selected = []
        for group in ordered:
            selected.append(min(group, key=lambda item: ranks.get(item.block_id, len(ranks))))
        remaining = sorted((item for group in groups for item in group if item not in selected),
                           key=lambda item: ranks.get(item.block_id, len(ranks)))
        combined = selected + remaining
        if not standalone and not enumeration and source[start] in combined:
            combined.remove(source[start])
            combined.insert(0, source[start])
        return combined[:3]

    def _attribution_context(self, queries: list[dict], blocks: list[Evidence],
                             selected: list[Evidence], document_ids: list[str], index: tuple) -> list[Evidence]:
        """Add a bounded set of attribution candidates from the frozen snapshot.

        Introduction/section prose and nearby continuations are retrieval hints,
        never proof of attribution. Each remains an independent canonical block;
        generation must cite the necessary passages and pass isolated review.
        """
        budget = min(6, MAX_EVIDENCE - len(selected))
        if budget <= 0 or not selected:
            return []
        request = " ".join(item["text"] for item in queries)
        stop = {"a", "an", "the", "of", "in", "and", "or", "to", "for", "is", "are", "on", "with",
                "we", "our", "this", "that", "it", "model", "paper", "use", "using", "based"}
        terms = set(self._search_terms(request)) - stop
        facets = [
            {"architecture", "encoder", "decoder", "attention", "mechanism", "design", "structure"},
            {"train", "training", "pretrain", "objective", "loss", "masked", "prediction", "likelihood"},
            {"knowledge", "memory", "retrieval", "retriever", "parametric", "external"},
        ]
        wanted = [facet for facet in facets if terms & facet]
        seen = {(item.document_id, _normalized(unicodedata.normalize("NFKC", item.text))) for item in selected}
        selected_ids = {item.block_id for item in selected}
        candidates = []
        for document_id in document_ids:
            seeds = [item for item in selected if item.document_id == document_id]
            if not seeds:
                continue
            runs = {item.run_id for item in seeds}
            source = self._reading_order([item for item in blocks
                                          if item.document_id == document_id and item.run_id in runs])
            positions = {item.block_id: position for position, item in enumerate(source)}
            seed_positions = [positions[item.block_id] for item in seeds]
            ranked = []
            for position, item in enumerate(source):
                key = (document_id, _normalized(unicodedata.normalize("NFKC", item.text)))
                if item.block_id in selected_ids or key in seen or item.block_id in index[3]:
                    continue
                text = unicodedata.normalize("NFKC", item.text)
                text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
                text = " ".join(text.split())
                words = set(self._search_terms(text))
                if len(words) < 8 or self._evidence_quality(item, request, index[3]) < 0.85:
                    continue
                # Source-code footnotes and benchmark inventories often contain
                # names but do not explain a model's identity or methods.
                if re.search(r"https?://|\b(?:code|software)\b.{0,50}\b(?:available|released|open.sourc)", text, re.I):
                    continue
                overlap = len(terms & words)
                facet_overlap = sum(min(3, len(words & facet)) for facet in wanted)
                introduction = bool(re.search(
                    r"\b(?:we\s+(?:introduce|propose|present|develop|build)|(?:called|refer to as)\s|by proposing\b)", text, re.I))
                training_intro = bool(re.search(
                    r"\b(?i:we\s+(?:pre[ -]?train|train))\s+[A-Z][A-Za-z0-9_-]+\b", text))
                definition = bool(re.search(
                    r"\b[A-Z][A-Za-z0-9_-]+\s+(?:uses?|combines?|alleviates?|adopts?|is designed|consists?)\b", text))
                # Keep the preceding section introduction, even when the body
                # says 'we' and the heading supplies its explicitly named scope.
                heading_context = False
                for previous in source[max(0, position - 2):position]:
                    heading = self._heading(previous)
                    if heading and terms.intersection(self._search_terms(heading[0])):
                        heading_context = True
                nearest = min(abs(position - seed) for seed in seed_positions)
                nearby = nearest <= 3 and any(abs(item.page - seed.page) <= 1 for seed in seeds)
                anchor = introduction or training_intro or definition
                if overlap == 0 or (wanted and facet_overlap == 0):
                    continue
                if not anchor and not (nearby and heading_context):
                    continue
                score = (4 * anchor + 4 * training_intro + 1.5 * heading_context
                         + min(4, overlap) + min(6, facet_overlap) + 0.5 * nearby)
                ranked.append((score, position, item))
            ranked.sort(key=lambda value: (-value[0], value[1]))
            candidates.extend(item for _, _, item in ranked)
        # Round robin prevents one name-heavy document consuming the context
        # budget; at most three additions from any one selected document.
        fair = self._fair_blocks(candidates, document_ids, len(candidates))
        counts, additions = Counter(), []
        for item in fair:
            key = (item.document_id, _normalized(unicodedata.normalize("NFKC", item.text)))
            if counts[item.document_id] >= 3 or key in seen:
                continue
            additions.append(item)
            counts[item.document_id] += 1
            seen.add(key)
            if len(additions) >= budget:
                break
        return additions

    @staticmethod
    def _excerpts(selected: list[Evidence]) -> list[Evidence]:
        return [block.model_copy(update={"text": block.text[:MAX_EVIDENCE_CHARS],
                     "truncated": len(block.text) > MAX_EVIDENCE_CHARS}) for block in selected]

    def retrieve(self, question: str, document_ids: list[str], *, limit: int = 12) -> list[Evidence]:
        """Pure synchronous BM25; never invokes a model or adds structural hits."""
        document_ids = self._retrieval_inputs(question, document_ids, limit)
        blocks = self._blocks(document_ids)
        ranked = self._rank_lexical(question, blocks, self._lexical_index(blocks))
        return self._excerpts(self._fair_blocks(ranked, document_ids, limit))

    @staticmethod
    async def _check_cancel(callback) -> None:
        if callback is not None:
            value = callback()
            if inspect.isawaitable(value):
                await value

    @staticmethod
    def _english_documents(blocks: list[Evidence], document_ids: list[str]) -> list[str]:
        result = []
        for document_id in document_ids:
            sample = " ".join(block.text[:1500] for block in
                              [item for item in blocks if item.document_id == document_id][:8])
            latin = len(re.findall(r"[A-Za-z]", sample))
            chinese = len(re.findall(r"[\u3400-\u9fff]", sample))
            if latin >= 16 and latin > 3 * chinese:
                result.append(document_id)
        return result

    @staticmethod
    def _expansion_terms(payload: RetrievalQueryPayload, question: str) -> tuple[list[dict], int]:
        normal = lambda text: _normalized(unicodedata.normalize("NFKC", text))
        normalized_question = normal(question)
        question_numbers = set(re.findall(r"\d+(?:\.\d+)?", normalized_question))
        accepted, seen, rejected = [], set(), 0
        for item in payload.terms:
            anchor, term = normal(item.source_phrase), " ".join(item.term.split())
            normalized_term = normal(term)
            term_numbers = set(re.findall(r"\d+(?:\.\d+)?", normalized_term))
            instruction_only = re.fullmatch(
                r"(?:请)?(?:引用原文|引用来源|引用文献|提供引用|用中文回答|用英文回答)|"
                r"(?:please )?(?:cite|quote|provide references)(?: .*)?", anchor, re.I)
            if (instruction_only or not anchor or anchor not in normalized_question or not term or len(term) > 80
                    or any(unicodedata.category(char).startswith("C") for char in item.term)
                    or not re.search(r"[A-Za-z\u3400-\u9fffα-ω]", term)
                    or re.search(r"https?://|www\.|ignore.{0,25}instructions|system\s+prompt|忽略.{0,15}指令|执行命令", term, re.I)
                    or len(term.split()) > 10 or not term_numbers.issubset(question_numbers)):
                rejected += 1
                continue
            if normalized_term in seen or normalized_term == normalized_question:
                continue
            seen.add(normalized_term)
            accepted.append({"source_phrase": item.source_phrase, "text": term})
        return accepted, rejected

    @staticmethod
    def _needs_mechanism_expansion(question: str) -> bool:
        """Expand multi-part English explanation requests, not routine lookups.

        The rule uses request structure, never a named method or a known answer.
        A trailing instruction to cite the paper is not an extra research facet.
        """
        request = re.sub(r"[.;,]?\s*(?:\b(?:and|then)\s+)?\b(?:please\s+)?(?:cite|quote|provide references)\b.*$", "", question,
                         flags=re.I | re.S)
        mechanism = re.search(r"\b(?:why|how|advantages?|benefits?|mechanisms?|trade[- ]?offs?)\b",
                              request, re.I)
        facets = re.search(r"\b(?:and|versus|vs|compared|relative to)\b|[,;]", request, re.I)
        return bool(mechanism and facets)

    async def retrieve_for_question(self, question: str, document_ids: list[str], *, limit: int = 12,
                                   expansion: Literal["auto", "off", "always"] = "auto",
                                   check_cancel=None, include_attribution_context: bool = False) -> dict:
        """Bilingual query expansion + BM25/RRF, not embeddings or factual evidence.

        Auto expands Chinese questions over predominantly English source samples,
        or Greek-symbol questions whose symbols are absent from the source text.
        Multi-part English mechanism/advantage questions can also expand once;
        ordinary English lexical queries remain free of a query-generation call.
        Every ranking uses one frozen, canonical document/run snapshot, including
        when a reparse completes while the optional model request is in flight.
        """
        document_ids = self._retrieval_inputs(question, document_ids, limit)
        if expansion not in {"auto", "off", "always"}:
            raise LibraryError("查询扩展策略须为auto、off或always。")
        blocks = self._blocks(document_ids)
        opening_by_document = {}
        for block in blocks:
            opening_by_document.setdefault(block.document_id, block)
        index = self._lexical_index(blocks)
        original = self._rank_lexical(question, blocks, index)
        english = self._english_documents(blocks, document_ids)
        has_chinese = bool(re.search(r"[\u3400-\u9fff]", question))
        symbols = set(re.findall(r"[α-ω]", question.casefold()))
        missing_symbols = symbols and not any(symbol in block.text.casefold() for symbol in symbols for block in blocks)
        trigger = ("explicit_always" if expansion == "always" else
                   "chinese_question_english_sources" if has_chinese and english else
                   "unmatched_mathematical_symbols" if missing_symbols and english else
                   "english_multifacet_mechanism" if english and not has_chinese
                   and self._needs_mechanism_expansion(question) else "not_needed")
        eligible = bool(blocks and self.provider.configured and expansion != "off" and trigger != "not_needed")
        info = {"policy": expansion, "trigger": trigger, "status": "not_needed", "calls": 0,
                "max_calls": 1, "max_output_tokens": MAX_EXPANSION_TOKENS, "rejected_terms": 0}
        queries = [{"kind": "original", "text": question}]
        trace = [self._trace("retrieve", f"原问题BM25在{len(document_ids)}篇固定版本文献中匹配{len(original)}个文本块。")]
        if eligible:
            await self._check_cancel(check_cancel)
            try:
                info["calls"] = 1
                generated = await self.provider.generate(_prompt("retrieval_query_v2"), {
                    "task": "bilingual_retrieval_terms", "question": question,
                    "source_language_hint": "predominantly_english" if english else "mixed_or_unknown",
                    "max_terms": MAX_EXPANSION_TERMS,
                    "coverage": "cover_each_subquestion_with_specific_terms; prioritize_mechanism_for_why_questions",
                    "purpose": "query_terms_only_not_an_answer",
                }, RetrievalQueryPayload)
                payload = RetrievalQueryPayload.model_validate(
                    generated.model_dump() if isinstance(generated, RetrievalQueryPayload) else generated)
                accepted, info["rejected_terms"] = self._expansion_terms(payload, question)
                queries.extend({"kind": "expansion", **item} for item in accepted)
                info["status"] = "applied" if accepted else "no_usable_terms"
            except (RuntimeError, ValueError, TypeError, OSError, httpx.HTTPError):
                # JobStore.TaskCancelled inherits Exception directly, and asyncio
                # cancellation inherits BaseException; neither is swallowed here.
                info["status"] = "failed_fallback"
                info["error"] = "query_expansion_failed"
            await self._check_cancel(check_cancel)
            if info["status"] == "applied":
                trace.append(self._trace("query_expansion", "双语查询扩展仅用于检索，不作为原文证据：" + "；".join(item["text"] for item in queries[1:])))
            else:
                trace.append(self._trace("query_expansion", "查询扩展失败或没有合规词组；保留原问题BM25结果，未伪造证据。"))
        else:
            info["status"] = "disabled" if expansion == "off" else "no_source_text" if not blocks else "unconfigured" if not self.provider.configured else "not_needed"
            trace.append(self._trace("query_expansion", "未触发查询扩展模型调用；使用本地原文检索。"))
        rankings = [self._fair_blocks(original, document_ids, MAX_EVIDENCE)]
        rankings.extend(self._fair_blocks(self._rank_lexical(item["text"], blocks, index), document_ids, MAX_EVIDENCE)
                        for item in queries[1:])
        # Each translated facet gets a vote. Dividing all expansion weights by
        # their count lets Latin paper names in a Chinese question overwhelm
        # the translated architecture, objectives, and evaluation concepts.
        weights = [1.0] + [1.0] * (len(queries) - 1)
        bibliographic = bool(re.search(r"作者|机构|单位|隶属|\bauthors?\b|\baffiliations?\b", question, re.I))
        if bibliographic and blocks:
            opening = []
            for document_id in document_ids:
                first_page = min((block.page for block in blocks if block.document_id == document_id), default=None)
                opening.extend([block for block in blocks if block.document_id == document_id and block.page == first_page][:8])
            rankings.append(self._fair_blocks(opening, document_ids, MAX_EVIDENCE))
            weights.append(0.75)
            trace.append(self._trace("bibliographic_candidates", "作者/机构问题加入各文献最早可读页的有限文本块；这是版面候选，是否包含答案仍须原文支持。"))
        if len(rankings) == 1:
            selected = self._fair_blocks(original, document_ids, limit)
            mode = "bm25_lexical"
        else:
            scores, canonical = {}, {block.block_id: block for block in blocks}
            for ranking, weight in zip(rankings, weights):
                for rank, block in enumerate(ranking, 1):
                    scores[block.block_id] = scores.get(block.block_id, 0.0) + weight / (60 + rank)
            fused = [canonical[block_id].model_copy(update={
                "score": round(score * self._evidence_quality(canonical[block_id], question, index[3]), 8)})
                for block_id, score in scores.items()]
            fused.sort(key=lambda block: (-block.score, block.document_id, block.page, block.block_id))
            # Reserve the available per-paper budget for distinct translated
            # subquestions; a high-frequency model name cannot crowd out a rare
            # training objective or explanatory mechanism. Any remaining slots
            # still follow fused relevance. All candidates remain original blocks.
            facets = {}
            for query, ranking in zip(queries[1:], rankings[1:len(queries)]):
                if _normalized(query["text"]) == _normalized(query["source_phrase"]):
                    continue
                # Entity names remain in lexical fusion but do not consume a
                # comparison dimension's reserved slots.
                anchor = query["source_phrase"].strip()
                if len(document_ids) > 1 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", anchor) and any(
                        re.search(r"\b" + re.escape(anchor) + r"s?\b", block.document_title, re.I) for block in blocks):
                    continue
                contextual = []
                if len(document_ids) > 1:
                    for document_id in document_ids:
                        contextual.extend(self._section_candidates(query["text"], blocks, index, document_id))
                facets.setdefault(_normalized(anchor), []).extend(contextual + ranking)
            reserved = []
            reserved_ids = set()
            per_document = max(1, math.ceil(limit / max(len(document_ids), 1)))
            fused_by_id = {item.block_id: item for item in fused}
            for document_id in document_ids:
                count = 0
                while count < per_document:
                    before = count
                    for ranking in facets.values():
                        if count >= per_document:
                            break
                        candidate = next((item for item in ranking if item.document_id == document_id
                                          and item.block_id not in reserved_ids
                                          and self._evidence_quality(item, question, index[3]) >= 0.85), None)
                        if candidate:
                            reserved.append(fused_by_id.get(candidate.block_id, candidate))
                            reserved_ids.add(candidate.block_id)
                            count += 1
                    if len(document_ids) == 1 or count == before:
                        break
            selected = self._fair_blocks(reserved + fused, document_ids, limit)
            mode = "bilingual_query_expansion_bm25_rrf" if len(queries) > 1 else "bm25_with_bibliographic_candidates"
            trace.append(self._trace("rank_fusion", f"对{len(rankings)}组候选进行RRF融合，去除篇内重复文本，兼顾正文质量、子问题和各篇文献；选出{len(selected)}个真实原文块。"))
        additions = self._attribution_context(queries, blocks, selected, document_ids, index) if include_attribution_context else []
        selected.extend(additions)
        if include_attribution_context:
            trace.append(self._trace("attribution_context", f"从同一固定版本快照补充{len(additions)}个模型介绍或章节上下文块；各块保持独立来源，仍需逐条引用审核。"))
        return {"evidence": self._excerpts(selected), "mode": mode, "queries": queries,
                "attribution_context": {"enabled": include_attribution_context,
                                        "added_block_ids": [item.block_id for item in additions],
                                        "max_added": 6, "max_per_document": 3},
                # Internal snapshot data, excluded from persisted/public audit.
                "_opening_candidates": self._excerpts(list(opening_by_document.values())),
                "query_terms": [item["text"] for item in queries[1:]], "expansion": info,
                "prompt_version": RETRIEVAL_PROMPT_VERSION, "trace": trace,
                "selected_runs": {document_id: next((block.run_id for block in blocks if block.document_id == document_id), None)
                                  for document_id in document_ids},
                "evidence_rule": "only_canonical_document_run_blocks; generated_terms_are_not_evidence"}

    @staticmethod
    def _validate_claims(claims: list[Claim], evidence: list[Evidence]) -> set[str]:
        allowed = {block.evidence_id: block for block in evidence}
        used = set()
        for claim_index, claim in enumerate(claims):
            for citation_index, citation in enumerate(claim.citations):
                source = allowed.get(citation.evidence_id)
                if source is None:
                    raise ModelOutputError("模型引用了本次检索范围外的来源，结果已拒绝。")
                if not citation.quote.strip() or _quote_normalized(citation.quote) not in _quote_normalized(source.text):
                    raise ModelOutputError(f"claim[{claim_index}].citations[{citation_index}] 模型引文与检索原文不一致，结果已拒绝。")
                used.add(citation.evidence_id)
        return used

    @staticmethod
    def _trace(step: str, detail: str) -> dict:
        return {"step": step, "detail": detail}

    @staticmethod
    def _citation_spans(evidence: list[Evidence]) -> dict[str, dict[str, str]]:
        """Number contiguous canonical passages; the model selects, never copies."""
        catalog = {}
        for block in evidence:
            # Keep source characters exactly, including PDF hyphens and ligatures.
            # Sentence-sized spans give the reviewer enough context and bound JSON.
            units = re.split(r"(?<=[.!?。！？])(?=\s+(?:[A-Z\u3400-\u9fff]|$))", block.text)
            spans = []
            for unit in units:
                remaining = unit.strip()
                while len(remaining) > 1800:
                    cut = remaining.rfind(" ", 0, 1800)
                    if cut < 900:
                        cut = 1800
                    spans.append(remaining[:cut])
                    remaining = remaining[cut:].strip()
                if remaining:
                    spans.append(remaining)
            catalog[block.evidence_id] = {f"Q{index + 1}": text for index, text in enumerate(spans)}
        return catalog

    @staticmethod
    def _resolve_citation_spans(claims: list[Claim], catalog: dict[str, dict[str, str]]) -> None:
        for index, claim in enumerate(claims):
            for citation in claim.citations:
                if citation.quote_id is None:
                    continue  # Legacy quote-only responses still face exact membership.
                quote = catalog.get(citation.evidence_id, {}).get(citation.quote_id)
                if quote is None:
                    raise ModelOutputError(f"claim[{index}] 引用了无效的原文片段编号，结果已拒绝。")
                if citation.quote.strip() and _quote_normalized(citation.quote) != _quote_normalized(quote):
                    raise ModelOutputError(f"claim[{index}] 的原文片段编号与引句不一致，结果已拒绝。")
                citation.quote = quote

    @staticmethod
    def _identifier_text_forms(text: str) -> tuple[str, ...]:
        """Match typography only; retain both readings of a PDF line-end hyphen."""
        text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
        text = text.translate(str.maketrans({"’": "'", "‘": "'", "‐": "-", "‑": "-", "–": "-"}))
        # A line break may split Transformer or follow a real variant separator.
        # Neither reading may turn RAG-Sequence into the bare identifier RAG.
        preserved = re.sub(r"(?<=[A-Za-z0-9])-\s*\n\s*(?=[A-Za-z0-9])", "-", text)
        joined = re.sub(r"(?<=[A-Za-z0-9])-\s*\n\s*(?=[A-Za-z0-9])", "", text)
        return tuple(dict.fromkeys((preserved, joined)))

    @classmethod
    def _identifier_tokens(cls, text: str) -> set[str]:
        token = r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*(?![A-Za-z0-9_-])"
        return {match.group().casefold() for form in cls._identifier_text_forms(text)
                for match in re.finditer(token, form)}

    @classmethod
    def _named_attribution_identifiers(cls, catalog: dict[str, dict[str, str]]) -> set[str]:
        """Recognize conservative model names, never import their factual context.

        This vocabulary is only a rejection hint. Ordinary words and task/type
        abbreviations are not model identities; uncertain names stay with the
        isolated semantic reviewer rather than being guessed by this guard.
        """
        generic = {
            "a", "an", "the", "this", "that", "these", "those", "our", "their", "its", "we", "both",
            "each", "every", "all", "other", "another", "one", "two", "new", "old", "first", "second",
            "most", "many", "some", "several", "different", "such", "specific", "single", "multiple",
            "large", "small", "neural", "language", "masked", "pretrained", "pre-trained", "trained",
            "general", "generative", "probabilistic", "baseline", "original", "proposed", "existing",
            "model", "models", "architecture", "method", "approach", "framework", "system", "network",
            "nlp", "nlu", "nlg", "qa", "mlm", "nsp", "lm", "llm", "rnn", "cnn", "dnn", "seq2seq",
            "mask", "cls", "sep", "sota", "figure", "table", "appendix",
        }
        identifier = r"(?P<name>[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*)"
        patterns = (
            rf"(?<![A-Za-z0-9_-]){identifier}(?:'s)?\s+(?i:models?\b|architecture\b)",
            rf"\b(?i:we\s+(?:introduce[ds]?|propose[ds]?|present(?:ed)?|develop(?:ed)?|build|built)\s+)"
            rf"(?:(?i:a|an|the|new)\s+)*{identifier}(?![A-Za-z0-9_-])",
            rf"\b(?i:(?:model|architecture)\s+(?:called|named)\s+){identifier}(?![A-Za-z0-9_-])",
        )
        names = set()
        for spans in catalog.values():
            for quote in spans.values():
                for form in cls._identifier_text_forms(quote):
                    for pattern in patterns:
                        for match in re.finditer(pattern, form):
                            name = match.group("name")
                            if name.casefold() not in generic and len(name) >= 2 and any(char.isupper() for char in name):
                                names.add(name.casefold())
        return names

    @classmethod
    def _missing_named_attribution(cls, claim: Claim, names: set[str]) -> list[str]:
        """Reject an absent literal name; presence alone never proves support."""
        named_forms = [cls._identifier_tokens(form) & names for form in cls._identifier_text_forms(claim.text)]
        if not any(named_forms):
            return []
        quotes = "\n".join(citation.quote for citation in claim.citations)
        # A translated name/expansion may legitimately replace a Latin spelling.
        # Other-script or accented aliases can occur in otherwise English prose.
        # Leave these uncertain cases to semantic review, even when they are short.
        normalized_quotes = unicodedata.normalize("NFKC", quotes)
        if any(char.isalpha() and not char.isascii() for char in normalized_quotes):
            return []
        quoted = cls._identifier_tokens(quotes)
        # Treat the two possible line-break readings as alternatives, not two
        # distinct model identities that both have to appear in the quotations.
        return min((sorted(named - quoted) for named in named_forms), key=lambda missing: (len(missing), missing))

    async def _generate_grounded(self, prompt: str, payload: dict, output_model: type[T],
                                 evidence: list[Evidence], *, check_cancel=None,
                                 required_documents: list[str] | None = None,
                                 required_sections: set[str] | None = None) -> tuple[T, set[str], dict]:
        """Bounded repair against one frozen retrieval snapshot; never waive checks."""
        audit = {"prompt_version": "claim-support-v1", "coverage_prompt_version": "coverage-v1",
                 "mode": "isolated_claim_citations", "attempts": [], "model_checked": False,
                 "claim_model_calls": 0, "coverage_model_calls": 0, "cache_hits": 0,
                 "limitation": "model_semantic_review_is_not_a_proof"}
        support_cache: dict[str, ClaimSupportPayload] = {}
        supported_review_claims: Counter[str] | None = None
        catalog = self._citation_spans(evidence)
        named_identifiers = self._named_attribution_identifiers(catalog)
        payload = {**payload, "evidence": [{**block.model_dump(), "citation_spans": [
            {"quote_id": quote_id, "text": text} for quote_id, text in catalog[block.evidence_id].items()]}
            for block in evidence]}
        current_payload = dict(payload)
        for attempt in range(2):
            await self._check_cancel(check_cancel)
            result = await self.provider.generate(prompt, current_payload, output_model)
            claims = result.claims if isinstance(result, AnswerPayload) else [
                claim for section in result.sections for claim in section.claims]
            problems = []
            try:
                self._resolve_citation_spans(claims, catalog)
                used = self._validate_claims(claims, evidence)
                if supported_review_claims is not None and result.status == "generated":
                    repaired = Counter(json.dumps(claim.model_dump(), sort_keys=True, ensure_ascii=False) for claim in claims)
                    if repaired != supported_review_claims:
                        raise ModelOutputError("综述纠正必须完整保留已通过的结论及其引用，不能改写、重复或重新加入已移除的结论。")
                if result.status in {"answered", "generated"}:
                    if not claims:
                        raise ModelOutputError("生成结果没有可核验的来源依据，已拒绝。")
                    if required_documents and {block.document_id for block in evidence if block.evidence_id in used} != set(required_documents):
                        raise ModelOutputError("生成结果缺少选定文献的原文依据。")
                    if required_sections and not required_sections.issubset(
                            {_normalized(section.heading) for section in result.sections}):
                        raise ModelOutputError("论文框架缺少规定章节：Abstract、Introduction、Related Work。")
            except ModelOutputError as error:
                problems.append(str(error))
            reviews = []
            if not problems and claims:
                # Exact quotation as the entire claim entails itself. All actual
                # paraphrases, translations and multi-source conclusions need review.
                direct = [len(claim.citations) == 1 and _quote_normalized(claim.text) == _quote_normalized(claim.citations[0].quote)
                          for claim in claims]
                if sum(not item for item in direct) > MAX_ISOLATED_CLAIMS:
                    raise ModelOutputError("单次结果超过 24 条需独立复核的结论，请缩小生成范围；结果已拒绝。")
                for index, claim in enumerate(claims):
                    await self._check_cancel(check_cancel)
                    claim_data = claim.model_dump()
                    cache_key = json.dumps(claim_data, sort_keys=True, ensure_ascii=False)
                    missing_names = self._missing_named_attribution(claim, named_identifiers)
                    if missing_names:
                        verdict = ClaimSupportPayload(verdict="unsupported", reason=(
                            "The claim names model identifier(s) " + ", ".join(missing_names)
                            + ", but its own quotations do not contain those complete identifiers. "
                            "A variant name does not establish the broader model family. Add a relevant identifying quotation "
                            "to this claim or narrow/remove it; other source passages are not attached support.")[:600])
                        method = "named_attribution_guard"
                    elif direct[index]:
                        verdict = ClaimSupportPayload(verdict="supported", reason="Exact source quotation.")
                        method = "exact_quote"
                    elif cache_key in support_cache:
                        verdict = support_cache[cache_key]
                        audit["cache_hits"] += 1
                        method = "cached_isolated_review"
                    else:
                        # The judge must not see neighbouring claims, titles, the
                        # question, or unquoted parts of the source block. These
                        # could supply precisely the support missing from this card.
                        verdict = await self.provider.generate(_prompt("claim_support_v1"), {
                            "task": "verify_single_claim", "claim_index": index, "claim": claim_data,
                        }, ClaimSupportPayload)
                        await self._check_cancel(check_cancel)
                        support_cache[cache_key] = verdict
                        audit["claim_model_calls"] += 1
                        audit["model_checked"] = True
                        method = "isolated_review"
                    reviews.append({"claim_index": index, **verdict.model_dump(), "method": method})
                    if verdict.verdict == "unsupported":
                        problems.append(f"claim[{index}]: {verdict.reason or 'The quoted evidence does not support the whole claim.'}")
                proposed = [section.model_dump(include={"heading", "proposed_work"})
                            for section in getattr(result, "sections", [])]
                all_direct = all(direct) and not result.message and not any(item["proposed_work"] for item in proposed)
                # Overall coverage is separate and cannot override a rejected card.
                if not problems and not all_direct:
                    await self._check_cancel(check_cancel)
                    coverage = await self.provider.generate(_prompt("coverage_v1"), {
                        "task": "verify_response_coverage", "request": payload.get("question") or payload.get("instruction", ""),
                        "response_kind": payload["task"], "response_message": result.message,
                        "proposed_sections": proposed,
                        "selected_document_ids": payload.get("selected_document_ids", required_documents or []),
                        "claims": [{"claim_index": index, **claim.model_dump()} for index, claim in enumerate(claims)],
                        "evidence": [block.model_dump() for block in evidence],
                    }, CoveragePayload)
                    await self._check_cancel(check_cancel)
                    audit["model_checked"] = True
                    audit["coverage_model_calls"] += 1
                    problems.extend("Uncited statement outside claims: " + item for item in coverage.unverified_statements)
                    problems.extend("Unanswered request aspect: " + item for item in coverage.missing_aspects)
            audit["attempts"].append({"attempt": attempt + 1, "problems": problems, "reviews": reviews})
            await self._check_cancel(check_cancel)
            if not problems:
                audit["status"] = "passed" if claims else "no_asserted_claims"
                return result, used, audit
            if attempt == 1:
                raise ModelOutputError("回答或引文复核未通过，纠正后仍缺乏可靠支持；结果已拒绝。" + problems[0][:200])
            previous = result.model_dump()
            subset_repair = None
            # A short review can keep a useful, fully cited subset. Freeze it
            # before the sole repair instead of trusting the model to remove a
            # rejected assertion. Never salvage malformed citations or omit an
            # entire selected document, and still run the coverage gate below.
            if payload.get("task") == "review" and isinstance(result, SynthesisPayload) and len(reviews) == len(claims):
                kept_indices = [item["claim_index"] for item in reviews if item["verdict"] == "supported"]
                rejected_indices = [item["claim_index"] for item in reviews if item["verdict"] == "unsupported"]
                kept = [claims[index] for index in kept_indices]
                kept_sources = {citation.evidence_id for claim in kept for citation in claim.citations}
                kept_documents = {block.document_id for block in evidence if block.evidence_id in kept_sources}
                if rejected_indices and kept and (not required_documents or kept_documents == set(required_documents)):
                    supported_review_claims = Counter(json.dumps(claim.model_dump(), sort_keys=True, ensure_ascii=False) for claim in kept)
                    offset, retained = 0, set(kept_indices)
                    for section in previous["sections"]:
                        original = section["claims"]
                        section["claims"] = [claim for index, claim in enumerate(original, offset) if index in retained]
                        offset += len(original)
                    subset_repair = {
                        "mode": "preserve_supported_review_claims",
                        "retained_original_indices": kept_indices,
                        "removed_original_indices": rejected_indices,
                        "rule": "The previous_result below contains ONLY independently supported claims. Keep every retained claim text and citation ID exactly, once each. Do not add, rewrite or reinstate any claim. You may reorganize sections. Explain all requested aspects not answered by this subset in message as limitations of the supplied excerpts, without asserting new facts. Coverage will still be checked; return insufficient_evidence if this subset cannot form a useful review.",
                    }
                    audit["review_subset_repair"] = {key: value for key, value in subset_repair.items() if key != "rule"}
            previous_claims = previous.get("claims", []) + [claim for section in previous.get("sections", [])
                                                           for claim in section.get("claims", [])]
            for claim in previous_claims:
                for citation in claim["citations"]:
                    if citation.get("quote_id"):
                        citation["quote"] = ""  # Keep the repair request on the ID-selection protocol.
            current_payload = {**payload, "correction": {
                "previous_result": previous, "problems": problems,
                "rule": "Fix only against the original supplied evidence. Select citation_spans by evidence_id and quote_id; leave quote empty. Preserve qualifications and narrow claims. "
                        "Each claim is checked in isolation using ONLY its own quotations. Add missing supporting passage IDs to that claim itself, "
                        "including explicit model attribution, or narrow/split the claim. Adjacent claims cannot lend their citations. "
                        "Preserve unchanged supported claims. Use multiple citations when necessary. Do not fill gaps from memory; explicitly state missing aspects or return insufficient_evidence.",
            }}
            if subset_repair is not None:
                current_payload["correction"]["supported_subset"] = subset_repair
                current_payload["correction"]["rule"] = subset_repair["rule"]
        raise ModelOutputError("未获得可核验结果。")

    async def answer(self, question: str, document_ids: list[str], *,
                     mode: Literal["qa", "compare"] = "qa", check_cancel=None) -> dict:
        self._require_model()
        if mode not in {"qa", "compare"}:
            raise LibraryError("问答模式无效。")
        document_ids = self._validate_documents(document_ids)
        if mode == "compare" and len(document_ids) < 2:
            raise LibraryError("跨文献比较需要至少两篇文献。")
        retrieval = await self.retrieve_for_question(question, document_ids, limit=max(12, len(document_ids)),
                                                     check_cancel=check_cancel)
        evidence = retrieval["evidence"]
        retrieval_audit = {key: value for key, value in retrieval.items() if key not in {"evidence", "trace", "_opening_candidates"}}
        represented = {block.document_id for block in evidence}
        missing = [value for value in document_ids if value not in represented]
        trace = retrieval["trace"]
        if not evidence or (mode == "compare" and missing):
            return {"status": "insufficient_evidence", "answer": "现有可用文献中未找到足够证据，无法可靠回答。",
                    "claims": [], "evidence": [block.model_dump() for block in evidence],
                    "sources": [], "missing_document_ids": missing, "prompt_version": PROMPT_VERSION,
                    "retrieval": retrieval_audit,
                    "trace": trace + [self._trace("decline", "没有足够的匹配原文；未调用回答生成模型。")],
                    "verification": "no_matching_evidence"}
        await self._check_cancel(check_cancel)
        result, used, grounding = await self._generate_grounded(_prompt("qa_v5"), {
            "task": mode, "question": question, "selected_document_ids": document_ids,
            "evidence": [block.model_dump() for block in evidence],
        }, AnswerPayload, evidence, check_cancel=check_cancel,
            required_documents=document_ids if mode == "compare" else None)
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
                "retrieval": retrieval_audit,
                "grounding": grounding,
                "trace": trace + [self._trace("model", "已调用配置的模型生成结构化回答。"),
                    self._trace("validate", "已核验来源与原文引句，并对改述论断执行模型支持度复核；复核不等于正确性证明。")],
                "verification": "citation_membership_quote_match_and_bounded_semantic_review; correctness_not_guaranteed"}

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
                             progress: Callable[[dict], Any] | None = None, check_cancel=None) -> dict:
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
            result = await translate_protected_chunk(self.provider, chunk, target_language,
                                                     index + 1, len(chunks), check_cancel=check_cancel)
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
                    self._trace("translate", f"已调用模型翻译{len(chunks)}个片段，并核验保留标记、数字及目标语言。")],
                "verification": "protected_spans_and_numeric_preservation; target_language_sanity; translation_quality_not_guaranteed"}

    async def translate_block(self, block: Evidence | dict, *,
                              target_language: Literal["zh", "en"] = "zh", check_cancel=None) -> dict:
        self._require_model()
        source = self._canonical_block(block)
        translated = await self.translate_text(source.text, target_language=target_language,
                                               check_cancel=check_cancel)
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
                         *, instruction: str = "", check_cancel=None) -> dict:
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
        retrieval = await self.retrieve_for_question(query, document_ids, limit=max(12, len(document_ids)),
                                                     expansion="auto" if instruction.strip() else "off",
                                                     check_cancel=check_cancel, include_attribution_context=True)
        evidence = retrieval["evidence"]
        retrieval_audit = {key: value for key, value in retrieval.items() if key not in {"evidence", "trace", "_opening_candidates"}}
        # Reviews may include a selected document's opening passage when none of
        # its blocks matched headings. Reuse the same pre-await run snapshot;
        # a newly completed parse must not enter through the fallback path.
        if kind == "review":
            represented = {block.document_id for block in evidence}
            if represented != set(document_ids):
                for candidate in retrieval["_opening_candidates"]:
                    if candidate.document_id not in represented and len(evidence) < MAX_EVIDENCE:
                        evidence.append(candidate)
        trace = retrieval["trace"] + [self._trace("retrieve_scope", f"本次生成使用{len(evidence)}个选定文献片段，未自动核验外部最新研究。")]
        limitations = ["仅基于所选文献的有限检索片段，未验证覆盖全文。",
                       "引用校验验证来源归属与原文引句，不等于自动证明全部推论正确。"]
        if kind == "future_work":
            limitations.append("未进行外部最新文献检索；是否已解决及新颖性均为证据不足，需后续研究核验。")
        if not evidence:
            return {"kind": kind, "status": "insufficient_evidence", "content": "未找到足够的相关原文，无法可靠生成。",
                    "sections": [], "sources": [], "references": [], "architecture": None,
                    "limitations": limitations, "prompt_version": PROMPT_VERSION, "trace": trace,
                    "retrieval": retrieval_audit,
                    "future_work_verification": "evidence_insufficient" if kind == "future_work" else None}
        await self._check_cancel(check_cancel)
        result, used, grounding = await self._generate_grounded(_prompt("synthesis_v5"), {
            "task": kind, "instruction": instruction, "evidence": [block.model_dump() for block in evidence],
            "external_freshness_verified": False,
        }, SynthesisPayload, evidence, check_cancel=check_cancel,
            required_documents=document_ids if kind == "review" else None,
            required_sections={"abstract", "introduction", "related work"} if kind == "draft" else None)
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
                "retrieval": retrieval_audit,
                "grounding": grounding,
                "trace": trace + [self._trace("model", "已调用模型生成分节内容。"),
                    self._trace("validate", "已回填并核验原文引句，对改述论断执行支持度复核，来源清单取自本地文献记录。")],
                "future_work_verification": "evidence_insufficient" if kind == "future_work" else None,
                "search_time_range": None,
                "verification": "citation_membership_quote_match_and_bounded_semantic_review; bibliographic_details_not_verified"}


_PRESERVATION_MARKER = re.compile(r"⟪K[a-f0-9]{12}_\d+⟫")
# Undelimited mathematics must be bounded by its syntax, never by the line:
# a paragraph containing E = mc^2 still has prose that needs translation.
_MATH_NAME = r"(?:\\[A-Za-z]+|[A-Za-zα-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω₀-₉²³]*|[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
_MATH_OPERATOR = r"[+\-*/^=<>≤≥≈≠±×÷⋅]"
_MATH_SIMPLE = rf"{_MATH_NAME}(?:[ \t]*{_MATH_OPERATOR}[ \t]*{_MATH_NAME})*"
_MATH_GROUP = rf"(?:\({_MATH_SIMPLE}(?:[ \t]*,[ \t]*{_MATH_SIMPLE})*\)|\{{{_MATH_SIMPLE}\}})"
_MATH_ATOM = rf"(?:{_MATH_NAME}(?:{_MATH_GROUP})+|{_MATH_GROUP}|{_MATH_NAME})"
_RAW_MATH = (
    rf"(?<![A-Za-z0-9_]){_MATH_ATOM}(?:[ \t]*{_MATH_OPERATOR}[ \t]*{_MATH_ATOM})+"
    rf"|[∑∏∫√][ \t]*{_MATH_ATOM}"
)
_NON_MATH_SPAN = re.compile(
    r"\\[A-Za-z]+(?:\{[^{}]*\})*|[α-ωΑ-Ω∑∏∫√≤≥±]|"
    r"[+-]?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?(?:%|‰)?"
)
_PROTECTED_SPAN = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|"
    r"\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|"
    r"https?://[^\s)]+|\[[^\]\n]{1,300}\]|"
    rf"(?P<raw_math>{_RAW_MATH})|{_NON_MATH_SPAN.pattern}",
    re.MULTILINE,
)


def protect_text(text: str) -> tuple[str, dict[str, str]]:
    namespace = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    replacements = {}
    def replace(match):
        span = match.group(0)
        if (match.lastgroup == "raw_math" and not re.search(r"[=+<>≤≥≈≠±×÷⋅*/^∑∏∫√]", span)
                and not re.search(r"[ \t]-[ \t]", span)
                and any(len(word) > 1 for word in re.findall(r"[A-Za-z]+", span))):
            # A hyphenated term such as state-of-the-art is prose. Still
            # protect embedded values in terms such as model-12.
            return _NON_MATH_SPAN.sub(replace, span)
        token = f"⟪K{namespace}_{len(replacements)}⟫"
        replacements[token] = span
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


async def translate_protected_chunk(provider, chunk: str, target_language: str,
                                    segment: int, total_segments: int, check_cancel=None) -> TranslationPayload:
    """Shared direct/queued translation protocol with one bounded language repair."""
    request = {"target_language": target_language, "text": chunk,
               "segment": segment, "total_segments": total_segments}
    for attempt in range(2):
        if check_cancel is not None:
            value = check_cancel()
            if inspect.isawaitable(value):
                await value
        result = await provider.generate(_prompt("translation_v1"), request, TranslationPayload)
        # Corrupt markers must fail immediately, never be silently repaired.
        validate_preserved_tokens(chunk, result.translated_text)
        try:
            validate_translation_language(chunk, result.translated_text, target_language)
        except ModelOutputError:
            if attempt:
                raise
            request["translation_feedback"] = (
                "The previous response left source-language prose untranslated. Translate all prose "
                "into the requested target language, retaining every opaque marker unchanged. "
                "Do not add explanations or facts."
            )
        else:
            return result
    raise ModelOutputError("翻译未通过目标语言校验，结果已拒绝。")


def validate_translation_language(source: str, translated: str, target_language: str) -> None:
    """Reject obvious untranslated prose; this is not a semantic quality score."""
    source_prose = _PRESERVATION_MARKER.sub("", source)
    translated_prose = _PRESERVATION_MARKER.sub("", translated)
    source_han = re.findall(r"[\u3400-\u9fff]", source_prose)
    translated_han = re.findall(r"[\u3400-\u9fff]", translated_prose)
    source_words = re.findall(r"\b[A-Za-z]{2,}\b", source_prose)
    if not source_han and not source_words:
        return  # Formula/code-only content is valid without translated prose.
    if not re.search(r"[A-Za-z\u3400-\u9fff]", translated_prose):
        raise ModelOutputError("翻译未返回可用的正文，结果已拒绝。")
    if target_language == "en" and source_han:
        # A few named entities can remain; most Chinese prose must not.
        if len(translated_han) >= max(2, math.ceil(len(source_han) * 0.2)):
            raise ModelOutputError("译文仍含较多未翻译的中文，未满足英文目标语言要求，结果已拒绝。")
    has_english_prose = bool(re.search(
        r"\b(?:the|is|are|was|were|has|have|with|for|and|from|we|our|this|that|"
        r"use|uses|using|can|not|in|on|to|as|it|by|at|of)\b", source_prose, re.I))
    if target_language == "zh" and len(source_words) >= 3 and has_english_prose and not translated_han:
        raise ModelOutputError("译文未包含中文正文，未满足中文目标语言要求，结果已拒绝。")


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
