"""Pack an approved release folder into one zip and print its sha256 (stdlib only).
  python scripts/pack_release.py --release <id or first 6 characters>  [--out DIR]
It contains the folder `<date>_<id6>/` (manifest, jsonl files, needs_review.csv, report.md, approval.json). Put it in any private storage;
the owner unpacks it into pipeline/output/releases/ (or lets pipeline-load.yml download it) and runs `release import`, `load` (dry-run), `load --apply`.
The checksum is for transport; the bundle's own integrity check is manifest.json (`release verify`)."""
import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RELEASES = HERE / "output" / "releases"


def pack(folder: Path, out_dir: Path) -> tuple:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{folder.name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, f"{folder.name}/{p.relative_to(folder).as_posix()}")
    return target, hashlib.sha256(target.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", default=str(HERE / "output" / "bundles"))
    ap.add_argument("--releases-dir", default=str(RELEASES))
    a = ap.parse_args(argv)
    hits = sorted(Path(a.releases_dir).glob(f"*_{a.release[:6]}"))
    if not hits:
        print(f"no release folder for {a.release} in {a.releases_dir}")
        return 1
    folder = hits[-1]
    if not (folder / "approval.json").exists():
        print("refusing: this release has no approval.json (run `release approve` first)")
        return 1
    target, digest = pack(folder, Path(a.out))
    print(f"{target}\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
