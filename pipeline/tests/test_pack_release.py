import hashlib
import importlib.util
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("pack_release", Path(__file__).resolve().parent.parent / "scripts" / "pack_release.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


def test_pack_contains_the_folder_and_prints_the_zip_checksum(tmp_path, capsys):
    rel = tmp_path / "releases" / "2026-11-02_da464f"
    rel.mkdir(parents=True)
    (rel / "manifest.json").write_text("{}", encoding="utf-8")
    (rel / "approval.json").write_text("{}", encoding="utf-8")
    (rel / "professors.jsonl").write_text("x\n", encoding="utf-8")
    assert pr.main(["--release", "da464f5e-1", "--releases-dir", str(tmp_path / "releases"), "--out", str(tmp_path / "out")]) == 0
    z = tmp_path / "out" / "2026-11-02_da464f.zip"
    names = sorted(zipfile.ZipFile(z).namelist())
    assert names == ["2026-11-02_da464f/approval.json", "2026-11-02_da464f/manifest.json", "2026-11-02_da464f/professors.jsonl"]
    assert hashlib.sha256(z.read_bytes()).hexdigest() in capsys.readouterr().out


def test_an_unapproved_release_is_not_packed(tmp_path):
    rel = tmp_path / "releases" / "2026-11-02_aaaaaa"
    rel.mkdir(parents=True)
    (rel / "manifest.json").write_text("{}", encoding="utf-8")
    assert pr.main(["--release", "aaaaaa", "--releases-dir", str(tmp_path / "releases"), "--out", str(tmp_path / "o")]) == 1
    assert pr.main(["--release", "bbbbbb", "--releases-dir", str(tmp_path / "releases"), "--out", str(tmp_path / "o")]) == 1
