"""Single-process, crash-resumable PDF parsing worker."""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
import logging
import os
from pathlib import Path
import time

from .library import Library
from .worker_process import guard_parent


class WorkerLock(AbstractContextManager):
    """An OS-held lock is automatically released even after forced termination."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir).resolve() / ".worker.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("此数据目录已有解析worker运行。") from exc
        self.handle = handle
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.handle is not None:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="科研助手PDF解析worker")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--once", action="store_true", help="处理最多一个任务后退出，供验证使用")
    args = parser.parse_args()
    if not 0.05 <= args.poll_interval <= 60:
        parser.error("poll-interval必须介于0.05和60秒之间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    guard_parent(args.parent_pid)
    library = Library(args.data_dir)
    try:
        with WorkerLock(args.data_dir):
            recovered = library.recover_interrupted_runs()
            logging.info("Worker started; recovered %s interrupted task(s).", recovered)
            while True:
                processed = library.process_next_job()
                if args.once:
                    return 0
                if not processed:
                    time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        logging.info("Worker stopped; interrupted tasks will resume on restart.")
        return 0
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
