"""
Database Performance Analysis Tools
Provides classes for analyzing database performance, indexes, and query profiling.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class IndexInfo:
    """Information about a database index"""
    table_name: str
    index_name: str
    index_type: str
    columns: List[str]
    is_unique: bool
    is_primary: bool
    size_bytes: int
    usage_count: int = 0
    last_used: Optional[datetime] = None


@dataclass
class IndexRecommendation:
    """Index recommendation"""
    type: str  # 'missing', 'unused', 'duplicate'
    table_name: str
    columns: List[str]
    reason: str
    priority: str  # 'high', 'medium', 'low'
    estimated_impact: str
    sql: str


@dataclass
class SlowQuery:
    """Information about a slow query"""
    query: str
    execution_time: float
    rows_returned: int
    rows_examined: int
    timestamp: datetime
    error: Optional[str] = None


class DatabasePerformanceAnalyzer:
    """Analyzes database performance metrics"""
    
    def __init__(self, connection, slow_query_threshold: float = 1.0):
        """
        Initialize performance analyzer.
        
        Args:
            connection: Database connection
            slow_query_threshold: Threshold in seconds for slow queries
        """
        self.conn = connection
        self.slow_query_threshold = slow_query_threshold
    
    def analyze_performance(self, include_index_analysis: bool = True) -> Dict[str, Any]:
        """Perform comprehensive performance analysis"""
        result = {
            'connection_info': self._get_connection_info(),
            'table_statistics': self._get_table_statistics(),
            'slow_queries': self._get_slow_queries(),
        }
        
        if include_index_analysis:
            index_analyzer = IndexAnalyzer(self.conn)
            result['indexes'] = index_analyzer.get_all_indexes()
            result['index_recommendations'] = index_analyzer.analyze_missing_indexes()
        
        return result
    
    def get_performance_report(self) -> str:
        """Get human-readable performance report"""
        analysis = self.analyze_performance()
        
        report = []
        report.append("=" * 80)
        report.append("DATABASE PERFORMANCE REPORT")
        report.append("=" * 80)
        report.append("")
        
        # Connection info
        conn_info = analysis.get('connection_info', {})
        report.append(f"Active Connections: {conn_info.get('active_connections', 'N/A')}")
        report.append(f"Max Connections: {conn_info.get('max_connections', 'N/A')}")
        report.append("")
        
        # Table statistics
        table_stats = analysis.get('table_statistics', {})
        report.append("Table Statistics:")
        for table, stats in table_stats.items():
            report.append(f"  {table}: {stats.get('row_count', 0)} rows, {stats.get('size_mb', 0):.2f} MB")
        report.append("")
        
        # Slow queries
        slow_queries = analysis.get('slow_queries', [])
        report.append(f"Slow Queries (>={self.slow_query_threshold}s): {len(slow_queries)}")
        report.append("")
        
        return "\n".join(report)
    
    def _get_connection_info(self) -> Dict[str, Any]:
        """Get connection information"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT 
                    count(*) as active_connections,
                    (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') as max_connections
                FROM pg_stat_activity
                WHERE state = 'active'
            """)
            row = cur.fetchone()
            cur.close()
            
            if row:
                return {
                    'active_connections': row[0],
                    'max_connections': row[1]
                }
        except Exception as e:
            logger.error(f"Error getting connection info: {e}")
        
        return {'active_connections': 0, 'max_connections': 100}
    
    def _get_table_statistics(self) -> Dict[str, Dict[str, Any]]:
        """Get table statistics"""
        stats = {}
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT 
                    schemaname,
                    tablename,
                    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
                    pg_total_relation_size(schemaname||'.'||tablename) as size_bytes
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
            """)
            
            for row in cur.fetchall():
                table_name = row[1]
                stats[table_name] = {
                    'size': row[2],
                    'size_mb': row[3] / (1024 * 1024) if row[3] else 0
                }
            
            cur.close()
        except Exception as e:
            logger.error(f"Error getting table statistics: {e}")
        
        return stats
    
    def _get_slow_queries(self) -> List[Dict[str, Any]]:
        """Get slow queries (requires pg_stat_statements extension)"""
        slow_queries = []
        try:
            cur = self.conn.cursor()
            # Check if pg_stat_statements is available
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
                )
            """)
            has_extension = cur.fetchone()[0]
            
            if has_extension:
                cur.execute("""
                    SELECT 
                        query,
                        mean_exec_time / 1000.0 as execution_time,
                        calls,
                        rows
                    FROM pg_stat_statements
                    WHERE mean_exec_time / 1000.0 >= %s
                    ORDER BY mean_exec_time DESC
                    LIMIT 10
                """, (self.slow_query_threshold * 1000,))
                
                for row in cur.fetchall():
                    slow_queries.append({
                        'query': row[0][:500] if len(row[0]) > 500 else row[0],
                        'execution_time': row[1],
                        'calls': row[2],
                        'rows': row[3]
                    })
            else:
                logger.debug("pg_stat_statements extension not available")
            
            cur.close()
        except Exception as e:
            logger.debug(f"Could not get slow queries: {e}")
        
        return slow_queries


class IndexAnalyzer:
    """Analyzes database indexes and provides recommendations"""
    
    def __init__(self, connection):
        """
        Initialize index analyzer.
        
        Args:
            connection: Database connection
        """
        self.conn = connection
    
    def get_all_indexes(self) -> List[IndexInfo]:
        """Get all indexes in the database"""
        indexes = []
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT
                    t.tablename as table_name,
                    i.indexname as index_name,
                    i.indexdef as index_def,
                    pg_relation_size(i.indexname::regclass) as size_bytes,
                    i.indexdef LIKE '%UNIQUE%' as is_unique,
                    i.indexdef LIKE '%PRIMARY KEY%' as is_primary
                FROM pg_indexes i
                JOIN pg_tables t ON i.tablename = t.tablename AND i.schemaname = t.schemaname
                WHERE i.schemaname = 'public'
                ORDER BY t.tablename, i.indexname
            """)
            
            for row in cur.fetchall():
                # Extract columns from index definition
                index_def = row[2]
                columns = self._extract_columns_from_def(index_def)
                
                indexes.append(IndexInfo(
                    table_name=row[0],
                    index_name=row[1],
                    index_type='btree',  # Default, could be enhanced
                    columns=columns,
                    is_unique=row[4],
                    is_primary=row[5],
                    size_bytes=row[3] or 0
                ))
            
            cur.close()
        except Exception as e:
            logger.error(f"Error getting indexes: {e}")
        
        return indexes
    
    def analyze_missing_indexes(self) -> List[IndexRecommendation]:
        """Analyze and recommend missing indexes"""
        recommendations = []
        
        try:
            cur = self.conn.cursor()
            # Get tables with foreign keys that might need indexes
            cur.execute("""
                SELECT
                    tc.table_name,
                    kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = 'public'
            """)
            
            for row in cur.fetchall():
                table_name = row[0]
                column_name = row[1]
                
                # Check if index exists on this column
                cur.execute("""
                    SELECT COUNT(*)
                    FROM pg_indexes
                    WHERE tablename = %s
                        AND indexdef LIKE %s
                """, (table_name, f'%{column_name}%'))
                
                has_index = cur.fetchone()[0] > 0
                
                if not has_index:
                    recommendations.append(IndexRecommendation(
                        type='missing',
                        table_name=table_name,
                        columns=[column_name],
                        reason=f'Foreign key column {column_name} lacks an index',
                        priority='medium',
                        estimated_impact='Improves join performance',
                        sql=f'CREATE INDEX idx_{table_name}_{column_name} ON {table_name}({column_name});'
                    ))
            
            cur.close()
        except Exception as e:
            logger.error(f"Error analyzing missing indexes: {e}")
        
        return recommendations
    
    def get_index_statistics(self) -> Dict[str, Any]:
        """Get index statistics"""
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) as total_indexes,
                    SUM(pg_relation_size(indexname::regclass)) as total_size_bytes
                FROM pg_indexes
                WHERE schemaname = 'public'
            """)
            
            row = cur.fetchone()
            cur.close()
            
            if row:
                return {
                    'total_indexes': row[0] or 0,
                    'total_size_mb': (row[1] or 0) / (1024 * 1024)
                }
        except Exception as e:
            logger.error(f"Error getting index statistics: {e}")
        
        return {'total_indexes': 0, 'total_size_mb': 0}
    
    def _extract_columns_from_def(self, index_def: str) -> List[str]:
        """Extract column names from index definition"""
        # Simple extraction - look for columns in parentheses
        import re
        match = re.search(r'\(([^)]+)\)', index_def)
        if match:
            columns_str = match.group(1)
            # Split by comma and clean up
            columns = [col.strip().split()[0] for col in columns_str.split(',')]
            return columns
        return []


class QueryProfiler:
    """Profiles query execution"""
    
    def __init__(self, connection, slow_query_threshold: float = 1.0):
        """
        Initialize query profiler.
        
        Args:
            connection: Database connection
            slow_query_threshold: Threshold in seconds for slow queries
        """
        self.conn = connection
        self.slow_query_threshold = slow_query_threshold
        self.profiled_queries: List[SlowQuery] = []
    
    def profile_query(self, query: str, params=None) -> Dict[str, Any]:
        """Profile a single query execution"""
        import time
        
        start_time = time.time()
        error = None
        rows_returned = 0
        
        try:
            cur = self.conn.cursor()
            if params:
                cur.execute(query, params)
            else:
                cur.execute(query)
            
            rows_returned = cur.rowcount
            if cur.description:
                rows_returned = len(cur.fetchall())
            
            cur.close()
        except Exception as e:
            error = str(e)
        
        execution_time = time.time() - start_time
        
        # Record slow queries
        if execution_time >= self.slow_query_threshold:
            self.profiled_queries.append(SlowQuery(
                query=query[:500],
                execution_time=execution_time,
                rows_returned=rows_returned,
                rows_examined=rows_returned,  # Simplified
                timestamp=datetime.now(),
                error=error
            ))
        
        return {
            'execution_time': execution_time,
            'rows_returned': rows_returned,
            'error': error
        }
    
    def get_slow_queries(self, limit: int = 10) -> List[SlowQuery]:
        """Get slow queries from this session"""
        return sorted(self.profiled_queries, key=lambda x: x.execution_time, reverse=True)[:limit]
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        if not self.profiled_queries:
            return {
                'total_queries': 0,
                'slow_queries': 0,
                'average_time': 0
            }
        
        total_time = sum(q.execution_time for q in self.profiled_queries)
        
        return {
            'total_queries': len(self.profiled_queries),
            'slow_queries': len([q for q in self.profiled_queries if q.execution_time >= self.slow_query_threshold]),
            'average_time': total_time / len(self.profiled_queries),
            'max_time': max(q.execution_time for q in self.profiled_queries)
        }

