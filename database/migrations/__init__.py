"""Versioned database migrations (DB-02).

Each migration is a module defining:

    version: str          # zero-padded, applied in lexicographic order
    name: str             # human-readable name
    def upgrade(conn)     # required; runs inside one transaction
    def downgrade(conn)   # optional, best-effort

The runner records applied versions in ``schema_migrations``.  No migration
may depend on import-time state; every statement is executed through the
passed connection/transaction.
"""
