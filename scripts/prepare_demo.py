"""Create fictional screenshot data offline, without opening the real database."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kotobalab.db import Repository
from kotobalab.models import Status
from kotobalab.runtime import DEMO_DB
from kotobalab.samples import sample_question


EDITOR = "데모 편집자"


def prepare_demo(path=DEMO_DB):
    path = Path(path).resolve()
    # Only demo storage or the project-local test directory is accepted.
    allowed = (ROOT / "data" / "demo", ROOT / ".pytest-tmp")
    if not any(path.is_relative_to(folder.resolve()) for folder in allowed):
        raise ValueError("데모 DB는 data/demo 또는 프로젝트 테스트 폴더에만 만들 수 있습니다.")
    if path.exists():
        return False  # Never reset existing demo edits or history.
    repo = Repository(path)
    draft = sample_question("N2", "어휘", "[데모] 해설 누락 확인")
    draft = draft.model_copy(update={"option_explanations": [""] + draft.option_explanations[1:]})
    repo.create(draft, "sample")
    repo.create(sample_question("N1", "어휘", "[데모] 편집 대기"), "sample")

    approved = sample_question("N2", "문법", "[데모] 승인 및 CSV")
    ident = repo.create(approved, "sample")
    repo.save(ident, approved, Status.APPROVED,
              "공개 촬영용 가상 승인 기록입니다. 실제 교재 검수 완료를 뜻하지 않습니다.", 1,
              confirmed=True, reviewer=EDITOR, editor=EDITOR, reason="샘플의 명시적 승인 흐름 시연")

    corrected = sample_question("N1", "문법", "[데모] AI 초안 오류 발견과 해설 수정")
    explanations = corrected.option_explanations.copy()
    explanations[0] = ""
    explanations[1] = "が早いか는 과거형 確認した 뒤에는 연결되지 않습니다."
    flawed = corrected.model_copy(update={"option_explanations": explanations})
    ident = repo.create(flawed, "sample")
    repo.save(ident, flawed, Status.NEEDS_REVISION,
              "AI 초안의 오류를 재현한 가상 사례입니다(실제 API 호출 없음). 정답 개별 해설이 누락되었고, 문법 해설의 접속 금지 단정에 문제가 있습니다.", 1,
              editor=EDITOR, reason="해설 누락과 부정확한 문법 설명 발견")
    repo.save(ident, corrected, Status.NEEDS_REVISION,
              "정답 개별 해설을 보완하고 오답 근거를 문맥의 시간 관계와 양보 관계 차이로 수정했습니다. 최종 언어 검수와 재승인은 대기 중입니다.", 2,
              editor=EDITOR, reason="문법의 잘못된 단정을 제거하고 문맥 중심 해설로 수정")
    return True


if __name__ == "__main__":
    created = prepare_demo()
    print("데모 DB 생성 완료: data/demo/kotobalab_demo.db" if created else "기존 데모 DB를 유지했습니다(덮어쓰기 없음).")
    print("실행: .\\.venv\\Scripts\\python.exe -m streamlit run app.py --server.port 8502 -- --demo")
