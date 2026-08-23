"""Trang Điều khiển — cần đăng nhập BRAIN, có nút hành động.

Mỗi khối ở đây gọi đúng những hàm mà lệnh CLI tương ứng gọi (`cmd_generate`,
`cmd_run`, `cmd_evaluate`, `cmd_correlate`, `cmd_alpha`) — nhiều hàm được nhập
thẳng từ `alphaforge.cli` thay vì viết lại, để hai giao diện không bao giờ ứng
xử khác nhau với cùng một hành động.

Hệ thống không bao giờ tự nộp alpha. Không nút nào ở trang này gọi điểm cuối
nộp của nền tảng — điểm cuối đó không tồn tại trong toàn bộ kho. "Đánh dấu đã
nộp" chỉ ghi chép việc người dùng nói rằng họ đã tự nộp.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from _shared import APP_TITLE, database, is_logged_in, render_login_box, session_settings

st.set_page_config(page_title=f"Điều khiển — {APP_TITLE}", page_icon="🎛️", layout="wide")
st.title("🎛️ Điều khiển")
st.caption(
    "Sinh biểu thức, chạy mô phỏng, thẩm định, kiểm tra tương quan, ghi nhận đã nộp. "
    "Một số bước gọi máy chủ BRAIN thật và tốn hạn mức mô phỏng của tài khoản."
)

render_login_box()

from alphaforge.brain.client import BrainClient  # noqa: E402
from alphaforge.brain.errors import BrainError  # noqa: E402
from alphaforge.cli import (  # noqa: E402
    _find_alpha,
    resolve_fields,
    simulation_settings,
)
from alphaforge.generator.engine import GenerationRequest, GeneratorEngine  # noqa: E402
from alphaforge.generator.templates import TEMPLATE_CATEGORIES  # noqa: E402
from alphaforge.history.fingerprint import fingerprint  # noqa: E402
from alphaforge.pipeline.correlation import CorrelationChecker  # noqa: E402
from alphaforge.pipeline.evaluation import EvaluationPipeline  # noqa: E402
from alphaforge.pipeline.robustness import PROFILES, RobustnessChecker  # noqa: E402
from alphaforge.pipeline.runner import SimulationRunner  # noqa: E402
from alphaforge.pipeline.scorer import Scorer  # noqa: E402
from alphaforge.research.memory import ResearchMemory  # noqa: E402
from alphaforge.storage.db import Status  # noqa: E402


class _Args:
    """Đối tượng giả `argparse.Namespace`, để tái dùng nguyên hàm của CLI."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return None


db = database()

tabs = st.tabs([
    "Sinh alpha", "Chạy mô phỏng", "Thẩm định cục bộ", "Tương quan", "Ứng viên & Nộp",
])

# ----------------------------------------------------------------------
# Sinh alpha — không cần BRAIN, chỉ cần trường dữ liệu đã tải về sẵn (lệnh `fields`)
# ----------------------------------------------------------------------
with tabs[0]:
    st.caption(
        "Sinh và xếp hàng biểu thức mới. Không gọi mạng — dùng danh mục trường "
        "dữ liệu đã tải sẵn trong kho bằng lệnh `alphaforge fields`."
    )
    with st.form("form_generate"):
        col1, col2, col3 = st.columns(3)
        strategy = col1.selectbox("Chiến lược", ["template", "pairwise", "mutate"])
        limit = col2.number_input("Số lượng tối đa", min_value=1, max_value=5000, value=100)
        tag = col3.text_input("Thẻ (tag)", value="streamlit")
        col4, col5, col6 = st.columns(3)
        region = col4.text_input("Region", value="USA")
        universe = col5.text_input("Universe", value="TOP3000")
        delay = col6.number_input("Delay", min_value=0, max_value=1, value=1)
        use_memory = st.checkbox(
            "Dùng trí nhớ nghiên cứu để lái trọng số", value=True,
            help="Bỏ chọn để sinh theo phân phối đều như phiên bản đầu tiên.",
        )
        dry_run = st.checkbox("Chỉ xem trước, không ghi vào kho", value=True)
        submitted = st.form_submit_button("Sinh biểu thức", type="primary")

    if submitted:
        settings = session_settings()
        args = _Args(
            strategy=strategy, limit=int(limit), tag=tag, fields=None, field_limit=200,
            templates=None, categories=None, seed=None, region=region, universe=universe,
            delay=int(delay), neutralization=None, decay=None, truncation=None,
            no_memory=not use_memory,
        )
        sim = simulation_settings(settings, args)
        fields = resolve_fields(db, args, sim)

        seed_expressions = []
        blocked = False
        if strategy == "mutate":
            seed_expressions = [r.expression for r in db.fetch_by_status(Status.PASSED, limit=200)]
            if not seed_expressions:
                st.error("Chưa có alpha nào đạt ngưỡng để làm gốc biến đổi.")
                blocked = True

        if not blocked:
            context = None
            if use_memory:
                context = ResearchMemory(db).build_context(
                    target_sharpe=float(settings.scoring.get("min_sharpe", 1.25) or 1.25),
                )

            request = GenerationRequest(
                strategy=strategy, limit=int(limit), fields=fields, seed_expressions=seed_expressions,
                max_length=int(settings.generator.get("max_expression_length", 480)), context=context,
            )
            engine = GeneratorEngine(request)
            expressions = engine.generate()

            if dry_run:
                st.success(f"{len(expressions)} biểu thức. Không ghi vào kho.")
                st.dataframe(pd.DataFrame({"expression": expressions}), use_container_width=True, hide_index=True)
            else:
                run_id = db.create_run(tag=tag, strategy=strategy)
                fingerprints = {}
                for expression in expressions:
                    meta = fingerprint(expression)
                    fingerprints[expression] = {"fingerprint": meta["family"], "family": meta["family"]}
                added = db.add_alphas(
                    expressions, sim, run_id=run_id, generation_strategy=strategy, fingerprints=fingerprints,
                )
                st.success(
                    f"Sinh {len(expressions)} biểu thức, thêm mới {added} bản ghi. "
                    f"Bỏ qua {len(expressions) - added} bản ghi trùng."
                )

# ----------------------------------------------------------------------
# Chạy mô phỏng — cần BRAIN, tốn hạn mức
# ----------------------------------------------------------------------
with tabs[1]:
    if not is_logged_in():
        # `st.stop()` dừng toàn bộ script Streamlit, không chỉ khối lệnh hiện
        # tại — dùng nó ở đây từng làm ba tab phía sau (Thẩm định, Tương quan,
        # Ứng viên & Nộp) không bao giờ được vẽ ra khi chưa đăng nhập. Rẽ nhánh
        # tường minh thay vì gọi hàm chặn chung.
        st.warning("Cần đăng nhập BRAIN trước. Nhập thông tin đăng nhập ở đầu trang này.")
    else:
        st.warning("Bước này gọi máy chủ BRAIN thật và tốn hạn mức mô phỏng của tài khoản.")
        pending = db.counts_by_status().get(Status.PENDING, 0)
        st.metric("Đang chờ trong hàng đợi", pending)

        col1, col2 = st.columns(2)
        concurrency = col1.number_input("Mức đồng thời (trần)", min_value=1, max_value=20, value=3)
        run_limit = col2.number_input(
            "Giới hạn số alpha chạy lượt này", min_value=1, max_value=1000, value=min(20, max(pending, 1)),
        )
        st.caption(
            "Trang sẽ chờ tới khi chạy xong — giao diện bị khoá trong lúc đó. "
            "Chạy lô nhỏ (10-30 alpha) mỗi lượt để không phải chờ lâu."
        )
        if st.button("Chạy mô phỏng", type="primary", disabled=(pending == 0)):
            settings = session_settings()
            client = BrainClient(settings)
            progress_box = st.empty()
            log_lines: list[str] = []

            def _on_progress(payload):
                log_lines.append(
                    f"{payload['status']:<9} {payload['alpha_id']} sharpe={payload['metrics'].get('sharpe')}"
                )
                progress_box.code("\n".join(log_lines[-20:]), language="text")

            runner = SimulationRunner(
                client=client, db=db, scorer=Scorer(settings.scoring),
                concurrency=int(concurrency), progress_callback=_on_progress,
            )
            with st.spinner(f"Đang mô phỏng, tối đa {int(run_limit)} alpha..."):
                try:
                    stats = runner.run(limit=int(run_limit))
                except BrainError as exc:
                    st.error(f"Lỗi BRAIN: {exc}")
                    stats = None
            if stats is not None:
                st.success("Hoàn tất lượt chạy.")
                st.json(stats)

# ----------------------------------------------------------------------
# Thẩm định cục bộ — không gọi BRAIN
# ----------------------------------------------------------------------
with tabs[2]:
    st.caption(
        "Chấm điểm, kiểm tra độ bền, lọc trùng cấu trúc. Không gọi mạng — chạy "
        "được kể cả khi chưa đăng nhập."
    )
    profile = st.selectbox("Hồ sơ kiểm tra độ bền", sorted(PROFILES), index=sorted(PROFILES).index("standard"))
    eval_limit = st.number_input("Giới hạn số bản ghi", min_value=1, max_value=5000, value=500)
    if st.button("Chạy thẩm định", type="primary"):
        settings = session_settings()
        pipeline = EvaluationPipeline(db, Scorer(settings.scoring), robustness=RobustnessChecker(profile=profile))
        with st.spinner("Đang thẩm định..."):
            outcome = pipeline.run(limit=int(eval_limit))
        st.success("Hoàn tất.")
        st.json(outcome.as_dict())
        if outcome.candidates == 0 and outcome.robust:
            st.info(
                f"{outcome.robust} alpha đã qua thẩm định. Dùng tab 'Ứng viên & Nộp' "
                "để đưa lên bậc ứng viên."
            )

# ----------------------------------------------------------------------
# Tương quan — cần BRAIN, tốn tài nguyên máy chủ
# ----------------------------------------------------------------------
with tabs[3]:
    if not is_logged_in():
        st.warning("Cần đăng nhập BRAIN trước. Nhập thông tin đăng nhập ở đầu trang này.")
    else:
        st.warning("Bước này gọi máy chủ BRAIN thật. Chỉ chạy trên nhóm alpha đã đạt ngưỡng.")
        corr_limit = st.number_input("Giới hạn số alpha kiểm tra", min_value=1, max_value=500, value=50)
        check_prod = st.checkbox("Kiểm cả tương quan sản phẩm (tốn nhất)", value=False)
        if st.button("Kiểm tra tương quan", type="primary"):
            settings = session_settings()
            checker = CorrelationChecker(client=BrainClient(settings), db=db, config=dict(settings.correlation), check_prod=check_prod)
            with st.spinner("Đang kiểm tra tương quan..."):
                try:
                    stats = checker.run(limit=int(corr_limit))
                except BrainError as exc:
                    st.error(f"Lỗi BRAIN: {exc}")
                    stats = None
            if stats is not None:
                st.success("Hoàn tất.")
                st.json(stats)

# ----------------------------------------------------------------------
# Ứng viên & Nộp — không gọi BRAIN, hệ thống không tự nộp thay
# ----------------------------------------------------------------------
with tabs[4]:
    st.caption(
        "Hệ thống **không nộp thay**. Nộp alpha luôn là hành động thủ công của bạn "
        "trên nền tảng BRAIN. Ở đây chỉ ghi nhận lại việc bạn đã tự nộp."
    )
    settings = session_settings()
    memory = ResearchMemory(db)
    candidates = memory.candidates(limit=50)
    if candidates:
        st.dataframe(pd.DataFrame(candidates), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có ứng viên nào. Chạy thẩm định trước.")

    st.subheader("Đưa alpha lên bậc ứng viên")
    col1, col2 = st.columns([1, 3])
    promote_id = col1.number_input("Số thứ tự bản ghi (row_id)", min_value=1, step=1, key="promote_id")
    if col2.button("Đưa lên ứng viên"):
        pipeline = EvaluationPipeline(db, Scorer(settings.scoring))
        if pipeline.promote_to_candidate(int(promote_id)):
            st.success(f"Alpha {int(promote_id)} đã lên bậc ứng viên.")
        else:
            st.error(f"Alpha {int(promote_id)} chưa qua đủ các bước thẩm định.")

    st.subheader("Ghi nhận đã tự nộp")
    col1, col2, col3 = st.columns([1, 2, 1])
    submit_id = col1.number_input("Số thứ tự bản ghi (row_id)", min_value=1, step=1, key="submit_id")
    by = col2.text_input("Tên người xác nhận", value="")
    if col3.button("Đánh dấu đã nộp", type="primary"):
        pipeline = EvaluationPipeline(db, Scorer(settings.scoring))
        if pipeline.mark_submitted(int(submit_id), confirmed_by=by):
            st.success(f"Đã ghi nhận alpha {int(submit_id)} là đã nộp.")
        else:
            st.error(
                f"Alpha {int(submit_id)} không ở trạng thái ứng viên hoặc đang xem xét."
            )

    st.subheader("Tra cứu một alpha")
    lookup = st.text_input("Mã alpha hoặc số thứ tự bản ghi", key="lookup_alpha")
    if lookup:
        record = _find_alpha(db, lookup)
        if record is None:
            st.warning(f"Không tìm thấy alpha {lookup}.")
        else:
            st.json({
                "row_id": record.id, "alpha_id": record.alpha_id, "expression": record.expression,
                "status": record.status, "evaluation_status": record.evaluation_status,
                "score": record.score, "metrics": record.metrics,
                "reject_reason": record.reject_reason,
            })
