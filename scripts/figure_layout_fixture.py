"""Use the same isolated, model-free HTTP fixture for frozen figure contracts."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.csv_extremes_fixture import main
from scripts.freeze_figure_layout import DEFAULT,verify

if __name__=='__main__':main(input_directory=DEFAULT,manifest_loader=verify,family='figure-layout',include_cases=False)
