from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest

from kotobalab.db import Repository
from kotobalab.models import Status
from kotobalab.samples import sample_question


APP = Path(__file__).resolve().parents[1] / "app.py"


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_sample_edit_review_approval(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "ui.db"
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["_test_db_path"] = path
    app.run()
    assert not app.exception
    button(app, "초안 생성").click().run()
    assert not app.exception
    repo = Repository(path)
    assert repo.list()[0]["status"] == "초안"
    button(app, "승인 완료로 저장").click().run()
    assert any("명시적" in e.value for e in app.error)
    next(t for t in app.text_area if t.label == "문제 (일본어)").set_value("地域の図書館は意見を（　）に取り入れた。")
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("테스트 편집자")
    button(app, "수정 필요로 저장").click().run()
    assert repo.list()[0]["status"] == "수정 필요"
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("테스트 편집자")
    app.checkbox[0].check()
    button(app, "승인 완료로 저장").click().run()
    assert not app.exception
    assert repo.list()[0]["status"] == "승인 완료"
    # A new revision cannot inherit the previous confirmation.
    assert not app.checkbox[0].value
    next(t for t in app.text_area if t.label == "정답 해설").set_value("해설을 재작성했습니다.")
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("테스트 편집자")
    button(app, "초안 저장").click().run()
    assert repo.list()[0]["status"] == "초안"
    assert not app.exception


def test_ui_duplicate_option_blocks_approval(tmp_path):
    path = tmp_path / "ui.db"
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["_test_db_path"] = path
    app.run()
    button(app, "초안 생성").click().run()
    option1 = next(t for t in app.text_input if t.label == "선택지 1").value
    next(t for t in app.text_input if t.label == "선택지 2").set_value(option1)
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("편집자")
    app.checkbox[0].check()
    button(app, "승인 완료로 저장").click().run()
    assert any("중복" in e.value for e in app.error)
    assert Repository(path).list()[0]["status"] == "초안"
    assert not app.exception


@pytest.mark.parametrize("label,expected", [
    ("정답 해설", "정답 해설이 비어 있습니다."),
    ("선택지 1 해설", "선택지 1의 정답 해설이 비어 있습니다."),
    ("선택지 2 해설", "선택지 2의 오답 해설이 비어 있습니다."),
    ("선택지 3 해설", "선택지 3의 오답 해설이 비어 있습니다."),
    ("선택지 4 해설", "선택지 4의 오답 해설이 비어 있습니다."),
])
def test_ui_missing_explanation_blocks_approval(tmp_path, label, expected):
    path = tmp_path / "ui.db"
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["_test_db_path"] = path
    app.run()
    button(app, "초안 생성").click().run()
    next(t for t in app.text_area if t.label == label).set_value(" \n　")
    button(app, "형식 검증").click().run()
    assert any(expected in e.value for e in app.error)
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("편집자")
    app.checkbox[0].check()
    button(app, "승인 완료로 저장").click().run()
    assert any(expected in e.value for e in app.error)
    assert Repository(path).list()[0]["status"] == "초안"
    assert not app.exception


def test_ui_legacy_export_exclusion_is_visible(tmp_path):
    path = tmp_path / "ui.db"
    repo = Repository(path)
    q = sample_question("N1", "문법", "기존 문항")
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.APPROVED, "기존 검수", 1, confirmed=True, reviewer="편집자")
    old = q.model_copy(update={"option_explanations": [""] + q.option_explanations[1:]})
    with repo.connection() as conn:
        conn.execute("UPDATE questions SET payload=? WHERE id=?", (old.model_dump_json(), ident))
    before = repo.get(ident)
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["_test_db_path"] = path
    app.run()
    assert any("CSV에서 제외" in w.value for w in app.warning)
    assert any("선택지 1의 정답 해설" in str(df.value) for df in app.dataframe)
    assert any("언어적 정확성 검수" in i.value for i in app.info)
    assert Repository(path).get(ident) == before
    assert not app.exception


def test_ui_edit_history_and_version_comparison(tmp_path):
    path = tmp_path / "history_ui.db"
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["_test_db_path"] = path
    app.run()
    button(app, "초안 생성").click().run()
    repo = Repository(path)
    original = repo.list()[0]["question"].prompt
    next(t for t in app.text_area if t.label == "문제 (일본어)").set_value("수정된 문제（　）。")
    next(t for t in app.text_area if t.label == "수정 사유 (이력에 기록)").set_value("문맥을 명확하게 수정")
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("연구원")
    button(app, "수정 필요로 저장").click().run()
    assert not app.exception
    saved = repo.history(repo.list()[0]["id"])[0]
    assert saved["editor"] == "연구원"
    assert saved["reason"] == "문맥을 명확하게 수정"
    next(s for s in app.selectbox if s.label == "비교할 저장 기록").select(saved["id"]).run()
    frames = [df.value for df in app.dataframe]
    assert any("수정 전" in df.columns and original in df["수정 전"].values
               and "수정된 문제（　）。" in df["수정 후"].values for df in frames)
    next(s for s in app.selectbox if s.label == "비교할 저장 기록").select(repo.history(repo.list()[0]["id"])[1]["id"]).run()
    assert not app.exception
    assert any("수정 후" in df.value.columns and original in df.value["수정 후"].values for df in app.dataframe)


def test_ui_no_change_records_attempt_without_new_version(tmp_path):
    path = tmp_path / "no_change.db"
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["_test_db_path"] = path
    app.run()
    button(app, "초안 생성").click().run()
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("편집자")
    next(t for t in app.text_area if t.label == "수정 사유 (이력에 기록)").set_value("변경 없는 저장 확인")
    prompt = next(t for t in app.text_area if t.label == "문제 (일본어)")
    prompt.set_value("　" + prompt.value + " ")
    button(app, "초안 저장").click().run()
    repo = Repository(path)
    assert repo.list()[0]["revision"] == 1
    assert repo.history(repo.list()[0]["id"])[0]["event"] == "no_change"
    assert any("실질적인 변경이 없어" in message.value for message in app.success)
    assert not app.exception


def test_ui_stale_form_does_not_overwrite_or_record_history(tmp_path):
    path = tmp_path / "conflict.db"
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["_test_db_path"] = path
    app.run()
    button(app, "초안 생성").click().run()
    repo = Repository(path)
    record = repo.list()[0]
    other_question = record["question"].model_copy(update={"prompt": "다른 편집자의 수정"})
    repo.save(record["id"], other_question, Status.NEEDS_REVISION, "", 1, editor="다른 편집자")
    before = repo.history(record["id"])
    next(t for t in app.text_area if t.label == "문제 (일본어)").set_value("현재 창의 수정")
    next(t for t in app.text_input if t.label == "편집자 이름").set_value("현재 편집자")
    button(app, "초안 저장").click().run()
    assert any("다른 창에서" in e.value for e in app.error)
    assert repo.history(record["id"]) == before
    assert repo.get(record["id"])["question"].prompt == "다른 편집자의 수정"
    button(app, "목록 새로고침").click().run()
    assert next(t for t in app.text_area if t.label == "문제 (일본어)").value == "다른 편집자의 수정"
    assert not app.exception
