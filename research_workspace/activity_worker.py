"""Background research jobs and explicitly enabled subscriptions, per workspace."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import logging
from pathlib import Path
import time

from .accounts import Accounts
from .discovery import DiscoveryService
from .jobs import JobStore
from .library import Library
from .worker import WorkerLock


def workspace_tick(library, *, parse_documents=False):
    """One research step per workspace before subscriptions and the next account."""
    worked = library.process_next_job(page_budget=1) if parse_documents else False
    worked = asyncio.run(JobStore(library).process_next()) or worked
    checked = DiscoveryService(library).due_checks(limit=1)
    return bool(checked) or worked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    root = args.data_dir.resolve()
    accounts = Accounts(root)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    libraries = {}
    with ExitStack() as locks:
        locks.enter_context(WorkerLock(root / '.activity'))
        while True:
            any_work = False
            for directory in accounts.workspace_directories():
                if directory not in libraries:
                    library = Library(directory)
                    library.network_rate_path = root / '.arxiv-network.sqlite3'
                    if directory != root:
                        locks.enter_context(WorkerLock(directory))
                        library.recover_interrupted_runs()
                    interrupted = JobStore(library).recover()
                    if interrupted:
                        logging.warning('%s research task(s) interrupted; explicit retry is required because model-call outcomes may be unknown.',interrupted)
                    libraries[directory] = library
                library = libraries[directory]
                try:
                    any_work = workspace_tick(library,parse_documents=directory != root) or any_work
                except Exception:
                    logging.exception('Workspace background operation failed; existing records are retained.')
            if args.once:
                return 0
            if not any_work:
                time.sleep(1)


if __name__=='__main__':
    raise SystemExit(main())
