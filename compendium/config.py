"""Configuration management for the compendium builder."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from compendium.models import DomainProfile, GuardrailConfig, TieredModelConfig


class CompendiumConfig(BaseSettings):
    """
    Configuration loaded from (in priority order):
    1. Environment variables (COMPENDIUM_ prefix)
    2. .env file
    3. CLAUDE_CODE_OAUTH_TOKEN (fallback for Claude Code sessions)
    4. Defaults below
    """

    model_config = SettingsConfigDict(
        env_prefix="COMPENDIUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API
    anthropic_api_key: str = Field(
        default="",
        alias="ANTHROPIC_API_KEY",
        description="Anthropic API key",
    )
    anthropic_auth_token: str = Field(
        default="",
        description="OAuth/Bearer token (fallback when no API key)",
    )
    model_root: str = Field(
        default="claude-sonnet-4-6",
        description="Model for root agent REPL loops",
    )
    model_sub_call: str = Field(
        default="claude-sonnet-4-6",
        description="Model for llm_query sub-calls (cheaper/faster)",
    )

    # Tiered model selection (overrides model_root/model_sub_call per tier)
    model_strategic: Optional[str] = Field(
        default=None,
        description="Model for strategic tier (scoping, quality, layer 0-1 entries). Falls back to model_root.",
    )
    model_synthesis: Optional[str] = Field(
        default=None,
        description="Model for synthesis tier (mid-layer entries, cross-referencing). Falls back to model_root.",
    )
    model_mechanical: Optional[str] = Field(
        default=None,
        description="Model for mechanical tier (deep-layer entries, formatting). Falls back to model_sub_call.",
    )

    # Provider-agnostic LLM endpoint (multi-provider ingest). When `llm_base_url`
    # is set to a non-Anthropic OpenAI-compatible endpoint, the pipeline runs on
    # the AnthropicCompatClient shim instead of the Anthropic SDK. Empty = default
    # Anthropic behavior. Env: COMPENDIUM_LLM_BASE_URL / _API_KEY / _PROVIDER.
    llm_base_url: str = Field(default="", description="OpenAI-compatible (or Anthropic) base URL")
    llm_api_key: str = Field(default="", description="API key for llm_base_url")
    llm_provider: str = Field(default="", description="'anthropic' | 'openai' (auto-detected from URL if blank)")

    # Vision / OCR provider (drives pdf figure transcription + view_image)
    vision_provider: str = Field(
        default="anthropic",
        description="'anthropic' (Claude Vision, cost = per-token) or 'ollama' (local Gemma 4, $0 marginal cost).",
    )
    vision_model: Optional[str] = Field(
        default=None,
        description="Model tag for the vision provider. Ollama: 'gemma4:e4b' (default), 'gemma4:26b-a4b'. Anthropic: falls back to tiered-model selection at the call site.",
    )
    vision_endpoint: str = Field(
        default="http://localhost:11434",
        description="Ollama endpoint for vision_provider='ollama'. Ignored otherwise.",
    )

    # Web search
    search_provider: str = Field(
        default="none",
        description="'tavily', 'brave', or 'none'",
    )
    search_api_key: str = Field(
        default="",
        description="API key for the search provider",
    )
    jina_api_key: str = Field(
        default="",
        alias="JINA_API_KEY",
        description="Jina Reader API key (reads r.jina.ai for browser-rendered markdown)",
    )

    # Output
    output_dir: str = Field(
        default="./output",
        description="Where to write compendium directories",
    )
    # Mode
    mode: str = Field(
        default="technical",
        description="Compendium mode: 'technical' (code-focused), 'scholarly' (research/humanities), or 'practical' (applied knowledge: fitness, design, crafts, engineering, etc.)",
    )

    # Quality & verification
    verify_sources: bool = Field(
        default=True,
        description="Verify citations via web search during validation",
    )
    include_critical_perspectives: bool = Field(
        default=True,
        description="Inject adversarial/critical sections (scholarly: Critical Perspectives, practical: Common Mistakes)",
    )
    voice_variation: bool = Field(
        default=False,
        description="Vary writing style across entries for diverse voice",
    )
    model_roster: list[str] = Field(
        default_factory=list,
        description="List of models to rotate through for entry generation",
    )
    domain_profile: Optional[DomainProfile] = Field(
        default=None,
        description="Domain vocabulary profile from Phase 0 calibration",
    )
    reference_document: Optional[str] = Field(
        default=None,
        description="External reference document content (from --context-file)",
    )
    # GVR (Generator-Verifier-Reviser) settings — Aletheia-inspired
    max_revision_attempts: int = Field(
        default=2,
        description="Max GVR revision loops per entry (0 disables)",
    )
    blocking_citation_verification: bool = Field(
        default=True,
        description="Treat low-confidence citations as revision issues in GVR loop",
    )
    adaptive_iterations: bool = Field(
        default=True,
        description="Dynamically adjust REPL iterations per entry based on layer/complexity",
    )

    # Cost estimation
    cost_history_file: Optional[str] = Field(
        default=None,
        description="Path to cost history JSON (default: ~/.claude/compendium-cost-history.json)",
    )

    # Guardrails
    max_repl_iterations: int = 50
    max_sub_calls_per_page: int = 10
    max_total_sub_calls: int = 200
    max_cost_dollars: float = 10.0
    max_tokens_per_page: int = 2000
    max_pages_per_ingest: int = 25
    # Lifetime cost cap per vault (sum of all .loop-state.json sessions).
    # Warning-only by default; set COMPENDIUM_VAULT_LIFETIME_CAP=50 to activate.
    # 0 or unset = no lifetime warning.
    vault_lifetime_cap: float = 0.0

    @model_validator(mode="after")
    def _resolve_auth(self) -> "CompendiumConfig":
        """Resolve API key from multiple sources.

        Priority:
        1. Explicit ANTHROPIC_API_KEY (non-empty)
        2. Key from .env file (when env var is empty string from Claude Code)
        3. CLAUDE_CODE_OAUTH_TOKEN as Bearer auth fallback
        """
        if not self.anthropic_api_key:
            # Claude Code sets ANTHROPIC_API_KEY="" which shadows .env.
            # Read .env directly as fallback.
            env_path = Path(".env")
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("ANTHROPIC_API_KEY=") and not line.startswith("#"):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            self.anthropic_api_key = val
                            break

        if not self.anthropic_api_key and not self.anthropic_auth_token:
            oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            if oauth_token:
                self.anthropic_auth_token = oauth_token
        return self

    @property
    def has_auth(self) -> bool:
        """Whether any form of authentication is configured. A configured LLM
        endpoint counts (a local one like Ollama needs no key)."""
        return bool(self.anthropic_api_key or self.anthropic_auth_token or self.llm_base_url)

    def _llm_is_anthropic(self) -> bool:
        from compendium.llm.providers import detect_provider
        prov = (self.llm_provider or "").strip().lower()
        if prov:
            return prov == "anthropic"
        return detect_provider(self.llm_base_url) == "anthropic"

    def create_anthropic_client(self):
        """Return the LLM client for the pipeline. For a non-Anthropic OpenAI-compatible
        `llm_base_url`, return the AnthropicCompatClient shim (quacks like the SDK). For
        an Anthropic `llm_base_url`, use the SDK pointed at that base. Otherwise the
        default Anthropic SDK client."""
        import anthropic
        if self.llm_base_url and not self._llm_is_anthropic():
            from compendium.llm.anthropic_compat import AnthropicCompatClient
            return AnthropicCompatClient(self.llm_base_url, self.llm_api_key or self.anthropic_api_key)
        if self.llm_base_url and self._llm_is_anthropic():
            return anthropic.Anthropic(
                api_key=self.llm_api_key or self.anthropic_api_key,
                base_url=self.llm_base_url,
                max_retries=5,
            )
        if self.anthropic_api_key:
            return anthropic.Anthropic(
                api_key=self.anthropic_api_key,
                max_retries=5,
            )
        if self.anthropic_auth_token:
            # The SDK reads ANTHROPIC_API_KEY from env even if it's empty
            # string, which shadows auth_token in the auth_headers property.
            # Temporarily remove it so the SDK sees api_key=None.
            saved = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                return anthropic.Anthropic(
                    auth_token=self.anthropic_auth_token,
                    max_retries=5,
                )
            finally:
                if saved is not None:
                    os.environ["ANTHROPIC_API_KEY"] = saved
        raise ValueError("No Anthropic API key or auth token configured")

    def get_guardrails(self) -> GuardrailConfig:
        return GuardrailConfig(
            max_repl_iterations=self.max_repl_iterations,
            max_sub_calls_per_page=self.max_sub_calls_per_page,
            max_total_sub_calls=self.max_total_sub_calls,
            max_cost_dollars=self.max_cost_dollars,
            max_tokens_per_page=self.max_tokens_per_page,
            max_pages_per_ingest=self.max_pages_per_ingest,
        )

    @property
    def tiered_models(self) -> TieredModelConfig:
        """Build TieredModelConfig with fallback resolution.

        Resolution priority per tier:
        - strategic: model_strategic -> model_root
        - synthesis: model_synthesis -> model_root
        - mechanical: model_mechanical -> model_sub_call
        """
        return TieredModelConfig(
            strategic=self.model_strategic or self.model_root,
            synthesis=self.model_synthesis or self.model_root,
            mechanical=self.model_mechanical or self.model_sub_call,
        )

