"""
Context-local transaction binding shared by a group of repositories.

Why this exists
---------------
Repository objects are long-lived and shared.  ``ContentDBService`` builds one
instance per table and the ingestion pipeline drives those same instances from
several worker threads at once (``IntegratedFileReader._process_with_threads``
starts one managed thread per file, and all of them store through the single
``StoragePipeline.db_service``).

A transaction therefore cannot be represented by mutating repository
attributes.  The previous implementation replaced ``service.<repo>`` with
connection-bound clones for the duration of a transaction; with two threads
inside ``process_full_document`` at the same time, thread B's clone assignment
overwrote thread A's, and thread A's next statement ran on thread B's
connection.  The visible symptoms were exactly the production failures:

* ``relation "tmp_words" does not exist`` - the temporary table is a
  per-*session* object; it was created on one connection and read on another;
* ``no COPY in progress`` and ``server sent data ("D" message) without prior
  row description ("T" message)`` - two threads driving COPY on one session;
* ``current transaction is aborted`` cascades, followed by foreign key
  violations against rows whose transaction had been rolled back.

:class:`TransactionScope` keeps the binding in a :class:`contextvars.ContextVar`
instead.  Every thread (and every asyncio task) sees its own connection, nested
``with`` blocks stack, and the previous value is restored when the block exits.
No shared object is mutated, so the repositories stay safe to share.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import itertools
import logging
import psycopg2

from database.exceptions import ContainedStatementError, QueryError, TransactionAbortedError

logger = logging.getLogger(__name__)

#: Keeps generated savepoint names unique within the process.
_savepoint_counter = itertools.count(1)


def connection_in_error_state(conn) -> bool:
    """Return True when PostgreSQL has aborted ``conn``'s transaction.

    A transaction enters this state as soon as any statement in it raises an
    error; every later statement is rejected with ``current transaction is
    aborted, commands ignored until end of transaction block`` until the
    transaction is rolled back.
    """
    if conn is None:
        return False
    try:
        from psycopg2 import extensions

        status = conn.info.transaction_status
        if status == extensions.TRANSACTION_STATUS_INERROR:
            return True
        if status == extensions.TRANSACTION_STATUS_UNKNOWN:
            # Connection is broken (server gone, network dropped): treat it as
            # unusable rather than letting the next statement fail obscurely.
            return True
        return False
    except Exception:
        return False


class TransactionScope:
    """Binds one connection to the calling thread/task for a group of repos.

    Args:
        name: Human-readable name used in logs and savepoint names.
    """

    def __init__(self, name: str = "transaction"):
        self.name = name
        # The tuple is a stack so nested ``transaction()`` blocks can stack
        # (inner blocks reuse the outer connection through savepoints).
        self._stack: ContextVar = ContextVar(f"inforaxis_tx_scope_{name}", default=())

    # ------------------------------------------------------------------
    # Binding
    # ------------------------------------------------------------------
    @property
    def current(self):
        """The connection bound to the calling thread/task, or None."""
        stack = self._stack.get()
        return stack[-1] if stack else None

    @property
    def active(self) -> bool:
        """True while a transaction is bound in the calling thread/task."""
        return self.current is not None

    def bind(self, connection):
        """Bind ``connection`` for the calling thread/task; returns a token."""
        stack = self._stack.get()
        return self._stack.set(stack + (connection,))

    def release(self, token) -> None:
        """Undo a previous :meth:`bind` using its token."""
        self._stack.reset(token)

    @contextmanager
    def connection(self, connection):
        """Bind ``connection`` for the duration of the block."""
        token = self.bind(connection)
        try:
            yield connection
        finally:
            self.release(token)

    # ------------------------------------------------------------------
    # Savepoints
    # ------------------------------------------------------------------
    @contextmanager
    def savepoint(self, name: str = None):
        """Run a block inside a ``SAVEPOINT`` so its failure is containable.

        Use this for optional work inside a transaction (display-text cache,
        keyword links, titles): if the block raises, only that block's
        statements are rolled back and the surrounding transaction stays
        usable, instead of the failure poisoning every later statement with
        ``current transaction is aborted``.

        Outside a transaction the block simply runs (each statement already
        stands alone).
        """
        conn = self.current
        if conn is None or conn.closed:
            yield
            return

        if connection_in_error_state(conn):
            # Nothing to contain: the transaction is already dead.  Let the
            # owner roll it back instead of issuing statements that cannot run.
            raise TransactionAbortedError(
                "current transaction is aborted, commands ignored until end "
                "of transaction block"
            )

        import threading

        sp_name = name or "sp_{}_{}".format(
            threading.get_ident() % 100000, next(_savepoint_counter)
        )
        cur = conn.cursor()
        try:
            cur.execute(f"SAVEPOINT {sp_name}")
        except psycopg2.Error as e:
            cur.close()
            if connection_in_error_state(conn):
                raise TransactionAbortedError(str(e).strip()) from e
            raise QueryError(f"Could not create savepoint: {e}") from e

        try:
            yield
        except BaseException as err:
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
            except psycopg2.Error as rollback_err:
                # The savepoint could not be restored (e.g. the connection
                # died): the caller must treat the whole transaction as lost.
                cur.close()
                raise TransactionAbortedError(
                    f"Savepoint {sp_name} could not be rolled back: {rollback_err}"
                ) from err
            cur.close()
            if isinstance(err, TransactionAbortedError):
                # Restored: the transaction is usable again, so it is no
                # longer "aborted". Surface a containable error instead.
                raise ContainedStatementError(str(err)) from err
            raise
        else:
            try:
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
            except psycopg2.Error as release_err:
                logger.warning("Could not release savepoint %s: %s", sp_name, release_err)
            finally:
                cur.close()
