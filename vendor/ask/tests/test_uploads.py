"""Upload registration + path resolution fallback (multi-file 'app' workflow)."""

import os

import pytest

from asklang.runtime import list_uploads, register_upload, resolve_read_path


def test_register_upload_copies_file(tmp_path):
    uploads = tmp_path / "uploads"
    src = tmp_path / "data.csv"
    src.write_text("a,b\n1,2\n")
    name = register_upload(str(src), str(uploads))
    assert name == "data.csv"
    assert (uploads / "data.csv").read_text() == "a,b\n1,2\n"


def test_register_upload_same_content_is_noop(tmp_path):
    uploads = tmp_path / "uploads"
    src = tmp_path / "data.csv"
    src.write_text("a,b\n1,2\n")
    n1 = register_upload(str(src), str(uploads))
    n2 = register_upload(str(src), str(uploads))
    assert n1 == n2 == "data.csv"
    assert len(list_uploads(str(uploads))) == 1


def test_register_upload_conflicting_name_gets_suffix(tmp_path):
    uploads = tmp_path / "uploads"
    src1 = tmp_path / "a" / "data.csv"
    src2 = tmp_path / "b" / "data.csv"
    src1.parent.mkdir()
    src2.parent.mkdir()
    src1.write_text("a,b\n1,2\n")
    src2.write_text("x,y\n9,9\n")   # same basename, different content
    n1 = register_upload(str(src1), str(uploads))
    n2 = register_upload(str(src2), str(uploads))
    assert n1 == "data.csv"
    assert n2 == "data (2).csv"
    assert set(list_uploads(str(uploads))) == {"data.csv", "data (2).csv"}


def test_list_uploads_newest_first(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    import time
    (uploads / "old.csv").write_text("x\n1\n")
    time.sleep(0.01)
    (uploads / "new.csv").write_text("x\n2\n")
    assert list_uploads(str(uploads))[0] == "new.csv"


def test_resolve_read_path_falls_back_to_uploads(tmp_path, monkeypatch):
    import asklang.runtime as runtime
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "mydata.csv").write_text("x\n1\n")
    monkeypatch.setattr(runtime, "UPLOADS_DIR", str(uploads))
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    resolved = resolve_read_path("mydata.csv")
    assert os.path.samefile(resolved, uploads / "mydata.csv")


def test_load_program_finds_uploaded_file(tmp_path, monkeypatch):
    import asklang.runtime as runtime
    from asklang import Env, run_program
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "mydata.csv").write_text("region,revenue\nWest,10\nEast,20\n")
    monkeypatch.setattr(runtime, "UPLOADS_DIR", str(uploads))
    monkeypatch.chdir(tmp_path)
    t, ch, notices, trace = run_program(
        'load "mydata.csv"\nshow total revenue as r', Env())
    assert int(t.df["r"].iloc[0]) == 30
