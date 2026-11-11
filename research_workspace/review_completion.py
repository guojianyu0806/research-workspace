"""Add missing, independently checked review facts without rewriting sound cards."""
from __future__ import annotations

import re
import json

from .review_retrieval import (supplement_review_evidence, directly_named_training, focus_review_evidence,
                               training_eligibility, is_training_aspect, explicit_training_task_span,
                               is_prediction_task_comparison)


async def complete_review(service, result, evidence, retrieval, documents, instruction,
                          shared_state, *, check_cancel=None):
    from .intelligence import (MAX_ISOLATED_CLAIMS, ModelOutputError, ReviewCompletionPayload, ClaimSupportPayload,
                               Section, SynthesisPayload, _normalized, _prompt)

    audit = shared_state["audit"]
    completion = audit.setdefault("review_completion", {
        "prompt_version": "review-completion-v2.0", "max_rounds": 2, "max_model_calls": 24, "model_calls": 0,
        "policy": "preserve_verified_claims_add_missing_facts_then_reaudit", "rounds": [],
    })
    for round_number in range(3):
        await service._check_cancel(check_cancel)
        records = audit.get("review_coverage", [])
        record = records[-1] if records else {}
        gap_message = service._review_gap_message(record, documents, evidence)
        if result.status != "generated" or gap_message is None:
            raise ModelOutputError("综述未形成有效的逐项覆盖记录，不能作为完整综述返回。")
        missing = [(row["document_id"], row["aspect"]) for row in record["assessments"]
                   if not row["answer_claim_indices"]]
        if not missing:
            completion.update(status="complete", answered_pairs=len(record["assessments"]), missing_pairs=[])
            return result, evidence, audit
        if round_number == 2:
            raise ModelOutputError(f"已补充检索和原文核验，但仍有{len(missing)}项文献维度没有可靠答案；未将缺口说明计作完整综述。")

        subjects = {}
        for known in record["claim_catalog"]:
            for cite in known["citations"]:
                # These are only lexical hints taken from already verified cards.
                # They never establish a subject/property relation for a new card.
                names = service._named_attribution_identifiers(service._citation_spans(
                    [item for item in evidence if item.document_id == cite["document_id"]]))
                identifiers = service._identifier_tokens(known["text"]) & names
                subjects.setdefault(cite["document_id"], set()).update(identifiers)
        subjects = {doc: sorted(names)[:4] for doc, names in subjects.items()}
        for document, _ in missing:
            if subjects.get(document):
                continue
            own_snapshot = [block for block in retrieval["_snapshot_blocks"] if block.document_id == document]
            names = service._named_attribution_identifiers(service._citation_spans(service._excerpts(own_snapshot)))
            # A paper with no retained fact still needs a lexical identity hint.
            # Names are search candidates only; no metadata becomes claim proof.
            preferred = service._identifier_tokens(instruction + " " + own_snapshot[0].document_title) & names
            subjects[document] = (sorted(preferred) or sorted(names, key=lambda name: (
                -sum(name in service._identifier_tokens(block.text) for block in own_snapshot), name)))[:2]
        repair_queries = {}
        stop = {"the", "a", "an", "we", "our", "of", "to", "in", "on", "and", "or", "is", "are",
                "for", "with", "as", "that", "this", "by", "be", "was", "were", "it", "from", "at"}
        for document, aspect in missing:
            candidates = [known for attempt in audit["attempts"][-4:] for known in attempt.get("claims", [])
                if any(cite["document_id"] == document for cite in known["citations"])
                and any(review["claim_index"] == known["claim_index"] and review["verdict"] == "unsupported"
                        for review in attempt["reviews"])]
            for known in candidates[-4:]:
                terms = service._search_terms(" ".join(cite["quote"] for cite in known["citations"]))
                terms = [term for term in dict.fromkeys(terms) if re.fullmatch(r"[a-z][a-z0-9_-]*", term)
                         and term not in stop]
                if terms:
                    repair_queries.setdefault((document, aspect), []).append(
                        " ".join(subjects.get(document, []) + terms[:24]))
        cited_ids = {cite["evidence_id"] for known in record["claim_catalog"] for cite in known["citations"]}
        feedback_source_ids = {cite["evidence_id"] for attempt in audit["attempts"][-4:]
            for known in attempt.get("claims", []) for cite in known["citations"]
            if any(review["claim_index"] == known["claim_index"] and review["verdict"] == "unsupported"
                   for review in attempt["reviews"])}
        feedback_source_ids.update(cite["evidence_id"] for previous in completion["rounds"][-2:]
            for pair in previous.get("pair_generations", []) for rejected in pair.get("local_rejections", [])
            for cite in rejected["citations"])
        retained_sources = [block for block in evidence if block.evidence_id in cited_ids | feedback_source_ids]
        retired = [block.block_id for block in evidence if block.evidence_id not in cited_ids | feedback_source_ids]
        evidence, search = supplement_review_evidence(service, retrieval["_snapshot_blocks"], retained_sources,
            retrieval["queries"], request=instruction, missing_pairs=missing, subject_names=subjects,
            repair_queries=repair_queries)
        search["retired_uncited_block_ids"] = [identifier for identifier in retired
                                               if identifier not in {block.block_id for block in evidence}]
        shared_state["review_evidence"] = evidence
        catalog = service._citation_spans(evidence, omit_trailing_fragment=True, whole_units_only=True)
        frozen = [claim for section in result.sections for claim in section.claims]
        original_ids = [service._review_claim_identity(claim, evidence)["claim_id"] for claim in frozen]
        entry = {"round": round_number + 1, "missing_pairs": [
            {"document_id": doc, "aspect": aspect} for doc, aspect in missing],
            "retained_claim_ids": original_ids, "retrieval": search}
        completion["rounds"].append(entry)
        # Feedback must include why a proposed fact failed, not only the name of
        # the dimension it was supposed to fill. Otherwise retries repeat it.
        failed_feedback = [{"attempt": attempt["attempt"], "problems": attempt["problems"],
                            "rejected_claims": [claim for claim in attempt.get("claims", [])
                                if any(review["claim_index"] == claim["claim_index"]
                                       and review["verdict"] == "unsupported"
                                       for review in attempt["reviews"])]}
                           for attempt in audit["attempts"] if attempt["problems"]][-3:]
        failed_feedback.extend({"attempt": f"completion-local-{previous['round']}-{index}",
            "problems": rejected["problems"], "aspect": pair["aspect"], "rejected_claims": [rejected]}
            for previous in completion["rounds"][:-1][-2:]
            for index, pair in enumerate(previous.get("pair_generations", []))
            for rejected in pair.get("local_rejections", [])[-2:])
        entry["received_feedback_attempts"] = [item["attempt"] for item in failed_feedback]
        await service._check_cancel(check_cancel)
        additions = ReviewCompletionPayload()
        entry["pair_generations"] = []
        for document, aspect in missing:
            own_evidence = [block for block in evidence if block.document_id == document]
            own_names = service._named_attribution_identifiers(service._citation_spans(own_evidence))
            target_names = subjects.get(document, [])
            # Rank this one task's directly named paragraphs first. Full canonical
            # passages remain available; no automatically generated text is cited.
            ranked_ids = []
            for query in search["searches"]:
                if query["document_id"] == document and query["aspect"] == aspect:
                    ranked_ids = query["candidate_block_ids"]
                    break
            ranking = {identifier: position for position, identifier in enumerate(ranked_ids)}
            own_evidence.sort(key=lambda block: (not bool(service._identifier_tokens(block.text).intersection(target_names)),
                                                ranking.get(block.block_id, 10000), block.page))
            own_evidence = focus_review_evidence(service, own_evidence, aspect, target_names,
                                                 max_blocks=12 if round_number == 0 else 20)
            requirement = audit.get("training_requirements", {}).get(document) if is_training_aspect(aspect) else None
            if requirement:
                reserved = {requirement["scope_evidence_id"]} | {identifier for task in requirement["tasks"] for identifier in task["preferred_evidence_ids"]}
                anchors = [block for block in evidence if block.evidence_id in reserved]
                limit = max(12 if round_number == 0 else 20, len(anchors))
                own_evidence = anchors + [block for block in own_evidence if block.evidence_id not in reserved][:limit-len(anchors)]
            pair_catalog = {block.evidence_id: catalog[block.evidence_id] for block in own_evidence}
            minimal_task = None
            if is_prediction_task_comparison(instruction, aspect) and not requirement:
                minimal_task = next(({"evidence_id": block.evidence_id,"quote_id":identifier,"text":text}
                    for block in own_evidence for identifier,text in pair_catalog[block.evidence_id].items()
                    if explicit_training_task_span(service,text,target_names)),None)
                if minimal_task:
                    own_evidence = [block for block in own_evidence if block.evidence_id == minimal_task["evidence_id"]]
                    pair_catalog = {minimal_task["evidence_id"]:{minimal_task["quote_id"]:minimal_task["text"]}}
            own_feedback = [{**item, "rejected_claims": [claim for claim in item["rejected_claims"]
                if any(cite["document_id"] == document for cite in claim["citations"])]}
                for item in failed_feedback if not item.get("aspect") or item["aspect"]==aspect]
            own_feedback = [item for item in own_feedback if item["rejected_claims"]]
            name_spans = [{"evidence_id": block.evidence_id, "quote_id": identifier, "text": text,
                           "page": block.page, "block_id": block.block_id}
                          for block in own_evidence for identifier, text in pair_catalog[block.evidence_id].items()
                          if service._identifier_tokens(text).intersection(target_names)]
            direct = [{**block.model_dump(), **({"text":minimal_task["text"]} if minimal_task else {}), "citation_spans": [
                {"quote_id": identifier, "text": text} for identifier, text in pair_catalog[block.evidence_id].items()]}
                for block in own_evidence if directly_named_training(block.text, target_names, service)]
            pair_payload = {
                "task": "complete_review_missing_dimensions", "instruction": instruction,
                "missing_pairs": [{"document_id": document, "aspect": aspect}],
                "requested_aspects": record["requested_aspects"],
                "retained_claims": [known for known in record["claim_catalog"]
                                    if any(c["document_id"] == document for c in known["citations"])],
                "target_subject_search_hints": target_names,
                "required_training_tasks": requirement,
                "focused_training_predicate": minimal_task,
                "own_named_passages": name_spans,
                "direct_named_training_passages": direct[:8],
                "preferred_direct_relation_spans": [span for span in name_spans
                    if directly_named_training(span["text"], target_names, service)],
                "evidence": [{**block.model_dump(), **({"text":minimal_task["text"]} if minimal_task else {}), "citation_spans": [
                    {"quote_id": identifier, "text": text}
                    for identifier, text in pair_catalog[block.evidence_id].items()]} for block in own_evidence],
                "previous_rejected_fact_feedback": own_feedback,
            }
            if minimal_task:
                pair_payload["retained_claims"] = []
                pair_payload["previous_rejected_fact_feedback"] = []
                pair_payload["scope_rule"] = "Translate only this exact named trained-for task statement into a concise claim, preserving its variant and purpose. In Chinese use '以...为训练任务'. Do not add other tasks, datasets, scores, losses or decoding mechanics from memory."
            pair_record = {"document_id": document, "aspect": aspect,
                           "focused_block_ids": [block.block_id for block in own_evidence], "attempts": []}
            entry["pair_generations"].append(pair_record)
            for protocol_attempt in range(2):
                await service._check_cancel(check_cancel)
                if completion["model_calls"] >= completion["max_model_calls"]:
                    raise ModelOutputError("综述补全已达到24次生成调用上限；未将局部结果标作完整综述。")
                completion["model_calls"] += 1
                proposed = await service.provider.generate(_prompt("review_completion_v1"), pair_payload,
                                                           ReviewCompletionPayload)
                await service._check_cancel(check_cancel)
                pair_record["attempts"].append(proposed.model_dump())
                if any((item.document_id, item.aspect) != (document, aspect) for item in proposed.additions):
                    raise ModelOutputError("单项补全返回了其他文献或维度，结果已拒绝。")
                missing_names, preflight_problems, resolved_candidates = [], [], []
                for item in proposed.additions:
                    try:
                        service._resolve_citation_spans([item.claim], pair_catalog)
                    except ModelOutputError as problem:
                        if protocol_attempt:
                            raise
                        preflight_problems.append(str(problem) + " Select only evidence_id and quote_id pairs currently present in this call's evidence citation_spans; rejected feedback is not a citation catalog.")
                        continue
                    resolved_candidates.append(item.claim)
                    missing_names.extend(service._missing_named_attribution(item.claim, own_names))
                    if is_training_aspect(aspect):
                        incomplete = training_eligibility(service, item.claim, own_names, requirement)
                        if incomplete:
                            preflight_problems.append("The training answer lacks: " + ", ".join(incomplete)
                                + ". Quote the actual training relation and, when required_training_tasks is present, the named task declaration and each actual prediction definition on THIS card; do not substitute decoder softmax or a single task.")
                    missing_scope = service._missing_review_qualifiers(item.claim)
                    if missing_scope:
                        preflight_problems.append("This card's own quotes lack the following asserted scope: "
                            + ", ".join(missing_scope) + ". Attach the actual scope passage from this CURRENT catalog on the SAME card, or state only the positively quoted relation without the extra qualifier.")
                    if (re.search(r"知识|语料|knowledge|external|corpus", aspect, re.I)
                            and re.search(r"提供.{0,12}知识|作为.{0,6}知识来源|provide.{0,20}knowledge|as.{0,12}knowledge source", item.claim.text, re.I)
                            and not re.search(r"knowledge|知识", " ".join(c.quote for c in item.claim.citations), re.I)):
                        preflight_problems.append("The quoted training corpus does not state the added knowledge-source interpretation. Keep the actual model/corpus/training role, and attach any needed language/stage scope. Do not add 'provides knowledge' or 'as an external knowledge source' merely to echo the aspect label.")
                    if (re.search(r"知识|knowledge|external", aspect, re.I)
                            and re.search(r"未描述|未使用|没有|不使用|仅依赖|no external|no retrieval|only relies", item.claim.text, re.I)
                            and not re.search(r"knowledge|retriev|外部知识|检索", " ".join(c.quote for c in item.claim.citations), re.I)):
                        preflight_problems.append("A negative knowledge/retrieval assertion has only unrelated structure quotations. Replace it with a positively supported corpus/input-information role and its actual named-method source; absence of a topic is not proof of non-use.")
                    if not missing_names and not preflight_problems:
                        # Reuse the exact same own-quote support cache and budget
                        # as the final audit; never buy a second check just because
                        # this fact was first checked during a local correction.
                        service._validate_claims([item.claim], evidence)
                        data = item.claim.model_dump()
                        key = json.dumps(data, sort_keys=True, ensure_ascii=False)
                        cache = shared_state.setdefault("support_cache", {})
                        if key in cache:
                            verdict = cache[key]
                            audit["cache_hits"] += 1
                            method = "cached_isolated_review"
                        else:
                            if audit["claim_model_calls"] >= MAX_ISOLATED_CLAIMS:
                                raise ModelOutputError("综述事实核验已达到24次调用上限；补充结果未作为完整综述返回。")
                            verdict = await service.provider.generate(_prompt("review_claim_support_v1"), {
                                "task": "verify_single_claim", "response_kind": "review", "claim_index": 0,
                                "claim": data}, ClaimSupportPayload)
                            await service._check_cancel(check_cancel)
                            cache[key] = verdict
                            audit["claim_model_calls"] += 1
                            audit["model_checked"] = True
                            method = "isolated_review"
                        pair_record.setdefault("semantic_prechecks", []).append({"local_attempt": protocol_attempt + 1,
                            **service._review_claim_identity(item.claim, evidence),
                            "method": method, **verdict.model_dump()})
                        if verdict.verdict == "unsupported":
                            preflight_problems.append("Independent own-quote semantic review rejected this entire candidate: " + verdict.reason
                                + " Repair the actual relation/citations, retaining the requested factual content; do not merely paraphrase the rejected predicate.")
                if missing_names or preflight_problems:
                    pair_record.setdefault("local_rejections", []).extend({**service._review_claim_identity(claim,evidence),
                        "problems": preflight_problems + (["Missing named subject: "+", ".join(missing_names)] if missing_names else [])}
                        for claim in resolved_candidates)
                if (missing_names or preflight_problems) and protocol_attempt == 0:
                    pair_payload["citation_correction"] = {
                        "previous_result": proposed.model_dump(), "missing_subject_identifiers": sorted(set(missing_names)),
                        "preflight_problems": preflight_problems,
                        "rule": "Repair the listed subject, current quotation-ID and/or scope problems as applicable. Select relevant own passages that ACTUALLY link this model to this property, plus its task/corpus details on this SAME card. A present model name does not repair missing language/stage scope or an unsupported interpretation. Keep the positively quoted relation and do not borrow retained cards. The program will independently judge the whole relation. If no supported link exists, use a narrower source-supported task/variant scope or additions [].",
                    }
                    continue
                if missing_names or preflight_problems:
                    pair_record["rejected_after_local_correction"] = True
                    break
                additions.additions.extend(proposed.additions)
                break
        if not additions.additions:
            entry.update(no_accepted_additions=True, retained_claims_unchanged=True, result_claim_ids=original_ids)
            completion["remaining_pairs"] = [{"document_id":doc,"aspect":aspect} for doc,aspect in missing]
            continue
        if len(frozen) + len(additions.additions) > MAX_ISOLATED_CLAIMS:
            raise ModelOutputError("补充后综述超过24条独立结论上限，请减少文献或维度。")
        entry["proposed_added_claims"] = [item.model_dump() for item in additions.additions]
        sources = {item.evidence_id: item for item in evidence}
        seen_pairs, identities = set(), set(original_ids)
        rebuilt = result.model_copy(deep=True)
        rebuilt.message = ""
        for addition in additions.additions:
            pair = (addition.document_id, addition.aspect)
            if pair not in missing or pair in seen_pairs:
                raise ModelOutputError("补充结论的文献维度无效或重复，结果已拒绝。")
            seen_pairs.add(pair)
            claim = addition.claim.model_copy(deep=True)
            service._resolve_citation_spans([claim], catalog)
            service._validate_claims([claim], evidence)
            if any(sources[cite.evidence_id].document_id != addition.document_id for cite in claim.citations):
                raise ModelOutputError("补充结论借用了其他文献的引用，结果已拒绝。")
            identity = service._review_claim_identity(claim, evidence)["claim_id"]
            if identity in identities:
                if identity in original_ids:
                    raise ModelOutputError("补充结论重复已有事实，不能据此填充缺口。")
                # Two requested dimensions may genuinely share one new fact.
                # Keep one card; the independent classifier, not these proposed
                # labels, decides which dimensions it actually answers.
                entry.setdefault("shared_new_candidates", []).append({
                    "document_id": addition.document_id, "aspect": addition.aspect, "claim_id": identity})
                continue
            identities.add(identity)
            section = next((section for section in rebuilt.sections
                            if _normalized(section.heading) == _normalized(addition.aspect)), None)
            if section is None:
                section = Section(heading=addition.aspect)
                rebuilt.sections.append(section)
            section.claims.append(claim)
        rebuilt = SynthesisPayload.model_validate(rebuilt.model_dump())
        result, _, audit = await service._generate_grounded(_prompt("review_synthesis_v1"), {
            "task": "review", "instruction": instruction,
            "external_freshness_verified": False,
        }, SynthesisPayload, evidence, required_documents=documents, check_cancel=check_cancel,
            initial_result=rebuilt, shared_state=shared_state)
        final_ids = [service._review_claim_identity(claim, evidence)["claim_id"]
                     for section in result.sections for claim in section.claims]
        if not set(original_ids).issubset(final_ids):
            raise ModelOutputError("补充流程改变了已核验结论，结果已拒绝。")
        entry.update(retained_claims_unchanged=True, result_claim_ids=final_ids)
        completion["remaining_pairs"] = [
            {"document_id": row["document_id"], "aspect": row["aspect"]}
            for row in audit["review_coverage"][-1]["assessments"] if not row["answer_claim_indices"]]
    raise ModelOutputError("综述补充未完成。")
