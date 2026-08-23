"""Đồ dùng chung cho các trang Streamlit.

Mọi hàm ở đây chỉ gói lại các lớp đã có trong `alphaforge` (Database,
ResearchMemory, BrainClient...). Không có logic nghiệp vụ mới nào được viết ở
lớp này — nếu một phép tính cần thay đổi thì sửa ở `alphaforge`, không sửa ở
đây, để dòng lệnh và Streamlit không bao giờ lệch nhau.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alphaforge.config import Credentials, Settings, load_settings  # noqa: E402
from alphaforge.storage.db import Database  # noqa: E402

APP_TITLE = "Alpha Research Hub"


def get_settings() -> Settings:
    """Cấu hình nền, không kèm thông tin đăng nhập của phiên trình duyệt.

    Không cache bằng `st.cache_resource`: mỗi phiên Streamlit có thể tự nhập
    thông tin đăng nhập riêng ở trang Điều khiển, và cache theo tiến trình sẽ
    làm một phiên nhìn thấy thông tin của phiên khác.
    """
    settings = load_settings()
    # Trang Theo dõi không bao giờ cần thông tin đăng nhập. Xóa hẳn để không
    # có tệp .env nào vô tình làm trang chỉ đọc có khả năng xác thực.
    settings.credentials = Credentials("", "")
    return settings


@st.cache_resource(show_spinner=False)
def get_database(db_path_str: str) -> Database:
    """Một kết nối `Database` dùng chung trong suốt phiên, theo đường dẫn kho."""
    return Database(Path(db_path_str))


def database() -> Database:
    settings = get_settings()
    return get_database(str(settings.db_path))


def session_settings() -> Settings:
    """Cấu hình nền, ghép với thông tin đăng nhập người dùng đã nhập trong phiên.

    Thông tin đăng nhập chỉ sống trong `st.session_state` — bộ nhớ của trình
    duyệt phiên đó — không bao giờ ghi xuống đĩa hay tệp cấu hình.
    """
    settings = get_settings()
    creds: Optional[Credentials] = st.session_state.get("brain_credentials")
    if creds is not None:
        settings.credentials = creds
    return settings


def is_logged_in() -> bool:
    return bool(st.session_state.get("brain_credentials"))


# Cố ý không có hàm "require_login() rồi st.stop()" dùng chung: `st.stop()`
# dừng toàn bộ script Streamlit, không chỉ khối `with tabs[i]:` đang chạy, nên
# gọi nó bên trong một tab sẽ khiến mọi tab đứng sau nó không bao giờ được vẽ
# ra. Mỗi trang tự rẽ nhánh bằng `if not is_logged_in(): ... else: ...`.


def status_counts_row(db: Database) -> Dict[str, int]:
    return db.counts_by_status()


def render_login_box() -> None:
    """Ô nhập thông tin đăng nhập, dùng ở đầu trang Điều khiển.

    Không kiểm tra tính hợp lệ tại đây — `BrainClient.authenticate()` là nơi
    duy nhất thật sự gọi máy chủ, nên lỗi đăng nhập chỉ lộ ra khi bấm hành
    động, đúng như dòng lệnh.
    """
    from alphaforge.config import Credentials

    with st.expander("Đăng nhập BRAIN", expanded=not is_logged_in()):
        st.caption(
            "Thông tin đăng nhập chỉ giữ trong bộ nhớ của phiên trình duyệt này, "
            "không bao giờ ghi xuống đĩa."
        )
        email = st.text_input(
            "Email", value=st.session_state.get("brain_email", ""), key="login_email"
        )
        password = st.text_input("Mật khẩu", type="password", key="login_password")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Đăng nhập", type="primary"):
                if not email or not password:
                    st.error("Cần cả email và mật khẩu.")
                else:
                    st.session_state["brain_credentials"] = Credentials(email, password)
                    st.session_state["brain_email"] = email
                    st.rerun()
        with col2:
            if is_logged_in() and st.button("Đăng xuất"):
                st.session_state.pop("brain_credentials", None)
                st.rerun()

        if is_logged_in():
            st.success(f"Đã lưu thông tin đăng nhập cho {st.session_state.get('brain_email')}.")


def as_dataframe_records(rows: Any) -> Any:
    """Chuẩn hóa danh sách bản ghi (dict hoặc dataclass) để đưa vào bảng."""
    return [dict(row) if not isinstance(row, dict) else row for row in rows]
