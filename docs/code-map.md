# Code map

How the code fits together: packages, classes, functions, and the calls between them. This file is updated in the same change as the code it describes (rule in [CLAUDE.md](../CLAUDE.md)). For *why* things are designed this way, see [design.md](design.md).

**Last updated:** M1 (facts path).

## Legend

The code is mixed: plain functions for logic, classes for data shapes and swappable interfaces. The diagrams show functions as members of a `<<module>>` box.

| Marker | Meaning |
|---|---|
| `<<module>>` | A Python module. Its "methods" are module-level functions |
| `<<dataclass>>`, `<<pydantic>>` | Data shapes |
| `<<protocol>>` | An interface that packs or providers implement (`typing.Protocol`) |
| `<<external>>` | A third-party class, shown only where our code depends on it |
| `<<pack>>` | Code in `packs/<pack>/`, loaded at runtime, not part of the core |
| `+name` / `-name` | Public / private (leading underscore) |
| `A --> B` | A calls or uses B |
| `A <\|-- B` | B subclasses A |
| `A ..\|> B` | A implements protocol B |
| `A ..> B : returns` | A produces B |
| Dashed grey box (package map) | Planned, not built yet |

## 1. Package map

Which packages exist and which depend on which. Solid boxes are built; dashed boxes are planned, with the milestone that builds them.

```mermaid
flowchart TD
    classDef built fill:#d6f5d6,stroke:#2e7d32,color:#111
    classDef planned fill:#f2f2f2,stroke:#999,stroke-dasharray:4 3,color:#666
    classDef ext fill:#e8eefc,stroke:#5470c6,color:#111

    cli["cli<br/>vrag command"]:::built
    config["config<br/>Settings"]:::built
    refs["refs<br/>f_12, calc_3"]:::built
    store["store<br/>migrate, db, templates"]:::built
    ingest["ingest<br/>periods, normalize, pipeline"]:::built
    packs["packs<br/>interface, loader"]:::built
    tools["tools<br/>resolve, calculator, calculate"]:::built
    finance["packs/finance<br/>EDGAR connector, concepts"]:::built

    agent["agent<br/>LangGraph graph (M2)"]:::planned
    verify["verify<br/>verifier, confidence (M2)"]:::planned
    obs["observability (M2)"]:::planned
    api["api<br/>FastAPI (M6)"]:::planned
    mcp["mcp<br/>MCP server (M6)"]:::planned

    pg[("Postgres + pgvector")]:::ext
    edgar[("SEC EDGAR APIs")]:::ext

    cli --> config
    cli --> store
    cli --> ingest
    cli --> packs
    cli --> tools
    ingest --> packs
    packs -->|"loads at runtime"| finance
    finance --> edgar
    store --> pg
    ingest --> pg
    tools --> store
    tools --> refs
    store --> refs

    agent -.-> tools
    agent -.-> store
    agent -.-> verify
    verify -.-> store
    agent -.-> obs
    api -.-> agent
    mcp -.-> api
```

## 2. Classes and functions

Built code only. A planned component is added here when its code lands.

### 2a. Ingestion: packs, connector, normalization

```mermaid
classDiagram
    direction LR

    class loader {
        <<module>>
        +load_spec(pack_id, packs_dir) PackSpec
        +load_pack(pack_id, packs_dir) Pack
    }
    class Pack {
        <<dataclass>>
        +spec: PackSpec
        +root: Path
        +create_connector: ConnectorFactory
    }
    class PackSpec {
        <<pydantic>>
        +id: str
        +parser_version: str
        +document_types: tuple
        +entities: tuple~EntityConfig~
        +concepts: tuple~ConceptSpec~
        +units: dict
    }
    class ConceptSpec {
        <<pydantic>>
        +name: str
        +unit_kind: str
        +tags: tuple  priority order
        +aliases: tuple
    }
    class EntityConfig {
        <<pydantic>>
        +ref: str
        +aliases: tuple
    }
    class PackContext {
        <<dataclass>>
        +raw_dir: Path
        +document_types: frozenset
        +offline: bool
    }
    class Connector {
        <<protocol>>
        +fetch_entity(entity, wanted_tags) EntityBatch
    }
    class EntityBatch {
        <<pydantic>>
        +entity: EntityRecord
        +documents: tuple~DocumentRecord~
        +facts: tuple~SourceFact~
    }
    class SourceFact {
        <<pydantic>>
        +document_external_id: str
        +tag: str
        +unit: str
        +value: Decimal
        +period_start: date
        +period_end: date
        +reported_fiscal_year: int
    }
    class EdgarConnector {
        <<pack>>
        +fetch_entity(entity, wanted_tags) EntityBatch
        -_get(url) bytes  rate limit + retry
        -_cached(relative, url) Any
        -_cik(ref) int
        -_submissions(cik)
        -_facts(companyfacts, wanted) list
        -_documents(...) list
    }
    class EdgarSettings {
        <<pack>>
        +sec_user_agent: str  from env only
        +requests_per_second: float
        +max_attempts: int
    }

    class periods {
        <<module>>
        +derive(start, end, fye) FiscalPeriod
        +fiscal_year_of(day, fye) int
    }
    class FiscalYearEnd {
        <<dataclass>>
        +month: int
        +day: int
        +parse(mmdd) FiscalYearEnd
        +anchor(year) date
    }
    class normalize {
        <<module>>
        +normalize(batch, spec) NormalizedEntity
        +tag_mappings(spec) dict
        +wanted_tags(spec) frozenset
        +parser_version(spec) str
        -_cross_check(fact, derived, doc, report)
        -_content_hash(doc, facts) str
    }
    class NormalizedEntity {
        <<dataclass>>
        +entity: EntityRecord
        +documents: tuple~NormalizedDocument~
        +report: NormalizeReport
    }
    class NormalizedDocument {
        <<dataclass>>
        +record: DocumentRecord
        +facts: tuple~NormalizedFact~
        +content_hash: str
    }
    class NormalizeReport {
        <<dataclass>>
        +conflicts: list
        +fiscal_year_mismatches: list
        +bad_unit: int
        +superseded: int
    }
    class pipeline {
        <<module>>
        +ingest(conn, pack, ctx, entities) IngestReport
        +sync_concepts(conn, spec) dict
        +write_entity(conn, spec, normalized, concept_ids, result)
        +mark_latest(conn, entity_id)
        -_write_document(...)  COPY facts
    }
    class IngestReport {
        <<dataclass>>
        +results: list~EntityResult~
        +ok: bool
    }

    loader ..> Pack : returns
    Pack *-- PackSpec
    PackSpec *-- ConceptSpec
    PackSpec *-- EntityConfig
    EdgarConnector ..|> Connector
    EdgarConnector --> EdgarSettings
    EdgarConnector --> PackContext
    Connector ..> EntityBatch : returns
    EntityBatch *-- SourceFact
    pipeline --> loader : via Pack
    pipeline --> Connector : fetch_entity()
    pipeline --> normalize : normalize()
    normalize --> periods : derive()
    periods --> FiscalYearEnd
    normalize ..> NormalizedEntity : returns
    NormalizedEntity *-- NormalizedDocument
    NormalizedEntity *-- NormalizeReport
    pipeline ..> IngestReport : returns
```

### 2b. Query path: database access, templates, tools

Everything the agent will call in M2. `store/` and `tools/` have no LangChain or LangGraph imports.

```mermaid
classDiagram
    direction LR

    class db {
        <<module>>
        +connect(url) Connection  autocommit
        +read_only(conn, limits)  context manager
        +returned_id(row) int
    }
    class QueryLimits {
        <<dataclass>>
        +timeout_ms: int
        +max_rows: int
    }
    class templates {
        <<module>>
        +run_template(conn, params, limits) QueryResult
        +facts_by_id(conn, ids, limits) dict
        +calculations_by_id(conn, ids, limits) dict
        +list_entities(conn, pack_id, limits) list
        +list_concept_aliases(conn, pack_id, limits) list
    }
    class TemplateParams {
        <<pydantic>>
        discriminated on template
        LookupParams
        TimeSeriesParams
        CompareEntitiesParams
        BreakdownParams
        FactSourceParams
    }
    class QueryResult {
        <<dataclass>>
        +rows: list~FactRow~
        +truncated: bool
    }
    class FactRow {
        <<dataclass>>
        +fact_id: int
        +entity_name: str
        +concept_name: str
        +value: Decimal
        +unit: str
        +fiscal_year: int
        +fiscal_period: str
        +document_external_id: str
        +source_uri: str
        +ref() str
        +scaled_value() Decimal
    }
    class resolve {
        <<module>>
        +resolve_entity(conn, pack_id, name, limits) list~EntityCandidate~
        +resolve_concept(conn, pack_id, phrase, limits) list~ConceptCandidate~
        +unambiguous(candidates) candidate or None
        +normalize_text(text) str
    }
    class calculate {
        <<module>>
        +calculate(conn, expression, inputs, limits, request_id) CalcResult
        +load_operands(conn, inputs, limits) dict
    }
    class calculator {
        <<module>>
        +evaluate(expression, operands) Evaluated
        +parse_unit(unit) Dims
        +format_unit(dims) str
        -_Evaluator  ast whitelist
    }
    class Operand {
        <<dataclass>>
        +ref: str
        +value: Decimal
        +unit: str
        +period_type: str
    }
    class CalcResult {
        <<dataclass>>
        +calc_id: int
        +value: Decimal
        +unit: str
        +ref() str
    }
    class CalculationError {
        <<exception>>
    }
    class refs {
        <<module>>
        +parse_ref(text) Ref
        +fact_ref(id) str
        +calc_ref(id) str
    }

    templates --> db : read_only()
    db --> QueryLimits
    templates --> TemplateParams
    templates ..> QueryResult : returns
    QueryResult *-- FactRow
    FactRow --> refs : ref()
    resolve --> templates : list_entities(), list_concept_aliases()
    calculate --> refs : parse_ref()
    calculate --> templates : facts_by_id(), calculations_by_id()
    calculate --> calculator : evaluate()
    calculator --> Operand
    calculate ..> CalcResult : returns
    calculator ..> CalculationError : raises
```

### 2c. Entry points and configuration

```mermaid
classDiagram
    direction LR

    class cli {
        <<module>>
        +main(argv) int
        -_migrate(settings, status)
        -_ingest(settings, args)
        -_query(conn, settings, args)
        -_pick_entity(...) EntityCandidate
        -_pick_concept(...) ConceptCandidate
    }
    class Refusal {
        <<exception>>
        exit code 2
    }
    class config {
        <<module>>
        +get_settings() Settings
    }
    class Settings {
        <<pydantic>>
        +database_url: str
        +packs_dir: Path
        +raw_dir: Path
        +query_timeout_ms: int
        +query_max_rows: int
    }
    class migrate {
        <<module>>
        +discover(package) list~Migration~
        +apply_pending(database_url, migrations) list~Migration~
        +status(database_url) list~str~
    }
    class Migration {
        <<dataclass>>
        +version: int
        +filename: str
        +sql: str
        +checksum() str
    }
    class BaseSettings {
        <<external>>
        pydantic_settings
    }

    cli --> config : get_settings()
    cli --> migrate : migrate command
    cli --> pipeline : ingest command
    cli --> loader : load_pack()
    cli --> templates : lookup, series, compare, source
    cli --> resolve : resolve-entity, resolve-concept
    cli --> calculate : calc command
    cli ..> Refusal : raises when ambiguous
    config ..> Settings : returns (cached)
    BaseSettings <|-- Settings
    migrate ..> Migration : returns

    class pipeline {
        <<module>>
        see 2a
    }
    class loader {
        <<module>>
        see 2a
    }
    class templates {
        <<module>>
        see 2b
    }
    class resolve {
        <<module>>
        see 2b
    }
    class calculate {
        <<module>>
        see 2b
    }
```

## 3. Key flows

### `vrag ingest`

```mermaid
sequenceDiagram
    actor Dev
    participant CLI as cli.main
    participant L as packs.loader
    participant P as ingest.pipeline
    participant C as EdgarConnector
    participant SEC as SEC EDGAR
    participant N as ingest.normalize
    participant DB as Postgres

    Dev->>CLI: vrag ingest [AAPL ...]
    CLI->>L: load_pack("finance")
    L-->>CLI: Pack (spec from YAML, connector factory)
    CLI->>P: ingest(conn, pack, ctx, entities)
    P->>DB: sync_concepts: upsert concepts and aliases
    loop each entity, one transaction each
        P->>C: fetch_entity(entity, wanted_tags)
        C->>SEC: tickers, submissions pages, companyfacts (rate limited, retried)
        C->>C: cache raw JSON in the raw store
        C-->>P: EntityBatch
        P->>N: normalize(batch, spec)
        N->>N: map tags by priority, derive fiscal periods, drop conflicts
        N-->>P: NormalizedEntity with report
        P->>DB: upsert entity
        P->>DB: per changed document: upsert, delete old facts, COPY new facts
        P->>DB: mark_latest: newest filing wins per concept and period
    end
    P-->>CLI: IngestReport
    CLI-->>Dev: per entity counts, exit 1 if any failed
```

### `vrag lookup` and `vrag calc`

```mermaid
sequenceDiagram
    actor Dev
    participant CLI as cli.main
    participant R as tools.resolve
    participant T as store.templates
    participant K as tools.calculate
    participant DB as Postgres (vrag_reader role)

    Dev->>CLI: vrag lookup AAPL "net sales" --year 2024
    CLI->>R: resolve_entity, resolve_concept
    R->>T: list_entities, list_concept_aliases
    T->>DB: read-only transaction, SELECT from views
    R-->>CLI: ranked candidates
    alt ambiguous or no match
        CLI-->>Dev: candidates, exit 2
    else clear match
        CLI->>T: run_template(LookupParams)
        T->>DB: fixed SQL, bound params, timeout, row cap
        T-->>CLI: QueryResult of FactRow
        CLI-->>Dev: value, f_ref, filing, source URL
    end

    Dev->>CLI: vrag calc "(a - b) / b * 100" a=f_2 b=f_1
    CLI->>K: calculate(expression, inputs)
    K->>T: facts_by_id, calculations_by_id
    K->>K: calculator.evaluate: ast whitelist, Decimal, unit and period checks
    K->>DB: INSERT calculations (expression, input refs, result)
    K-->>CLI: CalcResult calc_N
    CLI-->>Dev: calc_N = value unit
```

### `vrag migrate`

```mermaid
sequenceDiagram
    actor Dev
    participant CLI as cli.main
    participant M as store.migrate
    participant DB as Postgres

    Dev->>CLI: vrag migrate
    CLI->>M: apply_pending(url)
    M->>M: discover() reads migrations/NNNN_*.sql
    M->>DB: pg_advisory_lock
    M->>DB: SELECT version, checksum FROM schema_migrations
    M->>M: _check_applied() rejects edited or unknown migrations
    loop each pending migration
        M->>DB: one transaction: run the file, record it in schema_migrations
    end
    M->>DB: pg_advisory_unlock
    CLI-->>Dev: "applied ..." or "database is up to date"
```

## 4. Database schema

From [0001_initial_schema.sql](../src/verified_rag/store/migrations/0001_initial_schema.sql) and [0002_facts_path.sql](../src/verified_rag/store/migrations/0002_facts_path.sql). Key columns only; the SQL files are the source of truth.

```mermaid
erDiagram
    entities ||--o{ documents : files
    entities ||--o{ facts : "reported for"
    documents ||--o{ facts : contains
    documents ||--o{ chunks : "split into"
    concepts ||--o{ facts : classifies
    concepts ||--o{ concept_aliases : "known as"
    requests ||--o{ calculations : produces

    entities {
        bigint entity_id PK
        text pack_id
        text external_id "CIK for finance"
        text name
        text_array aliases "tickers, short names"
        jsonb attributes "ticker, fiscal_year_end"
    }
    documents {
        bigint document_id PK
        bigint entity_id FK
        text external_id "accession number"
        text doc_type
        text content_hash
        text parser_version
        text status "pending, indexed, failed"
    }
    concepts {
        bigint concept_id PK
        text pack_id
        text name
        text unit_kind
    }
    concept_aliases {
        bigint concept_id PK, FK
        text alias PK
        text source "curated, tag, suggested"
        int priority "lower wins, for tags"
    }
    facts {
        bigint fact_id PK
        bigint entity_id FK
        bigint document_id FK
        bigint concept_id FK
        numeric value
        text unit
        text period_type "instant, duration"
        int fiscal_year "derived, ADR-9"
        text fiscal_period "FY, Q1-Q4, H1, 9M"
        jsonb dimensions
        boolean is_latest_reported "unique per period"
        text source_tag "e.g. us-gaap:Revenues"
    }
    chunks {
        bigint chunk_id PK
        bigint document_id FK
        text table_id "links to facts.table_id"
        vector embedding "384 dims"
        tsvector tsv "generated"
        boolean injection_flagged
    }
    requests {
        uuid request_id PK
        text user_id
        text status
        numeric cost_usd
    }
    calculations {
        bigint calc_id PK
        uuid request_id FK
        text expression
        jsonb input_refs "fact or calc refs"
        numeric result
        text unit
        text period_type
    }
    schema_migrations {
        int version PK
        text checksum
    }
```

**Read-only access.** Role `vrag_reader` (no login) can only `SELECT` from the views `v_entities`, `v_concepts`, `v_concept_aliases`, `v_facts`, and `v_calculations`. `v_facts` joins entity, concept, and document names and hides documents not yet `indexed`. Query templates switch to this role with `SET LOCAL ROLE` inside a read-only transaction. In a hosted database, the app's login role must be granted membership: `GRANT vrag_reader TO <app_role>`.

## Keeping this file current

Update it in the same change whenever you:

- add, rename, or remove a module, public function, class, or Protocol (section 2)
- add or change a dependency between packages, or a package moves from planned to built (section 1)
- add a user-facing flow: a CLI command, an API endpoint, an agent graph path, or an ingestion pipeline (section 3)
- add a migration that changes tables, keys, relationships, views, or roles (section 4)

Private helpers can be left out once a module grows large; public functions and every cross-module call stay. Bump **Last updated** to the current milestone. Check that the diagrams still render (GitHub preview, or VS Code's Markdown preview with a Mermaid extension).
