"""Snapshot comparison, without guessing linguistic equivalence."""


def normalized(value):
    if isinstance(value, str):
        # Only surrounding whitespace and newline encoding are insignificant.
        # Internal spaces and option ordering may alter meaning and remain significant.
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    return value


def same_content(left, right):
    return normalized(left.model_dump()) == normalized(right.model_dump())


def comparison_rows(before: dict | None, after: dict, *, changed_only=False):
    before = before or {}
    old, new = before.get("question", {}), after.get("question", {})
    rows = []

    def add(label, left, right):
        if not changed_only or left != right:
            rows.append({"항목": label, "수정 전": left, "수정 후": right})

    for key, label in (("level", "레벨"), ("area", "영역"), ("topic", "주제"), ("prompt", "문제")):
        add(label, old.get(key, ""), new.get(key, ""))
    for key, label in (("options", "선택지"), ("option_explanations", "선택지 해설")):
        left, right = old.get(key, []), new.get(key, [])
        for index in range(max(4, len(left), len(right))):
            add(f"{label} {index + 1}", left[index] if index < len(left) else "", right[index] if index < len(right) else "")
    add("정답 번호", str(old.get("answer", "")), str(new.get("answer", "")))
    add("정답 해설", old.get("explanation", ""), new.get("explanation", ""))
    for key, label in (("status", "상태"), ("review_note", "검수 메모"), ("reviewer", "승인자"), ("approved_at", "승인 시각(UTC)")):
        add(label, before.get(key) or "", after.get(key) or "")
    return rows
