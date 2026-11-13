"""Add the attachments field required by source reads and writes."""

version = "0003"
name = "sources_attachments"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE sources "
            "ADD COLUMN IF NOT EXISTS attachments VARCHAR(255) NULL"
        )
