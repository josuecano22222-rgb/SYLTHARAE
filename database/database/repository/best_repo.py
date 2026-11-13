"""
Enhanced BaseRepository with transaction support and proper error handling.

Transaction model
-----------------
A repository either runs *inside* a transaction or it stands alone:

* **Inside a transaction** the owning
  :class:`~database.database.repository.transaction_scope.TransactionScope`
  hands the repository the transaction's connection, bound to the calling
  thread/task.  Statements join the caller's unit of work; the repository
  never commits and **never rolls back**, because only the transaction owner
  knows whether the unit of work is still viable.
* **Outside a transaction** each call takes a pooled connection, commits (or
  rolls back) and returns it.

The distinction matters.  A driver-level rollback issued from inside a
transaction silently discards work the caller has already done (the hash and
path rows it is about to reference), which is the mechanism behind the
production ``Hash ID N does not exist (transaction may have been rolled back)``
and the ``violates foreign key constraint`` cascade that followed it.
"""
from contextlib import contextmanager
import csv
import io
from typing import Optional, Any
import logging
import psycopg2
from psycopg2 import errors

from database.exceptions import QueryError, TransactionAbortedError, TransactionError
from database.database.repository.transaction_scope import (
    connection_in_error_state,
    TransactionScope,
)

logger = logging.getLogger(__name__)


def _safe_rollback(conn):
    """Roll back ``conn``, ignoring an already-clean or dead connection."""
    try:
        if conn is not None and not conn.closed:
            conn.rollback()
    except Exception as rollback_err:  # pragma: no cover - defensive
        logger.warning("Rollback failed: %s", rollback_err)


class BaseRepository:
    """
    Base repository with transaction support and connection pooling.

    Features:
    - Transaction participation through a shared :class:`TransactionScope`
      (thread/task-local connection binding - no shared state is mutated)
    - Connection pooling support
    - Standardized error handling
    - Support for both transactional and non-transactional operations
    """

    def __init__(self, db, connection: Optional[Any] = None,
                 scope: Optional[TransactionScope] = None):
        """
        Initialize repository.

        Args:
            db: Database instance
            connection: Explicit connection for this repository instance
                (legacy usage: single-threaded, standalone repositories).
            scope: Transaction scope shared by a group of repositories.  When
                a transaction is active in the current thread/task, every
                repository bound to the scope uses its connection.
        """
        self.db = db
        self._scope = scope
        self._explicit_connection = connection

    # ------------------------------------------------------------------
    # Connection resolution
    # ------------------------------------------------------------------
    @property
    def _connection(self):
        """The connection this repository must use for the current call."""
        if self._explicit_connection is not None:
            return self._explicit_connection
        if self._scope is not None:
            return self._scope.current
        return None

    @_connection.setter
    def _connection(self, value):
        # Backwards compatibility for callers that bind a connection directly.
        self._explicit_connection = value

    @contextmanager
    def get_cursor(self, commit: bool = True):
        """
        Yield a cursor, translating driver errors into :class:`QueryError`.

        Two very different modes exist and they must not be confused:

        *No transaction context* (``self._connection is None``) - the
        repository owns the connection for the duration of the call:
        commit on success, roll back on failure, then return it to the pool.

        *Transaction context* (``self._connection`` set) - the connection
        belongs to the caller's transaction.  This method therefore never
        commits and, critically, **never rolls back**: rolling back here
        would silently discard the work the caller already did in the
        transaction (hash, path, words, ...) while the caller keeps using the
        ids of those discarded rows.

        When PostgreSQL has already aborted the transaction, a
        :class:`TransactionAbortedError` is raised immediately: the
        transaction owner is the only party that may end it.

        Args:
            commit: If True and no transaction context is active, commit on
                success.  Ignored inside a transaction context.

        Yields:
            Database cursor
        """
        if self._connection is not None:
            with self._transaction_cursor() as cur:
                yield cur
        else:
            with self._pooled_cursor(commit) as cur:
                yield cur

    @contextmanager
    def _transaction_cursor(self):
        """Cursor on the caller-owned transaction connection (never commits)."""
        conn = self._connection
        if conn is None or conn.closed:
            raise QueryError("Transaction connection is closed")

        if connection_in_error_state(conn):
            raise TransactionAbortedError(
                "current transaction is aborted, commands ignored until end "
                "of transaction block"
            )

        try:
            cur = conn.cursor()
        except Exception as e:  # driver-level failure
            raise QueryError(f"Failed to create cursor: {e}") from e

        try:
            yield cur
        except psycopg2.errors.InFailedSqlTransaction as e:
            # The statement failed *and* PostgreSQL aborted the transaction.
            # Report it as such; the transaction manager performs the single
            # rollback for the whole unit of work.
            raise TransactionAbortedError(
                "current transaction is aborted, commands ignored until end "
                "of transaction block"
            ) from e
        except psycopg2.Error as e:
            if connection_in_error_state(conn):
                raise TransactionAbortedError(str(e).strip()) from e
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass  # cursor cleanup must never mask the real error

    @contextmanager
    def _pooled_cursor(self, commit: bool):
        """Cursor on a connection this repository takes from the pool."""
        conn = self.db.connect()
        cur = None
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        except Exception as e:
            if commit:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if isinstance(e, QueryError):
                raise
            raise QueryError(f"Query execution failed: {e}") from e
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            self.db.putconn(conn)

    @staticmethod
    def _transaction_failed(conn) -> bool:
        """True when PostgreSQL has aborted the connection's transaction."""
        return connection_in_error_state(conn)

    # ------------------------------------------------------------------
    # Savepoints and transactions
    # ------------------------------------------------------------------
    @contextmanager
    def savepoint(self, name: Optional[str] = None):
        """Run a block inside a ``SAVEPOINT`` so its failure is containable.

        Use this for *optional* work inside a transaction (display-text cache,
        keyword links, titles): if the block raises, only the block's own
        statements are rolled back and the surrounding transaction stays
        usable, instead of the failure poisoning every later statement with
        ``current transaction is aborted``.

        Outside a transaction context a savepoint is meaningless (each
        statement already stands alone), so the block simply runs.

        Yields:
            None - the caller keeps using its repositories as usual.
        """
        if self._scope is not None:
            with self._scope.savepoint(name):
                yield
        else:
            yield

    @contextmanager
    def transaction(self):
        """
        Begin a transaction spanning every repository bound to the scope.

        Usage:
            with repo.transaction():
                repo.execute(...)
                repo.execute(...)
                # All operations commit together or rollback on error

        Inside an existing transaction the block becomes a savepoint, so a
        nested failure cannot discard the surrounding unit of work.

        Yields:
            The transaction-aware repository (``self``).
        """
        scope = self._scope

        # Nested transaction: keep the outer connection, contain failures.
        if scope is not None and scope.active:
            with scope.savepoint():
                yield self
            return

        if scope is None:
            # Legacy single-threaded behaviour: bind an explicit connection so
            # repositories created from this one... there are none; the caller
            # simply uses the same instance.
            conn = self.db.connect()
            token = None
            try:
                token = self._bind_standalone(conn)
                yield self
                conn.commit()
                logger.debug("Transaction committed successfully")
            except Exception as e:
                if conn is not None:
                    _safe_rollback(conn)
                logger.error("Transaction rolled back due to error: %s", e)
                if isinstance(e, QueryError):
                    raise
                raise TransactionError(f"Transaction failed: {e}") from e
            finally:
                self._unbind_standalone(token, conn)
            return

        conn = self.db.connect()
        token = scope.bind(conn)
        try:
            yield self
            conn.commit()
            logger.debug("Transaction committed successfully")
        except Exception as e:
            _safe_rollback(conn)
            logger.error("Transaction rolled back due to error: %s", e)
            if isinstance(e, QueryError):
                raise
            raise TransactionError(f"Transaction failed: {e}") from e
        finally:
            scope.release(token)
            self.db.putconn(conn)

    def _bind_standalone(self, conn):
        """Bind ``conn`` to this repository only (no scope)."""
        previous = self._explicit_connection
        self._explicit_connection = conn
        return previous

    def _unbind_standalone(self, token, conn):
        self._explicit_connection = token
        self.db.putconn(conn)

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------
    def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
        fetchone: bool = False,
        fetchall: bool = False,
        single: bool = False,
        fetch_one: bool = False,
        fetch_all: bool = False,
        commit: bool = True
    ):
        """
        Execute a query with proper error handling.

        Supports multiple calling conventions for backward compatibility:
        - execute(query, params, True) - positional fetchone
        - execute(query, params, False, True) - positional fetchone=False, fetchall=True
        - execute(query, params, fetchone=True) - keyword
        - execute(query, params, fetchall=True) - keyword
        - execute(query, params, single=True) - keyword (alias for fetchone)
        - execute(query, params, fetch_one=True) - keyword (alias for fetchone)
        - execute(query, params, fetch_all=True) - keyword (alias for fetchall)

        Args:
            query: SQL query string
            params: Query parameters
            fetchone: If True, return single row
            fetchall: If True, return all rows
            single: Alias for fetchone (for backward compatibility)
            fetch_one: Alias for fetchone (for backward compatibility)
            fetch_all: Alias for fetchall (for backward compatibility)
            commit: If True, commit transaction (False for transaction context)

        Returns:
            Query result based on fetch flags, or rowcount for INSERT/UPDATE/DELETE
        """
        # Normalize fetch flags - support all naming conventions
        should_fetchone = fetchone or single or fetch_one
        should_fetchall = fetchall or fetch_all

        with self.get_cursor(commit=commit) as cur:
            try:
                cur.execute(query, params)

                # Check if query has RETURNING clause (takes priority over fetch flags for INSERT/UPDATE/DELETE)
                query_upper = query.strip().upper()
                has_returning = 'RETURNING' in query_upper
                is_insert = query_upper.startswith('INSERT')
                is_update = query_upper.startswith('UPDATE')
                is_delete = query_upper.startswith('DELETE')

                # For INSERT/UPDATE/DELETE with RETURNING, always fetch the RETURNING result
                if (is_insert or is_update or is_delete) and has_returning:
                    if cur.rowcount > 0:
                        try:
                            result = cur.fetchone()
                            if result:
                                return result[0] if isinstance(result, tuple) and len(result) == 1 else result
                        except Exception as fetch_err:
                            logger.warning(f"Failed to fetch RETURNING result: {fetch_err}")
                            # Fallback to rowcount for INSERT, None for others
                            return cur.rowcount if is_insert else None
                    return None

                # Handle SELECT queries with fetch flags
                if should_fetchone:
                    return cur.fetchone()
                if should_fetchall:
                    return cur.fetchall()

                # For INSERT/UPDATE/DELETE without RETURNING
                if is_insert:
                    return cur.rowcount if cur.rowcount > 0 else None
                elif is_update or is_delete:
                    return cur.rowcount
                else:
                    # Other query types
                    return cur.rowcount

            except QueryError:
                # Already classified by the cursor layer (including
                # TransactionAbortedError, which subclasses QueryError).
                raise
            except (psycopg2.errors.InFailedSqlTransaction, errors.InFailedSqlTransaction) as e:
                # The statement failed *and* PostgreSQL aborted the transaction.
                logger.error(f"Query execution error (transaction aborted): {query[:100]}... - {e}")
                raise TransactionAbortedError(
                    "current transaction is aborted, commands ignored until end "
                    "of transaction block"
                ) from e
            except Exception as e:
                if self._transaction_failed(self._connection):
                    logger.error(f"Query execution error (transaction aborted): {query[:100]}... - {e}")
                    raise TransactionAbortedError(str(e).strip()) from e
                logger.error(f"Query execution error: {query[:100]}... - {e}")
                raise QueryError(f"Query execution failed: {e}") from e

    # ------------------------------------------------------------------
    # Word ingest
    # ------------------------------------------------------------------
    def bulk_upsert_words(self, rows, page_size: int = 2000):
        """Insert many words into ``words``, ignoring already-known ones.

        This replaces the previous ``CREATE TEMP TABLE tmp_words`` + ``COPY``
        + ``INSERT ... SELECT FROM tmp_words`` sequence.  That design was
        unsafe for this application:

        * ``tmp_words`` is a per-*session* object with a fixed name: two
          concurrent operations on the same pooled connection destroyed each
          other's table in the middle of the COPY;
        * the DDL is transactional, so a rollback removed the table while the
          retry logic still expected it to exist;
        * every call had to drive the COPY protocol state machine - the source
          of ``no COPY in progress`` and ``server sent data ("D" message)
          without prior row description ("T" message)``.

        A paged ``INSERT ... VALUES %s ON CONFLICT DO NOTHING`` is atomic,
        connection-local and concurrency-safe: it creates no session state and
        cannot be disturbed by another thread.

        Args:
            rows: Iterable of ``(word,)`` tuples (or plain strings).
            page_size: Rows per round trip; keeps memory bounded for very
                large documents while still pipelining the inserts.
        """
        if not rows:
            return

        from psycopg2.extras import execute_values

        values = []
        for row in rows:
            word = row[0] if isinstance(row, (tuple, list)) else row
            if word is None:
                continue
            word = str(word).strip()
            if word:
                values.append((word,))

        if not values:
            return

        # Ordered, duplicate-free insertion.
        #
        # ``ON CONFLICT DO NOTHING`` tolerates repeated words, but it is the
        # *order* in which a transaction touches the ``words_word_key`` btree
        # that decides whether two concurrent documents can deadlock. Each
        # document used to insert its own token order, so two files sharing
        # vocabulary acquired index locks in opposite orders and PostgreSQL
        # aborted one of them mid-write:
        #
        #   deadlock detected
        #   Process 18648 waits for ShareLock on transaction 324875; blocked by
        #   process 14328. ... while inserting index tuple in relation "words"
        #
        # observed while two documents were stored concurrently. Inserting
        # every batch in one global order makes lock acquisition monotone for
        # all transactions - the standard fix for concurrent inserts into a
        # shared dictionary - so a document is never rolled back (and re-stored)
        # because another document happened to share words with it.
        #
        # The set() also drops repeats before they reach the database; a
        # 170k-word spreadsheet has far fewer distinct tokens. ``ON CONFLICT``
        # still covers words that already exist in the table.
        values = sorted(set(values))

        with self.get_cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO words (word) VALUES %s ON CONFLICT (word) DO NOTHING",
                values,
                page_size=page_size,
            )

    def create_temp_copy_to_words(self, query_export: str, buffer):
        """Deprecated compatibility shim for the removed temp-table COPY path.

        The old implementation created the session-scoped ``tmp_words`` table,
        loaded a CSV buffer into it with ``COPY`` and ran an export query.  That
        is precisely what broke under concurrency (shared sessions, temp-table
        visibility, COPY protocol state), so :class:`WordsRepository` now
        inserts directly through :meth:`bulk_upsert_words`.

        Kept for external callers: a CSV buffer with one word per row is
        inserted straight into ``words``.  Any other export query is rejected
        rather than silently inserting data into the wrong table.

        Args:
            query_export: Export query used by the caller (kept for signature
                compatibility; the buffer content is what gets inserted).
            buffer: CSV text (or file-like object) with one word per row.
        """
        logger.warning(
            "create_temp_copy_to_words() is deprecated; the words repository "
            "now inserts directly via bulk_upsert_words()."
        )
        if not buffer:
            return

        if hasattr(buffer, "read"):
            content = buffer.read()
        else:
            content = buffer

        words = []
        for row in csv.reader(io.StringIO(content)):
            if row:
                words.append((row[0],))

        self.bulk_upsert_words(words)
