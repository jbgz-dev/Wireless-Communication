"""Extract supplied static archive locally, ignoring macOS resource forks."""
from pathlib import Path
from zipfile import ZipFile
import argparse

BASE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--archive',type=Path,default=BASE/'data/static_train.zip')
parser.add_argument('--destination',type=Path,default=BASE/'data/static')
args = parser.parse_args()
root = args.destination.resolve()
with ZipFile(args.archive) as archive:
    for info in archive.infolist():
        if info.is_dir() or '__MACOSX' in info.filename or Path(info.filename).name.startswith('.'):
            continue
        target = (root/info.filename).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Unsafe archive path')
        payload = archive.read(info)
        if target.exists() and target.read_bytes() != payload:
            raise ValueError(f'Refusing to overwrite changed data: {target}')
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            target.write_bytes(payload)
print(f'Static sessions available at {root}')
