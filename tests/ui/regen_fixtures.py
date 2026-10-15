"""Regenerate UI examples from the same ledger projection served in production."""
from pathlib import Path
import json
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.test_board import seed
from hx import board, show, goals


def main():
    destination=Path(__file__).parent/'fixtures'
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        seed(root)
        documents={'board.json':board.collect(root),'goals.json':goals.collect(root)}
        for identity in ('orders','orders.work','run-1','be-001'):
            documents[f'show-{identity}.json']=show.collect(root,identity)
        for old in destination.glob('*.json'): old.unlink()
        for name,document in documents.items():
            text=json.dumps(document,indent=2).replace(str(root),'/fixture/hx')
            (destination/name).write_text(text+'\n')


if __name__=='__main__': main()
