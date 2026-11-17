"""Train data for the D2 scripts: extracted once from 02_数据/Train/*.zip into a cache folder.

The cache is ~/.cache/floorsense_train (outside /tmp, which macOS cleans). Delete it to re-extract.
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1].parent      # project_wireless
TRAIN = ROOT/'02_数据'/'Train'
CACHE = Path.home()/'.cache'/'floorsense_train'


def _extract(name):
    out = CACHE/name
    done = out/'.complete'
    if not done.exists():
        with zipfile.ZipFile(TRAIN/f'{name}.zip') as z:
            members = [m for m in z.namelist() if '__MACOSX' not in m and not Path(m).name.startswith('._')]
            for m in members:
                if Path(m).is_absolute() or '..' in Path(m).parts:
                    raise ValueError(f'unsafe path in zip: {m}')
            z.extractall(out, members)
        done.touch()
    return out


def static_sessions():
    """Folder holding the 200 static Train session folders."""
    out = _extract('static_train')
    return next(p.parent.parent for p in sorted(out.glob('**/meta.json')))


def dynamic_sessions():
    """Folder holding the 20 dynamic Train session folders."""
    out = _extract('dynamic_train')
    return next(p.parent.parent for p in sorted(out.glob('**/sensors.csv')))
