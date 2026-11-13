"""Add category hierarchy support to databases created before its baseline."""

version = "0002"
name = "category_parent_id"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE categorys
            ADD COLUMN IF NOT EXISTS parent_id INTEGER
            REFERENCES categorys(id) ON DELETE RESTRICT ON UPDATE CASCADE
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_categorys_parent_id "
            "ON categorys (parent_id)"
        )
