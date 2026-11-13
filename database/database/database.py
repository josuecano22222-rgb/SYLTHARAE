"""
Enhanced Database class with connection pooling and transaction support.
"""
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
from typing import Optional, Dict, Any
import logging
import threading
import time

from database.database.config import DatabaseConfig

logger = logging.getLogger(__name__)

#: Error fragments that mean "the pool or the server is saturated right now"
#: rather than "the database is unreachable".  They are retried with backoff
#: and are never treated as a broken connection: reconnecting on saturation is
#: what turned one busy moment into the production reconnect storm
#: ("Database connection unhealthy, attempting reconnect..." ->
#: "Failed to initialize connection pool: ... too many clients already").
_BUSY_ERROR_MARKERS = (
    'connection pool exhausted',
    'could not get connection',
    'too many clients',
    'remaining connection slots are reserved',
    'connection pool is closed',
)

#: How long a rebuilt pool must serve before another rebuild is attempted.
#: Several worker threads can observe the same failure; without a cooldown
#: they would each tear down and recreate the pool, multiplying connections
#: instead of recovering.
_RECONNECT_COOLDOWN_SECONDS = 2.0

#: One psycopg2 pool per connection target, shared by every Database instance
#: in the process.  The application creates Database objects in many places
#: (ContentDBService, StoragePipeline, DatabaseHub, TransactionManager,
#: helper query functions) and each one used to build its own pool; the
#: number of PostgreSQL backends therefore grew with the number of objects
#: rather than with the configured pool size, which is how the server reached
#: "FATAL: sorry, too many clients already".
_POOL_REGISTRY: Dict[tuple, "_SharedConnectionPool"] = {}
_POOL_REGISTRY_LOCK = threading.RLock()

#: ``(target, size, capped)`` advice already emitted for a live pool.  Pool
#: size advice used to be logged by every Database handle for the same pool,
#: which repeated one recommendation dozens of times in a single ingestion.
_POOL_ADVICE_SEEN: set = set()


def _is_busy_error(error) -> bool:
    """True when ``error`` describes saturation rather than an outage."""
    text = str(error).lower()
    return any(marker in text for marker in _BUSY_ERROR_MARKERS)


def _forget_pool_advice(key: tuple) -> None:
    """Allow the pool-size advice to be emitted again for ``key``."""
    with _POOL_REGISTRY_LOCK:
        # Materialise first: mutating the set while iterating it raises.
        for entry in [e for e in _POOL_ADVICE_SEEN if e[0] == key]:
            _POOL_ADVICE_SEEN.discard(entry)


def _first_pool_advice(key: tuple, size: int, capped: bool) -> bool:
    """True the first time this advice is given for a live pool."""
    entry = (key, size, capped)
    with _POOL_REGISTRY_LOCK:
        if entry in _POOL_ADVICE_SEEN:
            return False
        _POOL_ADVICE_SEEN.add(entry)
        return True


def _connection_target(config: DatabaseConfig) -> tuple:
    """Identity of the server/session a pool serves.

    Pools are shared only between Database objects that would connect to the
    same database as the same user, so a test or a second deployment pointing
    at another database never reuses the wrong connections.
    """
    return (
        str(config.host),
        int(config.port),
        str(config.dbname),
        str(config.user),
        str(config.password or ''),
    )


class _SharedConnectionPool:
    """The single psycopg2 pool for one connection target.

    ``refs`` counts the Database handles that currently use the pool.  A
    handle releasing the pool (``Database.close_all``) only closes the
    underlying connections when it was the last user, so a nested reader
    finishing its work can never pull the pool out from under its parent.
    """

    def __init__(self, key: tuple, config: DatabaseConfig):
        self.key = key
        self.config = config
        self.lock = threading.RLock()
        self.refs = 0
        self.generation = 0
        self.pool = None
        self.created_at = 0.0
        self.last_rebuild_attempt = 0.0
        #: Size the pool was clamped to because the server could not provide
        #: more (``None`` when the configured size fit) and why the clamp was
        #: applied (``'free'`` slots, or the server's per-process ``'ceiling'``
        #: when nothing was free).  Read by the pool-size advice so it never
        #: recommends a size this server cannot serve.
        self.capacity_cap = None
        self.capacity_reason = None
        self._build()

    def _build(self) -> None:
        """Create a replacement psycopg2 pool."""
        cfg = self.config
        self.capacity_cap = None
        self.capacity_reason = None
        self._cap_to_server_capacity(cfg)
        new_pool = pool.ThreadedConnectionPool(
            minconn=cfg.min_connections,
            maxconn=cfg.max_connections,
            **cfg.to_dict()
        )
        if not new_pool:
            raise ConnectionError("Connection pool creation returned None")
        self.pool = new_pool
        self.generation += 1
        self.created_at = time.time()

    def _cap_to_server_capacity(self, cfg: DatabaseConfig) -> None:
        """Reduce ``max_connections`` when the server cannot serve that many.

        A pool configured larger than the server's remaining capacity does not
        fail at startup - it fails later, once every connection is in use, as
        ``FATAL: sorry, too many clients already``, and then again on every
        reconnection attempt (exactly the loop in the production log).  The
        configured size is clamped to what PostgreSQL can actually give this
        process, and the reduction is logged so the operator can fix the
        setting rather than discover it under load.

        ``min_connections`` is never raised above the clamp, because a pool
        that cannot reach its minimum would refuse to start at all.
        """
        try:
            conn = psycopg2.connect(connect_timeout=5, **cfg.to_dict())
        except Exception as exc:
            # Cannot introspect (server down, credentials...): keep the
            # configured size and let normal connection handling report it.
            logger.debug("Could not determine server connection capacity: %s", exc)
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_setting('max_connections')::int, "
                    "current_setting('superuser_reserved_connections')::int"
                )
                max_connections, reserved = cur.fetchone()
                cur.execute("SELECT count(*) FROM pg_stat_activity")
                in_use = cur.fetchone()[0]
        except Exception as exc:
            logger.debug("Could not read server connection limits: %s", exc)
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

        available = int(max_connections) - int(reserved) - max(0, int(in_use) - 1)
        if available <= 0:
            # Every slot is taken right now (by other clients, or by this
            # process before it released them).  The pool must still not be
            # allowed to grow past the most this process could ever be granted,
            # or each connection beyond that limit fails with "too many clients
            # already" - the failure this sizing exists to prevent.  Clamp to
            # that ceiling and let the routine busy handling wait for a slot.
            ceiling = max(1, int(max_connections) - int(reserved))
            if cfg.max_connections > ceiling:
                logger.warning(
                    "Database server has no free connection slots (%s of %s in use, %s reserved); "
                    "capping the pool at the server's ceiling of %s connections until slots free up.",
                    in_use, max_connections, reserved, ceiling,
                )
                cfg.max_connections = ceiling
                self.capacity_cap = ceiling
                self.capacity_reason = "ceiling"
                if cfg.min_connections > cfg.max_connections:
                    cfg.min_connections = cfg.max_connections
            else:
                logger.warning(
                    "Database server has no free connection slots (%s of %s in use, %s reserved); "
                    "retrying until one is released",
                    in_use, max_connections, reserved,
                )
            return
        if cfg.max_connections > available:
            logger.warning(
                "Configured pool size (%s) exceeds what the database server can provide "
                "(%s free of %s total, %s reserved, %s in use by other clients); "
                "capping the pool at %s. Lower pool_max_conn to match the server's "
                "capacity to avoid 'too many clients already' failures.",
                cfg.max_connections, available, max_connections, reserved, in_use, available,
            )
            cfg.max_connections = available
            self.capacity_cap = available
            self.capacity_reason = "free"
            if cfg.min_connections > cfg.max_connections:
                cfg.min_connections = cfg.max_connections

    def replace(self) -> bool:
        """Rebuild the pool after a real failure; returns True on success.

        The replacement is created before the old pool is closed so that a
        rebuild which cannot connect leaves the previous pool untouched.  The
        cooldown applies to *every* caller, including ones that believe the
        connection is dead: a burst of worker threads all observing the same
        failure must produce one new pool, not one pool each (which is what
        drove PostgreSQL to "too many clients already").
        """
        with self.lock:
            now = time.time()
            if (now - self.last_rebuild_attempt) < _RECONNECT_COOLDOWN_SECONDS:
                logger.debug(
                    "Pool rebuild for %s:%s/%s skipped (cooldown, %.2fs since last attempt)",
                    self.config.host, self.config.port, self.config.dbname,
                    now - self.last_rebuild_attempt,
                )
                return True
            self.last_rebuild_attempt = now
            old_pool = self.pool
            try:
                self._build()
            except Exception as exc:
                logger.error(
                    "Failed to rebuild connection pool for %s:%s/%s: %s",
                    self.config.host, self.config.port, self.config.dbname, exc,
                )
                return False
            if old_pool is not None:
                try:
                    old_pool.closeall()
                except Exception as close_err:
                    logger.debug("Error closing replaced pool: %s", close_err)
            logger.info(
                "Connection pool rebuilt for %s:%s/%s (generation %d)",
                self.config.host, self.config.port, self.config.dbname,
                self.generation,
            )
            return True


def _acquire_shared_pool(config: DatabaseConfig) -> "_SharedConnectionPool":
    """Return the shared pool for ``config``, creating it on first use."""
    key = _connection_target(config)
    with _POOL_REGISTRY_LOCK:
        shared = _POOL_REGISTRY.get(key)
        if shared is None:
            shared = _SharedConnectionPool(key, config)
            _POOL_REGISTRY[key] = shared
            logger.info(
                "Shared connection pool initialized: %d-%d connections",
                config.min_connections, config.max_connections,
            )
        else:
            logger.debug(
                "Reusing shared connection pool (%d-%d connections) for %s:%s/%s",
                shared.config.min_connections, shared.config.max_connections,
                config.host, config.port, config.dbname,
            )
        shared.refs += 1
        return shared


def _release_shared_pool(shared: "_SharedConnectionPool") -> None:
    """Drop one reference; close the pool when the last user releases it."""
    with _POOL_REGISTRY_LOCK:
        if _POOL_REGISTRY.get(shared.key) is not shared:
            # Already retired (configuration change); nothing to release.
            return
        shared.refs -= 1
        if shared.refs > 0:
            return
        _POOL_REGISTRY.pop(shared.key, None)
        _forget_pool_advice(shared.key)
    try:
        if shared.pool is not None:
            shared.pool.closeall()
            logger.info("Shared connection pool closed (no remaining users)")
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Error closing shared connection pool: %s", exc)


def reset_connection_pools() -> None:
    """Close and forget every shared pool in this process.

    Used when the database configuration changes (settings invalidation):
    the next Database object must build a pool for the new target instead of
    reusing connections to the old one.
    """
    with _POOL_REGISTRY_LOCK:
        pools = list(_POOL_REGISTRY.values())
        _POOL_REGISTRY.clear()
        _POOL_ADVICE_SEEN.clear()
    for shared in pools:
        try:
            if shared.pool is not None:
                shared.pool.closeall()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Error closing connection pool during reset: %s", exc)
    if pools:
        logger.info("Closed %d shared connection pool(s) after configuration change", len(pools))


class Database:
    """
    Database connection manager with connection pooling.
    
    Features:
    - Connection pooling for better performance
    - Environment-based configuration
    - Health check support
    - Proper resource cleanup
    """
    
    _file_size_schema_lock = threading.Lock()
    _file_size_schema_checked = False

    def __init__(self, config: Optional[DatabaseConfig] = None):
        """
        Initialize database with configuration.
        
        Args:
            config: DatabaseConfig instance. If None, loads from environment.
        """
        self.config = config or DatabaseConfig.from_env()
        self._shared: Optional[_SharedConnectionPool] = None
        self._initialize_pool()

    # ------------------------------------------------------------------
    # Pool access
    # ------------------------------------------------------------------
    @property
    def _pool(self):
        """The psycopg2 pool behind this handle (compatibility attribute).

        Kept as a property so every Database handle - and every caller that
        inspects ``db._pool`` - sees the current shared pool even after a
        reconnect replaced it.
        """
        shared = self._shared
        return shared.pool if shared is not None else None

    def _current_pool(self):
        """The shared pool to use now, re-acquiring it when necessary.

        A handle whose ``close_all()`` ran (for example a hub closed at the end
        of one ingestion) must still be usable afterwards: instead of holding a
        permanently closed pool - which used to surface every later statement
        as "Failed to get connection (attempt 1)" - it transparently re-joins
        the process-wide pool for its target.  The same applies when the pool
        this handle knew about was retired by a configuration change
        (``reset_connection_pools``).
        """
        shared = self._shared
        key = _connection_target(self.config)
        with _POOL_REGISTRY_LOCK:
            registered = _POOL_REGISTRY.get(key)
        if shared is None or registered is not shared:
            self._shared = _acquire_shared_pool(self.config)
        return self._shared.pool
    
    def _initialize_pool(self):
        """Initialize the connection pool (shared per connection target)."""
        try:
            # Validate pool size is reasonable
            if self.config.max_connections < self.config.min_connections:
                logger.warning(
                    f"max_connections ({self.config.max_connections}) < min_connections "
                    f"({self.config.min_connections}), setting max to min"
                )
                self.config.max_connections = self.config.min_connections
            
            # Advise on a small pool once per target, and only when a larger
            # pool is actually possible: a pool that was capped to the
            # server's free connection slots (see _cap_to_server_capacity)
            # cannot be raised without raising max_connections on the server,
            # so recommending a bigger number there was unactionable.
            self._shared = _acquire_shared_pool(self.config)
            self._advise_on_pool_size()

            # Older installations may predate migration 0008 and still have
            # paths.file_size as INTEGER.  The application can be started
            # without the installer/bootstrap path, so repair this one safe,
            # additive schema change when the pool is first created as well.
            self._ensure_large_file_sizes()
                
        except Exception as e:
            # Release the reference taken above: without this, a handle whose
            # initialization failed (advice or schema repair raising) left a
            # reference on the shared pool forever, so the pool was never
            # closed even after every usable handle had been released.
            if self._shared is not None:
                _release_shared_pool(self._shared)
            self._shared = None
            logger.error(f"Failed to initialize connection pool: {e}")
            # Re-raise the exception so callers know initialization failed
            raise ConnectionError(f"Failed to initialize connection pool: {e}") from e

    def rebuild_pool(self, force: bool = False) -> bool:
        """Reconnect by replacing the shared pool when it is actually broken.

        Reconnection is for lost connections, not for load: when the pool can
        still serve a ping there is nothing to rebuild, and a rebuild attempt
        is additionally rate-limited so concurrent workers cannot each build a
        new pool.
        """
        if self._shared is None:
            try:
                self._shared = _acquire_shared_pool(self.config)
                return True
            except Exception as exc:
                logger.error(f"Failed to re-initialize connection pool: {exc}")
                return False

        if not force and self.health_check():
            logger.debug("Database connection healthy; no pool rebuild needed")
            return True
        return self._shared.replace()
    
    def _advise_on_pool_size(self) -> None:
        """Report a small pool once, and only when it could be enlarged.

        With concurrent workers each worker may need 2-3 connections, so a
        minimum of ten is recommended.  The advice is emitted once per pool
        instead of once per Database handle, which repeated the same line
        dozens of times per ingestion.
        """
        shared = self._shared
        # The size that matters is the pool's, not this handle's: the pool is
        # shared and was built from whichever config reached it first.
        size = shared.config.max_connections
        if size >= 10:
            return
        cap = shared.capacity_cap
        if cap is not None and size >= cap:
            if _first_pool_advice(shared.key, size, True):
                source = (
                    "is the most the database server can grant this process "
                    "(max_connections minus superuser_reserved_connections)"
                    if shared.capacity_reason == "ceiling"
                    else "is the database server's free connection capacity"
                )
                logger.info(
                    "Connection pool size (%s) %s; raise the server's "
                    "max_connections to allow a larger pool for concurrent "
                    "processing.",
                    size, source,
                )
            return
        if _first_pool_advice(shared.key, size, False):
            logger.warning(
                f"Connection pool size ({size}) may be too small for concurrent processing. "
                f"Recommended: at least 15-25 connections for 4 workers. "
                f"Current setting may cause 'connection pool exhausted' errors."
            )

    def _ensure_large_file_sizes(self):
        """Ensure legacy databases accept files larger than 2 GiB.

        This is intentionally limited to the additive BIGINT widening covered
        by migration 0008.  It is a compatibility bridge for deployments that
        start the web app directly instead of running the installer; the
        migration remains the authoritative upgrade path.
        """
        cls = type(self)
        if cls._file_size_schema_checked:
            return
        with cls._file_size_schema_lock:
            if cls._file_size_schema_checked:
                return
            conn = None
            try:
                conn = self._current_pool().getconn()
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema = current_schema() "
                        "AND table_name = 'paths' AND column_name = 'file_size'"
                    )
                    row = cur.fetchone()
                    if row and row[0] == 'integer':
                        cur.execute(
                            "ALTER TABLE paths ALTER COLUMN file_size TYPE BIGINT"
                        )
                        conn.commit()
                        logger.info("Upgraded paths.file_size from INTEGER to BIGINT")
                    elif row:
                        conn.rollback()
                cls._file_size_schema_checked = True
            except Exception:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                # Do not make an otherwise usable pool unavailable because a
                # compatibility check could not run (for example during a
                # first-install race). The normal migration reports failures.
                logger.warning("Could not verify paths.file_size schema", exc_info=True)
            finally:
                if conn is not None:
                    self.putconn(conn)

    def _discard(self, conn):
        """Return a broken connection to its pool so the slot is freed.

        psycopg2 only releases a pool slot when ``putconn`` is called; closing
        the connection directly left it in ``_used`` forever, so every broken
        connection permanently shrank the pool until it reported
        "Connection pool exhausted" while the server had plenty of capacity.
        ``putconn`` on a closed connection discards it and frees the slot.
        """
        if conn is None:
            return
        shared = self._shared
        pool_obj = shared.pool if shared is not None else None
        if pool_obj is None:
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            pool_obj.putconn(conn)
        except Exception as put_err:
            # "trying to put unkeyed connection" means the connection came from
            # a pool that has since been replaced; it is not an error, the
            # connection simply has to be closed.
            logger.debug("Could not release connection to the pool: %s", put_err)
            try:
                conn.close()
            except Exception:
                pass

    def connect(self, timeout=30):
        """
        Get a connection from the pool with improved waiting and retry logic.
        
        When the pool is exhausted, this method will wait and retry with exponential backoff
        to allow connections to be returned to the pool.
        
        Args:
            timeout: Maximum time to wait for a connection (seconds)
        
        Returns:
            psycopg2 connection object
            
        Raises:
            ConnectionError: If connection cannot be obtained after timeout
        """
        if not self._current_pool():
            raise ConnectionError("Connection pool not initialized")

        max_retries = max(10, int(timeout * 2))  # More retries for longer waits
        retry_count = 0
        start_time = time.time()
        last_error = None

        while retry_count < max_retries:
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                detail = f"; last error: {last_error}" if last_error is not None else ""
                raise ConnectionError(
                    f"Connection pool exhausted - timeout after {timeout}s "
                    f"(pool size: {self.config.max_connections}){detail}"
                )

            try:
                pool_obj = self._current_pool()
            except Exception as acquire_err:
                last_error = acquire_err
                retry_count += 1
                time.sleep(min(0.1 * (2 ** min(retry_count, 5)), 2.0))
                continue

            try:
                # psycopg2's getconn() raises PoolError when the pool is
                # exhausted and OperationalError when the server refuses the
                # connection (for example "too many clients already").
                conn = pool_obj.getconn()
            except psycopg2.pool.PoolError as pool_err:
                if _is_busy_error(pool_err):
                    retry_count += 1
                    wait_time = min(0.1 * (2 ** min(retry_count, 5)), 2.0)
                    if retry_count % 5 == 0:  # Log every 5th attempt
                        logger.warning(
                            f"Connection pool exhausted (attempt {retry_count}/{max_retries}), "
                            f"waiting {wait_time:.2f}s... (pool size: {self.config.max_connections}, "
                            f"elapsed: {elapsed:.1f}s)"
                        )
                    time.sleep(wait_time)
                    continue
                # Other pool errors are structural: re-raise immediately.
                raise ConnectionError(f"Pool error: {pool_err}") from pool_err
            except ConnectionError:
                raise
            except Exception as e:
                last_error = e
                if _is_busy_error(e):
                    # Server-side saturation ("sorry, too many clients
                    # already") is transient: wait for a slot instead of
                    # reopening pools, which only makes saturation worse.
                    retry_count += 1
                    wait_time = min(0.1 * (2 ** min(retry_count, 5)), 2.0)
                    if retry_count % 5 == 0:
                        logger.warning(
                            f"Database connection limit reached (attempt {retry_count}/{max_retries}), "
                            f"waiting {wait_time:.2f}s... ({e})"
                        )
                    time.sleep(wait_time)
                    continue
                logger.error(f"Failed to get connection (attempt {retry_count + 1}): {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    raise ConnectionError(f"Failed to get connection after {max_retries} attempts: {e}") from e
                # Wait before retrying (exponential backoff)
                time.sleep(0.1 * retry_count)
                continue

            if not conn:
                raise ConnectionError("Failed to get connection from pool: got None")

            # Check if connection is still valid
            if conn.closed:
                logger.warning(f"Got closed connection from pool (attempt {retry_count + 1}), discarding and retrying...")
                self._discard(conn)
                retry_count += 1
                time.sleep(0.05)
                continue

            # Verify connection is actually usable
            try:
                test_cur = conn.cursor()
                test_cur.execute("SELECT 1")
                test_cur.close()
            except Exception as test_err:
                logger.warning(f"Connection failed health check (attempt {retry_count + 1}): {test_err}, retrying...")
                self._discard(conn)
                retry_count += 1
                time.sleep(0.05)
                continue

            # Connection is valid
            return conn

        detail = f"; last error: {last_error}" if last_error is not None else ""
        raise ConnectionError(
            f"Connection pool exhausted after {max_retries} attempts "
            f"(timeout: {timeout}s, pool size: {self.config.max_connections}){detail}. "
            f"Consider increasing pool_max_conn or reducing concurrent operations."
        )
    
    def putconn(self, conn):
        """
        Return a connection to the pool.
        Ensures connection is in a valid state before returning.
        
        Args:
            conn: Connection to return
        """
        if not conn:
            return

        shared = self._shared
        pool_obj = shared.pool if shared is not None else None
        if pool_obj is None:
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            if conn.closed:
                # Release the pool slot; psycopg2 discards closed connections.
                self._discard(conn)
                return

            # PRODUCTION: Reset connection state if it is in a bad transaction
            # state so the next user of this connection starts clean.
            try:
                from psycopg2 import extensions

                status = conn.info.transaction_status
                if status != extensions.TRANSACTION_STATUS_IDLE:
                    try:
                        conn.rollback()
                    except Exception as rollback_err:
                        logger.warning(
                            f"Connection in bad state (rollback failed: {rollback_err}), "
                            f"discarding instead of returning to pool"
                        )
                        self._discard(conn)
                        return
            except Exception:
                # State could not be determined; a rollback is the safe reset.
                try:
                    conn.rollback()
                except Exception:
                    pass

            # Return connection to pool (frees the slot even for dead ones)
            try:
                pool_obj.putconn(conn)
            except Exception as put_err:
                logger.debug(f"Could not put connection back to pool: {put_err}")
                self._discard(conn)
        except Exception as e:
            logger.error(f"Error returning connection to pool: {e}")
            self._discard(conn)
    
    @contextmanager
    def get_connection(self):
        """
        Context manager for getting and returning connections.
        
        Usage:
            with db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT ...")
                conn.commit()
        """
        conn = None
        try:
            conn = self.connect()
            yield conn
        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn is not None:
                self.putconn(conn)
    
    def health_check(self, timeout: float = 5.0) -> bool:
        """
        Check if database connection is healthy.

        A saturated pool or a saturated PostgreSQL server is *not* an
        unhealthy database: it is reported as healthy (with a debug note) so
        callers do not respond to load by reconnecting, which multiplies
        connections and turns load into an outage.

        Args:
            timeout: Maximum time to wait for a connection when checking.

        Returns:
            True if connection is healthy, False otherwise
        """
        conn = None
        try:
            conn = self.connect(timeout=timeout)
        except (psycopg2.pool.PoolError, ConnectionError) as pool_err:
            # Pool errors are expected when the pool is busy - not a health issue
            if _is_busy_error(pool_err):
                logger.debug(f"Health check: database busy (expected): {pool_err}")
                return True
            logger.debug(f"Health check: database unreachable: {pool_err}")
            return False
        except Exception as e:
            if _is_busy_error(e):
                logger.debug(f"Health check: database busy (expected): {e}")
                return True
            # Log the error type and message for debugging
            error_type = type(e).__name__
            logger.debug(f"Health check failed: {error_type}: {e}")
            return False

        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            return True
        except Exception as e:
            logger.debug(f"Health check query failed: {type(e).__name__}: {e}")
            return False
        finally:
            self.putconn(conn)
    
    def check_tables_exist(self, table_names: Optional[list] = None) -> Dict[str, bool]:
        """
        Check if required tables exist in the database.
        
        Args:
            table_names: List of table names to check. If None, checks all standard tables.
        
        Returns:
            Dictionary mapping table names to existence status
        """
        # Default list of all tables in the system
        if table_names is None:
            table_names = [
                'words', 'categorys', 'words_categorys', 'words_hashs',
                'keywords_hashs', 'keywords', 'contents', 'titles_content',
                'sides', 'sources', 'hashs', 'hash_contexts', 'paths',
                'punctuation', 'alerts'
            ]
        
        result = {}
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                for table_name in table_names:
                    try:
                        cur.execute(
                            """
                            SELECT EXISTS (
                                SELECT FROM information_schema.tables 
                                WHERE table_schema = 'public' 
                                AND table_name = %s
                            )
                            """,
                            (table_name,)
                        )
                        exists = cur.fetchone()[0]
                        result[table_name] = exists
                    except Exception as e:
                        logger.warning(f"Error checking table {table_name}: {e}")
                        result[table_name] = False
                cur.close()
        except Exception as e:
            logger.error(f"Error checking tables: {e}")
            # Return all False if connection fails
            result = {table: False for table in table_names}
        
        return result
    
    def validate_schema(self) -> Dict[str, Any]:
        """
        Validate that all required tables exist.
        
        Returns:
            Dictionary with validation results:
            - 'valid': bool - True if all tables exist
            - 'missing_tables': list - List of missing table names
            - 'existing_tables': list - List of existing table names
            - 'table_status': dict - Status of each table
        """
        table_status = self.check_tables_exist()
        missing_tables = [table for table, exists in table_status.items() if not exists]
        existing_tables = [table for table, exists in table_status.items() if exists]
        
        return {
            'valid': len(missing_tables) == 0,
            'missing_tables': missing_tables,
            'existing_tables': existing_tables,
            'table_status': table_status
        }
    
    def close_all(self):
        """Release this handle's reference to the shared connection pool.

        The underlying connections are only closed when the last Database
        handle using them is released.  Closing the pool from one component
        (for example a nested reader that finished one extraction) must never
        tear down connections other components are still using - that is what
        produced the "Database connection unhealthy, attempting reconnect..."
        cascade.  A handle stays usable after close_all(): it re-joins the
        process-wide pool on its next use.
        """
        shared = self._shared
        if shared is None:
            return
        self._shared = None
        _release_shared_pool(shared)
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup."""
        self.close_all()
