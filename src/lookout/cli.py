"""Command-line interface for Lookout."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from lookout.discovery.models import DiscoveredPattern, ValidatedPattern
    from lookout.discovery.search.providers import SearchProvider
    from lookout.rule_generator import GeneratedCheck

from lookout.config import LookoutConfig
from lookout.rules_loader import load_all_rules
from lookout.runner import (
    format_findings_json,
    format_findings_text,
    format_pattern_instances_text,
    run_lookout,
)
from lookout.sarif import format_findings_sarif


@click.group()
@click.version_option()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Lookout: Structural Governance Engine for code pattern enforcement."""
    # Load config
    config = LookoutConfig.load()

    # Store config in context (though default_map is easier)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config

    # Set defaults for subcommands
    # Note: Click's default_map expects keys to match command names if they are nested,
    # or parameter names if we are setting them directly.
    # Since we have flat commands, we map them directly.

    ctx.default_map = {
        "check": {
            "rules_dir": config.rules_dir,
            "target": config.target,
            "format": config.format,
            "fail_on_rule_error": config.fail_on_rule_error,
        },
        "discover": {
            "rules_dir": config.rules_dir,
            "target": config.target,
            "fail_on_rule_error": config.fail_on_rule_error,
        },
        "promote": {
            "patterns_dir": config.patterns_dir,
            "output_dir": config.rules_dir,  # Default output to rules_dir
        },
        "research": {
            "patterns_dir": config.patterns_dir,
        },
        "validate": {
            "patterns_dir": config.patterns_dir,
        },
    }


@cli.command()
@click.option(
    "--rules-dir",
    type=click.Path(path_type=Path),
    help="Directory containing rule specifications",
)
@click.option(
    "--update-baseline",
    is_flag=True,
    help="Update baseline with current findings",
)
@click.option(
    "--format",
    type=click.Choice(["text", "json", "sarif"]),
    help="Output format",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show compiled Semgrep rules without running",
)
@click.option(
    "--target",
    type=click.Path(exists=True, path_type=Path),
    multiple=True,
    help="Paths to analyze (defaults to current directory)",
)
@click.option(
    "--fail-on-rule-error",
    is_flag=True,
    help="Exit with error if any rules fail to execute",
)
def check(
    rules_dir: Path | None,
    update_baseline: bool,
    format: str | None,
    dry_run: bool,
    target: tuple[Path, ...],
    fail_on_rule_error: bool,
) -> None:
    """Run structural governance checks on your codebase."""
    # Note: Click injects defaults from default_map, so values shouldn't be None
    # unless they are missing from config AND CLI. But we have Pydantic defaults.
    # However, targets coming from default_map might be a list, while CLI gives a tuple.

    # If parameters are None, it means they weren't in config OR CLI (shouldn't happen with our setup).
    # But for safety:

    config = click.get_current_context().obj["config"]

    final_rules_dir = rules_dir or config.rules_dir
    final_format = format or config.format
    final_target = list(target) if target else config.target
    final_fail_on_rule_error = fail_on_rule_error  # Default map handles the config fallback

    # Load rule specifications (built-in + user rules)
    specs = load_all_rules(user_rules_dir=final_rules_dir if final_rules_dir.exists() else None)

    if not specs:
        click.echo("No rule specifications found (neither built-in nor user rules)", err=True)
        sys.exit(1)

    # Run the enforcement engine
    findings, new_findings, _, errors, dry_output = run_lookout(
        specs=specs,
        targets=final_target,
        dry_run=dry_run,
        update_baseline=update_baseline,
        discovery_only=False,
    )

    # Report rule execution errors
    if errors:
        click.echo(f"Warning: {len(errors)} rules failed to execute:", err=True)
        for error in errors:
            click.echo(f"  [{error.rule_id}] {error.message}", err=True)
        click.echo("", err=True)
        if final_fail_on_rule_error:
            sys.exit(1)

    # Report results
    if dry_run:
        click.echo(dry_output)
        return

    if update_baseline:
        click.echo(f"✓ Baseline updated with {len(findings)} findings")
        return

    # Show findings in requested format
    if final_format == "json":
        click.echo(format_findings_json(findings))
    elif final_format == "sarif":
        click.echo(format_findings_sarif(findings))
    else:
        click.echo(format_findings_text(findings))

    # Exit with error code if there are new findings
    if new_findings:
        if final_format == "text":
            click.echo(f"\n❌ {len(new_findings)} new violation(s) found", err=True)
        sys.exit(1)
    else:
        if final_format == "text":
            click.echo("\n✓ No new violations")


@cli.command()
@click.option(
    "--rules-dir",
    type=click.Path(path_type=Path),
    help="Directory containing pattern discovery specifications",
)
@click.option(
    "--target",
    type=click.Path(exists=True, path_type=Path),
    multiple=True,
    help="Paths to analyze (defaults to current directory)",
)
@click.option(
    "--fail-on-rule-error",
    is_flag=True,
    help="Exit with error if any rules fail to execute",
)
def discover(rules_dir: Path | None, target: tuple[Path, ...], fail_on_rule_error: bool) -> None:
    """Discover pattern instances in your codebase."""
    config = click.get_current_context().obj["config"]
    final_rules_dir = rules_dir or config.rules_dir
    final_target = list(target) if target else config.target
    final_fail_on_rule_error = fail_on_rule_error

    # Load discovery specs (built-in + user rules)
    specs = load_all_rules(user_rules_dir=final_rules_dir if final_rules_dir.exists() else None)

    # Filter to only discovery specs
    from lookout.specs import is_discovery_spec

    discovery_specs = [s for s in specs if is_discovery_spec(s)]

    if not discovery_specs:
        click.echo(f"No pattern discovery specifications found in {final_rules_dir}", err=True)
        sys.exit(1)

    # Run pattern discovery
    _, _, instances, errors, _ = run_lookout(
        specs=discovery_specs,
        targets=final_target,
    )

    # Report rule execution errors
    if errors:
        click.echo(f"Warning: {len(errors)} discovery rules failed to execute:", err=True)
        for error in errors:
            click.echo(f"  [{error.rule_id}] {error.message}", err=True)
        click.echo("", err=True)
        if final_fail_on_rule_error:
            sys.exit(1)

    # Report discovered patterns
    click.echo(format_pattern_instances_text(instances))


@cli.command()
@click.option(
    "--rules-dir",
    type=click.Path(path_type=Path),
    default=".lookout-rules",
    help="Directory to create for local rules",
)
@click.option(
    "--copy-built-in-rules",
    is_flag=True,
    help="Copy all built-in rules to local rules directory",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help="Skip interactive prompts and use defaults",
)
def init(rules_dir: Path, copy_built_in_rules: bool, non_interactive: bool) -> None:
    """Initialize Lookout configuration for your project.

    Creates a configuration file (lookout.toml or pyproject.toml) and sets up
    the rules directory. Built-in rules are always available; this command
    optionally copies them locally for customization.

    Examples:

        # Interactive setup (recommended)
        lookout init

        # Non-interactive with defaults
        lookout init --non-interactive

        # Copy built-in rules for customization
        lookout init --copy-built-in-rules
    """
    from lookout.init import run_init

    run_init(
        rules_dir=rules_dir,
        copy_built_in_rules=copy_built_in_rules,
        interactive=not non_interactive,
    )


@cli.command()
@click.argument("pattern-id")
@click.option(
    "--patterns-dir",
    type=click.Path(exists=True, path_type=Path),
    help="Directory containing discovered patterns",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    help="Directory to save the promoted rule spec",
)
@click.option(
    "--generate-check",
    is_flag=True,
    help="Use LLM to generate an enforcement check from the pattern",
)
@click.option(
    "--provider",
    default=None,
    help="LLM provider for check generation (anthropic, openai, gemini). Defaults to config.",
)
@click.option(
    "--model",
    default=None,
    help="LLM model name. Defaults to config or provider default.",
)
def promote(
    pattern_id: str,
    patterns_dir: Path | None,
    output_dir: Path | None,
    generate_check: bool,
    provider: str | None,
    model: str | None,
) -> None:
    """Promote a validated pattern to an enforcement rule."""
    config = click.get_current_context().obj["config"]
    final_patterns_dir = patterns_dir or config.patterns_dir
    final_output_dir = output_dir or config.rules_dir

    from lookout.discovery.models import ValidatedPattern
    from lookout.discovery.storage import PatternStorage
    from lookout.promotion import promote_to_spec, save_spec

    storage = PatternStorage(final_patterns_dir)

    # Try to load from validated stage
    pattern = storage.load_pattern(pattern_id, stage="validated", model_class=ValidatedPattern)

    if not pattern:
        click.echo(
            f"Error: Validated pattern {pattern_id} not found in {final_patterns_dir}", err=True
        )
        sys.exit(1)

    # Optionally generate a check via LLM
    check_override = None
    scaffolding = None
    if generate_check:
        generated = asyncio.run(_generate_check(pattern, config, provider, model))
        if generated:
            import json

            check_override = generated.check
            scaffolding = generated.scaffolding
            check_json = check_override.model_dump(mode="json")
            click.echo(f"Generated check: {json.dumps(check_json, indent=2)}", err=True)
        else:
            click.echo("Warning: Check generation failed, using placeholder", err=True)

    # Promote to spec
    spec = promote_to_spec(pattern, check_override=check_override)

    # Save to rules directory
    save_path = save_spec(spec, final_output_dir, scaffolding=scaffolding)

    click.echo(f"✓ Promoted {pattern_id} to {save_path}")


async def _generate_check(
    pattern: ValidatedPattern,
    config: LookoutConfig,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> GeneratedCheck | None:
    """Generate a RuleCheck using the LLM rule generator.

    Returns None on any failure (missing API key, network error, parse failure).
    """
    from lookout.discovery.extractors.llm import LLMProvider
    from lookout.rule_generator import RuleGenerator

    provider_str = provider_override or config.llm_provider
    model = model_override or config.llm_model

    try:
        async with RuleGenerator(
            provider=LLMProvider(provider_str),
            model=model,
            temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens,
            max_retries=config.llm_max_retries,
        ) as gen:
            return await gen.generate_check(pattern)
    except Exception as e:
        click.echo(f"Warning: {e}", err=True)
        return None


@cli.command()
@click.option(
    "--focus",
    required=True,
    help="What patterns to look for (e.g. 'dependency injection patterns')",
)
@click.option(
    "--category",
    default="architecture",
    help="Pattern category to search",
)
@click.option(
    "--languages",
    multiple=True,
    help="Programming languages to filter by (can be repeated)",
)
@click.option(
    "--provider",
    default=None,
    help="LLM provider for extraction (anthropic, openai, gemini). Defaults to config.",
)
@click.option(
    "--patterns-dir",
    type=click.Path(path_type=Path),
    help="Directory to save discovered patterns",
)
@click.option(
    "--max-results",
    default=10,
    help="Maximum number of patterns to discover",
)
@click.option(
    "--no-llm",
    is_flag=True,
    help="Use keyword-only extraction (no LLM API key required)",
)
@click.option(
    "--docs",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Derive patterns from project documentation at this path",
)
def research(
    focus: str,
    category: str,
    languages: tuple[str, ...],
    provider: str | None,
    patterns_dir: Path | None,
    max_results: int,
    no_llm: bool,
    docs: Path | None,
) -> None:
    """Discover architectural patterns via AI-powered web research.

    Searches trusted sources and extracts coding patterns, saving them
    to the patterns directory for later validation and promotion.

    Examples:

        # Search with keyword-only extraction (no API key needed)
        lookout research --focus "dependency injection" --no-llm

        # Search with LLM extraction (requires ANTHROPIC_API_KEY)
        lookout research --focus "error handling patterns" --languages python

        # Derive patterns from project documentation
        lookout research --focus "architectural patterns" --docs . --no-llm
    """
    config = click.get_current_context().obj["config"]
    final_patterns_dir = patterns_dir or config.patterns_dir
    resolved_provider = provider or config.llm_provider

    patterns = asyncio.run(
        _run_research(
            focus=focus,
            category=category,
            languages=list(languages),
            provider=resolved_provider,
            no_llm=no_llm,
            max_results=max_results,
            docs_path=docs,
            config=config,
        )
    )

    if not patterns:
        click.echo("No patterns discovered.")
        return

    from lookout.discovery.storage import PatternStorage

    storage = PatternStorage(final_patterns_dir)
    for pattern in patterns:
        storage.save_pattern(pattern, "raw")

    click.echo(f"✓ {len(patterns)} patterns saved to {final_patterns_dir}/raw/")


def _create_search_provider() -> SearchProvider:
    """Pick the best available search provider based on environment variables.

    Priority:
      1. GOOGLE_SEARCH_API_KEY + GOOGLE_CSE_ID → GoogleSearchProvider
      2. BRAVE_SEARCH_API_KEY → BraveSearchProvider
      3. (no keys) → DuckDuckGoSearchProvider (free fallback)
    """
    google_key = os.getenv("GOOGLE_SEARCH_API_KEY", "")
    google_cse = os.getenv("GOOGLE_CSE_ID", "")
    if google_key and google_cse:
        from lookout.discovery.search.google import GoogleSearchProvider

        click.echo("Using Google Custom Search provider", err=True)
        return GoogleSearchProvider(api_key=google_key, cse_id=google_cse)

    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "")
    if brave_key:
        from lookout.discovery.search.brave import BraveSearchProvider

        click.echo("Using Brave Search provider", err=True)
        return BraveSearchProvider(api_key=brave_key)

    from lookout.discovery.search.duckduckgo import DuckDuckGoSearchProvider

    click.echo(
        "No search API key found — using DuckDuckGo (free, may be less reliable).\n"
        "For better results, set one of:\n"
        "  export BRAVE_SEARCH_API_KEY=...   (free: 2,000 queries/month)\n"
        "  export GOOGLE_SEARCH_API_KEY=...  (+ GOOGLE_CSE_ID)",
        err=True,
    )
    return DuckDuckGoSearchProvider()


async def _run_research(
    focus: str,
    category: str,
    languages: list[str],
    provider: str,
    no_llm: bool,
    max_results: int,
    docs_path: Path | None = None,
    config: LookoutConfig | None = None,
) -> list[DiscoveredPattern]:
    """Async helper that runs the discovery pipeline."""
    from lookout.discovery.agents.base import SearchConstraints, SearchFocus

    if no_llm:
        from lookout.discovery.extractors.keyword import KeywordExtractor

        extractor_ctx: object = KeywordExtractor()
    else:
        from lookout.discovery.extractors.hybrid import HybridExtractor
        from lookout.discovery.extractors.llm import LLMProvider

        extractor_ctx = HybridExtractor(
            llm_provider=LLMProvider(provider),
            llm_model=config.llm_model if config else None,
            llm_temperature=config.llm_temperature if config else 0.0,
            llm_max_tokens=config.llm_max_tokens if config else 4096,
            llm_max_retries=config.llm_max_retries if config else 3,
        )

    from lookout.discovery.extractors.base import PatternExtractor

    if docs_path:
        from lookout.discovery.agents.docs import DocsAgent

        async with extractor_ctx as extractor:  # type: ignore[attr-defined]
            assert isinstance(extractor, PatternExtractor)
            agent = DocsAgent(extractor=extractor)

            focus_obj = SearchFocus(
                focus_type=category,
                description=focus,
                codebase_path=str(docs_path),
            )
            constraints = SearchConstraints(
                languages=languages,
                max_results=max_results,
            )

            return await agent.discover_patterns(focus_obj, constraints)
    else:
        from lookout.discovery.agents.architecture import ArchitectureAgent
        from lookout.discovery.search.credibility import SourceCredibilityScorer
        from lookout.discovery.search.web_search import WebSearchClient

        search_provider = _create_search_provider()
        scorer = SourceCredibilityScorer()

        async with (  # noqa: SIM117
            WebSearchClient(scorer, search_provider=search_provider) as search_client,
            extractor_ctx as extractor,  # type: ignore[attr-defined]
        ):
            assert isinstance(extractor, PatternExtractor)
            web_agent = ArchitectureAgent(extractor=extractor, search_client=search_client)

            focus_obj = SearchFocus(
                focus_type=category,
                description=focus,
                keywords=[focus],
            )
            constraints = SearchConstraints(
                languages=languages,
                max_results=max_results,
            )

            return await web_agent.discover_patterns(focus_obj, constraints)


@cli.command()
@click.argument("pattern-id")
@click.option(
    "--patterns-dir",
    type=click.Path(path_type=Path),
    help="Directory containing discovered patterns",
)
@click.option(
    "--notes",
    default="",
    help="Review notes to attach to the validated pattern",
)
@click.option(
    "--tier",
    type=click.IntRange(1, 3),
    default=None,
    help="Override the inferred tier (1=Non-Negotiable, 2=Strong Recommendation, 3=Contextual)",
)
def validate(
    pattern_id: str,
    patterns_dir: Path | None,
    notes: str,
    tier: int | None,
) -> None:
    """Validate a discovered pattern and promote it to the validated stage.

    Loads a raw pattern from the patterns directory, attaches validation
    metadata, and saves it to the validated stage for later promotion.

    Examples:

        lookout validate ARCH-DI-001
        lookout validate ARCH-DI-001 --tier 1 --notes "Confirmed by team review"
    """
    config = click.get_current_context().obj["config"]
    final_patterns_dir = patterns_dir or config.patterns_dir

    from lookout.discovery.models import DiscoveredPattern, ValidatedPattern
    from lookout.discovery.storage import PatternStorage

    storage = PatternStorage(final_patterns_dir)

    raw = storage.load_pattern(pattern_id, stage="raw", model_class=DiscoveredPattern)

    if not raw:
        click.echo(
            f"Error: Raw pattern {pattern_id} not found in {final_patterns_dir}",
            err=True,
        )
        sys.exit(1)

    validated_by = os.getenv("USER", "unknown")
    validated = ValidatedPattern.from_discovered(
        raw,
        validated_by=validated_by,
        review_notes=notes,
        tier_override=tier,
    )

    storage.save_pattern(validated, "validated")

    click.echo(
        f"✓ Validated {pattern_id} (tier {validated.effective_tier})"
        f" — run 'lookout promote {pattern_id}' to create enforcement rule"
    )


@cli.group()
@click.pass_context
def batch(ctx: click.Context) -> None:
    """Batch generation of Semgrep checks for pattern registry."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = ctx.parent.obj["config"]  # type: ignore[union-attr]


def _get_batch_config() -> LookoutConfig:
    """Get config from the batch command group context."""
    ctx = click.get_current_context()
    parent = ctx.parent
    assert parent is not None
    config: LookoutConfig = parent.obj["config"]
    return config


@batch.command("submit")
@click.option("--languages", required=True, help="Comma-separated languages")
@click.option("--frameworks", multiple=True, help="language:fw1,fw2 pairs")
@click.option("--all-missing", is_flag=True, help="Select all MISSING cells")
@click.option("--all-stale", is_flag=True, help="Select all stale + missing cells")
@click.option("--dry-run", is_flag=True, help="Show what would be submitted")
@click.option(
    "--rules-dir",
    type=click.Path(path_type=Path),
    help="Directory containing pattern specs",
)
def batch_submit(
    languages: str,
    frameworks: tuple[str, ...],
    all_missing: bool,
    all_stale: bool,
    dry_run: bool,
    rules_dir: Path | None,
) -> None:
    """Submit a batch of check generation requests."""
    from lookout.batch.models import CellStatus
    from lookout.batch.orchestrator import BatchOrchestrator

    config = _get_batch_config()
    final_rules_dir = rules_dir or config.rules_dir

    lang_list = [lang.strip() for lang in languages.split(",")]
    fw_map: dict[str, list[str]] = {}
    for fw_spec in frameworks:
        fw_lang, fws = fw_spec.split(":", 1)
        fw_map[fw_lang] = [f.strip() for f in fws.split(",")]

    orch = BatchOrchestrator(config=config, patterns_dir=final_rules_dir)
    cells = orch.build_grid(lang_list, fw_map)

    # Filter cells
    if all_stale:
        cells = [
            c
            for c in cells
            if c.status in (CellStatus.MISSING, CellStatus.POSSIBLY_STALE, CellStatus.LIKELY_STALE)
        ]
    elif all_missing:
        cells = [c for c in cells if c.status == CellStatus.MISSING]

    if not cells:
        click.echo("No cells selected for batch generation.")
        return

    missing = sum(1 for c in cells if c.status == CellStatus.MISSING)
    stale = sum(
        1 for c in cells if c.status in (CellStatus.POSSIBLY_STALE, CellStatus.LIKELY_STALE)
    )

    click.echo(f"{len(cells)} cell(s) selected ({missing} missing, {stale} stale)")

    if dry_run:
        for c in cells:
            fw = c.framework or "generic"
            click.echo(f"  {c.pattern_id} / {c.language} / {fw} [{c.status.value}]")
        return

    # Submit
    job = asyncio.run(orch.submit_batch(cells))
    jobs_dir = config.batch_jobs_dir
    orch.save_job(job, jobs_dir)
    click.echo(f"Submitted batch {job.job_id}")
    click.echo(f"Job saved to {jobs_dir / f'{job.job_id}.json'}")


@batch.command("status")
@click.argument("job_id")
@click.option(
    "--jobs-dir",
    type=click.Path(path_type=Path),
    help="Directory containing batch job files",
)
def batch_status(job_id: str, jobs_dir: Path | None) -> None:
    """Check the status of a batch job."""
    from lookout.batch.orchestrator import BatchOrchestrator

    config = _get_batch_config()
    final_jobs_dir = jobs_dir or config.batch_jobs_dir

    job_path = final_jobs_dir / f"{job_id}.json"
    if not job_path.exists():
        click.echo(f"Job {job_id} not found in {final_jobs_dir}", err=True)
        sys.exit(1)

    orch = BatchOrchestrator(config=config, patterns_dir=config.rules_dir)
    job = orch.load_job(job_path)

    click.echo(f"Batch {job.job_id} ({job.provider}/{job.model})")
    click.echo(f"Status: {job.status.value}")
    if job.request_counts:
        parts = [f"{v} {k}" for k, v in job.request_counts.items() if v]
        click.echo(f"Counts: {', '.join(parts)}")
    click.echo(f"Requests: {len(job.requests)}")


@batch.command("list")
@click.option(
    "--jobs-dir",
    type=click.Path(path_type=Path),
    help="Directory containing batch job files",
)
def batch_list(jobs_dir: Path | None) -> None:
    """List all batch jobs."""
    config = _get_batch_config()
    final_jobs_dir = jobs_dir or config.batch_jobs_dir

    if not final_jobs_dir.exists():
        click.echo("No batch jobs found.")
        return

    import json

    job_files = sorted(final_jobs_dir.glob("*.json"))
    if not job_files:
        click.echo("No batch jobs found.")
        return

    for f in job_files:
        with f.open("r") as fh:
            data = json.load(fh)
        count = data.get("request_count", len(data.get("requests", [])))
        click.echo(
            f"  {data['job_id']}  {data['status']}  "
            f"{data['provider']}/{data['model']}  "
            f"{count} requests"
        )


@batch.command("results")
@click.argument("job_id")
@click.option(
    "--jobs-dir",
    type=click.Path(path_type=Path),
    help="Directory containing batch job files",
)
@click.option(
    "--rules-dir",
    type=click.Path(path_type=Path),
    help="Directory containing pattern specs",
)
def batch_results(job_id: str, jobs_dir: Path | None, rules_dir: Path | None) -> None:
    """Retrieve and apply results from a completed batch."""
    from lookout.batch.orchestrator import BatchOrchestrator

    config = _get_batch_config()
    final_jobs_dir = jobs_dir or config.batch_jobs_dir
    final_rules_dir = rules_dir or config.rules_dir
    job_path = final_jobs_dir / f"{job_id}.json"
    if not job_path.exists():
        click.echo(f"Job {job_id} not found in {final_jobs_dir}", err=True)
        sys.exit(1)

    orch = BatchOrchestrator(config=config, patterns_dir=final_rules_dir)
    job = orch.load_job(job_path)
    job = asyncio.run(orch.retrieve_results(job))
    orch.save_job(job, final_jobs_dir)

    succeeded = sum(1 for r in job.results if r.success)
    failed = sum(1 for r in job.results if not r.success)
    click.echo(f"Retrieved {len(job.results)} results: {succeeded} succeeded, {failed} failed")


@batch.command("cancel")
@click.argument("job_id")
@click.option(
    "--jobs-dir",
    type=click.Path(path_type=Path),
    help="Directory containing batch job files",
)
def batch_cancel(job_id: str, jobs_dir: Path | None) -> None:
    """Cancel a batch job."""
    from lookout.batch.orchestrator import BatchOrchestrator

    config = _get_batch_config()
    final_jobs_dir = jobs_dir or config.batch_jobs_dir

    job_path = final_jobs_dir / f"{job_id}.json"
    if not job_path.exists():
        click.echo(f"Job {job_id} not found in {final_jobs_dir}", err=True)
        sys.exit(1)

    orch = BatchOrchestrator(config=config, patterns_dir=config.rules_dir)
    job = orch.load_job(job_path)

    asyncio.run(orch.cancel_batch(job))
    click.echo(f"Cancelled batch {job_id}")


if __name__ == "__main__":
    cli()
