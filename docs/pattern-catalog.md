# Lookout Pattern Catalog

## Existing Patterns (10)

### Enforcement Patterns
| ID | Title | Category | Tier | Type |
|----|-------|----------|------|------|
| ARCH-001 | HTTP Calls Require Resilience Wrappers | architecture | 1 | must_wrap |
| ARCH-003 | Use Logging Instead of Print Statements | architecture | 1 | forbidden |
| DI-001 | Dependency Injection via Constructor | architecture | 2 | raw |
| LAYER-001 | Layered Architecture Import Boundaries | architecture | 2 | raw |
| REPO-001 | Repository Pattern for Data Access | architecture | 2 | raw |

### Discovery-Only Patterns
| ID | Title | Category | Tier | Type |
|----|-------|----------|------|------|
| PATTERN-DISC-006 | Adapter Pattern | architecture | 3 | pattern_discovery |
| PATTERN-DISC-010 | Pipeline Pattern | architecture | 3 | pattern_discovery |
| PATTERN-DISC-011 | Dependency Injection Pattern | architecture | 3 | pattern_discovery |
| PATTERN-DISC-012 | Context Manager Pattern | architecture | 3 | pattern_discovery |
| PATTERN-DISC-015 | Custom Exception Hierarchy Pattern | architecture | 3 | pattern_discovery |

---

## New Design Patterns (12)

| ID | Title | Category | Tier | Severity | Description |
|----|-------|----------|------|----------|-------------|
| CQRS-001 | Command-Query Separation | architecture | 2 | warning | Methods should either change state (command) or return data (query), never both. Detects methods that mutate state and return non-void values. |
| FACTORY-001 | Factory Method for Object Creation | architecture | 3 | info | Complex object creation should use factory methods/classes rather than scattered constructor calls with configuration logic. |
| EVENT-001 | Event-Driven Decoupling | architecture | 2 | warning | Cross-module communication should use events/signals rather than direct method calls between bounded contexts. Detects direct cross-boundary coupling. |
| STRAT-001 | Strategy Pattern Over Conditionals | architecture | 3 | info | Behavioral branching on type or category should use the strategy pattern rather than long if/elif/switch chains. Detects switch-on-type anti-patterns. |
| IMMUT-001 | Immutable Data Transfer Objects | reliability | 2 | warning | Data objects passed across boundaries should be immutable (frozen dataclasses, readonly interfaces, records). Detects mutable DTOs. |
| RETRY-001 | Retry with Backoff for External Calls | reliability | 1 | error | External service calls (HTTP, RPC, DB connections) must implement retry with exponential backoff, not bare retry loops or no retry at all. |
| GUARD-001 | Guard Clause over Nested Conditionals | architecture | 3 | info | Functions should use early returns (guard clauses) rather than deeply nested if/else blocks. Detects functions with >3 levels of nesting. |
| NULL-001 | Null Object Pattern over Null Checks | architecture | 3 | info | Repeated null/None/undefined checks on the same type should use the null object pattern instead. Detects scattered null-check patterns. |
| CIRCUIT-001 | Circuit Breaker for External Dependencies | reliability | 1 | error | Calls to external services must be wrapped in circuit breaker logic to prevent cascade failures. Detects unwrapped external calls in critical paths. |
| CONFIG-001 | Configuration via Environment, Not Hardcoded | security | 1 | error | Secrets, URLs, and environment-specific values must come from environment variables or config files, not hardcoded strings. Detects hardcoded credentials and URLs. |
| DISPOSE-001 | Resource Cleanup on All Exit Paths | reliability | 1 | error | Resources (files, connections, locks) must be cleaned up using language-appropriate mechanisms (context managers, try-finally, using/defer). Detects unmanaged resource acquisition. |
| OBS-001 | Structured Observability for Service Calls | architecture | 2 | warning | External service calls should emit structured telemetry (traces, metrics) not just log lines. Detects service calls without observability instrumentation. |

## New Anti-Patterns (6)

| ID | Title | Category | Tier | Severity | Description |
|----|-------|----------|------|----------|-------------|
| ANTI-GOD-001 | God Object / God Class | architecture | 2 | warning | Classes with excessive responsibilities (too many methods, too many fields, too many dependencies). Detects classes exceeding complexity thresholds. |
| ANTI-SVC-001 | Service Locator Anti-Pattern | architecture | 2 | warning | Dependencies resolved at runtime via a global registry rather than injected explicitly. Detects calls to service locator / container.resolve patterns. |
| ANTI-CATCH-001 | Bare Except / Catch-All Error Swallowing | reliability | 1 | error | Catching all exceptions without re-raising or specific handling silently swallows errors. Detects `except:`, `except Exception:` with pass/continue, catch(Exception) with empty body. |
| ANTI-SECRET-001 | Hardcoded Secrets and Credentials | security | 1 | error | Passwords, API keys, tokens, and connection strings hardcoded in source. Detects string assignments to variables with names matching secret patterns. |
| ANTI-MUT-001 | Mutable Default Arguments | reliability | 1 | error | Using mutable objects (lists, dicts, sets) as default function parameters leads to shared state bugs. Detects mutable defaults in function signatures. |
| ANTI-CIRCULAR-001 | Circular Dependency Between Modules | architecture | 2 | warning | Modules that import each other create fragile initialization ordering and tight coupling. Detects import cycles between packages/modules. |

---

## Complete Catalog (28 patterns)

| # | ID | Title | Kind | Category |
|---|-----|-------|------|----------|
| 1 | ARCH-001 | HTTP Calls Require Resilience Wrappers | enforcement | architecture |
| 2 | ARCH-003 | Use Logging Instead of Print Statements | enforcement | architecture |
| 3 | DI-001 | Dependency Injection via Constructor | enforcement | architecture |
| 4 | LAYER-001 | Layered Architecture Import Boundaries | enforcement | architecture |
| 5 | REPO-001 | Repository Pattern for Data Access | enforcement | architecture |
| 6 | PATTERN-DISC-006 | Adapter Pattern | discovery | architecture |
| 7 | PATTERN-DISC-010 | Pipeline Pattern | discovery | architecture |
| 8 | PATTERN-DISC-011 | Dependency Injection Pattern | discovery | architecture |
| 9 | PATTERN-DISC-012 | Context Manager Pattern | discovery | architecture |
| 10 | PATTERN-DISC-015 | Custom Exception Hierarchy Pattern | discovery | architecture |
| 11 | CQRS-001 | Command-Query Separation | enforcement | architecture |
| 12 | FACTORY-001 | Factory Method for Object Creation | discovery | architecture |
| 13 | EVENT-001 | Event-Driven Decoupling | enforcement | architecture |
| 14 | STRAT-001 | Strategy Pattern Over Conditionals | discovery | architecture |
| 15 | IMMUT-001 | Immutable Data Transfer Objects | enforcement | reliability |
| 16 | RETRY-001 | Retry with Backoff for External Calls | enforcement | reliability |
| 17 | GUARD-001 | Guard Clause over Nested Conditionals | discovery | architecture |
| 18 | NULL-001 | Null Object Pattern over Null Checks | discovery | architecture |
| 19 | CIRCUIT-001 | Circuit Breaker for External Dependencies | enforcement | reliability |
| 20 | CONFIG-001 | Configuration via Environment, Not Hardcoded | enforcement | security |
| 21 | DISPOSE-001 | Resource Cleanup on All Exit Paths | enforcement | reliability |
| 22 | OBS-001 | Structured Observability for Service Calls | enforcement | architecture |
| 23 | ANTI-GOD-001 | God Object / God Class | enforcement | architecture |
| 24 | ANTI-SVC-001 | Service Locator Anti-Pattern | enforcement | architecture |
| 25 | ANTI-CATCH-001 | Bare Except / Catch-All Error Swallowing | enforcement | reliability |
| 26 | ANTI-SECRET-001 | Hardcoded Secrets and Credentials | enforcement | security |
| 27 | ANTI-MUT-001 | Mutable Default Arguments | enforcement | reliability |
| 28 | ANTI-CIRCULAR-001 | Circular Dependency Between Modules | enforcement | architecture |
