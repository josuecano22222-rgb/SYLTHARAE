/**
 * Cursor-Based Pagination Client
 * Provides seamless pagination for billion+ record datasets
 */

// Define CursorPaginator class
class CursorPaginator {
    /**
     * Initialize cursor-based paginator
     * 
     * @param {Object} options Configuration options
     * @param {string} options.table - Table name
     * @param {string} options.table_alias - Table alias (optional)
     * @param {number} options.limit - Records per page (default: 50)
     * @param {Object} options.filters - Filter dictionary
     * @param {Array<string>} options.joins - JOIN clauses
     * @param {Array<string>} options.select_columns - Columns to select
     * @param {string} options.sort_column - Column to sort by
     * @param {string} options.sort_direction - ASC or DESC
     * @param {Function} options.onPageLoad - Callback when page loads
     * @param {Function} options.onProgress - Callback for progress updates
     * @param {Function} options.onError - Callback for errors
     */
    constructor(options = {}) {
        this.table = options.table;
        this.table_alias = options.table_alias || null;
        this.limit = options.limit || 50;
        this.filters = options.filters || {};
        this.joins = options.joins || [];
        this.select_columns = options.select_columns || null;
        this.sort_column = options.sort_column || null;
        this.sort_direction = options.sort_direction || 'ASC';
        
        this.onPageLoad = options.onPageLoad || (() => {});
        this.onProgress = options.onProgress || (() => {});
        this.onError = options.onError || (() => {});
        
        // State
        this.currentCursor = null;
        this.nextCursor = null;
        this.prevCursor = null;
        this.hasNext = false;
        this.hasNext = false;
        this.currentData = [];
        this.totalEstimated = null;
        this.queryTimeMs = null;
        this.loading = false;
        
        // SSE connection for streaming
        this.eventSource = null;
        this.streaming = false;
        
        // Background prefetching
        this.prefetchEnabled = options.prefetchEnabled !== false;
        this.prefetchQueue = [];
    }
    
    /**
     * Load a page
     * 
     * @param {number|null} cursor - Cursor value (null for first page)
     * @returns {Promise<Object>} Page data
     */
    async loadPage(cursor = null) {
        if (this.loading) {
            console.warn('Page load already in progress');
            return Promise.resolve(this.currentData);
        }
        
        this.loading = true;
        this.currentCursor = cursor;
        
        try {
            // Build query parameters
            const params = new URLSearchParams({
                table: this.table,
                limit: this.limit.toString()
            });
            
            if (cursor !== null) {
                params.append('cursor', cursor.toString());
            }
            
            if (this.sort_column) {
                params.append('sort_column', this.sort_column);
                params.append('sort_direction', this.sort_direction);
            }
            
            if (Object.keys(this.filters).length > 0) {
                params.append('filters', JSON.stringify(this.filters));
            }
            
            if (this.joins.length > 0) {
                params.append('joins', JSON.stringify(this.joins));
            }
            
            if (this.select_columns) {
                params.append('select_columns', JSON.stringify(this.select_columns));
            }
            
            if (this.table_alias) {
                params.append('table_alias', this.table_alias);
            }
            
            // Make request
            const response = await fetch(`/api/query/cursor?${params.toString()}`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();
            
            if (!result.success) {
                throw new Error(result.error || 'Unknown error');
            }
            
            // Update state
            this.currentData = result.data || [];
            this.nextCursor = result.next_cursor;
            this.prevCursor = result.prev_cursor;
            this.hasNext = result.has_next;
            this.hasPrev = result.has_prev;
            this.totalEstimated = result.total_estimated;
            this.queryTimeMs = result.query_time_ms;
            
            // Prefetch next page in background
            if (this.prefetchEnabled && this.hasNext) {
                this._prefetchNextPage();
            }
            
            // Call callback
            this.onPageLoad({
                data: this.currentData,
                cursor: this.currentCursor,
                nextCursor: this.nextCursor,
                prevCursor: this.prevCursor,
                hasNext: this.hasNext,
                hasPrev: this.hasPrev,
                totalEstimated: this.totalEstimated,
                queryTimeMs: this.queryTimeMs
            });
            
            return result;
        
        } catch (error) {
            console.error('Error loading page:', error);
            this.onError(error);
            throw error;
        } finally {
            this.loading = false;
        }
    }
    
    /**
     * Load next page
     */
    async loadNextPage() {
        if (!this.hasNext || !this.nextCursor) {
            return null;
        }
        return await this.loadPage(this.nextCursor);
    }
    
    /**
     * Load previous page
     */
    async loadPrevPage() {
        if (!this.hasPrev || !this.prevCursor) {
            return null;
        }
        return await this.loadPage(this.prevCursor);
    }
    
    /**
     * Stream all records using Server-Sent Events
     * 
     * @param {Function} onData - Callback for each batch of data
     * @param {Function} onComplete - Callback when streaming completes
     * @param {number} batchSize - Records per batch (default: 50)
     */
    streamAll(onData, onComplete, batchSize = 50) {
        if (this.streaming) {
            console.warn('Streaming already in progress');
            return;
        }
        
        this.streaming = true;
        
        // Build query parameters
        const params = new URLSearchParams({
            table: this.table,
            limit: '10000',  // Large limit for streaming
            batch_size: batchSize.toString()
        });
        
        if (Object.keys(this.filters).length > 0) {
            params.append('filters', JSON.stringify(this.filters));
        }
        
        if (this.joins.length > 0) {
            params.append('joins', JSON.stringify(this.joins));
        }
        
        if (this.select_columns) {
            params.append('select_columns', JSON.stringify(this.select_columns));
        }
        
        if (this.table_alias) {
            params.append('table_alias', this.table_alias);
        }
        
        // Create EventSource for SSE
        const url = `/api/query/stream?${params.toString()}`;
        this.eventSource = new EventSource(url);
        
        let totalRecords = 0;
        
        this.eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                
                if (data.type === 'start') {
                    this.onProgress({
                        type: 'start',
                        table: data.table,
                        limit: data.limit
                    });
                } else if (data.type === 'data') {
                    totalRecords += data.records.length;
                    onData(data.records, {
                        totalSoFar: totalRecords,
                        hasMore: data.has_more
                    });
                    
                    this.onProgress({
                        type: 'progress',
                        totalSoFar: totalRecords,
                        hasMore: data.has_more
                    });
                } else if (data.type === 'complete') {
                    this.onProgress({
                        type: 'complete',
                        totalRecords: data.total_records,
                        elapsedTime: data.elapsed_time,
                        recordsPerSecond: data.records_per_second
                    });
                    
                    if (onComplete) {
                        onComplete({
                            totalRecords: data.total_records,
                            elapsedTime: data.elapsed_time,
                            recordsPerSecond: data.records_per_second
                        });
                    }
                    
                    this.eventSource.close();
                    this.streaming = false;
                } else if (data.type === 'error') {
                    const error = new Error(data.error);
                    this.onError(error);
                    this.eventSource.close();
                    this.streaming = false;
                }
            } catch (error) {
                console.error('Error parsing SSE message:', error);
                this.onError(error);
            }
        };
        
        this.eventSource.onerror = (error) => {
            console.error('SSE connection error:', error);
            this.onError(new Error('SSE connection failed'));
            this.eventSource.close();
            this.streaming = false;
        };
    }
    
    /**
     * Stop streaming
     */
    stopStreaming() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
            this.streaming = false;
        }
    }
    
    /**
     * Check query integrity
     * 
     * @param {number} cursor - Cursor value to check
     * @param {number} expectedCount - Expected record count
     * @returns {Promise<Object>} Integrity check results
     */
    async checkIntegrity(cursor, expectedCount) {
        try {
            const params = new URLSearchParams({
                table: this.table,
                cursor: cursor.toString(),
                expected_count: expectedCount.toString()
            });
            
            if (Object.keys(this.filters).length > 0) {
                params.append('filters', JSON.stringify(this.filters));
            }
            
            const response = await fetch(`/api/query/integrity?${params.toString()}`);
            const result = await response.json();
            
            return result;
        } catch (error) {
            console.error('Integrity check error:', error);
            throw error;
        }
    }
    
    /**
     * Prefetch next page in background
     * @private
     */
    _prefetchNextPage() {
        if (!this.nextCursor || this.prefetchQueue.includes(this.nextCursor)) {
            return;
        }
        
        this.prefetchQueue.push(this.nextCursor);
        
        // Prefetch asynchronously (don't await)
        this.loadPage(this.nextCursor).then(() => {
            const index = this.prefetchQueue.indexOf(this.nextCursor);
            if (index > -1) {
                this.prefetchQueue.splice(index, 1);
            }
        }).catch(error => {
            console.warn('Prefetch failed:', error);
            const index = this.prefetchQueue.indexOf(this.nextCursor);
            if (index > -1) {
                this.prefetchQueue.splice(index, 1);
            }
        });
    }
    
    /**
     * Update filters and reload
     */
    async updateFilters(newFilters) {
        this.filters = { ...this.filters, ...newFilters };
        this.currentCursor = null;
        this.nextCursor = null;
        this.prevCursor = null;
        return await this.loadPage(null);
    }
    
    /**
     * Reset to first page
     */
    async reset() {
        this.currentCursor = null;
        this.nextCursor = null;
        this.prevCursor = null;
        this.currentData = [];
        return await this.loadPage(null);
    }
} // End of CursorPaginator class

// Make CursorPaginator globally available
if (typeof window !== 'undefined') {
    window.CursorPaginator = CursorPaginator;
}

// Export for CommonJS
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CursorPaginator;
}

// Export for ES modules
export { CursorPaginator };
export default CursorPaginator;

