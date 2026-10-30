"""Serve an isolated demo; real background work requires --enable-worker."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8776)
    parser.add_argument('--enable-worker', action='store_true',
                        help='Enable real PDF parsing and research workers in this isolated demo. '
                             'Model credentials must still be applied through the local UI.')
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    if not directory.is_relative_to(ROOT/'artifacts') or not (directory/'submission-demo-manifest.json').is_file():
        raise ValueError('Choose a prepared isolated submission-demo workspace, not the personal library.')
    if not 1024 <= args.port <= 65535 or args.port == 8765:
        raise ValueError('The isolated demonstration must use a separate local port.')
    for key in ['RESEARCH_MODEL_API_KEY', 'RESEARCH_MODEL_BASE_URL', 'RESEARCH_MODEL']:
        os.environ[key] = ''
    os.environ['RESEARCH_DATA_DIR'] = str(directory)
    os.environ['RESEARCH_OCR_ENABLED'] = '0'
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = '0'
    from research_workspace.api import create_app
    import uvicorn
    # Historical viewing remains the default. Explicit live recording can opt in
    # to normal workers; credentials are never copied from the parent process.
    uvicorn.run(create_app(directory, start_worker=args.enable_worker),
                host='127.0.0.1', port=args.port, workers=1)


if __name__ == '__main__':
    main()
