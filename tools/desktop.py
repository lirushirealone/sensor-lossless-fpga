"""Run the source GUI with a local Python environment, without bundled packages."""
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'tools')]
from PySide6 import QtWidgets as W
from app_config import configure


def main():
    Window,_=configure(ROOT);app=W.QApplication(sys.argv[:1]);window=Window();window.show();return app.exec()


if __name__=='__main__':sys.exit(main())
