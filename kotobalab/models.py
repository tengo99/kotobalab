from enum import Enum
import unicodedata

from pydantic import BaseModel, ConfigDict


class Status(str, Enum):
    DRAFT = "초안"
    NEEDS_REVISION = "수정 필요"
    APPROVED = "승인 완료"


class Question(BaseModel):
    """Editable draft. Semantic/format checks are separate from JSON typing."""

    model_config = ConfigDict(extra="forbid")
    level: str
    area: str
    topic: str
    prompt: str
    options: list[str]
    answer: int  # 1-based; 0 represents a missing answer while editing
    explanation: str
    option_explanations: list[str]  # four required explanations, aligned with options


def format_errors(q: Question) -> list[str]:
    errors = []
    if q.level not in ("N1", "N2"):
        errors.append("레벨은 N1 또는 N2여야 합니다.")
    if q.area not in ("어휘", "문법"):
        errors.append("영역은 어휘 또는 문법이어야 합니다.")
    for label, value in (("주제", q.topic), ("문제", q.prompt), ("정답 해설", q.explanation)):
        if not value.strip():
            errors.append(f"{label}이 비어 있습니다.")
    if len(q.options) != 4:
        errors.append("선택지는 정확히 4개여야 합니다.")
    normalized = [unicodedata.normalize("NFKC", x).strip().casefold() for x in q.options]
    if any(not x for x in normalized):
        errors.append("빈 선택지가 있습니다.")
    if len(set(normalized)) != len(normalized):
        errors.append("중복 선택지가 있습니다(공백·전각/반각 차이 포함).")
    if not 1 <= q.answer <= len(q.options) or q.answer > 4:
        errors.append("정답이 누락되었거나 선택지 범위를 벗어났습니다.")
    if len(q.option_explanations) != 4:
        errors.append("선택지별 해설 슬롯은 정확히 4개여야 합니다.")
    for i in range(1, 5):
        if i > len(q.option_explanations) or not q.option_explanations[i - 1].strip():
            role = "정답" if i == q.answer else "오답" if 1 <= q.answer <= 4 else "개별"
            errors.append(f"선택지 {i}의 {role} 해설이 비어 있습니다.")
    return errors
