"""Bounded, dimension-specific searches over an already frozen source snapshot.

Query vocabulary is a search aid, never an assertion about a selected paper.
Each hit and context paragraph retains its own canonical identity and quotation.
"""
from __future__ import annotations

import re


MAX_REVIEW_EVIDENCE = 96


def is_training_aspect(aspect):
    return bool(re.search(r"训练|损失|\b(?:pre[- ]?)?train\w*\b|\bloss\b", aspect, re.I))


def is_information_role_comparison(request, aspect):
    """A corpus role cannot answer an explicit yes/no retrieval question."""
    topic = r"外部知识.{0,6}(?:作用|来源)|知识来源|信息来源|knowledge\s+(?:source|role|use|usage)|information\s+(?:source|role)"
    aspect_topic = r"外部知识|知识来源|信息来源|external\s+knowledge|knowledge\s+(?:source|role|use|usage)|information\s+(?:source|role)"
    return bool(re.search(topic, request, re.I) and re.search(aspect_topic, aspect, re.I)
                and not re.search(r"是否|有无|是不是|whether|yes.?no|does.{0,40}retriev", request, re.I))


def _positive_training_data_predicate(text):
    plain = " ".join(text.split())
    return bool(re.search(
        r"(?:预训练|训练)(?:阶段|时|中|过程|信息|的)?(?:使用|采用|利用|基于|来自|源于)"
        r"|(?:预训练|训练)(?:的)?(?:数据|语料)(?:来源)?(?:为|是|包括|来自|源于)"
        r"|(?:使用|采用|用)[^，,。；;]{1,100}(?:作为(?:预训练|训练)(?:数据|语料)|用于(?:预训练|训练)|进行(?:预训练|训练)|来(?:预训练|训练))"
        r"|(?:语料|数据集)[^，,。；;]{0,30}上(?:预训练|训练)"
        r"|\b(?:pre[- ]?)?train(?:ed|s|ing)?\b[^,.;]{0,70}\b(?:on|using|with)\b"
        r"|\b(?:pre[- ]?)?training\s+(?:corpus|data)\s+(?:is|includes|contains|comes)\b", plain, re.I))


def _quoted_data_usage(text):
    plain = " ".join(text.split())
    return (_positive_training_data_predicate(plain) or bool(re.search(
        r"\b(?:we\s+)?(?:use|used|uses)\b[^.;]{0,160}(?:corpus|corpora|data\s*set)"
        r"|\b(?:pre[- ]?)?training\s+corpus\b.{0,50}\b(?:use|used|uses|contains|includes)\b"
        r"|(?:使用|采用|用)[^。；;]{0,100}(?:语料|数据集)", plain, re.I)))


def verified_training_corpus_role(service, identity, document, names):
    """Organize an ALREADY supported own-card training/data relation by its stage.

    This does not verify facts, infer retrieval absence or accept a dataset name
    alone. The caller must only supply claims that passed isolated fact support.
    """
    text = identity["text"]
    if not service._identifier_tokens(text).intersection(names):
        return None
    if re.search(r"未使用|没有|不使用|从未|未(?:用|在|曾)|不在|\b(?:not|never)\s+(?:(?:been|directly)\s+)?(?:train\w*|used)|no\s+training|without\s+data", text, re.I):
        return None
    if not (re.search(r"训练|\b(?:pre[- ]?)?train\w*\b", text, re.I)
            and re.search(r"语料|数据集|训练数据|数据来源|corpus|corpora|data\s*set|training\s+data", text, re.I)):
        return None
    if not _positive_training_data_predicate(text):
        return None
    own = [cite for cite in identity["citations"] if cite["document_id"] == document]
    subjects = [cite for cite in own if service._identifier_tokens(cite["quote"]).intersection(names)
                and directly_named_training(cite["quote"], names, service)]
    corpora = [cite for cite in own if re.search(r"corpus|corpora|data\s*set|training\s+data|语料|数据集|训练数据", cite["quote"], re.I)
               and _quoted_data_usage(cite["quote"])]
    if not subjects or not corpora:
        return None
    return {"document_id": document, "role": "training_corpus",
            "subject_citation_id": subjects[0]["citation_id"], "answer_citation_id": corpora[0]["citation_id"],
            "policy_version": "information-source-stage-v1.0",
            "interpretation": "A positively documented training-data role answers the broad source/stage comparison; it does not assert inference-time retrieval or its absence."}


def _task_training_relation(text):
    return bool(re.search(r"\btrain(?:ed|s)?\s+for\s+(?!as\b)|\btrain(?:ed|s)?.{0,100}\b(?:minimiz|predict|objective|loss)",
                          " ".join(text.split()), re.I))


def explicit_training_task_span(service, text, names):
    plain = " ".join(text.split())
    return (directly_named_training(text, names, service)
            and bool(re.search(r"\btrain(?:ed|s)?\s+for\s+(?!as\b|only\b|at least\b|\d)", plain, re.I))
            and bool(re.search(r"-to-|\b(?:task|translat\w*|predict\w*|classif\w*|generat\w*|parsing|tagging|recognition|comprehension)\b", plain, re.I)))


def is_prediction_task_comparison(request, aspect):
    if not is_training_aspect(aspect) or re.search(r"损失|loss|公式|equation", aspect, re.I):
        return False
    if not re.search(r"预测内容|预测目标|训练任务|prediction\s+task|training\s+task", request, re.I):
        return False
    positive = [part for part in re.split(r"[。；;，,]", request) if not re.search(
        r"不要|勿|不臆测|不需要|未写出|do not|without invent|avoid", part, re.I)]
    return not any(re.search(r"损失函数|loss\s+function|数学公式|equation", part, re.I) for part in positive)


def verified_single_training_task_role(service, identity, document, names, requirement=None):
    if requirement or not service._identifier_tokens(identity["text"]).intersection(names):
        return None
    if not re.search(r"(?:在|为|针对|以)[^。；;，,]{0,50}(?:任务|预测|翻译|分类|生成)[^。；;，,]{0,20}训练|训练(?:目标|任务)|\btrain(?:ed|s)?\b[^.;,]{0,60}\b(?:for|to)\b", identity["text"], re.I):
        return None
    if re.search(r"未在|不在|没有|not\s+trained|never\s+trained", identity["text"], re.I):
        return None
    own = [cite for cite in identity["citations"] if cite["document_id"] == document
           and explicit_training_task_span(service, cite["quote"], names)]
    if not own:
        return None
    return {"document_id":document,"role":"explicit_training_task",
            "subject_citation_id":own[0]["citation_id"],"answer_citation_id":own[0]["citation_id"],
            "policy_version":"explicit-single-training-task-v1.0",
            "interpretation":"An already verified named training-purpose statement answers an explicit prediction-task request; no loss formula is inferred."}


def training_task_requirements(service, source, names):
    """Recognize only an explicit named multi-task declaration plus numbered tasks."""
    ordered = service._reading_order(source)
    catalog = service._citation_spans(service._excerpts(ordered), omit_trailing_fragment=True, whole_units_only=True)
    numbers = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
               **{str(n): n for n in range(2, 7)}}
    for pos, block in enumerate(ordered):
        for quote_id, text in catalog[block.evidence_id].items():
            plain = " ".join(text.split())
            count = re.search(r"\b(two|three|four|five|six|[2-6])\b.{0,30}\btasks?\b", plain, re.I)
            if not count or not directly_named_training(text, names, service):
                continue
            tasks = []
            nearby = ordered[pos+1:pos+32]
            for offset, candidate in enumerate(nearby):
                if candidate.page > block.page + 2:
                    break
                heading = re.match(r"\s*Task\s*#?\s*([1-6])\s*:\s*([^\n]{1,100})", candidate.text, re.I)
                if not heading:
                    continue
                group = [candidate]
                for following in nearby[offset+1:offset+5]:
                    if (re.match(r"\s*Task\s*#?\s*[1-6]\s*:", following.text, re.I)
                            or following.page > candidate.page + 1 or service._heading(following)):
                        break
                    group.append(following)
                tasks.append({"number": int(heading[1]), "label": heading[2].strip(),
                              "evidence_ids": [item.evidence_id for item in group],
                              "preferred_evidence_ids": [candidate.evidence_id] + [item.evidence_id for item in group[1:]
                                  if re.search(r"predict|target|classif|minimiz|recover|detect|预测", item.text, re.I)][:1]})
            total = numbers[count[1].lower()]
            if [task["number"] for task in tasks[:total]] == list(range(1, total+1)):
                return {"scope_evidence_id": block.evidence_id, "scope_quote_id": quote_id,
                        "subject_identifiers": sorted(names),
                        "scope_text": text, "tasks": tasks[:total],
                        "rule": "Each training answer must quote this named task scope and its own prediction definitions for ALL listed tasks; labels alone do not answer them."}
    return None


def has_training_relation(service, claim, names):
    """A decoding computation alone cannot establish an actual training task."""
    text = " ".join(cite.quote for cite in claim.citations)
    return (directly_named_training(text, names, service) or
            bool(re.search(r"\b(?:pre[- ]?)?training\s+(?:objective|task|loss)|\b(?:pre[- ]?)?training.{0,45}\bpredict|\bobjective\b|\bminimiz\w*\b|训练目标|训练任务|训练损失", text, re.I)))


def training_eligibility(service, claim, names, requirement=None):
    missing = [] if has_training_relation(service, claim, names) else ["actual training task/objective relation"]
    if (re.search(r"softmax|线性变换|linear\s+(?:transfor|projection)", claim.text, re.I)
            and not re.search(r"训练|目标|train|objective|learning task", claim.text, re.I)):
        missing.append("an actual training target in the claim, rather than output computation alone")
    if requirement:
        own = {(cite.evidence_id, cite.quote_id) for cite in claim.citations}
        exact_scope=(requirement["scope_evidence_id"], requirement["scope_quote_id"]) in own
        # A resolved longer original passage may contain the same frozen scope
        # sentence. Its alternate catalog ID must not turn valid context into a gap.
        contained_scope=any(cite.evidence_id==requirement['scope_evidence_id'] and
            ' '.join(requirement['scope_text'].split()) in ' '.join(cite.quote.split())
            for cite in claim.citations)
        if not (exact_scope or contained_scope):
            missing.append("named multiple-task scope declaration")
        ids = {cite.evidence_id for cite in claim.citations}
        missing.extend(task["label"] for task in requirement["tasks"] if not ids.intersection(task["evidence_ids"]))
    return missing


def focus_review_evidence(service, evidence, aspect, names, *, max_blocks=12):
    """Select a small canonical reading set, never turn search labels into facts."""
    objective = bool(re.search(r"训练|目标|损失|train|objective|loss", aspect, re.I))
    information = bool(re.search(r"知识|语料|knowledge|external|corpus", aspect, re.I))
    ranked = []
    for position, block in enumerate(evidence):
        text = " ".join(block.text.split())
        named = bool(service._identifier_tokens(block.text).intersection(names))
        direct = directly_named_training(block.text, names, service)
        corpus = bool(re.search(r"corpus|corpora|training\s+(?:data|set)|data\s*set|sentence\s+pairs|\btrain(?:ed|s)?\s+on\b|语料|训练集", text, re.I))
        memory = bool(re.search(r"retriev|non.parametric|external\s+knowledge|document\s+index|外部知识|检索", text, re.I))
        target = bool(re.search(r"\b(?:pre.?training|training)\s+(?:objective|task|loss)|\b(?:predict|prediction|predicting|masked|likelihood|minimiz|maximiz)|训练目标|预测", text, re.I))
        task_relation = direct and _task_training_relation(text)
        if objective:
            priority = 0 if task_relation else 1 if direct else 2 if target else 3 if corpus else 4
        elif information:
            priority = 0 if named and (corpus or memory) else 1 if corpus or memory else 2 if direct else 3
        else:
            priority = 0 if named else 1
        ranked.append((priority, position, block))
    ranked.sort(key=lambda item: item[:2])
    # When there are actual training/source candidates, unrelated architecture
    # passages cannot overwhelm them. An unfamiliar aspect keeps its normal pool.
    if objective and any(priority < 3 for priority, _, _ in ranked):
        ranked = [item for item in ranked if item[0] < 4]
    elif information and any(priority < 2 for priority, _, _ in ranked):
        ranked = [item for item in ranked if item[0] < 3]
    return [block for _, _, block in ranked[:max_blocks]]


def subject_hints(service, snapshot):
    """Lexical identity hints only, never a source of factual attribution."""
    hints = {}
    for document in dict.fromkeys(block.document_id for block in snapshot):
        own = [block for block in snapshot if block.document_id == document]
        names = service._named_attribution_identifiers(service._citation_spans(service._excerpts(own)))
        title_names = service._identifier_tokens(own[0].document_title) & names
        hints[document] = (sorted(title_names)[:2] if title_names else sorted(names, key=lambda name: (
            -sum(name in service._identifier_tokens(block.text) for block in own), name))[:1])
    return hints


def directly_named_training(text, names, service):
    """Identify candidate wording, not whether it entails a generated claim."""
    if not service._identifier_tokens(text).intersection(names):
        return False
    plain = " ".join(text.split())
    return bool(re.search(r"\b(?:pre[- ]?)?train(?:ed|s|ing)?\s+(?:on|for|using|with)\s+(?!as\b|only\b|at least\b|\d)", plain, re.I)
                or re.search(r"\b(?:pre[- ]?)?train(?:ed|s|ing)?\s+(?:the\s+)?(?:"+
                    "|".join(re.escape(name) for name in names)+r")\b", plain, re.I)) if names else False


def aspect_queries(aspect: str, expanded_queries: list[dict]) -> list[str]:
    groups = [aspect]
    if re.search(r"结构|架构|architecture|structure", aspect, re.I):
        groups += ["model architecture encoder decoder attention mechanism"]
    if re.search(r"训练|目标|损失|任务|train|objective|loss", aspect, re.I):
        groups += ["pretraining training objective prediction loss likelihood",
                   "training data supervised target sequence pairs",
                   "train minimize negative log likelihood",
                   "masked language model predict tokens"]
    if re.search(r"知识|外部|语料|knowledge|external|corpus|corpora", aspect, re.I):
        groups += ["external knowledge retrieval memory index",
                   "pretraining training data corpus"]
    groups += [item["text"] for item in expanded_queries if item.get("source_phrase")
               and (item["source_phrase"].casefold() in aspect.casefold()
                    or aspect.casefold() in item["source_phrase"].casefold())]
    return list(dict.fromkeys(groups))


_REQUEST_BOUNDARY = re.compile(r"[。！？；;!?\n]+|\.(?=\s|$)|但是|不过|但|而是|\bbut\b", re.I)
_POSITIVE_ACTION = re.compile(
    r"(?:(?:请|需要|然后|并|只|仅)\s*)*(?:比较|讨论|说明|解释|描述|介绍|分析|回答|给出|列出|展示|写)"
    r"|\b(?:(?:please|then|only)\s+)*(?:compare|discuss|explain|describe|analy[sz]e|give|list|show|write|cover)\b", re.I)
_REQUEST_EXCLUSION = re.compile(
    r"不要|不用|无需|不必|不需要|不要求|不讨论|不包括|不涉及|不包含|不谈|不提|不介绍|不考虑|不展开|勿"
    r"|\b(?:do\s+not|don['’]t|no\s+need(?:\s+to)?|avoid|omit|skip|exclude"
    r"|without\s+(?:discussing|comparing|covering|including|describing|mentioning))\b", re.I)
_FACTUAL_QUESTION = re.compile(r"是否|能否|为什么|为何|如何|怎样|怎么|有没有|有无|是不是|\b(?:whether|why|how)\b", re.I)


def positive_request_parts(request: str) -> list[str]:
    """Keep requested clauses and exclude explicit opt-outs, including their lists."""
    parts = []
    for sentence in _REQUEST_BOUNDARY.split(request):
        excluded = False
        for clause in re.split(r"[，,]", sentence):
            clause = clause.strip()
            factual = bool(_FACTUAL_QUESTION.search(clause))
            if not clause or (excluded and not (_POSITIVE_ACTION.match(clause) or factual)):
                continue
            exclusion = _REQUEST_EXCLUSION.search(clause)
            prefix = clause[:exclusion.start()] if exclusion else ""
            excluded = exclusion is not None and not _FACTUAL_QUESTION.search(prefix)
            if excluded:
                # Keep a preceding instruction, but not a topic followed by "不用讨论".
                if _POSITIVE_ACTION.search(prefix):
                    parts.append(prefix.strip())
            else:
                parts.append(clause)
    return parts


def request_aspects(request: str, expanded_queries: list[dict]) -> list[str]:
    """Reserve search slots for explicit common dimensions, without adding facts."""
    positive = " ".join(positive_request_parts(request))
    aspects = []
    for pattern, label in [
        (r"结构|架构|architecture|structure", "模型结构"),
        (r"训练目标|预训练任务|训练任务|training objectiv|training task|loss function", "训练目标"),
        (r"外部知识|知识来源|语料来源|external knowledge|knowledge source", "外部知识的作用"),
    ]:
        if re.search(pattern, positive, re.I):
            aspects.append(label)
    # Unfamiliar dimensions still use the actual query expansion anchors.
    if not aspects:
        normalized = " ".join(positive.casefold().split())
        has_exclusions = bool(_REQUEST_EXCLUSION.search(request))
        aspects = list(dict.fromkeys(item["source_phrase"] for item in expanded_queries
                                    if item.get("source_phrase")
                                    and (not has_exclusions
                                         or " ".join(item["source_phrase"].casefold().split()) in normalized)))[:6]
    return aspects


def supplement_review_evidence(service, snapshot, current, expanded_queries, *,
                               request: str, missing_pairs=None, max_evidence=MAX_REVIEW_EVIDENCE,
                               subject_names=None, repair_queries=None):
    """Search every target fairly; never reopen the database after a model await."""
    documents = list(dict.fromkeys(item.document_id for item in snapshot))
    aspects = request_aspects(request, expanded_queries)
    pairs = (list(dict.fromkeys(missing_pairs)) if missing_pairs is not None else
             [(document, aspect) for document in documents for aspect in aspects])
    selected = list(current)
    seen = {item.block_id for item in selected}
    queues, observations = [], []
    for document, aspect in pairs:
        source = service._reading_order([item for item in snapshot if item.document_id == document])
        if not source:
            continue
        index = service._lexical_index(source)
        queries = aspect_queries(aspect, expanded_queries)
        names = (subject_names or {}).get(document, [])
        if names:
            # Short queries preserve inflection-specific source wording. A long
            # generic objective query can rank a nameless formula above a directly
            # named task, while "trained" and "training" are distinct BM25 tokens.
            focused = (["trained", "training corpus", "pretraining data"]
                       if re.search(r"知识|knowledge|external|corpus", aspect, re.I) else
                       ["architecture", "encoder decoder", "attention"]
                       if re.search(r"结构|架构|architecture|structure", aspect, re.I) else
                       ["trained", "negative log likelihood", "prediction task"])
            queries = [" ".join(names) + " " + query for query in focused + queries] + queries
        queries = list(dict.fromkeys((repair_queries or {}).get((document, aspect), []) + queries))
        rankings = []
        for query in queries:
            structural = service._section_candidates(query, source, index, document)
            lexical = [item for item in service._rank_lexical(query, source, index)
                       if item.block_id not in index[3]
                       and service._evidence_quality(item, query, index[3]) >= 0.8]
            # Keep several different explanatory paragraphs, not just one name hit.
            # Give the corpus/objective phrase its own lexical slot. A broad
            # "pre-training" heading must not crowd out the actual corpus paragraph.
            named = [item for item in lexical if service._identifier_tokens(item.text).intersection(names)]
            if re.search(r"train|objective|训练|知识|knowledge|external|corpus", aspect, re.I):
                named.sort(key=lambda item: (not directly_named_training(item.text, names, service),
                    not _task_training_relation(item.text)))
            rankings.extend([named[:3], lexical[:4], structural[:3]])
        seeds, seed_ids = [], set()
        for position in range(max((len(ranking) for ranking in rankings), default=0)):
            for ranking in rankings:
                if position < len(ranking) and ranking[position].block_id not in seed_ids:
                    seeds.append(ranking[position])
                    seed_ids.add(ranking[position].block_id)
        # Reserve a real method/task declaration and a corpus definition before
        # broad headings and adjacent paragraphs consume the fair-search slots.
        # These are canonical candidates, not inferred facts or attribution.
        scope_anchors = [item for item in source if item.block_id not in index[3]
            and any(directly_named_training(text, names, service) and
                    re.search(r"\btasks?\b|\bobjectives?\b", text, re.I)
                    for text in service._citation_spans([item], omit_trailing_fragment=True)[item.evidence_id].values())]
        corpus_anchors = [item for item in source if item.block_id not in index[3]
            and re.search(r"\b(?:pre[- ]?)?training\s+corpus\b|\b(?:we\s+)?trained\s+on\b",
                          " ".join(item.text.split()), re.I)]
        corpus_anchors.sort(key=lambda item: not directly_named_training(item.text, names, service))
        anchors = (scope_anchors[:2] if re.search(r"训练|目标|train|objective|loss", aspect, re.I) else
                   corpus_anchors[:2] if re.search(r"知识|语料|knowledge|external|corpus", aspect, re.I) else [])
        requirement = training_task_requirements(service, source, names) if re.search(r"训练|目标|train|objective|loss", aspect, re.I) else None
        if requirement:
            ids = [requirement["scope_evidence_id"]] + [task["preferred_evidence_ids"][0] for task in requirement["tasks"]]
            ids += [identifier for task in requirement["tasks"] for identifier in task["preferred_evidence_ids"][1:]]
            by_id = {item.evidence_id: item for item in source}
            anchors = [by_id[identifier] for identifier in dict.fromkeys(ids)]
        if anchors:
            seeds = anchors + [item for item in seeds if item.block_id not in {anchor.block_id for anchor in anchors}]
        positions = {item.block_id: i for i, item in enumerate(source)}
        contexts = []
        for seed in seeds[:6]:
            position = positions[seed.block_id]
            for other in source[max(0, position - 1):position + 2]:
                if (other.block_id not in index[3] and abs(other.page-seed.page) <= 1
                        and (len(service._search_terms(other.text)) >= 6 or service._heading(other))):
                    contexts.append(other)
            # An explicit section introduction may identify the otherwise unnamed
            # method in the following paragraphs. It must still be cited by each card.
            for previous in range(position - 1, max(-1, position - 12), -1):
                if service._heading(source[previous]):
                    contexts.extend(source[previous:min(previous+2, len(source))])
                    break
        observations.append({"document_id": document, "aspect": aspect, "queries": queries,
                             "candidate_block_ids": [item.block_id for item in seeds],
                             "role_anchor_block_ids": [item.block_id for item in anchors],
                             "context_block_ids": list(dict.fromkeys(item.block_id for item in contexts))})
        queues.append(seeds[:6] + contexts + seeds[6:])
    # Round robin over document/dimension pairs bounds starvation, including
    # when one paper contains many mentions of the requested model names.
    while any(queues) and len(selected) < max_evidence:
        for queue in queues:
            while queue and queue[0].block_id in seen:
                queue.pop(0)
            if queue and len(selected) < max_evidence:
                item = queue.pop(0)
                seen.add(item.block_id)
                selected.append(item)
    added = selected[len(current):]
    return service._excerpts(selected), {
        "mode": "frozen_snapshot_dimension_search", "max_evidence": max_evidence,
        "added_block_ids": [item.block_id for item in added], "searches": observations,
        "selected_runs": {doc: next(item.run_id for item in snapshot if item.document_id == doc)
                          for doc in documents},
        "evidence_rule": "individual_canonical_blocks; context_is_not_automatic_fact_support",
    }
