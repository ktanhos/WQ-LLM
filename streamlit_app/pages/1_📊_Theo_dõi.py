"""Trang Theo dõi — chỉ đọc, không đăng nhập, không gọi BRAIN.

Bảy khối tương ứng bảy màn hình của bảng theo dõi FastAPI
(`alphaforge/web/app.py`): Hàng đợi, Tổng quan nghiên cứu, Khoảng trống,
Ưu tiên, Thí nghiệm, Phả hệ alpha, Alpha lịch sử.

Mọi khối chỉ gọi thẳng các lớp trong `alphaforge.research` / `alphaforge.storage`
/ `alphaforge.history` — đúng những lớp mà FastAPI dashboard cũng gọi — nên hai
giao diện không bao giờ tính ra hai con số khác nhau cho cùng một câu hỏi.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from _shared import APP_TITLE, as_dataframe_records, database

st.set_page_config(page_title=f"Theo dõi — {APP_TITLE}", page_icon="📊", layout="wide")
st.title("📊 Theo dõi")
st.caption("Chỉ đọc kho SQLite cục bộ. Không đăng nhập, không có lệnh gọi mạng nào ở trang này.")

db = database()

from alphaforge.history.analyzer import analyze, research_gaps  # noqa: E402
from alphaforge.history.report import build_report, load_history  # noqa: E402
from alphaforge.research.advisor import RuleBasedResearchAdvisor  # noqa: E402
from alphaforge.research.gap import ResearchGap  # noqa: E402
from alphaforge.research.memory import ResearchMemory  # noqa: E402
from alphaforge.research.priority import ResearchPriority  # noqa: E402
from alphaforge.research.report import ExperimentReport  # noqa: E402
from alphaforge.research.store import ResearchStore  # noqa: E402

memory = ResearchMemory(db)
store = ResearchStore(db)

tabs = st.tabs([
    "Hàng đợi", "Tổng quan nghiên cứu", "Khoảng trống", "Ưu tiên",
    "Thí nghiệm", "Phả hệ alpha", "Alpha lịch sử",
])

# ----------------------------------------------------------------------
# Hàng đợi
# ----------------------------------------------------------------------
with tabs[0]:
    counts = db.counts_by_status()
    total = sum(counts.values())
    finished = counts.get("PASSED", 0) + counts.get("REJECTED", 0) + counts.get("FAILED", 0)
    progress = round(finished / total * 100, 1) if total else 0.0
    st.progress(min(1.0, progress / 100), text=f"Tiến độ: {progress}%")

    cols = st.columns(len(counts) or 1)
    for col, (status, count) in zip(cols, sorted(counts.items())):
        col.metric(status, count)

    st.subheader("Xếp hạng theo điểm tổng hợp")
    top = db.top_alphas(50)
    if top:
        rows = []
        for item in as_dataframe_records(top):
            metrics = item.get("metrics") or {}
            rows.append({
                "alpha_id": item.get("alpha_id"),
                "expression": item.get("expression"),
                "status": item.get("status"),
                "score": item.get("score"),
                "sharpe": metrics.get("sharpe"),
                "fitness": metrics.get("fitness"),
                "turnover": metrics.get("turnover"),
                "self_correlation": item.get("self_correlation"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có dữ liệu.")

    st.subheader("Hoạt động gần đây")
    recent = as_dataframe_records(db.recent_activity(30))
    if recent:
        st.dataframe(pd.DataFrame(recent), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có dữ liệu.")

# ----------------------------------------------------------------------
# Tổng quan nghiên cứu
# ----------------------------------------------------------------------
with tabs[1]:
    statistics = memory.statistics()
    counts = statistics["counts"]
    # Tách "đã sinh" khỏi "đã mô phỏng": sinh ra một biểu thức không đồng
    # nghĩa với đã tốn một lượt mô phỏng của tài khoản.
    cols = st.columns(4)
    cols[0].metric("Đã sinh", counts["tested"])
    cols[1].metric("Đã mô phỏng", counts["simulated"])
    cols[2].metric("Đạt", counts["passed"])
    cols[3].metric("Đã nộp", counts["submitted"])
    cols = st.columns(4)
    cols[0].metric("Bị loại", counts["rejected"])
    cols[1].metric("Không hợp lệ", counts["invalid"])
    cols[2].metric("Sharpe trung vị", statistics["median_sharpe"])
    cols[3].metric("Sharpe tốt nhất", statistics["best_sharpe"])

    st.subheader("Độ phủ theo từng chiều")
    st.caption("Đây là mức độ đã nghiên cứu, không phải đánh giá chất lượng.")
    coverage = memory.coverage()
    coverage_rows = [
        {
            "chiều": dimension,
            "số giá trị": statistics["coverage_sizes"].get(dimension, 0),
            "hay gặp nhất": ", ".join(
                f"{key}={count}" for key, count in counter.most_common(8)
            ) or "chưa khảo sát",
        }
        for dimension, counter in coverage.items()
    ]
    st.dataframe(pd.DataFrame(coverage_rows), use_container_width=True, hide_index=True)

    st.subheader("Nên nghiên cứu gì tiếp")
    st.caption(
        "Đề xuất dựa trên độ phủ và thiếu hụt, kèm thí nghiệm gợi ý. Hệ thống "
        "chỉ đề xuất; quyết định chạy hay không là của người nghiên cứu."
    )
    suggestions = RuleBasedResearchAdvisor(memory).suggest(5)
    if not suggestions:
        st.info("Chưa đủ dữ liệu để đề xuất. Chạy: alphaforge history scan")
    for item in suggestions:
        with st.container(border=True):
            st.markdown(f"**{item.rank}. {item.direction}**")
            for reason in item.reasons:
                st.markdown(f"- {reason.lstrip('- ')}")
            st.caption(
                f"Cỡ mẫu {item.sample_size} · Bão hòa {item.saturation} · "
                f"Ưu tiên {item.priority} · Độ tin cậy {item.confidence}"
            )
            experiment = item.suggested_experiment
            if experiment:
                st.code(
                    f"Biểu thức gốc: {experiment.get('base_expression')}\n"
                    f"Biến khảo sát: {experiment.get('variable')}\n"
                    f"Giá trị:       {experiment.get('values')}",
                    language="text",
                )

    st.subheader("Họ cấu trúc tốt nhất và kém nhất")
    analysis = analyze(load_history(db))
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Tốt nhất**")
        best = as_dataframe_records(analysis.get("high_performing_structures", []))
        if best:
            st.dataframe(pd.DataFrame(best), use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có dữ liệu.")
    with col_b:
        st.markdown("**Kém nhất**")
        worst = as_dataframe_records(analysis.get("low_performing_structures", []))
        if worst:
            st.dataframe(pd.DataFrame(worst), use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có dữ liệu.")

# ----------------------------------------------------------------------
# Khoảng trống
# ----------------------------------------------------------------------
with tabs[2]:
    st.caption("Họ còn ít alpha nhưng đã cho tín hiệu khả quan, đáng dành thêm hạn mức mô phỏng.")
    family_gaps = research_gaps(analyze(load_history(db)), limit=20)
    if family_gaps:
        st.dataframe(pd.DataFrame(family_gaps), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa phát hiện khoảng trống nào.")

    st.subheader("Thiếu hụt theo từng loại")
    detailed = ResearchGap(memory).find(limit_per_kind=5)
    rows = [
        {
            "loại": item.kind, "khóa": item.key, "đã thử": item.count,
            "ưu tiên": item.priority.score if item.priority else None,
            "độ tin cậy": item.priority.confidence if item.priority else None,
            "lý do": item.reason,
        }
        for item in detailed
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa phát hiện thiếu hụt nào. Kho lịch sử có thể còn rỗng.")

# ----------------------------------------------------------------------
# Ưu tiên
# ----------------------------------------------------------------------
with tabs[3]:
    dimension = st.selectbox(
        "Chiều xếp hạng", ["family", "field", "operator", "template"], index=0,
    )
    engine = ResearchPriority()
    profiles = memory.profiles()
    coverage_for_dim = memory.coverage()[dimension]
    scores = [
        engine.score(
            key, dimension=dimension, sample_size=count,
            median_sharpe=profiles[key].median_sharpe if key in profiles else None,
            pass_rate=profiles[key].pass_rate if key in profiles else 0.0,
        )
        for key, count in coverage_for_dim.items()
    ]
    ranked = engine.rank(scores, limit=30)
    if ranked:
        rows = [
            {
                "khóa": item.key, "cỡ mẫu": item.sample_size, "điểm": item.score,
                "độ tin cậy": item.confidence, "lý do": "; ".join(item.reasons),
            }
            for item in ranked
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có dữ liệu để xếp hạng.")

# ----------------------------------------------------------------------
# Thí nghiệm
# ----------------------------------------------------------------------
with tabs[4]:
    connection = db.connect()
    try:
        experiment_rows = [dict(row) for row in connection.execute(
            "SELECT id, hypothesis_id, name, variable_changed, status,"
            " base_expression FROM experiments ORDER BY id DESC"
        ).fetchall()]
    finally:
        connection.close()

    if not experiment_rows:
        st.info("Chưa có thí nghiệm nào. Tạo bằng: alphaforge experiment design")
    else:
        st.dataframe(pd.DataFrame(experiment_rows), use_container_width=True, hide_index=True)
        ids = [row["id"] for row in experiment_rows]
        chosen = st.selectbox("Xem chi tiết thí nghiệm", ids)
        if chosen:
            detail = store.experiment_detail(chosen)
            experiment = detail["experiment"]
            design = detail["design"]
            st.markdown(
                f"**{experiment.get('name')}** — đổi **{experiment.get('variable_changed')}** "
                f"qua {design.get('values')}"
            )
            st.code(experiment.get("base_expression") or "", language="text")
            counts_text = " · ".join(
                f"{key}={value}" for key, value in detail["status_counts"].items()
            ) or "chưa có alpha nào"
            st.caption(f"Alpha: {counts_text}")

            try:
                report = ExperimentReport(db).build(chosen)
                st.caption(f"Mức bằng chứng: {report['conclusion']['evidence']}")
                st.write(report["conclusion"]["summary"])
                variant_results = {item["label"]: item for item in report.get("variants", [])}
            except ValueError:
                variant_results = {}

            variant_rows = []
            for variant in detail["variants"]:
                result = variant_results.get(variant["label"], {})
                variant_rows.append({
                    "biến thể": variant["label"],
                    "biểu thức": variant["expression"],
                    "trạng thái": variant["result_status"],
                    "số alpha": result.get("sample_size", 0),
                    "sharpe trung vị": result.get("median_sharpe"),
                })
            st.dataframe(pd.DataFrame(variant_rows), use_container_width=True, hide_index=True)

# ----------------------------------------------------------------------
# Phả hệ alpha
# ----------------------------------------------------------------------
with tabs[5]:
    st.caption(
        "Tra bằng mã nền tảng hoặc mã cục bộ (ví dụ LOCAL-00000001). Alpha có mã "
        "cục bộ ngay khi sinh, chỉ có mã nền tảng sau khi mô phỏng xong."
    )
    key = st.text_input("Mã alpha")
    if key:
        connection = db.connect()
        try:
            row = connection.execute(
                "SELECT id, local_id, alpha_id, expression, status, evaluation_status,"
                " score, experiment_id, variant_id, family FROM alphas"
                " WHERE alpha_id = ? OR local_id = ? LIMIT 1",
                (key, key),
            ).fetchone()
        finally:
            connection.close()
        record = dict(row) if row else None

        lookup_keys = [key]
        if record:
            lookup_keys = [k for k in (record.get("alpha_id"), record.get("local_id")) if k] or lookup_keys

        ancestry, seen = [], set()
        children = []
        for lookup in lookup_keys:
            for item in store.ancestry(lookup):
                marker = item.get("alpha_id")
                if marker not in seen:
                    seen.add(marker)
                    ancestry.append(item)
            children.extend(store.get_children(lookup))

        if not record and not ancestry:
            st.warning("Không tìm thấy alpha nào mang mã này.")
        else:
            if record:
                st.code(record.get("expression") or "", language="text")
                st.caption(
                    f"Trạng thái {record.get('status')} · Thẩm định {record.get('evaluation_status')} · "
                    f"Mã cục bộ {record.get('local_id') or '—'} · "
                    f"Mã nền tảng {record.get('alpha_id') or 'chưa mô phỏng'}"
                )
            if ancestry:
                st.dataframe(pd.DataFrame(ancestry), use_container_width=True, hide_index=True)
            st.caption(f"{len(children)} alpha con được sinh ra từ alpha này.")

# ----------------------------------------------------------------------
# Alpha lịch sử
# ----------------------------------------------------------------------
with tabs[6]:
    st.subheader("Lịch sử nộp trên BRAIN")
    history_rows = [
        {k: v for k, v in row.items() if k != "raw_json"}
        for row in load_history(db)
        if str(row.get("status") or "").upper() not in ("", "UNSUBMITTED")
    ][:50]
    if history_rows:
        st.dataframe(pd.DataFrame(history_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có dữ liệu.")

    st.subheader("Dự án nghiên cứu")
    projects = []
    for project in store.list_projects():
        hypotheses = store.list_hypotheses(int(project["id"]))
        experiment_count = sum(
            len(store.list_experiments(int(h["id"]))) for h in hypotheses
        )
        projects.append({
            "dự án": project.get("name"), "họ": project.get("family"),
            "mục tiêu": project.get("objective"), "giả thuyết": len(hypotheses),
            "thí nghiệm": experiment_count, "trạng thái": project.get("status"),
        })
    if projects:
        st.dataframe(pd.DataFrame(projects), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có dự án nào.")

    st.subheader("Tương quan")
    connection = db.connect()
    try:
        corr_rows = [dict(row) for row in connection.execute(
            """
            SELECT id, alpha_id, expression, status, score,
                   self_correlation, prod_correlation, reject_reason
              FROM alphas
             WHERE self_correlation IS NOT NULL OR prod_correlation IS NOT NULL
             ORDER BY id DESC LIMIT 50
            """
        ).fetchall()]
    finally:
        connection.close()
    if corr_rows:
        st.dataframe(pd.DataFrame(corr_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có kết quả tương quan nào.")
