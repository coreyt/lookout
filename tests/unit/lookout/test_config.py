"""Tests for LookoutConfig LLM settings."""

from __future__ import annotations

from pathlib import Path

from lookout.config import LookoutConfig


class TestLLMConfigDefaults:
    def test_default_provider(self) -> None:
        config = LookoutConfig()
        assert config.llm_provider == "anthropic"

    def test_default_model_is_none(self) -> None:
        config = LookoutConfig()
        assert config.llm_model is None

    def test_default_temperature(self) -> None:
        config = LookoutConfig()
        assert config.llm_temperature == 0.0

    def test_default_max_tokens(self) -> None:
        config = LookoutConfig()
        assert config.llm_max_tokens == 4096

    def test_default_max_retries(self) -> None:
        config = LookoutConfig()
        assert config.llm_max_retries == 3

    def test_default_timeout(self) -> None:
        config = LookoutConfig()
        assert config.llm_timeout == 60.0


class TestLLMConfigFromTOML:
    def test_load_llm_provider_from_lookout_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "lookout.toml"
        toml_file.write_text('llm_provider = "openai"\n')
        config = LookoutConfig._load_from_toml(toml_file, section=None)
        assert config.llm_provider == "openai"

    def test_load_llm_model_from_lookout_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "lookout.toml"
        toml_file.write_text('llm_model = "gpt-4o"\n')
        config = LookoutConfig._load_from_toml(toml_file, section=None)
        assert config.llm_model == "gpt-4o"

    def test_load_llm_temperature_from_lookout_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "lookout.toml"
        toml_file.write_text("llm_temperature = 0.7\n")
        config = LookoutConfig._load_from_toml(toml_file, section=None)
        assert config.llm_temperature == 0.7

    def test_load_llm_max_tokens_from_lookout_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "lookout.toml"
        toml_file.write_text("llm_max_tokens = 8192\n")
        config = LookoutConfig._load_from_toml(toml_file, section=None)
        assert config.llm_max_tokens == 8192

    def test_load_llm_max_retries_from_lookout_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "lookout.toml"
        toml_file.write_text("llm_max_retries = 5\n")
        config = LookoutConfig._load_from_toml(toml_file, section=None)
        assert config.llm_max_retries == 5

    def test_load_llm_settings_from_pyproject_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text(
            "[tool.lookout]\n"
            'llm_provider = "gemini"\n'
            'llm_model = "gemini-2.0-flash-exp"\n'
            "llm_temperature = 0.5\n"
        )
        config = LookoutConfig._load_from_toml(toml_file, section="tool.lookout")
        assert config.llm_provider == "gemini"
        assert config.llm_model == "gemini-2.0-flash-exp"
        assert config.llm_temperature == 0.5

    def test_unknown_llm_fields_ignored(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "lookout.toml"
        toml_file.write_text(
            'llm_provider = "anthropic"\nllm_unknown_field = "should be ignored"\n'
        )
        config = LookoutConfig._load_from_toml(toml_file, section=None)
        assert config.llm_provider == "anthropic"

    def test_mixed_core_and_llm_settings(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "lookout.toml"
        toml_file.write_text(
            'rules_dir = ".my-rules"\n'
            'format = "sarif"\n'
            'llm_provider = "openai"\n'
            'llm_model = "gpt-4o-mini"\n'
            "llm_max_tokens = 2048\n"
        )
        config = LookoutConfig._load_from_toml(toml_file, section=None)
        assert config.rules_dir == Path(".my-rules")
        assert config.format == "sarif"
        assert config.llm_provider == "openai"
        assert config.llm_model == "gpt-4o-mini"
        assert config.llm_max_tokens == 2048
