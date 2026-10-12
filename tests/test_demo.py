import hashlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from kotobalab.db import Repository
from kotobalab.export import approved_csv
from kotobalab.models import Status, format_errors
from kotobalab.runtime import ROOT, is_demo_mode
from scripts.prepare_demo import prepare_demo


def test_demo_data_is_separate_and_preserves_repeat_run(tmp_path):
    target = tmp_path / "demo.db"
    real = ROOT / "data" / "kotobalab.db"
    digest = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    assert prepare_demo(target)
    repo = Repository(target)
    records = repo.list()
    assert len(records) == 4
    assert {r["question"].level for r in records} == {"N1", "N2"}
    assert {r["status"] for r in records} == {s.value for s in Status}
    assert {r["reviewer"] for r in records if r["reviewer"]} == {"데모 편집자"}
    assert all(e["editor"] in {"데모 편집자", "샘플 생성"} for r in records for e in repo.history(r["id"]))
    fixed = next(r for r in records if "AI 초안" in r["question"].topic)
    newest, detected, initial = repo.history(fixed["id"])
    assert initial["after_snapshot"]["question"]["option_explanations"][0] == ""
    assert "연결되지 않습니다" in initial["after_snapshot"]["question"]["option_explanations"][1]
    assert newest["content_changed"]
    assert newest["editor"] == "데모 편집자"
    assert detected["after_snapshot"]["status"] == "수정 필요"
    assert not format_errors(fixed["question"])
    assert "데모 편집자" in approved_csv(records).decode("utf-8-sig")
    before = target.read_bytes()
    assert not prepare_demo(target)
    assert target.read_bytes() == before
    if digest:
        assert hashlib.sha256(real.read_bytes()).hexdigest() == digest


def test_demo_preparation_rejects_production_database():
    with pytest.raises(ValueError, match="데모 DB"):
        prepare_demo(ROOT / "data" / "kotobalab.db")


def test_demo_flag_is_explicit():
    assert not is_demo_mode([])
    assert is_demo_mode(["--demo"])
    assert not is_demo_mode(["--demo-other"])


def test_demo_app_never_loads_env_or_offers_api(tmp_path, monkeypatch):
    import dotenv
    import sys

    target = tmp_path / "demo.db"
    prepare_demo(target)
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo must never load the real .env")
    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    monkeypatch.setattr(sys, "argv", ["app.py", "--demo"])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-never-use-this-demo-key")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    app.session_state["_test_db_path"] = target
    app.run()
    assert not app.exception
    assert next(r for r in app.radio if r.label == "생성 방식").options == ["샘플 (API 키 불필요)"]
    assert next(t for t in app.text_input if t.label == "편집자 이름").value == "데모 편집자"
    assert any("가상 데이터" in c.value for c in app.caption)
    next(b for b in app.button if b.label == "초안 생성").click().run()
    assert not app.exception
    assert len(Repository(target).list()) == 5


@pytest.mark.parametrize("exists", [False, True])
def test_default_demo_path_never_falls_back_to_real_database(tmp_path, monkeypatch, exists):
    import sys
    import kotobalab.runtime as runtime
    import kotobalab.db as database

    target = tmp_path / "selected_demo.db"
    if exists:
        prepare_demo(target)
    monkeypatch.setattr(runtime, "DEMO_DB", target)
    monkeypatch.setattr(sys, "argv", ["app.py", "--demo"])
    original = database.Repository
    def checked_repository(path):
        assert Path(path) == target, "Demo must not open any other database"
        return original(path)
    monkeypatch.setattr(database, "Repository", checked_repository)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert not app.exception
    if exists:
        assert len(app.session_state["editing_record"]["question"].options) == 4
        assert not app.error
    else:
        assert any("데모 DB가 없습니다" in error.value for error in app.error)
        assert not target.exists()
