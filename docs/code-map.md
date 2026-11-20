# Code map

How the code fits together: packages, classes, functions, and the calls between them. This file is updated in the same change as the code it describes (rule in [CLAUDE.md](../CLAUDE.md)). For *why* things are designed this way, see [design.md](design.md).

**Last updated:** M0 (repository skeleton).

## Legend

The code is mixed: plain functions for logic, classes for data shapes and swappable interfaces. The diagrams show functions as members of a `<<module>>` box.

| Marker | Meaning |
|---|---|
| `<<module>>` | A Python module. Its "methods" are module-level functions |
| `<<dataclass>>`, `<<pydantic>>` | Data shapes |
| `<<protocol>>` | An interface that packs or providers implement (`typing.Protocol`) |
| `<<external>>` | A third-party class, shown only where our code depends on it |
| `+name` / `-name` | Public / private (leading underscore) |
| `A --> B` | A calls or uses B |
| `A <\|-- B` | B subclasses A |
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
    store["store<br/>migrate, schema"]:::built

    tools["tools<br/>resolve, query_facts,<br/>search_text, calculate (M1)"]:::planned
    ingest["ingest<br/>pipeline steps (M1, M3)"]:::planned
    packs["packs<br/>loader + Protocols (M1)"]:::planned
    agent["agent<br/>LangGraph graph (M2)"]:::planned
    verify["verify<br/>verifier, confidence (M2)"]:::planned
    api["api<br/>FastAPI (M6)"]:::planned
    mcp["mcp<br/>MCP server (M6)"]:::planned
    obs["observability (M2)"]:::planned
    finance["packs/finance<br/>(M1)"]:::planned

    pg[("Postgres + pgvector")]:::ext

    cli --> config
    cli --> store
    store --> pg

    cli -.-> ingest
    ingest -.-> packs
    packs -.-> finance
    ingest -.-> store
    tools -.-> store
    agent -.-> tools
    agent -.-> verify
    verify -.-> store
    api -.-> agent
    mcp -.-> api
    agent -.-> obs
```

## 2. Classes and functions

Built code only. A planned component is added here when its code lands.

```mermaid
classDiagram
    direction LR

    class cli {
        <<module>>
        +main(argv) int
    }

    class config {
        <<module>>
        +get_settings() Settings
    }

    class Settings {
        <<pydantic>>
        +database_url: str
    }

    class migrate {
        <<module>>
        +discover(package) list~Migration~
        +apply_pending(database_url, migrations) list~Migration~
        +status(database_url) list~str~
        -_applied(conn) dict
        -_check_applied(migrations, applied) None
    }

    class Migration {
        <<dataclass>>
        +version: int
        +name: str
        +filename: str
        +sql: str
        +checksum() str
    }

    class MigrationError {
        <<exception>>
    }

    class BaseSettings {
        <<external>>
        pydantic_settings
    }

    class RuntimeError {
        <<external>>
        builtin
    }

    class Connection {
        <<external>>
        psycopg
    }

    cli --> config : get_settings()
    cli --> migrate : apply_pending() / status()
    config ..> Settings : returns (cached)
    BaseSettings <|-- Settings
    migrate ..> Migration : returns
    migrate ..> MigrationError : raises
    RuntimeError <|-- MigrationError
    migrate --> Connection : SQL with bound params
```

## 3. Key flows

### `vrag migrate`

```mermaid
sequenceDiagram
    actor Dev
    participant CLI as cli.main
    participant Cfg as config.get_settings
    participant M as store.migrate
    participant DB as Postgres

    Dev->>CLI: vrag migrate
    CLI->>Cfg: get_settings()
    Cfg-->>CLI: Settings(database_url)
    CLI->>M: apply_pending(url)
    M->>M: discover() reads migrations/NNNN_*.sql
    M->>DB: pg_advisory_lock
    M->>DB: CREATE TABLE IF NOT EXISTS schema_migrations
    M->>DB: SELECT version, checksum
    M->>M: _check_applied() rejects edited or unknown migrations
    loop each pending migration
        M->>DB: one transaction: run the file, record it in schema_migrations
    end
    M->>DB: pg_advisory_unlock
    M-->>CLI: applied migrations
    CLI-->>Dev: "applied ..." or "database is up to date"
```

## 4. Database schema

From [0001_initial_schema.sql](../src/verified_rag/store/migrations/0001_initial_schema.sql). Key columns only; the SQL file is the source of truth.

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
        jsonb attributes
    }
    documents {
        bigint document_id PK
        bigint entity_id FK
        text external_id "accession number"
        text doc_type
        text content_hash
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
        text source
    }
    facts {
        bigint fact_id PK
        bigint entity_id FK
        bigint document_id FK
        bigint concept_id FK
        numeric value
        text unit
        text period_type "instant, duration"
        jsonb dimensions
        boolean is_latest_reported "unique per period"
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
        jsonb input_refs "fact or calc IDs"
        numeric result
    }
    schema_migrations {
        int version PK
        text checksum
    }
```

## Keeping this file current

Update it in the same change whenever you:

- add, rename, or remove a module, public function, class, or Protocol (section 2)
- add or change a dependency between packages, or a package moves from planned to built (section 1)
- add a user-facing flow: a CLI command, an API endpoint, an agent graph path, or an ingestion pipeline (section 3)
- add a migration that changes tables, keys, or relationships (section 4)

Private helpers can be left out once a module grows large; public functions and every cross-module call stay. Bump **Last updated** to the current milestone. Check that the diagrams still render (GitHub preview, or `npx -y @mermaid-js/mermaid-cli -i docs/code-map.md -o <scratch>/out.md`).
