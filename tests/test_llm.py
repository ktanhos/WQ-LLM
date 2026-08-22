"""Kiểm thử lớp mô hình ngôn ngữ tùy chọn.

Không kiểm thử nào ở đây gọi API thật. Trọng tâm là: thiếu cấu hình thì hệ
thống báo rõ và tiếp tục chạy, chứ không sập.
"""

from alphaforge.llm import ClaudeProvider, NullProvider, OllamaProvider, get_provider
from alphaforge.llm.base import (
    _prompt_explain,
    _prompt_gaps,
    _prompt_hypotheses,
    _prompt_summarize,
)


# ----------------------------------------------------------------------
# Lựa chọn nhà cung cấp
# ----------------------------------------------------------------------
def test_default_is_no_provider(monkeypatch):
    monkeypatch.delenv("ALPHAFORGE_LLM_PROVIDER", raising=False)
    provider = get_provider({})
    assert isinstance(provider, NullProvider)
    assert provider.is_available() is False


def test_explicit_none_disables_provider(monkeypatch):
    monkeypatch.delenv("ALPHAFORGE_LLM_PROVIDER", raising=False)
    assert isinstance(get_provider({"provider": "none"}), NullProvider)


def test_unknown_provider_falls_back_instead_of_raising(monkeypatch):
    """Cấu hình sai không được làm hỏng các lệnh không liên quan."""
    monkeypatch.delenv("ALPHAFORGE_LLM_PROVIDER", raising=False)
    assert isinstance(get_provider({"provider": "khong-ton-tai"}), NullProvider)


def test_provider_selected_from_config(monkeypatch):
    monkeypatch.delenv("ALPHAFORGE_LLM_PROVIDER", raising=False)
    assert isinstance(get_provider({"provider": "claude"}), ClaudeProvider)
    assert isinstance(get_provider({"provider": "ollama"}), OllamaProvider)


def test_environment_variable_overrides_config(monkeypatch):
    monkeypatch.setenv("ALPHAFORGE_LLM_PROVIDER", "ollama")
    assert isinstance(get_provider({"provider": "claude"}), OllamaProvider)


def test_model_can_be_set_from_environment(monkeypatch):
    monkeypatch.setenv("ALPHAFORGE_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("ALPHAFORGE_LLM_MODEL", "mo-hinh-rieng")
    assert get_provider({}).model == "mo-hinh-rieng"


# ----------------------------------------------------------------------
# Nhà cung cấp rỗng
# ----------------------------------------------------------------------
def test_null_provider_returns_actionable_message():
    response = NullProvider().complete("bat ky")
    assert response.available is False
    assert response.text == ""
    assert "settings.yaml" in response.error


def test_null_provider_supports_every_research_task():
    provider = NullProvider()
    for response in (
        provider.summarize_research({"total": 0}),
        provider.propose_hypotheses({"total": 0}),
        provider.analyze_gaps([]),
        provider.explain_alpha("rank(close)"),
    ):
        assert response.available is False
        assert response.error


# ----------------------------------------------------------------------
# Claude
# ----------------------------------------------------------------------
def test_claude_is_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = ClaudeProvider()
    assert provider.is_available() is False
    response = provider.complete("xin chao")
    assert response.available is False
    assert "ANTHROPIC_API_KEY" in response.error


def test_claude_never_reads_key_from_config(monkeypatch):
    """Khóa API chỉ đến từ biến môi trường, không bao giờ từ tệp cấu hình."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = get_provider({"provider": "claude", "api_key": "khong-duoc-dung"})
    assert provider.api_key == ""
    assert provider.is_available() is False


# ----------------------------------------------------------------------
# Ollama
# ----------------------------------------------------------------------
def test_ollama_host_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert OllamaProvider().host == "http://127.0.0.1:11434"


def test_ollama_reports_unavailable_when_service_is_down(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    provider = OllamaProvider(host="http://127.0.0.1:1")
    assert provider.is_available() is False


def test_ollama_complete_returns_error_instead_of_raising():
    provider = OllamaProvider(host="http://127.0.0.1:1", timeout=0.5)
    response = provider.complete("xin chao")
    assert response.available is False
    assert response.error


# ----------------------------------------------------------------------
# Lời nhắc
# ----------------------------------------------------------------------
def test_summary_prompt_includes_sample_size():
    """Lời nhắc phải nêu cỡ mẫu để mô hình không kết luận quá mức."""
    prompt = _prompt_summarize(
        {"total": 10, "counts": {}, "pass_rate": 0.2,
         "metrics": {"sharpe": {"median": 1.2, "p25": 0.9, "p75": 1.6, "count": 10}}}
    )
    assert "cỡ mẫu" in prompt
    assert "1.2" in prompt


def test_hypotheses_prompt_mentions_saturated_families():
    prompt = _prompt_hypotheses(
        {"underexplored_structures": [{"family": "F1"}],
         "frequently_tested_structures": [{"family": "F2"}]},
        3,
    )
    assert "bão hòa" in prompt
    assert "3 giả thuyết" in prompt


def test_gaps_and_explain_prompts_are_built():
    assert "khoảng trống" in _prompt_gaps([{"family": "F1"}])
    assert "rank(close)" in _prompt_explain("rank(close)", {"sharpe": 1.5})
