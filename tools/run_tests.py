"""Execute the unchanged scheduling/source tests without a physical PYNQ board."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'runtime/research'),str(ROOT/'runtime/board')]


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(['test_unified','test_lz77_input']))
    raise SystemExit(0 if result.wasSuccessful() else 1)
