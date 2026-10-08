"""Measure real, locally acquired PDFs without committing their copyrighted bytes."""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from research_workspace.library import Library


def peak_memory_mb() -> float | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / 1024 / 1024, 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", type=Path, nargs="?", default=Path("data/samples"))
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = Path("artifacts/benchmarks") / stamp
    output.mkdir(parents=True, exist_ok=False)
    library = Library(output / "library")
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Publicly acquired samples; not the official competition package. Text extraction and literal search only.",
              "files": []}
    for source in sorted(args.samples.glob("*.pdf")):
        content = source.read_bytes()
        started = time.perf_counter()
        document = library.import_document(content, source.name)["document"]
        library.process_next_job()
        seconds = round(time.perf_counter() - started, 3)
        run = library.get_document(document["id"])["current_run"]
        checks = []
        with pymupdf.open(source) as pdf:
            for page_number in sorted({1, len(pdf) // 2 + 1, len(pdf)}):
                raw = [b for b in pdf[page_number - 1].get_text("blocks", sort=True) if b[6] == 0 and b[4].strip()]
                if not raw:
                    checks.append({"page": page_number, "result": "no_text_in_source"})
                    continue
                # Choose a meaningful passage directly from the independent source
                # file, before querying stored evidence for the expected physical page.
                phrase = " ".join(max(raw, key=lambda b: len(b[4]))[4].split())[:90]
                hits = library.search(phrase, document_id=document["id"], limit=100)
                found = any(hit["page"] == page_number for hit in hits)
                png = library.render_page(document["id"], page_number, scale=0.5)
                checks.append({"page": page_number, "query": phrase,
                               "found_on_expected_page": found,
                               "page_image_valid": png.startswith(b"\x89PNG")})
        report["files"].append({"filename": source.name, "sha256": hashlib.sha256(content).hexdigest(),
                                "bytes": len(content), "seconds": seconds,
                                "status": run["status"], "total_pages": run["total_pages"],
                                "processed_pages": run["pages_done"], "text_pages": run["text_pages"],
                                "no_text_pages": run["no_text_pages"], "failed_pages": run["failed_pages"],
                                "checks": checks})
    report["peak_process_memory_mb"] = peak_memory_mb()
    report["memory_note"] = "Peak working set of this entire benchmark process, not per document."
    with (output / "results.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"report": str(output / "results.json"), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
