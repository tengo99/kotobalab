from pathlib import Path
import os

from dotenv import load_dotenv
import streamlit as st

from kotobalab.db import Repository
from kotobalab.export import approval_export_errors, approved_csv
from kotobalab.generator import GenerationError, generate_question
from kotobalab.models import Question, Status, format_errors
from kotobalab.samples import sample_question
from kotobalab.history import comparison_rows
from kotobalab.runtime import DEMO_DB, is_demo_mode


ROOT = Path(__file__).resolve().parent
DEMO_MODE = is_demo_mode()
if not DEMO_MODE:
    load_dotenv(ROOT / ".env")
st.set_page_config(page_title="KotobaLab | 문항 편집실", page_icon="📚", layout="wide")


def repository():
    # Test override is session-scoped; production always uses project-local data.
    default_path = DEMO_DB if DEMO_MODE else ROOT / "data" / "kotobalab.db"
    path = st.session_state.get("_test_db_path", default_path)
    if DEMO_MODE and not Path(path).exists():
        st.error("데모 DB가 없습니다. 먼저 python scripts/prepare_demo.py를 실행하세요.")
        st.stop()
    return Repository(path)


repo = repository()
st.title("KotobaLab · 문항 편집실")
if DEMO_MODE:
    st.caption("공개 촬영용 데모 · 가상 데이터 · 실제 API 호출 없음")
st.caption("JLPT N1·N2 문항 초안 생성 → 편집 → 내용 검수 → 명시적 승인")
st.info("자동 형식 검증: 선택지 수·중복, 정답 번호, 정답 해설 및 모든 선택지의 개별 해설 누락을 확인합니다. 언어적 정확성 검수: 일본어의 자연스러움, 복수 정답 가능성, 난도와 해설의 정확성은 편집자가 최종 판단합니다. AI 생성이나 형식 검증 통과는 언어적 정확성을 보증하지 않습니다.")
if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))

with st.sidebar:
    st.header("초안 생성")
    mode = st.radio("생성 방식", ["샘플 (API 키 불필요)"] if DEMO_MODE else ["샘플 (API 키 불필요)", "OpenAI API"])
    level = st.selectbox("JLPT 레벨", ["N2", "N1"])
    area = st.selectbox("영역", ["어휘", "문법"])
    topic = st.text_input("주제", value="학습과 업무", max_chars=200)
    if mode.startswith("샘플"):
        st.caption("레벨·영역별 고정 예시 4종입니다. 주제는 분류용으로 저장되며 예시 내용은 바뀌지 않습니다.")
    else:
        st.caption(f"모델: {os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')}")
        if not os.getenv("OPENAI_API_KEY", "").strip():
            st.warning("API 키가 없습니다. .env 설정 후 다시 실행하세요.")
    if st.button("초안 생성", type="primary", width="stretch"):
        if not topic.strip():
            st.error("주제를 입력하세요.")
        else:
            try:
                with st.spinner("문항 초안 생성 중…"):
                    q = sample_question(level, area, topic) if mode.startswith("샘플") else generate_question(level, area, topic.strip())
                    new_id = repo.create(q, "sample" if mode.startswith("샘플") else "openai")
                st.session_state["selected_id"] = new_id
                st.session_state["status_filter"] = "전체"
                st.session_state["notice"] = f"문항 #{new_id}을 초안으로 저장했습니다. 내용 검수가 필요합니다."
                st.rerun()
            except GenerationError as exc:
                st.error(str(exc))
    st.divider()
    status_filter = st.selectbox("상태 필터", ["전체"] + [s.value for s in Status], key="status_filter")
    if st.button("목록 새로고침", width="stretch"):
        st.session_state.pop("editing_record", None)
        st.rerun()

records = repo.list()
cols = st.columns(3)
for col, status in zip(cols, Status):
    col.metric(status.value, sum(r["status"] == status.value for r in records))

editor_tab, export_tab, history_tab = st.tabs(["편집 및 검수", "승인 문항 내보내기", "수정 이력 및 검수 기록"])
with editor_tab:
    visible = [r for r in records if status_filter == "전체" or r["status"] == status_filter]
    if not visible:
        st.info("표시할 문항이 없습니다. 왼쪽에서 샘플 초안을 생성하거나 상태 필터를 변경하세요.")
    else:
        by_id = {r["id"]: r for r in visible}
        if st.session_state.get("selected_id") not in by_id:
            st.session_state["selected_id"] = visible[0]["id"]
        selected = st.selectbox("편집할 문항", list(by_id), key="selected_id",
                                format_func=lambda i: f"#{i} · {by_id[i]['question'].level} {by_id[i]['question'].area} · {by_id[i]['status']} · {by_id[i]['question'].topic}")
        loaded = st.session_state.get("editing_record")
        if loaded is None or loaded["id"] != selected:
            st.session_state["editing_record"] = by_id[selected]
        record = st.session_state["editing_record"]
        q = record["question"]
        st.caption(f"현재 상태: {record['status']} | 출처: {record['source']} | 버전: {record['revision']}")
        if record["approved_at"]:
            st.caption(f"승인자: {record['reviewer']} | 승인 시각(UTC): {record['approved_at']}")
        errors = format_errors(q)
        if errors:
            st.warning("저장된 문항의 형식 오류: " + " / ".join(errors))
        else:
            st.success("저장된 문항: 형식 검증 통과 · 일본어 내용의 정확성을 보증하지 않습니다.")
        if record["status"] == Status.APPROVED.value:
            st.caption("승인된 문항의 내용을 수정해 저장하면 초안으로 돌아갑니다. 저장 후 다시 검수·승인하세요.")
        prefix = f"{selected}_{record['revision']}"
        with st.form(f"edit_{prefix}"):
            meta = st.columns(2)
            edited_level = meta[0].selectbox("레벨", ["N1", "N2"], index=["N1", "N2"].index(q.level))
            edited_area = meta[1].selectbox("문항 영역", ["어휘", "문법"], index=["어휘", "문법"].index(q.area))
            edited_topic = st.text_input("문항 주제", q.topic)
            prompt = st.text_area("문제 (일본어)", q.prompt, height=110)
            options = [st.text_input(f"선택지 {i}", q.options[i - 1] if i <= len(q.options) else "") for i in range(1, 5)]
            answer = st.selectbox("정답 번호", [0, 1, 2, 3, 4], index=q.answer if 0 <= q.answer <= 4 else 0,
                                  format_func=lambda i: "미선택" if i == 0 else str(i))
            explanation = st.text_area("정답 해설", q.explanation)
            st.caption("선택지별 해설 4개는 모두 필수입니다. 정답 선택지의 개별 해설도 작성하세요(위 정답 해설이 있어도 생략할 수 없습니다). 정답 번호를 바꾸면 모든 해설을 다시 확인하세요.")
            explanations = [st.text_area(f"선택지 {i} 해설", q.option_explanations[i - 1] if i <= len(q.option_explanations) else "") for i in range(1, 5)]
            note = st.text_area("검수 메모", record["review_note"])
            reason = st.text_area("수정 사유 (이력에 기록)", key=f"reason_{prefix}",
                                  help="표현 수정의 근거나 상태 변경 이유를 기록하세요. 검수 메모와 별도로 매 저장 기록에 보존됩니다.")
            st.caption("모든 저장에는 편집자 이름이 필요합니다. 앞뒤 공백·줄바꿈 방식만 달라진 입력은 버전과 승인을 유지하고 저장 시도만 기록합니다. 선택지 순서나 문장 내부 공백 변경은 실제 변경으로 취급합니다.")
            st.subheader("언어적 정확성 검수 · 편집자 최종 확인")
            st.caption("자동 검사는 해설이 비어 있는지만 확인합니다. 각 선택지의 문맥 적합성, 다른 정답의 가능성, 문법 설명의 근거를 직접 검토하세요.")
            reviewer = st.text_input("편집자 이름", value="데모 편집자" if DEMO_MODE else "", key=f"reviewer_{prefix}")
            confirmed = st.checkbox("일본어의 자연스러움·복수 정답 가능성·정답 및 오답별 해설의 정확성·레벨 적합성을 직접 검수했으며 이 문항의 승인을 명시적으로 확인합니다.",
                                    key=f"confirm_{prefix}")
            buttons = st.columns(4)
            validate = buttons[0].form_submit_button("형식 검증")
            draft = buttons[1].form_submit_button("초안 저장")
            revise = buttons[2].form_submit_button("수정 필요로 저장")
            approve = buttons[3].form_submit_button("승인 완료로 저장", type="primary")
        if any((validate, draft, revise, approve)):
            edited = Question(level=edited_level, area=edited_area, topic=edited_topic, prompt=prompt,
                              options=options, answer=answer, explanation=explanation, option_explanations=explanations)
            current_errors = format_errors(edited)
            if validate:
                if current_errors:
                    for error in current_errors:
                        st.error(error)
                else:
                    st.success("입력 내용의 형식 검증을 통과했습니다. 아직 저장하지 않았으며 일본어 내용 검수는 별도입니다.")
            else:
                target = Status.APPROVED if approve else Status.NEEDS_REVISION if revise else Status.DRAFT
                try:
                    if not reviewer.strip():
                        raise ValueError("저장할 편집자 이름이 필요합니다. 승인 시에는 언어 검수의 명시적 확인도 필요합니다.")
                    changed = repo.save(selected, edited, target, note, record["revision"], confirmed=confirmed,
                                        reviewer=reviewer, editor=reviewer, reason=reason)
                    actual = repo.get(selected)["status"]
                    st.session_state.pop("editing_record", None)
                    st.session_state["notice"] = (f"문항 #{selected}을 '{actual}' 상태로 저장하고 이력을 기록했습니다." if changed
                                                  else "실질적인 변경이 없어 기존 버전과 승인 정보를 유지했습니다. 저장 시도는 이력에 기록했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

with export_tab:
    approved = [r for r in records if r["status"] == Status.APPROVED.value]
    exportable = [r for r in approved if not approval_export_errors(r)]
    excluded = [r for r in approved if approval_export_errors(r)]
    st.subheader("승인 완료 문항 CSV")
    st.caption("필수 해설과 승인 정보가 완전한 승인 문항을 내보냅니다(왼쪽 필터와 무관). CSV는 UTF-8 BOM이며 언어적 정확성은 편집자의 검수에 의존합니다.")
    st.write(f"승인 완료 문항: {len(approved)}개")
    st.write(f"내보내기 가능: {len(exportable)}개")
    if excluded:
        st.warning("기존 승인 문항 중 현재 형식 검증을 통과하지 못한 문항은 CSV에서 제외합니다. 기존 데이터와 상태는 보존됩니다. 내용을 보완해 초안으로 저장한 뒤 다시 검수·승인하세요.")
        st.dataframe([{"ID": r["id"], "제외 사유": " / ".join(approval_export_errors(r))} for r in excluded], hide_index=True)
    st.download_button("승인 문항 CSV 다운로드", data=approved_csv(records), file_name="kotobalab_approved.csv",
                       mime="text/csv", disabled=not exportable)
    if exportable:
        st.dataframe([{"ID": r["id"], "레벨": r["question"].level, "영역": r["question"].area,
                       "주제": r["question"].topic, "승인자": r["reviewer"]} for r in exportable], hide_index=True)

with history_tab:
    st.subheader("수정 이력 및 검수 기록")
    st.caption("생성·수정·상태 변경·승인 확인·변경 없는 저장 시도를 별도 기록으로 보존합니다. 일시는 UTC입니다.")
    if not records:
        st.info("이력을 조회할 문항이 없습니다.")
    else:
        history_by_id = {r["id"]: r for r in records}
        history_id = st.selectbox("이력을 조회할 문항", list(history_by_id), key="history_question_id",
                                  format_func=lambda i: f"#{i} · {history_by_id[i]['question'].topic}")
        entries = repo.history(history_id)
        labels = {"created": "초안 생성", "baseline": "기존 문항 기준 버전", "saved": "저장 및 검수", "no_change": "변경 없는 저장 시도"}
        st.dataframe([{"기록 ID": e["id"], "일시(UTC)": e["recorded_at"], "편집자": e["editor"],
                       "버전": f"{e['from_revision'] or '—'} → {e['to_revision']}",
                       "상태 변경": f"{(e['before_snapshot'] or {}).get('status', '—')} → {e['after_snapshot']['status']}",
                       "기록 유형": labels[e["event"]], "수정 사유": e["reason"],
                       "명시적 검수 확인": bool(e["confirmed"])} for e in entries], hide_index=True)
        if entries:
            entry_by_id = {e["id"]: e for e in entries}
            entry_id = st.selectbox("비교할 저장 기록", list(entry_by_id), key=f"history_entry_{history_id}",
                                     format_func=lambda i: f"기록 #{i} · v{entry_by_id[i]['to_revision']} · {labels[entry_by_id[i]['event']]}")
            entry = entry_by_id[entry_id]
            st.write(f"편집자: {entry['editor']} | 수정 사유: {entry['reason'] or '(미입력)'}")
            if entry["event"] == "baseline":
                st.info("이력 기능 도입 시점의 기존 버전입니다. 도입 이전의 수정 과정과 편집자는 복원할 수 없습니다.")
            elif entry["event"] == "no_change":
                st.info("실질적인 변경이 없어 문항·버전·승인 정보는 그대로 유지되었습니다.")
                with st.expander("저장 시도 입력과 기존 내용 비교"):
                    st.dataframe(comparison_rows(entry["before_snapshot"], entry["submitted_snapshot"], changed_only=True), hide_index=True)
            st.markdown("**수정 전·후 비교**")
            changed_only = st.checkbox("변경된 항목만 보기", key=f"history_changes_{history_id}")
            rows = comparison_rows(entry["before_snapshot"], entry["after_snapshot"], changed_only=changed_only)
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch")
            else:
                st.info("변경된 항목이 없습니다.")
            with st.expander("수정 전·후 전체 내용과 승인 정보"):
                left, right = st.columns(2)
                left.write("수정 전")
                left.json(entry["before_snapshot"] or {"안내": "이전 버전 기록 없음"})
                right.write("수정 후")
                right.json(entry["after_snapshot"])
