"""Bounded, dimension-specific searches over an already frozen source snapshot.

Query vocabulary is a search aid, never an assertion about a selected paper.
Each hit and context paragraph retains its own canonical identity and quotation.
"""
from __future__ import annotations

import re


MAX_REVIEW_EVIDENCE = 96


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


def request_aspects(request: str, expanded_queries: list[dict]) -> list[str]:
    """Reserve search slots for explicit common dimensions, without adding facts."""
    aspects = []
    for pattern, label in [
        (r"结构|架构|architecture|structure", "模型结构"),
        (r"训练目标|预训练任务|训练任务|training objectiv|training task|loss function", "训练目标"),
        (r"外部知识|知识来源|语料来源|external knowledge|knowledge source", "外部知识的作用"),
    ]:
        if re.search(pattern, request, re.I):
            aspects.append(label)
    # Unfamiliar dimensions still use the actual query expansion anchors.
    if not aspects:
        aspects = list(dict.fromkeys(item["source_phrase"] for item in expanded_queries
                                    if item.get("source_phrase")))[:6]
    return aspects


def supplement_review_evidence(service, snapshot, current, expanded_queries, *,
                               request: str, missing_pairs=None, max_evidence=MAX_REVIEW_EVIDENCE):
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
        rankings = []
        for query in queries:
            structural = service._section_candidates(query, source, index, document)
            lexical = [item for item in service._rank_lexical(query, source, index)
                       if item.block_id not in index[3]
                       and service._evidence_quality(item, query, index[3]) >= 0.8]
            # Keep several different explanatory paragraphs, not just one name hit.
            # Give the corpus/objective phrase its own lexical slot. A broad
            # "pre-training" heading must not crowd out the actual corpus paragraph.
            rankings.extend([lexical[:4], structural[:3]])
        seeds, seed_ids = [], set()
        for position in range(max((len(ranking) for ranking in rankings), default=0)):
            for ranking in rankings:
                if position < len(ranking) and ranking[position].block_id not in seed_ids:
                    seeds.append(ranking[position])
                    seed_ids.add(ranking[position].block_id)
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
