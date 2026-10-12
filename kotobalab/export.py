import pandas as pd

from kotobalab.models import Status, format_errors


def safe_cell(value):
    # Prevent spreadsheet formula execution for free-text fields.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def approval_export_errors(record: dict) -> list[str]:
    errors = format_errors(record["question"])
    if not record["reviewer"].strip() or not record["approved_at"]:
        errors.append("승인자 또는 승인 시각이 누락되었습니다.")
    return errors


def approved_csv(records: list[dict]) -> bytes:
    columns = ["id", "level", "area", "topic", "prompt", "option_1", "option_2", "option_3", "option_4",
               "answer", "explanation", "option_explanation_1", "option_explanation_2",
               "option_explanation_3", "option_explanation_4", "status", "reviewer", "review_note", "approved_at"]
    rows = []
    for record in records:
        q = record["question"]
        if record["status"] != Status.APPROVED.value or approval_export_errors(record):
            continue
        row = {key: getattr(q, key) for key in ("level", "area", "topic", "prompt", "answer", "explanation")}
        row.update({key: record[key] for key in ("id", "status", "reviewer", "review_note", "approved_at")})
        row.update({f"option_{i}": value for i, value in enumerate(q.options, 1)})
        row.update({f"option_explanation_{i}": value for i, value in enumerate(q.option_explanations, 1)})
        rows.append({key: safe_cell(value) for key, value in row.items()})
    return pd.DataFrame(rows, columns=columns).to_csv(index=False).encode("utf-8-sig")
