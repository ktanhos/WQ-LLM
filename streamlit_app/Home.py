"""Trang chủ Streamlit của Alpha Research Hub.

Chỉ đọc kho SQLite để hiện vài con số tổng quan. Không đăng nhập, không gọi
BRAIN. Hai trang con:

    Theo dõi     chỉ đọc, không cần đăng nhập, an toàn tuyệt đối
    Điều khiển   cần đăng nhập BRAIN, có nút hành động (sinh, mô phỏng, nộp)
"""

from __future__ import annotations

import streamlit as st

from _shared import APP_TITLE, database, is_logged_in

st.set_page_config(page_title=APP_TITLE, page_icon="🔬", layout="wide")

st.title("🔬 " + APP_TITLE)
st.caption(
    "Giao diện Streamlit của alphaforge. Toàn bộ logic nằm trong gói "
    "`alphaforge` — trang này chỉ hiển thị, không tính toán gì mới."
)

db = database()
counts = db.counts_by_status()
total = sum(counts.values())

cols = st.columns(5)
cols[0].metric("Tổng biểu thức", total)
cols[1].metric("Đang chờ", counts.get("PENDING", 0))
cols[2].metric("Đạt ngưỡng", counts.get("PASSED", 0))
cols[3].metric("Ứng viên", counts.get("CANDIDATE", 0))
cols[4].metric("Đã nộp", counts.get("SUBMITTED", 0))

st.divider()

col_left, col_right = st.columns(2)
with col_left:
    st.subheader("📊 Theo dõi")
    st.write(
        "Trí nhớ nghiên cứu, khoảng trống, ưu tiên, thí nghiệm, phả hệ alpha, "
        "lịch sử đã nộp. **Chỉ đọc, không cần đăng nhập.**"
    )
    st.page_link("pages/1_📊_Theo_dõi.py", label="Mở trang Theo dõi", icon="📊")

with col_right:
    st.subheader("🎛️ Điều khiển")
    st.write(
        "Sinh biểu thức, chạy mô phỏng, thẩm định, kiểm tra tương quan, ghi "
        "nhận đã nộp. **Cần đăng nhập BRAIN**, một số bước tốn hạn mức mô phỏng."
    )
    st.page_link("pages/2_🎛️_Điều_khiển.py", label="Mở trang Điều khiển", icon="🎛️")
    if is_logged_in():
        st.caption(f"Đã đăng nhập: {st.session_state.get('brain_email')}")
    else:
        st.caption("Chưa đăng nhập.")

st.divider()
st.caption(
    "Hệ thống không bao giờ tự nộp alpha. Bậc cao nhất mà máy đưa alpha tới "
    "là CANDIDATE; nộp là hành động của người, xác nhận thủ công ở trang Điều khiển."
)
