import csv
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import APIConnectionError
import httpx

from kotobalab.db import ConflictError, Repository
from kotobalab.export import approved_csv
from kotobalab.generator import GenerationError, generate_question
from kotobalab.models import Status, format_errors
from kotobalab.samples import sample_question


@pytest.fixture
def q():
    return sample_question("N2", "어휘", "학습")


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "questions.db")


@pytest.mark.parametrize("level,area", [("N1", "어휘"), ("N1", "문법"), ("N2", "어휘"), ("N2", "문법")])
def test_samples_valid(level, area):
    assert not format_errors(sample_question(level, area, "테스트"))


@pytest.mark.parametrize("updates,fragment", [
    ({"options": ["a", "ａ", "c", "d"]}, "중복"),
    ({"options": ["a", "b", "c"]}, "4개"),
    ({"options": ["a", " ", "c", "d"]}, "빈 선택지"),
    ({"answer": 0}, "정답"), ({"answer": 5}, "정답"),
    ({"explanation": ""}, "정답 해설"),
    ({"option_explanations": ["", "", "x", "y"]}, "오답 해설"),
    ({"option_explanations": []}, "해설 슬롯"),
    ({"prompt": ""}, "문제"), ({"topic": " "}, "주제"),
])
def test_format_errors(q, updates, fragment):
    assert any(fragment in e for e in format_errors(q.model_copy(update=updates)))


def test_create_persist_and_edit(repo, q):
    ident = repo.create(q, "openai")
    assert repo.get(ident)["status"] == "초안"
    edited = q.model_copy(update={"prompt": "수정한 문제"})
    repo.save(ident, edited, Status.NEEDS_REVISION, "표현 재검토", 1)
    reopened = Repository(repo.path).get(ident)
    assert reopened["question"] == edited
    assert reopened["review_note"] == "표현 재검토"
    assert reopened["status"] == "수정 필요"


@pytest.mark.parametrize("confirmed,reviewer", [(False, "편집자"), (True, " ")])
def test_approval_requires_human(repo, q, confirmed, reviewer):
    ident = repo.create(q, "sample")
    with pytest.raises(ValueError, match="명시적"):
        repo.save(ident, q, Status.APPROVED, "", 1, confirmed=confirmed, reviewer=reviewer)
    assert repo.get(ident)["status"] == "초안"


def test_invalid_draft_saved_but_not_approved(repo, q):
    ident = repo.create(q, "sample")
    invalid = q.model_copy(update={"answer": 0})
    repo.save(ident, invalid, Status.NEEDS_REVISION, "정답 확인", 1)
    with pytest.raises(ValueError, match="형식 오류"):
        repo.save(ident, invalid, Status.APPROVED, "", 2, confirmed=True, reviewer="편집자")


def test_approved_edit_revokes_approval(repo, q):
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.APPROVED, "검수", 1, confirmed=True, reviewer="김편집")
    edited = q.model_copy(update={"explanation": "새 해설"})
    with pytest.raises(ValueError, match="먼저 초안"):
        repo.save(ident, edited, Status.APPROVED, "", 2, confirmed=True, reviewer="김편집")
    repo.save(ident, edited, Status.NEEDS_REVISION, "재검수", 2)
    assert repo.get(ident)["status"] == "초안"
    assert repo.get(ident)["approved_at"] is None
    assert repo.get(ident)["reviewer"] == ""
    repo.save(ident, edited, Status.APPROVED, "재검수 완료", 3, confirmed=True, reviewer="이편집")
    assert repo.get(ident)["status"] == "승인 완료"


def test_concurrent_editor_conflict(repo, q):
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.DRAFT, "다른 편집", 1)
    with pytest.raises(ConflictError):
        repo.save(ident, q, Status.APPROVED, "", 1, confirmed=True, reviewer="편집자")
    assert repo.get(ident)["status"] == "초안"


def test_csv_only_approved_roundtrip(repo, q):
    repo.create(q, "sample")
    ident = repo.create(q.model_copy(update={"topic": '=HYPERLINK("test")'}), "sample")
    approved = repo.get(ident)["question"]
    repo.save(ident, approved, Status.APPROVED, "쉼표, 줄바꿈\n메모", 1, confirmed=True, reviewer="편집자")
    exported = approved_csv(repo.list())
    assert exported.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(exported.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["prompt"] == q.prompt
    assert rows[0]["topic"].startswith("'=")
    assert rows[0]["option_explanation_1"] == q.option_explanations[0]
    assert rows[0]["option_explanation_2"] == q.option_explanations[1]
    assert rows[0]["review_note"] == "쉼표, 줄바꿈\n메모"


def test_empty_csv_header():
    assert "option_explanation_4" in approved_csv([]).decode("utf-8-sig")


@pytest.mark.parametrize("answer", [1, 2, 3, 4])
@pytest.mark.parametrize("slot", [1, 2, 3, 4])
@pytest.mark.parametrize("blank", ["", " \n\t　"])
def test_every_option_explanation_required(repo, q, answer, slot, blank):
    explanations = ["편집자가 검수할 개별 해설"] * 4
    explanations[slot - 1] = blank
    invalid = q.model_copy(update={"answer": answer, "option_explanations": explanations})
    role = "정답" if slot == answer else "오답"
    message = f"선택지 {slot}의 {role} 해설이 비어 있습니다."
    assert message in format_errors(invalid)
    ident = repo.create(invalid, "sample")
    before = repo.get(ident)
    with pytest.raises(ValueError, match=message):
        repo.save(ident, invalid, Status.APPROVED, "", 1, confirmed=True, reviewer="편집자")
    assert repo.get(ident) == before


def test_missing_correct_slot_is_reported(q):
    invalid = q.model_copy(update={"answer": 4, "option_explanations": ["해설"] * 3})
    assert "선택지 4의 정답 해설이 비어 있습니다." in format_errors(invalid)


def test_summary_explanation_also_required(repo, q):
    invalid = q.model_copy(update={"explanation": " \n　"})
    assert all(x.strip() for x in invalid.option_explanations)
    ident = repo.create(invalid, "sample")
    with pytest.raises(ValueError, match="정답 해설이 비어"):
        repo.save(ident, invalid, Status.APPROVED, "", 1, confirmed=True, reviewer="편집자")


def test_format_validation_does_not_certify_language(repo, q):
    unreviewed = q.model_copy(update={"option_explanations": ["언어적 정확성 검수가 필요한 설명"] * 4})
    assert not format_errors(unreviewed)
    ident = repo.create(unreviewed, "openai")
    with pytest.raises(ValueError, match="명시적"):
        repo.save(ident, unreviewed, Status.APPROVED, "", 1, reviewer="편집자")
    assert repo.get(ident)["status"] == "초안"


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_editing_individual_explanation_revokes_approval(repo, q, slot):
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.APPROVED, "검수", 1, confirmed=True, reviewer="편집자")
    explanations = q.option_explanations.copy()
    explanations[slot] = "수정한 해설: 다시 언어 검수해야 합니다."
    edited = q.model_copy(update={"option_explanations": explanations})
    repo.save(ident, edited, Status.DRAFT, "재검수", 2)
    record = repo.get(ident)
    assert record["status"] == "초안"
    assert record["reviewer"] == ""
    assert record["approved_at"] is None
    assert len(list(csv.DictReader(io.StringIO(approved_csv(repo.list()).decode("utf-8-sig"))))) == 0
    with pytest.raises(ValueError, match="명시적"):
        repo.save(ident, edited, Status.APPROVED, "", 3, reviewer="편집자")


def test_legacy_approval_missing_correct_explanation_excluded_without_changes(repo, q):
    ident = repo.create(q, "sample")
    repo.save(ident, q, Status.APPROVED, "기존 검수", 1, confirmed=True, reviewer="편집자")
    legacy = q.model_copy(update={"option_explanations": [""] + q.option_explanations[1:]})
    # Simulate data approved under the previous policy, without migrating it.
    with repo.connection() as conn:
        conn.execute("UPDATE questions SET payload=? WHERE id=?", (legacy.model_dump_json(), ident))
    before = repo.get(ident)
    rows = list(csv.DictReader(io.StringIO(approved_csv(repo.list()).decode("utf-8-sig"))))
    assert not rows
    assert Repository(repo.path).get(ident) == before


def test_grammar_sample_explains_context_instead_of_false_prohibition():
    sample = sample_question("N1", "문법", "검수")
    assert "과거형 確認した 뒤에는 연결되지 않습니다" not in sample.option_explanations[1]
    assert "시간 관계" in sample.option_explanations[1]


def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(GenerationError, match="키"):
        generate_question("N2", "어휘", "학습")


def test_api_structured_output(q):
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=q)
    assert generate_question("N2", "어휘", "학습", client=client) == q
    assert client.responses.parse.call_args.kwargs["text_format"] is type(q)
    assert client.responses.parse.call_args.kwargs["store"] is False


@pytest.mark.parametrize("kind", ["refusal", "invalid", "mismatch", "missing_correct_explanation"])
def test_api_bad_response(q, kind):
    client = Mock()
    result = None if kind == "refusal" else q.model_copy(update={"answer": 0} if kind == "invalid" else {"level": "N1"})
    if kind == "missing_correct_explanation":
        result = q.model_copy(update={"option_explanations": [""] + q.option_explanations[1:]})
    client.responses.parse.return_value = SimpleNamespace(output_parsed=result)
    with pytest.raises(GenerationError):
        generate_question("N2", "어휘", "학습", client=client)


def test_api_error_does_not_expose_secret():
    client = Mock()
    client.responses.parse.side_effect = APIConnectionError(message="sk-secret", request=httpx.Request("POST", "https://example.com"))
    with pytest.raises(GenerationError) as exc:
        generate_question("N2", "어휘", "학습", client=client)
    assert "sk-secret" not in str(exc.value)
