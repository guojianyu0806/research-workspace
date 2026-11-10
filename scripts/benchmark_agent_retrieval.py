"""Benchmark local Agent evidence retrieval against two pinned public PDFs."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
from statistics import median
import subprocess
import sys
from time import perf_counter

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace import semantic
from research_workspace.intelligence import IntelligenceService, _quote_normalized
from research_workspace.library import Library


CASES = {
    "bert": ("1810.04805v2.pdf",),
    "transformer": ("1706.03762v7.pdf",),
    "compare": ("1810.04805v2.pdf", "1706.03762v7.pdf"),
}
QUERIES = {
    "bert": "BERT model architecture multi-layer bidirectional Transformer encoder",
    "transformer": "Transformer model architecture stacked self-attention and point-wise fully connected layers",
    "compare": "model architecture encoder and attention layers",
}
ARCHITECTURE = {
    "bert": "multi-layer bidirectional Transformer encoder",
    "transformer": "stacked self-attention and point-wise, fully connected layers",
}


class OfflineProvider:
    configured = False

    async def generate(self, *_args, **_kwargs):
        raise AssertionError("The benchmark must not call a remote model")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def timed(timings, name, fn):
    def wrapped(*args, **kwargs):
        started = perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            timings[name] = timings.get(name, 0.0) + perf_counter() - started

    return wrapped


def snapshot(source, output):
    items = json.loads((source / "inputs.json").read_text(encoding="utf-8-sig"))["documents"]
    output.mkdir(parents=True)
    (output / "originals").mkdir()
    uri = (source / "library.sqlite3").resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as original, closing(
        sqlite3.connect(output / "library.sqlite3")
    ) as copy:
        original.backup(copy)
        for item in items:
            row = original.execute(
                """SELECT d.storage_name, d.sha256, r.status FROM documents d
                   JOIN parse_runs r ON r.document_id=d.id WHERE d.id=? AND r.id=?""",
                (item["document_id"], item["run_id"]),
            ).fetchone()
            if not row or row[2] != "completed":
                raise ValueError(f"Missing completed parse for {item['filename']}")
            storage_name, digest, _ = row
            path = source / "originals" / storage_name
            if sha256(path.read_bytes()) != digest or digest != item["original_sha256"]:
                raise ValueError(f"Unexpected source bytes for {item['filename']}")
            shutil.copyfile(path, output / "originals" / storage_name)
            item["storage_name"] = storage_name
    return {item["filename"]: item for item in items}


def compact(text):
    return "".join(_quote_normalized(text).split())


def check_hit(library, item, hit):
    if hit["run_id"] != item["run_id"]:
        raise ValueError(f"Unexpected parse version for {hit['block_id']}")
    with closing(sqlite3.connect(library.database_path)) as db:
        row = db.execute(
            "SELECT page, bbox, text FROM blocks WHERE id=? AND run_id=?",
            (hit["block_id"], hit["run_id"]),
        ).fetchone()
    if not row or (row[0], json.loads(row[1])) != (hit["page"], hit["bbox"]):
        raise ValueError(f"Missing canonical position for {hit['block_id']}")
    quote = compact(hit["text"])
    if not quote or not row[2].startswith(hit["text"]):
        raise ValueError(f"Noncanonical evidence text for {hit['block_id']}")
    x0, y0, x1, y1 = hit["bbox"]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"Invalid normalized box for {hit['block_id']}")
    with pymupdf.open(library.files_dir / item["storage_name"]) as pdf:
        if not 1 <= hit["page"] <= len(pdf):
            raise ValueError(f"Invalid source page for {hit['block_id']}")
        page = pdf[hit["page"] - 1]
        clip = pymupdf.Rect(hit["bbox"]) * pymupdf.Rect(0, 0, 1, 1).torect(page.rect)
        text = page.get_text(clip=clip + (-1, -1, 1, 1))
    if quote not in compact(text):
        raise ValueError(f"Evidence text missing from its PDF box for {hit['block_id']}")
    fields = ("evidence_id", "document_id", "run_id", "block_id", "page", "bbox", "score", "truncated")
    return {field: hit[field] for field in fields} | {"text_sha256": sha256(hit["text"].encode())}, text


def assert_same_hits(actual, expected):
    def source(hit):
        return {key: value for key, value in hit.items() if key != "score"}

    if [source(hit) for hit in actual] != [source(hit) for hit in expected]:
        raise ValueError("Ranked evidence or PDF position changed")
    if any(not math.isclose(a["score"], b["score"], rel_tol=0, abs_tol=1e-6)
           for a, b in zip(actual, expected, strict=True)):
        raise ValueError("Retrieval score changed by more than 1e-6")


def assert_same_results(current, baseline):
    fields = ("case", "query", "limit", "selected_documents")
    if any(current[field] != baseline[field] for field in fields):
        raise ValueError("Baseline uses different query inputs or frozen source documents")
    for actual, expected in zip(current["runs"], baseline["runs"], strict=True):
        for key in ("model", "fingerprint", "chunks"):
            if actual["semantic"][key] != expected["semantic"][key]:
                raise ValueError(f"Baseline has a different semantic {key}")
        assert_same_hits(actual["hits"], expected["hits"])


async def measure(args, documents):
    library = Library(args.output)
    if (library.data_dir / "semantic.sqlite3").exists():
        raise ValueError("Cold measurement requires an empty vector cache")
    timings = {}
    semantic._load_model = timed(timings, "load_model", semantic._load_model)
    service = IntelligenceService(library, provider=OfflineProvider())
    encode = service.semantic.encoder

    def measure_encode(texts, query=False):
        name = "query_embedding" if query else "passage_embedding"
        return timed(timings, name, encode)(texts, query)

    service.semantic.encoder = measure_encode
    for name in ("_blocks", "_lexical_index", "_rank_lexical"):
        setattr(service, name, timed(timings, name.removeprefix("_"), getattr(service, name)))
    selected = [documents[filename] for filename in CASES[args.case]]
    run_ids = {item["document_id"]: item["run_id"] for item in selected}
    query = QUERIES[args.case]
    runs = []
    for trial in range(args.repeats + 1):
        timings.clear()
        started = perf_counter()
        result = await service.retrieve_for_question(
            query, list(run_ids), limit=8, expansion="off", run_ids=run_ids,
        )
        elapsed = perf_counter() - started
        stats = result["semantic"]
        if (result["mode"] != "bm25_dense_rrf" or stats["status"] != "applied"
                or stats["network_used"] or result["expansion"]["calls"]):
            raise ValueError("Expected real local BGE retrieval with no query expansion")
        if result["selected_runs"] != run_ids:
            raise ValueError("Retrieval selected different parse versions")
        if not stats["chunks"] or (stats["cache_hits"], stats["new_chunks"]) != (
            (stats["chunks"], 0) if trial else (0, stats["chunks"])
        ):
            raise ValueError("Unexpected vector cache state for this trial")
        if not result["evidence"]:
            raise ValueError("No retrieval results")
        by_id = {item["document_id"]: item for item in selected}
        verified = [check_hit(library, by_id[hit.document_id], hit.model_dump()) for hit in result["evidence"]]
        hits = [hit for hit, _ in verified]
        if set(run_ids) != {hit["document_id"] for hit in hits}:
            raise ValueError("A selected paper has no evidence in the result")
        if trial:
            assert_same_hits(hits, runs[0]["hits"])
        anchor = None
        if args.case in ARCHITECTURE:
            phrase = ARCHITECTURE[args.case]
            for rank, (hit, text) in enumerate(verified, 1):
                joined = re.sub(r"-[^\S\r\n]*\r?\n[^\S\r\n]*(?=[a-z])", "", text)
                if hit["page"] == 3 and compact(phrase) in compact(joined):
                    anchor = {"rank": rank, "page": 3, "block_id": hit["block_id"], "phrase": phrase}
                    break
            if not anchor:
                raise ValueError("Missing the architecture passage on PDF page 3")
        runs.append({"elapsed_seconds": elapsed, "phase_seconds": timings.copy(),
                     "semantic": stats, "hits": hits, "selected_runs": result["selected_runs"],
                     "verified_architecture": anchor})
    return {
        "case": args.case, "query": query, "limit": 8, "selected_documents": selected,
        "cold_seconds": runs[0]["elapsed_seconds"],
        "warm_median_seconds": median(run["elapsed_seconds"] for run in runs[1:]),
        "runs": runs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Saved acceptance directory with inputs.json and the library")
    parser.add_argument("--output", type=Path, required=True, help="New benchmark directory")
    parser.add_argument("--baseline", type=Path, help="Previous output directory for comparison")
    parser.add_argument("--repeats", type=int, default=5, help="Same-query warm repeats per cold run")
    parser.add_argument("--case", choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("At least one warm repeat is required")
    if args.case:
        os.environ["RESEARCH_SEMANTIC_ENABLED"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"
        report = asyncio.run(measure(args, snapshot(args.source, args.output)))
        if args.baseline:
            baseline = json.loads((args.baseline / args.case / "results.json").read_text(encoding="utf-8"))
            assert_same_results(report, baseline)
            report["matches_baseline"] = True
        with (args.output / "results.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print(f"{args.case}: cold {report['cold_seconds']:.4f}s, warm median "
              f"{report['warm_median_seconds']:.4f}s", flush=True)
        return
    args.output.mkdir(parents=True)
    env = os.environ | {"PYTHONHASHSEED": "0"}
    for case in CASES:
        command = [
            sys.executable, __file__, "--source", str(args.source), "--output",
            str(args.output / case), "--repeats", str(args.repeats), "--case", case,
        ]
        if args.baseline:
            command.extend(("--baseline", str(args.baseline)))
        subprocess.run(command, check=True, env=env)


if __name__ == "__main__":
    main()
