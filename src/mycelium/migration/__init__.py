"""Legacy migration helpers."""

from mycelium.migration.base import (
    MIGRATION_CONFIDENCE,
    MigrationRecord,
    MigrationResult,
    MigrationSource,
)
from mycelium.migration.lancedb_importer import (
    extract_from_lancedb,
    import_lancedb_memories,
)
from mycelium.migration.memory_file_extractor import (
    MemoryExtractionError,
    MemoryFactExtractor,
    OpenAIMemoryFactExtractor,
    extract_and_import_memory_files,
    extract_from_memory_file,
    extract_from_memory_files,
    import_memory_file_records,
)
from mycelium.migration.runner import (
    FullMigrationResult,
    MigrationSourceRun,
    run_migration_plan,
)
from mycelium.migration.supabase_importer import (
    extract_from_supabase,
    import_supabase_learnings,
)

__all__ = [
    "MIGRATION_CONFIDENCE",
    "MigrationRecord",
    "MigrationResult",
    "MigrationSource",
    "extract_from_lancedb",
    "import_lancedb_memories",
    "MemoryExtractionError",
    "MemoryFactExtractor",
    "OpenAIMemoryFactExtractor",
    "extract_from_memory_file",
    "extract_from_memory_files",
    "extract_and_import_memory_files",
    "import_memory_file_records",
    "extract_from_supabase",
    "import_supabase_learnings",
    "MigrationSourceRun",
    "FullMigrationResult",
    "run_migration_plan",
]
