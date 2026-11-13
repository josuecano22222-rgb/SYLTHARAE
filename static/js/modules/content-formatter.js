/**
 * Content Formatter Module
 * Comprehensive file content formatter with language detection and RTL/LTR support
 * Formats file content based on file type for accurate original-format display
 * 
 * Features:
 * - Language detection and automatic RTL/LTR direction
 * - Support for all file types (Word, Excel, PDF, PowerPoint, Email, Images, etc.)
 * - Organized storage format parsing
 * - Realistic table display with borders and frames
 * - Original format preservation
 * - Spatial order preservation: Content is displayed in the same order as it appears
 *   in the original document (top-to-bottom, left-to-right)
 * - Element ordering: Tables, paragraphs, images, and other elements maintain their
 *   original spatial relationships from the source document
 */

// --- Accessibility-label translations (set by the host page) ---------------
// English defaults keep the viewer self-contained; pages inject their
// catalog translations via setContentFormatterTranslations() so screen
// readers get localized navigation labels.
const formatterI18n = {
    slidesNavLabel: 'Slides',
    previousSlideLabel: 'Previous slide',
    nextSlideLabel: 'Next slide',
    sheetsNavLabel: 'Sheets'
};

/**
 * Localize viewer accessibility labels.
 * @param {Object} translations - map of i18n keys to translated strings;
 *        unknown/missing keys keep their English defaults.
 */
export function setContentFormatterTranslations(translations) {
    if (translations && typeof translations === 'object') {
        for (const key of Object.keys(formatterI18n)) {
            if (translations[key]) {
                formatterI18n[key] = String(translations[key]);
            }
        }
    }
}

/**
 * Detect text direction (RTL/LTR) based on language
 * @param {string} text - Text to analyze
 * @returns {string} - 'rtl' or 'ltr'
 */
function detectTextDirection(text) {
    if (!text || typeof text !== 'string') return 'ltr';
    
    // RTL language patterns (Arabic, Hebrew, Persian, Urdu, etc.)
    const rtlPatterns = [
        /[\u0590-\u05FF]/, // Hebrew
        /[\u0600-\u06FF]/, // Arabic
        /[\u06A0-\u06FF]/, // Arabic Supplement
        /[\u0700-\u074F]/, // Syriac
        /[\u0750-\u077F]/, // Arabic Supplement
        /[\u08A0-\u08FF]/, // Arabic Extended-A
        /[\uFB50-\uFDFF]/, // Arabic Presentation Forms-A
        /[\uFE70-\uFEFF]/  // Arabic Presentation Forms-B
    ];
    
    // Check for RTL characters
    for (const pattern of rtlPatterns) {
        if (pattern.test(text)) {
            return 'rtl';
        }
    }
    
    // Default to LTR
    return 'ltr';
}

/**
 * Detect language from text content
 * @param {string} text - Text to analyze
 * @returns {string} - Language code (e.g., 'ar', 'he', 'en')
 */
function detectLanguage(text) {
    if (!text || typeof text !== 'string') return 'en';
    
    // Simple language detection based on character ranges
    if (/[\u0590-\u05FF]/.test(text)) return 'he'; // Hebrew
    if (/[\u0600-\u06FF]/.test(text)) return 'ar'; // Arabic
    if (/[\u4E00-\u9FFF]/.test(text)) return 'zh'; // Chinese
    if (/[\u3040-\u309F\u30A0-\u30FF]/.test(text)) return 'ja'; // Japanese
    if (/[\uAC00-\uD7AF]/.test(text)) return 'ko'; // Korean
    
    return 'en'; // Default to English
}

/**
 * Detect if a line is a table header (from storage format)
 * @param {string} line - Line to check
 * @returns {Object|null} - Table info or null
 */
function detectTableHeader(line) {
    if (!line || typeof line !== 'string') return null;
    
    // Pattern: "Table N" or "Table N | Caption: ..." (case-insensitive)
    // Also match "table N" (lowercase)
    const tableMatch = line.match(/^[Tt]able\s+(\d+)(?:\s*\|\s*(.+))?$/i);
    if (tableMatch) {
        const result = {
            type: 'table',
            number: parseInt(tableMatch[1]),
            caption: tableMatch[2] ? tableMatch[2].replace(/^Caption:\s*/i, '').trim() : null
        };
        console.log('detectTableHeader: Found table header:', result);
        return result;
    }
    
    // Pattern: "Sheet: SheetName | Rows: X | Columns: Y"
    const sheetMatch = line.match(/^Sheet:\s*([^|]+)(?:\s*\|\s*(.+))?$/i);
    if (sheetMatch) {
        const metadata = sheetMatch[2] || '';
        const rowsMatch = metadata.match(/Rows:\s*(\d+)/i);
        const colsMatch = metadata.match(/Columns:\s*(\d+)/i);
        return {
            type: 'sheet',
            name: sheetMatch[1].trim(),
            rows: rowsMatch ? parseInt(rowsMatch[1]) : null,
            columns: colsMatch ? parseInt(colsMatch[1]) : null
        };
    }
    
    return null;
}

/**
 * Check if a line is explanatory/metadata text that should be hidden
 * @param {string} line - Line to check
 * @returns {boolean} - True if line is explanatory text
 */
function isExplanatoryText(line) {
    if (!line || typeof line !== 'string') return false;
    
    const trimmed = line.trim();
    
    // Page markers: "Page N", "Page N | Method: ...", etc.
    if (/^Page\s+\d+(\s*\|\s*.*)?$/i.test(trimmed)) {
        return true;
    }
    
    // Slide markers: "Slide N", "Slide N | ..."
    if (/^Slide\s+\d+(\s*\|\s*.*)?$/i.test(trimmed)) {
        return true;
    }
    
    // Table markers: "Table N", "Table N | Caption: ..."
    if (/^Table\s+\d+(\s*\|\s*(Caption|Title):\s*.*)?$/i.test(trimmed)) {
        return true;
    }
    
    // Sheet markers: "Sheet: ... | Rows: ... | Columns: ..."
    if (/^Sheet:\s*[^|]+(\s*\|\s*(Rows|Columns):\s*\d+.*)?$/i.test(trimmed)) {
        return true;
    }
    
    // Style markers: "[Style: ...]"
    if (/^\[Style:\s*[^\]]+\]\s*$/.test(trimmed)) {
        return true;
    }
    
    // Metadata lines: "Method: ...", "Length: ...", "Rows: ...", "Columns: ..."
    if (/^(Method|Length|Rows|Columns|Total\s+(Pages|Slides|Sheets)):\s*.*$/i.test(trimmed)) {
        return true;
    }
    
    // PDF metadata: "Total Pages: ...", "OCR Used: ...", etc.
    if (/^(Total\s+(Pages|Slides|Sheets)|OCR\s+Used|OCR\s+Languages|Encrypted|Title|Author|Subject|Creator|Producer):\s*.*$/i.test(trimmed)) {
        return true;
    }
    
    // Slides metadata: "Total Slides: ..."
    if (/^Total\s+Slides:\s*\d+$/i.test(trimmed)) {
        return true;
    }
    
    // Image metadata: "Width: ...", "Height: ...", "Format: ...", "GPS: ..."
    if (/^(Width|Height|Format|Mode|GPS|Maps):\s*.*$/i.test(trimmed)) {
        return true;
    }
    
    // Chapter markers: "Chapter ID: ...", "Chapter N"
    if (/^Chapter\s+(ID|Number)?:?\s*.*$/i.test(trimmed)) {
        return true;
    }
    
    // Database metadata: "SQLite Version: ...", "Tables: ..."
    if (/^(SQLite\s+Version|Tables|Table\s+Count):\s*.*$/i.test(trimmed)) {
        return true;
    }
    
    return false;
}

/**
 * Remove explanatory text from a line while preserving actual content
 * @param {string} line - Line to process
 * @returns {string} - Line with explanatory text removed
 */
function removeExplanatoryText(line) {
    if (!line || typeof line !== 'string') return line;
    
    // If entire line is explanatory, return empty
    if (isExplanatoryText(line.trim())) {
        return '';
    }
    
    // Remove style markers from beginning: "[Style: ...] actual text"
    const styleMatch = line.match(/^\[Style:\s*[^\]]+\]\s*(.+)$/);
    if (styleMatch) {
        return styleMatch[1];
    }
    
    // Remove metadata from pipe-separated format: "Page N | Method: ... | actual content"
    // Keep only the actual content part
    if (line.includes('|')) {
        const parts = line.split('|').map(p => p.trim());
        const contentParts = parts.filter(part => {
            // Keep parts that don't match explanatory patterns
            return !isExplanatoryText(part);
        });
        
        if (contentParts.length > 0) {
            return contentParts.join(' | ');
        }
    }
    
    return line;
}

/**
 * Detect if content contains table-like structures
 * @param {string} content - Content to analyze
 * @returns {boolean} - True if content appears to contain tables
 */
function detectTableStructure(content) {
    if (!content || typeof content !== 'string') return false;
    
    const lines = content.split('\n').filter(line => line.trim());
    if (lines.length < 2) return false;
    
    // Check for storage format table headers
    for (const line of lines) {
        const header = detectTableHeader(line);
        if (header) return true;
    }
    
    // Check for tab-separated values (common in Word/Excel exports)
    const tabSeparatedLines = lines.filter(line => line.includes('\t')).length;
    if (tabSeparatedLines >= lines.length * 0.3) return true;
    
    // Check for consistent column counts (pipe, comma, or multiple spaces)
    const columnCounts = lines.map(line => {
        if (line.includes('|')) {
            return line.split('|').filter(c => c.trim()).length;
        } else if (line.includes(',')) {
            return line.split(',').filter(c => c.trim()).length;
        } else {
            // Count multiple spaces as column separators
            return line.split(/\s{2,}/).filter(c => c.trim()).length;
        }
    }).filter(count => count > 1);
    
    if (columnCounts.length < 2) return false;
    
    // Check if most lines have similar column counts
    const avgColumns = columnCounts.reduce((a, b) => a + b, 0) / columnCounts.length;
    const consistentLines = columnCounts.filter(count => 
        Math.abs(count - avgColumns) <= 1
    ).length;
    
    return consistentLines >= columnCounts.length * 0.7;
}

/**
 * Parse content into table structure (handles storage format)
 * @param {string} content - Content to parse
 * @param {Object} headerInfo - Optional header info from detectTableHeader
 * @returns {Array<Array<string>>} - Array of rows, each row is an array of cells
 */
function parseTableContent(content, headerInfo = null) {
    if (!content || typeof content !== 'string') return [];
    
    const lines = content.split('\n').filter(line => line.trim());
    if (lines.length === 0) return [];
    
    const rows = [];
    let skipHeader = false;
    let expectedColumns = null;
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        
        // Skip table/sheet header lines
        if (detectTableHeader(line)) {
            skipHeader = true;
            continue;
        }
        
        // Skip metadata lines (Rows:, Columns:, etc.)
        if (line.match(/^(Rows|Columns|Sheet):/i)) {
            // Extract column count if available
            const colsMatch = line.match(/Columns:\s*(\d+)/i);
            if (colsMatch) {
                expectedColumns = parseInt(colsMatch[1]);
            }
            continue;
        }
        
        // Skip empty lines after headers
        if (skipHeader && !line.trim()) {
            continue;
        }
        skipHeader = false;
        
        let cells = [];
        
        // Try tab-separated first (most common in Word/Excel storage format)
        if (line.includes('\t')) {
            cells = line.split('\t').map(cell => cell.trim()).filter(cell => cell !== '');
        }
        // Try pipe-separated (but not if it's metadata)
        else if (line.includes('|') && !line.match(/^(Table|Sheet|Rows|Columns):/i)) {
            cells = line.split('|').map(cell => cell.trim()).filter(cell => cell);
        }
        // Try comma-separated (CSV-like)
        else if (line.includes(',') && line.split(',').length > 2) {
            cells = line.split(',').map(cell => cell.trim());
        }
        // Try multiple spaces (common in storage format where cells are space-separated)
        else {
            // For space-separated, we need to be smarter
            // Look for patterns of 2+ spaces that likely separate columns
            // But preserve single spaces within cell content
            
            // First, try splitting on 3+ spaces (more reliable for column separation)
            const tripleSpaceSplit = line.split(/\s{3,}/).map(cell => cell.trim()).filter(cell => cell);
            
            if (tripleSpaceSplit.length > 1) {
                cells = tripleSpaceSplit;
            } else {
                // Try 2+ spaces
                const doubleSpaceSplit = line.split(/\s{2,}/).map(cell => cell.trim()).filter(cell => cell);
                
                if (doubleSpaceSplit.length > 1) {
                    // Check if this looks like a table row vs. regular text
                    // Table rows typically have consistent column counts
                    if (expectedColumns && doubleSpaceSplit.length === expectedColumns) {
                        cells = doubleSpaceSplit;
                    } else if (rows.length > 0) {
                        // Compare with previous row - if similar column count, likely a table row
                        const prevCols = rows[rows.length - 1].length;
                        if (Math.abs(doubleSpaceSplit.length - prevCols) <= 1 && doubleSpaceSplit.length >= 2) {
                            cells = doubleSpaceSplit;
                        }
                    } else if (doubleSpaceSplit.length >= 2) {
                        // First row with 2+ columns - likely a table header
                        cells = doubleSpaceSplit;
                        expectedColumns = doubleSpaceSplit.length;
                    }
                }
            }
        }
        
        // Add row if we have cells
        if (cells.length > 1) {
            // Normalize column count - pad or trim to match expected
            if (expectedColumns && cells.length !== expectedColumns) {
                if (cells.length < expectedColumns) {
                    // Pad with empty cells
                    while (cells.length < expectedColumns) {
                        cells.push('');
                    }
                } else if (cells.length > expectedColumns) {
                    // Merge excess cells into last column
                    const excess = cells.slice(expectedColumns).join(' ');
                    cells = cells.slice(0, expectedColumns);
                    cells[expectedColumns - 1] = (cells[expectedColumns - 1] || '') + ' ' + excess;
                }
            }
            rows.push(cells);
            // Update expected columns from first row
            if (!expectedColumns && rows.length === 1) {
                expectedColumns = cells.length;
            }
        } else if (cells.length === 1 && cells[0] && headerInfo) {
            // Single cell row in a table context
            rows.push(cells);
        }
    }
    
    return rows;
}

/**
 * Parse a table row intelligently, handling space-separated cells
 * Enhanced version that better handles storage format where cells are space-separated
 * @param {string} line - Line to parse
 * @param {number} expectedColumns - Expected number of columns (if known)
 * @param {Array<Array<string>>} previousRows - Previous rows for pattern analysis
 * @returns {Array<string>} - Array of cell values
 */
function parseTableRow(line, expectedColumns = null, previousRows = []) {
    if (!line || !line.trim()) return [];
    
    // If we have tab-separated values, use that (most reliable)
    if (line.includes('\t')) {
        const cells = line.split('\t').map(c => c.trim());
        // Normalize to expected columns if provided
        if (expectedColumns && cells.length !== expectedColumns) {
            if (cells.length < expectedColumns) {
                while (cells.length < expectedColumns) {
                    cells.push('');
                }
            } else {
                const excess = cells.slice(expectedColumns).join(' ');
                cells.splice(expectedColumns);
                cells[expectedColumns - 1] = (cells[expectedColumns - 1] || '') + ' ' + excess;
            }
        }
        return cells;
    }
    
    // If we have pipe-separated values (but not metadata), use that
    if (line.includes('|') && !line.match(/^(Table|Sheet|Rows|Columns|Page|Slide|Chapter):/i)) {
        const cells = line.split('|').map(c => c.trim()).filter(c => c);
        if (cells.length > 1) {
            // Normalize to expected columns if provided
            if (expectedColumns && cells.length !== expectedColumns) {
                if (cells.length < expectedColumns) {
                    while (cells.length < expectedColumns) {
                        cells.push('');
                    }
                } else {
                    const excess = cells.slice(expectedColumns).join(' ');
                    cells.splice(expectedColumns);
                    cells[expectedColumns - 1] = (cells[expectedColumns - 1] || '') + ' ' + excess;
                }
            }
            return cells;
        }
    }
    
    // For space-separated values, use intelligent parsing
    // Strategy 1: Look for multiple consecutive spaces (3+ spaces = very likely column separator)
    const tripleSpaceSplit = line.split(/\s{3,}/).map(c => c.trim()).filter(c => c);
    if (tripleSpaceSplit.length > 1) {
        // Normalize to expected columns
        if (expectedColumns && tripleSpaceSplit.length !== expectedColumns) {
            if (tripleSpaceSplit.length < expectedColumns) {
                while (tripleSpaceSplit.length < expectedColumns) {
                    tripleSpaceSplit.push('');
                }
            } else {
                const excess = tripleSpaceSplit.slice(expectedColumns).join(' ');
                tripleSpaceSplit.splice(expectedColumns);
                tripleSpaceSplit[expectedColumns - 1] = (tripleSpaceSplit[expectedColumns - 1] || '') + ' ' + excess;
            }
        }
        return tripleSpaceSplit;
    }
    
    // Strategy 2: Look for 2+ spaces (moderate confidence)
    const doubleSpaceSplit = line.split(/\s{2,}/).map(c => c.trim()).filter(c => c);
    if (doubleSpaceSplit.length > 1) {
        // Use previous rows to validate if this looks like a table row
        if (previousRows.length > 0) {
            const prevCols = previousRows[previousRows.length - 1].length;
            // If column count matches previous rows, likely a table row
            if (Math.abs(doubleSpaceSplit.length - prevCols) <= 1) {
                expectedColumns = prevCols;
            }
        }
        
        // Normalize to expected columns
        if (expectedColumns && doubleSpaceSplit.length !== expectedColumns) {
            if (doubleSpaceSplit.length < expectedColumns) {
                while (doubleSpaceSplit.length < expectedColumns) {
                    doubleSpaceSplit.push('');
                }
            } else {
                const excess = doubleSpaceSplit.slice(expectedColumns).join(' ');
                doubleSpaceSplit.splice(expectedColumns);
                doubleSpaceSplit[expectedColumns - 1] = (doubleSpaceSplit[expectedColumns - 1] || '') + ' ' + excess;
            }
        }
        return doubleSpaceSplit;
    }
    
    // Strategy 3: If we know expected columns, try intelligent token distribution
    if (expectedColumns && expectedColumns > 1) {
        const tokens = line.split(/\s+/);
        
        if (tokens.length >= expectedColumns) {
            // Analyze previous rows to find column boundaries
            if (previousRows.length > 0) {
                // Calculate average token distribution from previous rows
                const avgTokensPerCol = previousRows.map(row => {
                    const rowText = row.join(' ');
                    return rowText.split(/\s+/).length / row.length;
                }).reduce((a, b) => a + b, 0) / previousRows.length;
                
                // Distribute tokens based on average
                const cells = [];
                let tokenIndex = 0;
                for (let col = 0; col < expectedColumns; col++) {
                    const tokensForThisCol = Math.round(avgTokensPerCol);
                    const endIndex = Math.min(tokenIndex + tokensForThisCol, tokens.length);
                    cells.push(tokens.slice(tokenIndex, endIndex).join(' '));
                    tokenIndex = endIndex;
                }
                
                // Add any remaining tokens to last column
                if (tokenIndex < tokens.length) {
                    cells[expectedColumns - 1] = (cells[expectedColumns - 1] || '') + ' ' + tokens.slice(tokenIndex).join(' ');
                }
                
                return cells;
            } else {
                // First row - distribute evenly
                const tokensPerColumn = Math.ceil(tokens.length / expectedColumns);
                const cells = [];
                for (let i = 0; i < expectedColumns; i++) {
                    const start = i * tokensPerColumn;
                    const end = Math.min(start + tokensPerColumn, tokens.length);
                    cells.push(tokens.slice(start, end).join(' '));
                }
                return cells;
            }
        }
    }
    
    // Strategy 4: Single cell (not a table row)
    return [line.trim()];
}

/**
 * Format content as HTML table with proper borders and frames
 * Enhanced version with realistic table appearance
 * @param {Array<Array<string>>} rows - Table rows (data rows, excluding header)
 * @param {Object} headerInfo - Optional header info with number, caption, name, etc.
 * @param {Array<string>} headerRow - Optional explicit header row
 * @param {boolean} isNested - Whether this is a nested table (for Word documents)
 * @returns {string} - HTML table string
 */
function formatAsTable(rows, headerInfo = null, headerRow = null, isNested = false) {
    // Determine if we have headers
    const hasExplicitHeader = headerRow && headerRow.length > 0;
    const hasDataRows = rows && rows.length > 0;
    
    if (!hasExplicitHeader && !hasDataRows) return '';
    
    // Determine column count
    let columnCount = 0;
    if (hasExplicitHeader) {
        columnCount = headerRow.length;
    } else if (hasDataRows && rows[0]) {
        columnCount = rows[0].length;
    } else {
        return '';
    }
    
    // Normalize all rows to have the same column count
    const normalizedRows = [];
    if (hasDataRows) {
        rows.forEach(row => {
            const normalizedRow = [];
            for (let i = 0; i < columnCount; i++) {
                normalizedRow.push(row && row[i] !== undefined ? String(row[i]).trim() : '');
            }
            normalizedRows.push(normalizedRow);
        });
    }
    
    // Normalize header row
    const normalizedHeader = [];
    if (hasExplicitHeader) {
        for (let i = 0; i < columnCount; i++) {
            normalizedHeader.push(headerRow[i] !== undefined ? String(headerRow[i]).trim() : '');
        }
    }
    
    // Detect language and direction for table
    const tableText = rows.flat().join(' ') + (headerRow ? headerRow.join(' ') : '');
    const direction = detectTextDirection(tableText);
    const lang = detectLanguage(tableText);
    
    const containerClass = isNested ? 'formatted-table-container nested-table' : 'formatted-table-container';
    let html = `<div class="${containerClass}" dir="${direction}" lang="${lang}">`;
    
    // Don't display table/sheet header - it's just explanatory metadata
    // Table numbers and sheet names are used internally for ordering only
    // The headerInfo is still used to track table structure but not displayed
    
    html += '<table class="formatted-content-table">';
    
    // Add header row if available
    if (hasExplicitHeader && normalizedHeader.length > 0) {
        html += '<thead><tr>';
        normalizedHeader.forEach((cell, cellIndex) => {
            html += `<th>${escapeHtml(cell || '')}</th>`;
        });
        html += '</tr></thead>';
    }
    
    // Add data rows
    if (hasDataRows && normalizedRows.length > 0) {
        html += '<tbody>';
        normalizedRows.forEach((row, rowIndex) => {
            html += '<tr>';
            row.forEach((cell, cellIndex) => {
                // Check if cell contains nested table markers
                const cellContent = escapeHtml(cell || '');
                html += `<td>${cellContent}</td>`;
            });
            html += '</tr>';
        });
        html += '</tbody>';
    }
    
    html += '</table></div>';
    return html;
}

/**
 * Format Word document content (handles storage format with tables and styles)
 * Enhanced version with better table detection, nested table support, and language detection
 * @param {string} content - Content text
 * @returns {string} - Formatted HTML
 */
function formatWordContent(content) {
    if (!content || typeof content !== 'string') {
        console.log('formatWordContent: No content provided');
        return '';
    }
    
    // Detect language and direction
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    console.log('formatWordContent: Processing content, length:', content.length);
    const lines = content.split('\n');
    console.log('formatWordContent: Total lines:', lines.length);
    let html = `<div class="formatted-word-content" dir="${direction}" lang="${lang}">`;
    let currentTable = [];
    let currentTableHeader = null;
    let inTable = false;
    let currentParagraph = [];
    let expectedColumns = null;
    let tableRows = []; // Track previous rows for pattern analysis
    let lastElementType = null; // Track last element type to preserve order
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmedLine = line.trim();
        
        // Table marker FIRST - before the explanatory-text skip below.
        // "Table N" is structural metadata (never displayed) AND the signal
        // that starts a table; swallowing it as explanatory text is what made
        // every Word table render as tab-separated prose.
        if (isTableStartLine(trimmedLine)) {
            // End the paragraph in progress so document order is preserved.
            if (currentParagraph.length > 0) {
                html += formatParagraph(currentParagraph.join(' '));
                currentParagraph = [];
                lastElementType = 'paragraph';
            }
            
            // End the previous table, if any.
            if (currentTable.length > 0) {
                html += formatAsTable(currentTable, currentTableHeader);
                currentTable = [];
                tableRows = [];
            }
            
            currentTableHeader = tableHeaderInfo(trimmedLine);
            inTable = true;
            expectedColumns = null; // Column count comes from the first row
            lastElementType = 'table';
            continue;
        }
        
        if (!trimmedLine) {
            // Inside a table a line of nothing but tabs is an explicit empty
            // row, not a blank line: a single-column table's empty row cannot
            // be written any other way (it would be read as the end of the
            // table). Cell boundaries are still visible in ``line``.
            if (inTable && line.includes('\t')) {
                const emptyRow = normalizeRow([], expectedColumns);
                currentTable.push(emptyRow);
                tableRows.push(emptyRow);
                continue;
            }
            // Empty line - end current section if we have enough data
            if (inTable && currentTable.length > 0) {
                const nextNonEmpty = nextNonEmptyLine(lines, i);
                
                // A blank line between rows keeps its row position (an
                // all-empty row is still a row); otherwise it is the block
                // separator the extractor writes between document elements
                // and the table ends here.
                if (nextNonEmpty && continuesTable(nextNonEmpty, expectedColumns)) {
                    const emptyRow = normalizeRow([], expectedColumns);
                    currentTable.push(emptyRow);
                    tableRows.push(emptyRow);
                    continue;
                }
                
                html += formatAsTable(currentTable, currentTableHeader);
                currentTable = [];
                currentTableHeader = null;
                tableRows = [];
                inTable = false;
                expectedColumns = null;
            } else if (currentParagraph.length > 0) {
                const paraText = currentParagraph.join(' ');
                if (paraText.trim()) {
                    html += formatParagraph(paraText);
                }
                currentParagraph = [];
            }
            continue;
        }
        
        // Inside a table every non-blank line IS a row: a one-column table has
        // rows of a single cell with no separator at all, and requiring a
        // separator dropped them (and ended the table early).
        if (inTable) {
            const cells = splitTableRow(line, expectedColumns, currentTable);
            if (expectedColumns === null) {
                expectedColumns = cells.length;
            }
            const row = normalizeRow(cells, expectedColumns);
            currentTable.push(row);
            tableRows.push(row);
            continue;
        }
        
        // Skip explanatory text lines (metadata markers)
        if (isExplanatoryText(trimmedLine)) {
            // These are organizational markers - skip them but preserve structure
            continue;
        }
        
        // Check for word_paragraph marker (new ordered format)
        if (trimmedLine.match(/^word_paragraph/i)) {
            // Marker only: the paragraph text follows on the next line.
            lastElementType = 'paragraph';
            continue;
        }
        
        // Handle paragraph text (may include style info)
        // Remove style markers and explanatory text
        const cleanedLine = removeExplanatoryText(trimmedLine);
        
        if (cleanedLine && cleanedLine !== trimmedLine) {
            // Line had style marker or other metadata - use cleaned version
            if (trimmedLine.match(/^\[Style:\s*([^\]]+)\]/)) {
                const styleMatch = trimmedLine.match(/^\[Style:\s*([^\]]+)\]\s*(.+)$/);
                if (styleMatch && styleMatch[2].trim()) {
                    const style = styleMatch[1];
                    const text = styleMatch[2].trim();
                    // Styled paragraphs render immediately, so flush the
                    // buffered paragraph first: otherwise it would be emitted
                    // after this line and the document order would flip.
                    if (currentParagraph.length > 0) {
                        html += formatParagraph(currentParagraph.join(' '));
                        currentParagraph = [];
                    }
                    html += formatParagraph(text, style);
                }
            } else if (cleanedLine.trim()) {
                currentParagraph.push(cleanedLine.trim());
            }
        } else if (cleanedLine && cleanedLine.trim()) {
            // Regular paragraph text
            currentParagraph.push(cleanedLine.trim());
        }
    }
    
    // Close any remaining sections
    if (inTable && currentTable.length > 0) {
        console.log('Closing remaining table with', currentTable.length, 'rows');
        html += formatAsTable(currentTable, currentTableHeader);
    }
    if (currentParagraph.length > 0) {
        html += formatParagraph(currentParagraph.join(' '));
    }
    
    html += '</div>';
    console.log('formatWordContent: Final HTML length:', html.length);
    return html;
}

/**
 * Does this line open a table in the stored format?
 *
 * The extractor writes a table as a marker line - "Table 3", optionally
 * "Table 3 | Caption: ..." - followed by one line per row with cells
 * separated by tabs, then a blank line before the next document element.
 *
 * The marker is BOTH structural metadata (never displayed) and the signal
 * that switches the reader into table mode. It has to be recognised before
 * the explanatory-text skip, otherwise the marker is swallowed as metadata,
 * the reader never enters table mode, and the tab-delimited rows fall through
 * to the paragraph path - which is how every Word table came out as
 * tab-separated prose instead of a table.
 *
 * @param {string} line - Line to check
 * @returns {boolean} - True if the line starts a table
 */
function isTableStartLine(line) {
    if (/^(word|slide)_table/i.test(line)) return true;
    const header = detectTableHeader(line);
    return Boolean(header && header.type === 'table');
}

/**
 * Header info (table number / caption) of a table marker line.
 * @param {string} line - Table marker line
 * @returns {Object} - {number, caption}
 */
function tableHeaderInfo(line) {
    const header = detectTableHeader(line);
    if (header && header.type === 'table') {
        return { number: header.number, caption: header.caption };
    }
    const match = line.match(/table\s+(\d+)/i);
    return { number: match ? parseInt(match[1], 10) : null, caption: null };
}

/**
 * Cells of one stored row.
 *
 * Tabs are the extractor's cell separator, so a tab-delimited row is split
 * exactly - empty leading, middle and trailing cells included (they are
 * columns, not padding). A row with no tab is a single-cell row: it belongs to
 * a one-column table and must NOT be split on its spaces.
 *
 * @param {string} line - Raw row line (not trimmed)
 * @param {number|null} expectedColumns - Columns established by earlier rows
 * @param {Array<Array<string>>} previousRows - Rows already collected
 * @returns {Array<string>} - Cell values
 */
function splitTableRow(line, expectedColumns, previousRows) {
    if (line.includes('\t')) {
        return line.split('\t').map(cell => cell.trim());
    }
    const cells = parseTableRow(line, expectedColumns, previousRows);
    return cells.length ? cells : [line.trim()];
}

/**
 * Pad/shrink a row to the table's column count, keeping empty cells.
 * @param {Array<string>} cells - Row cells
 * @param {number} columnCount - Column count of the table
 * @returns {Array<string>} - Row with exactly ``columnCount`` cells
 */
function normalizeRow(cells, columnCount) {
    const width = Math.max(1, columnCount || 0);
    const row = cells.slice(0, width);
    while (row.length < width) row.push('');
    return row;
}

/**
 * Does the line after a blank line continue the table in progress?
 *
 * A blank line normally separates two document elements (the extractor joins
 * blocks with a blank line), so it ends the table. It only does NOT when the
 * next non-empty line is unambiguously another row of the same table: a
 * tab-delimited row, or a space-delimited row with the table's column count.
 * That keeps an all-empty row inside a table from being read as the end of it.
 *
 * @param {string} line - Next non-empty line (trimmed)
 * @param {number|null} expectedColumns - Columns of the table in progress
 * @returns {boolean} - True when the line continues the table
 */
function continuesTable(line, expectedColumns) {
    if (!line) return false;
    if (line.includes('\t')) {
        return line.split('\t').filter(c => c.trim()).length > 1;
    }
    if (!expectedColumns || expectedColumns < 2) return false;
    const bySpace = line.split(/\s{2,}/).map(c => c.trim()).filter(c => c);
    return bySpace.length === expectedColumns;
}

/**
 * The next non-empty line after ``startIndex`` (trimmed), or null.
 * @param {Array<string>} lines - Content lines
 * @param {number} startIndex - Index to look after
 * @returns {string|null} - Next non-empty trimmed line
 */
function nextNonEmptyLine(lines, startIndex) {
    for (let j = startIndex + 1; j < lines.length; j++) {
        if (lines[j].trim()) return lines[j].trim();
    }
    return null;
}

/**
 * Check if a line is likely a table row
 * @param {string} line - Line to check
 * @param {number} expectedColumns - Expected number of columns
 * @param {Array<Array<string>>} previousRows - Previous rows for comparison
 * @returns {boolean} - True if line looks like a table row
 */
function isLikelyTableRow(line, expectedColumns, previousRows) {
    if (!line || !line.trim()) return false;
    
    // Check for tab-separated values
    if (line.includes('\t')) {
        const cells = line.split('\t').filter(c => c.trim());
        return cells.length > 1;
    }
    
    // Check for multiple spaces (column separators)
    const tripleSpace = line.split(/\s{3,}/).filter(c => c.trim());
    if (tripleSpace.length > 1) return true;
    
    const doubleSpace = line.split(/\s{2,}/).filter(c => c.trim());
    if (doubleSpace.length > 1) {
        // If we have expected columns, check if it matches
        if (expectedColumns) {
            return Math.abs(doubleSpace.length - expectedColumns) <= 1;
        }
        // If we have previous rows, check if column count matches
        if (previousRows.length > 0) {
            const prevCols = previousRows[previousRows.length - 1].length;
            return Math.abs(doubleSpace.length - prevCols) <= 1;
        }
        return true;
    }
    
    return false;
}

/**
 * Format a paragraph with optional style and language detection
 * @param {string} text - Paragraph text
 * @param {string} style - Optional style name
 * @returns {string} - Formatted HTML
 */
function formatParagraph(text, style = null) {
    if (!text || !text.trim()) return '';
    
    // Detect language and direction
    const direction = detectTextDirection(text);
    const lang = detectLanguage(text);
    
    let className = 'formatted-paragraph';
    let styleAttr = '';
    
    // Apply style-based formatting
    if (style) {
        const styleLower = style.toLowerCase();
        if (styleLower.includes('heading') || styleLower.includes('title')) {
            className += ' formatted-heading';
            if (styleLower.includes('heading 1') || styleLower.includes('title')) {
                className += ' formatted-h1';
            } else if (styleLower.includes('heading 2')) {
                className += ' formatted-h2';
            } else if (styleLower.includes('heading 3')) {
                className += ' formatted-h3';
            }
        }
    }
    
    return `<p class="${className}" dir="${direction}" lang="${lang}"${styleAttr}>${escapeHtml(text.trim())}</p>`;
}

/**
 * Format Excel content as table (handles storage format with sheets)
 * Enhanced version with better parsing, display, and language detection
 * @param {string} content - Content text
 * @returns {string} - Formatted HTML table
 */
/**
 * Format Excel/Spreadsheet content
 * Preserves original sheet order and row/column spatial relationships
 * Content is already in spatial order from database storage
 * @param {string} content - Content text (already in spatial order)
 * @returns {string} - Formatted HTML
 */
function formatExcelContent(content) {
    if (!content || typeof content !== 'string') {
        console.log('formatExcelContent: No content provided');
        return '';
    }

    // Detect language and direction
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);

    // Phase 1: parse the stored structure into worksheets. The storage
    // layer marks every worksheet with a "Sheet: <name>" header followed by
    // "Rows:/Columns:" metadata and tab-delimited rows. Bare tab-delimited
    // tables (CSV exports) parse as a single implicit sheet.
    const sheets = parseExcelSheets(content);

    // Phase 2: render. Multiple worksheets get a tab bar for navigation;
    // every sheet stays rendered (stacked) so the whole-document search and
    // the DOM-safe highlighter cover every cell.
    return renderExcelSheets(sheets, direction, lang);
}

/**
 * Parse stored spreadsheet text into worksheets while preserving the EXACT
 * location of every cell: row numbers count blank rows, and empty leading/
 * trailing cells keep their column position (tab structure is never
 * trimmed). Rows are 1-based (first stored row = spreadsheet row 1);
 * columns come from the tab index.
 * @param {string} content - Content with "Sheet:" markers and tab rows
 * @returns {Array<{name: ?string, rowCount: ?number, columnCount: ?number, rows: Array<{num: number, cells: string[]}>}>}
 */
function parseExcelSheets(content) {
    const sheets = [];
    let current = null;
    let pendingBlanks = 0; // blank lines that are interior blank rows

    const lines = content.split('\n').map(l => l.replace(/\r$/, ''));

    const pushBlankRows = (n) => {
        for (let i = 0; i < n; i++) {
            current.rows.push({ num: current.rows.length + 1, cells: [] });
        }
    };

    for (const line of lines) {
        const trimmed = line.trim();

        if (!trimmed) {
            if (current) pendingBlanks++;
            continue;
        }

        // Worksheet boundary: "Sheet: <name>" (optionally pipe-separated)
        if (/^Sheet:\s*[^|\n]+/i.test(trimmed)) {
            if (current) {
                // Blocks are joined with a blank separator line: one blank
                // before the next sheet header is the separator, more than
                // one means the sheet ended with real (blank) rows.
                if (pendingBlanks >= 2) pushBlankRows(pendingBlanks - 1);
                sheets.push(current);
            }
            current = {
                name: trimmed.match(/^Sheet:\s*([^|\n]+)/i)[1].trim(),
                rowCount: null, columnCount: null, rows: [],
            };
            pendingBlanks = 0;
            continue;
        }

        if (!current) {
            current = { name: null, rowCount: null, columnCount: null, rows: [] };
            pendingBlanks = 0;
        }

        // Sheet metadata (storage format: "Rows: N" / "Columns: N")
        if (!line.includes('\t')) {
            const colsMatch = trimmed.match(/^Columns:\s*:?(\d+)/i);
            if (colsMatch) { current.columnCount = parseInt(colsMatch[1], 10); continue; }
            const rowsMatch = trimmed.match(/^Rows:\s*:?(\d+)/i);
            if (rowsMatch) { current.rowCount = parseInt(rowsMatch[1], 10); continue; }
            if (isExplanatoryText(trimmed)) continue;
        }

        // Blank lines BEFORE this data row are interior blank rows of the
        // sheet - they keep their row numbers (cell B5 stays B5).
        if (pendingBlanks) { pushBlankRows(pendingBlanks); pendingBlanks = 0; }

        // Split on tabs WITHOUT trimming the line: empty leading/trailing
        // cells keep their column positions. Only the cell text is trimmed.
        const cells = line.split('\t').map(c => c.trim());
        current.rows.push({ num: current.rows.length + 1, cells });
    }

    if (current) {
        // Trailing blank rows at end of content belong to the last sheet,
        // bounded by the sheet's known row count when available.
        const blanks = current.rowCount
            ? Math.min(pendingBlanks, Math.max(0, current.rowCount - current.rows.length))
            : 0;
        pushBlankRows(blanks);
        sheets.push(current);
    }
    return sheets.filter(sh => sh.rows.length > 0 || sh.name);
}

/** Column index (1-based) -> spreadsheet column letter (A..Z, AA..). */
function columnLetter(index) {
    let s = '';
    let n = index;
    while (n > 0) {
        const m = (n - 1) % 26;
        s = String.fromCharCode(65 + m) + s;
        n = Math.floor((n - 1) / 26);
    }
    return s;
}

/** Cell address, e.g. column 2 row 3 -> "B3". */
function cellAddress(colIndex, rowNum) {
    return `${columnLetter(colIndex)}${rowNum}`;
}

/**
 * Render parsed worksheets. A single sheet renders as one grid; multiple
 * sheets get a tab bar (worksheet navigation) with stacked, anchorable
 * sections so search stays whole-document.
 */
function renderExcelSheets(sheets, direction, lang) {
    if (!sheets.length) return '';
    const wrapperOpen = `<div class="formatted-excel-content" dir="${direction}" lang="${lang}">`;

    if (sheets.length === 1) {
        return wrapperOpen + renderSheetSection(sheets[0], 0, false) + '</div>';
    }

    let tabs = `<div class="sheet-tabs" role="tablist" aria-label="${escapeAttrText(formatterI18n.sheetsNavLabel)}">`;
    let sections = '';
    sheets.forEach((sheet, i) => {
        const label = sheet.name || ('#' + (i + 1));
        tabs += `<button type="button" class="sheet-tab${i === 0 ? ' active' : ''}" role="tab"`
            + ` aria-selected="${i === 0}" data-sheet-index="${i}"`
            + ` data-on-click="contentViewerNav.showSheet(this)">${escapeHtml(label)}</button>`;
        sections += renderSheetSection(sheet, i, true);
    });
    tabs += '</div>';
    return wrapperOpen + tabs + sections + '</div>';
}

/** One worksheet as a titled, anchorable section + address grid. */
function renderSheetSection(sheet, index, withAnchor) {
    const label = sheet.name || ('#' + (index + 1));
    let html = `<section class="sheet-section${withAnchor ? ' sheet-section-anchored' : ''}" data-sheet-index="${index}">`;
    if (withAnchor) {
        html += `<h4 class="sheet-section-title"><i class="bi bi-table" aria-hidden="true"></i>${escapeHtml(label)}</h4>`;
    }
    html += renderSheetGrid(sheet);
    html += '</section>';
    return html;
}

/**
 * Spreadsheet address grid: every cell carries its EXACT location -
 * a column-letter header row (A, B, C ...), a row-number gutter (1, 2, 3 ...)
 * and a data-cell-addr/title with the cell address (e.g. "B3") on each cell.
 * The grid is generated at render time (like the log viewer's gutter), so
 * the searchable text contains only real content.
 */
function renderSheetGrid(sheet) {
    const width = Math.max(
        sheet.columnCount || 0,
        ...sheet.rows.map(r => r.cells.length),
        1
    );

    let html = '<div class="formatted-table-container sheet-grid-container">';
    html += '<table class="formatted-content-table sheet-grid-table"><thead><tr>';
    html += '<th class="sheet-corner" aria-hidden="true"></th>';
    for (let c = 1; c <= width; c++) {
        html += `<th class="sheet-col-letter" scope="col">${columnLetter(c)}</th>`;
    }
    html += '</tr></thead><tbody>';

    sheet.rows.forEach((row, rIdx) => {
        const rowClass = rIdx === 0 ? ' class="sheet-first-row"' : '';
        html += `<tr${rowClass}><td class="sheet-row-num">${row.num}</td>`;
        for (let c = 1; c <= width; c++) {
            const value = row.cells[c - 1] !== undefined ? row.cells[c - 1] : '';
            const addr = cellAddress(c, row.num);
            html += `<td class="sheet-cell" data-cell-addr="${escapeAttrText(addr)}" title="${escapeAttrText(addr)}">${escapeHtml(value)}</td>`;
        }
        html += '</tr>';
    });

    html += '</tbody></table></div>';
    return html;
}

/**
 * Format image content - display image and text
 * Enhanced version that uses file path from database with language detection
 * @param {string} filePath - Path to the image file (from database)
 * @param {string} content - Extracted text content
 * @param {number} fileId - Optional file ID for preview endpoint
 * @returns {string} - Formatted HTML
 */
function formatImageContent(filePath, content, fileId = null) {
    const safeFilePath = typeof filePath === 'string' ? filePath : '';
    const numericFileId = Number(fileId);
    const safeFileId = Number.isSafeInteger(numericFileId) && numericFileId > 0
        ? numericFileId
        : null;
    if (!safeFilePath && !safeFileId) return '';

    // Detect language and direction from OCR text.
    const direction = content ? detectTextDirection(content) : 'ltr';
    const lang = content ? detectLanguage(content) : 'en';
    let html = `<div class="formatted-image-content" dir="${escapeAttrText(direction)}" lang="${escapeAttrText(lang)}">`;
    html += '<div class="formatted-image-container">';

    // Only use same-origin application endpoints. Direct file:// fallbacks do
    // not work for most readers and expose local paths to the browser.
    const imageSources = [];
    if (safeFilePath) {
        imageSources.push(`/api/file/serve?path=${encodeURIComponent(safeFilePath)}`);
    }
    if (safeFileId) {
        const idUrl = `/api/file/${safeFileId}/serve`;
        if (!imageSources.includes(idUrl)) imageSources.push(idUrl);
    }
    const imageSrc = imageSources[0] || '';
    const imageId = `img-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
    html += `<img id="${escapeAttrText(imageId)}" src="${escapeAttrText(imageSrc)}" alt="Image" class="formatted-image" `;
    if (imageSources.length > 1) {
        // JSON-encode first, then quote-escape the complete attribute value.
        // The load handler parses this with JSON.parse; no raw paths or quotes
        // are interpolated into an HTML attribute.
        html += `data-fallback-sources="${escapeAttrText(JSON.stringify(imageSources))}" `;
    }
    html += `data-file-id="${safeFileId || ''}" `;
    html += `data-file-path="${escapeAttrText(safeFilePath)}" `;
    html += 'data-current-source-index="0">';

    html += '<div class="image-load-error" style="display: none; padding: 1rem; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; color: #6c757d;">';
    html += '<i class="bi bi-exclamation-triangle me-2" aria-hidden="true"></i><span class="error-message">Loading image...</span>';
    html += '</div>';
    html += '</div>';

    if (safeFilePath) {
        html += '<div class="formatted-image-path">';
        html += '<small class="text-muted"><i class="bi bi-folder me-1" aria-hidden="true"></i>Path: <code>' + escapeHtml(safeFilePath) + '</code></small>';
        html += '</div>';
    }

    if (typeof content === 'string' && content.trim()) {
        html += '<div class="formatted-image-text">';
        html += '<h5 class="formatted-image-text-title">Extracted Text (OCR):</h5>';
        html += `<pre class="formatted-text">${escapeHtml(content)}</pre>`;
        html += '</div>';
    }

    html += '</div>';
    return html;
}

/**
 * Organize content data for accurate display
 * This function processes the raw content to ensure proper structure before formatting
 * @param {string} content - Raw content text
 * @param {string} fileType - File type
 * @returns {string} - Organized content ready for formatting
 */
function organizeContentForDisplay(content, fileType) {
    if (!content || typeof content !== 'string') {
        return content || '';
    }
    
    const fileTypeLower = (fileType || '').toLowerCase();
    
    // For Word/Excel/PDF/PowerPoint files, ensure proper line breaks and structure
    if (fileTypeLower.match(/\.(docx?|docm|rtf|odt|xlsx?|xlsm|xlsb|xltx?|ods|pdf|pptx?|potx?|odp)$/)) {
        // Ensure proper line breaks and structure preservation
        let organized = content;
        
        // Normalize line breaks
        organized = organized.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        
        // Preserve spatial ordering markers if present (for new ordered format)
        // These markers help maintain the original document structure
        organized = organized.replace(/(word_paragraph|word_table|slide_text|slide_table|slide_image)/gi, '\n$1\n');
        
        // Ensure table headers are on separate lines (both legacy and new format)
        organized = organized.replace(/(Table\s+\d+)/gi, '\n$1\n');
        organized = organized.replace(/(Sheet:\s*[^\n]+)/gi, '\n$1\n');
        
        // Ensure page markers are on separate lines
        organized = organized.replace(/(Page\s+\d+)/gi, '\n$1\n');
        organized = organized.replace(/(Slide\s+\d+)/gi, '\n$1\n');
        
        // Preserve paragraph style markers
        organized = organized.replace(/(\[Style:\s*[^\]]+\])/g, '\n$1\n');
        
        // Clean up excessive newlines (more than 3 consecutive) but preserve structure
        organized = organized.replace(/\n{4,}/g, '\n\n\n');
        
        // Trim each line but preserve structure
        const lines = organized.split('\n');
        const cleanedLines = lines.map((line, index) => {
            // Don't trim lines that are clearly table rows (have tabs or multiple spaces)
            if (line.includes('\t') || line.match(/\s{2,}/)) {
                return line;
            }
            // Don't trim marker lines
            if (line.match(/^(word_|slide_|Table\s+\d+|Page\s+\d+|Slide\s+\d+|\[Style:)/i)) {
                return line.trim();
            }
            return line.trim();
        });
        
        return cleanedLines.join('\n');
    }
    
    return content;
}

/**
 * Format content based on file type
 * Enhanced version with content organization and better file type support
 * @param {string} content - Content text
 * @param {string} fileType - File type (e.g., '.docx', '.xlsx', '.jpg')
 * @param {string} filePath - File path (for images)
 * @param {number} fileId - Optional file ID (for image preview)
 * @returns {string} - Formatted HTML
 */
export function formatContentByType(content, fileType, filePath = '', fileId = null) {
    if (!content && !filePath) {
        console.log('formatContentByType: No content or file path provided');
        return '';
    }
    
    const fileTypeLower = (fileType || '').toLowerCase();
    // Normalize file type - ensure it starts with dot for pattern matching
    const normalizedFileType = fileTypeLower.startsWith('.') ? fileTypeLower : '.' + fileTypeLower;
    console.log('formatContentByType called:', {
        fileType: fileTypeLower,
        normalizedFileType: normalizedFileType,
        contentLength: content ? content.length : 0,
        hasFilePath: !!filePath,
        fileId: fileId
    });
    
    // Organize content for accurate display
    const organizedContent = organizeContentForDisplay(content || '', fileType);
    
    // Image files
    if (normalizedFileType.match(/\.(jpg|jpeg|png|gif|bmp|tiff|tif|webp|svg)$/)) {
        console.log('Formatting as image');
        return formatImageContent(filePath, organizedContent, fileId);
    }
    
    // Word documents - match both with and without dot
    if (normalizedFileType.match(/\.(docx?|docm|rtf|odt)$/) || fileTypeLower.match(/^(docx?|docm|rtf|odt)$/)) {
        console.log('Formatting as Word document');
        const result = formatWordContent(organizedContent);
        console.log('Word formatting result length:', result.length);
        return result;
    }
    
    // Excel files - match both with and without dot
    if (normalizedFileType.match(/\.(xlsx?|xlsm|xlsb|xltx?|ods|csv)$/) || fileTypeLower.match(/^(xlsx?|xlsm|xlsb|xltx?|ods|csv)$/)) {
        console.log('Formatting as Excel file');
        const result = formatExcelContent(organizedContent);
        console.log('Excel formatting result length:', result.length);
        return result;
    }
    
    // PowerPoint files - format as structured content with slides
    if (normalizedFileType.match(/\.(pptx?|potx?|odp)$/) || fileTypeLower.match(/^(pptx?|potx?|odp)$/)) {
        console.log('Formatting as PowerPoint file');
        return formatPowerPointContent(organizedContent);
    }
    
    // PDF files - format as structured content with pages
    if (normalizedFileType === '.pdf' || fileTypeLower === 'pdf') {
        console.log('Formatting as PDF file');
        return formatPDFContent(organizedContent);
    }
    
    // Email files - format as structured email messages
    if (normalizedFileType.match(/\.(eml|msg|mbox|pst)$/) || fileTypeLower.match(/^(eml|msg|mbox|pst)$/)) {
        console.log('Formatting as email file');
        return formatEmailContent(organizedContent);
    }
    
    // HTML files
    if (normalizedFileType.match(/\.(html|htm)$/) || fileTypeLower.match(/^(html|htm)$/)) {
        console.log('Formatting as HTML file');
        return formatHTMLContent(organizedContent);
    }
    
    // JSON files
    if (normalizedFileType === '.json' || fileTypeLower === 'json') {
        console.log('Formatting as JSON file');
        return formatJSONContent(organizedContent);
    }
    
    // XML files
    if (normalizedFileType.match(/\.(xml|xsl|xslt)$/) || fileTypeLower.match(/^(xml|xsl|xslt)$/)) {
        console.log('Formatting as XML file');
        return formatXMLContent(organizedContent);
    }
    
    // Markdown files - rendered Markdown (headings, lists, code, tables)
    if (normalizedFileType.match(/\.(md|markdown|mdown)$/) || fileTypeLower.match(/^(md|markdown|mdown)$/)) {
        console.log('Formatting as Markdown file');
        return formatMarkdownContent(organizedContent);
    }
    
    // Text files with language detection; log files get a line-numbered
    // monospace view
    if (normalizedFileType.match(/\.(txt|text|log)$/) || fileTypeLower.match(/^(txt|text|log)$/)) {
        console.log('Formatting as text file');
        return formatTextContent(organizedContent, fileTypeLower);
    }
    
    // Default: plain text with better formatting and language detection
    if (organizedContent) {
        return formatTextContent(organizedContent);
    }
    
    return '';
}

/**
 * Format PowerPoint content with slides
 * Enhanced with language detection and spatial order preservation
 * Preserves original slide element order: text, tables, and images appear
 * in the same spatial order as in the original presentation
 * @param {string} content - Content text (already in spatial order from database)
 * @returns {string} - Formatted HTML
 */
function formatPowerPointContent(content) {
    if (!content || typeof content !== 'string') {
        return '';
    }
    
    // Detect language and direction
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    const lines = content.split('\n');
    let html = `<div class="formatted-powerpoint-content" dir="${direction}" lang="${lang}">`;
    let currentSlide = [];
    let currentSlideHeader = null;
    let inSlide = false;
    let currentSlideNumber = 0;
    let currentTable = [];
    let currentTableHeader = null;
    let inTable = false;
    let expectedColumns = null;
    const slides = []; // collected slide blocks, rendered with deck navigation
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmedLine = line.trim();
        
        if (!trimmedLine) {
            // Tab-only line: explicit empty row (see the Word path above).
            if (inTable && line.includes('\t')) {
                currentTable.push(normalizeRow([], expectedColumns));
                continue;
            }
            // Empty line - end current table if in one, unless the next line
            // is unambiguously another row of it (an all-empty row keeps its
            // position instead of ending the table).
            if (inTable && currentTable.length > 0) {
                const nextNonEmpty = nextNonEmptyLine(lines, i);
                if (nextNonEmpty && continuesTable(nextNonEmpty, expectedColumns)) {
                    const emptyRow = normalizeRow([], expectedColumns);
                    currentTable.push(emptyRow);
                    continue;
                }
                currentSlide.push({
                    type: 'table',
                    table: currentTable,
                    header: currentTableHeader
                });
                currentTable = [];
                currentTableHeader = null;
                inTable = false;
                expectedColumns = null;
            }
            continue;
        }
        
        // Slide table marker: must be recognised before the explanatory-text
        // skip below, which otherwise swallows "Table N" and leaves the
        // tab-delimited rows to be rendered as slide text.
        if (isTableStartLine(trimmedLine) && !trimmedLine.match(/^slide_image/i)) {
            if (!inSlide) {
                currentSlideNumber++;
                currentSlideHeader = { number: currentSlideNumber, title: null };
                inSlide = true;
            }
            if (currentTable.length > 0) {
                currentSlide.push({
                    type: 'table',
                    table: currentTable,
                    header: currentTableHeader
                });
                currentTable = [];
            }
            currentTableHeader = tableHeaderInfo(trimmedLine);
            inTable = true;
            expectedColumns = null;
            continue;
        }
        
        // Skip explanatory text lines (metadata markers)
        if (isExplanatoryText(trimmedLine)) {
            // Extract slide number from "Slide N" for structure, but don't display the marker
            const slideMatch = trimmedLine.match(/^Slide\s+(\d+)/i);
            if (slideMatch) {
                if (inSlide && currentSlide.length > 0) {
                    slides.push({ header: currentSlideHeader, content: currentSlide });
                    currentSlide = [];
                }
                
                currentSlideHeader = {
                    number: parseInt(slideMatch[1]),
                    title: null  // Don't display metadata titles
                };
                currentSlideNumber = parseInt(slideMatch[1]);
                inSlide = true;
            }
            // Skip other metadata lines
            continue;
        }
        
        // Check for slide header (legacy format)
        const slideMatch = trimmedLine.match(/^Slide\s+(\d+)(?:\s*\|\s*(.+))?$/i);
        if (slideMatch) {
            // End previous slide
            if (inSlide && currentSlide.length > 0) {
                slides.push({ header: currentSlideHeader, content: currentSlide });
                currentSlide = [];
            }
            
            currentSlideHeader = {
                number: parseInt(slideMatch[1]),
                title: null  // Don't display metadata titles
            };
            currentSlideNumber = parseInt(slideMatch[1]);
            inSlide = true;
            continue;
        }
        
        // Check for slide element markers (new ordered format).
        // Table markers were consumed above (``isTableStartLine``).
        const slideTextMatch = trimmedLine.match(/^slide_text/i);
        const slideImageMatch = trimmedLine.match(/^slide_image/i) || trimmedLine.match(/\[Image:\s*(.+)\]/i);
        
        if (slideTextMatch) {
            // Slide text element - add to current slide
            if (!inSlide) {
                // Start new slide if not in one
                currentSlideNumber++;
                currentSlideHeader = { number: currentSlideNumber, title: null };
                inSlide = true;
            }
            // Skip the marker line, next line will be the text
            continue;
        } else if (slideImageMatch) {
            // Slide image element
            if (!inSlide) {
                currentSlideNumber++;
                currentSlideHeader = { number: currentSlideNumber, title: null };
                inSlide = true;
            }
            const imageName = slideImageMatch[1] || 'image';
            currentSlide.push({
                type: 'image',
                name: imageName
            });
            continue;
        }
        
        if (inSlide) {
            // Check if we're in a table section: every non-blank line is a row
            // (a one-column table has single-cell rows with no separator).
            if (inTable) {
                const cells = splitTableRow(line, expectedColumns, currentTable);
                if (expectedColumns === null) {
                    expectedColumns = cells.length;
                }
                currentTable.push(normalizeRow(cells, expectedColumns));
            } else {
                // Regular text content
                currentSlide.push({
                    type: 'text',
                    text: trimmedLine
                });
            }
        } else {
            // Regular content before first slide
            html += formatParagraph(trimmedLine);
        }
    }
    
    // Close any remaining table
    if (inTable && currentTable.length > 0) {
        currentSlide.push({
            type: 'table',
            table: currentTable,
            header: currentTableHeader
        });
    }
    
    // Close last slide
    if (inSlide && currentSlide.length > 0) {
        slides.push({ header: currentSlideHeader, content: currentSlide });
    }
    
    // Deck rendering: a navigation bar plus every slide stacked. Slides stay
    // in the DOM so the whole-document search highlighter covers them all;
    // navigation scrolls to a slide and marks it active.
    if (slides.length > 0) {
        html += renderSlideDeckNav(slides);
        slides.forEach((sl, i) => {
            html += formatSlide(sl.content, sl.header, i === 0);
        });
    }
    
    html += '</div>';
    return html;
}

/**
 * Slide deck navigation bar: previous/next buttons, a position counter and
 * one numbered chip per slide (its title as tooltip). All slides remain
 * rendered below - the bar jumps between them.
 */
function renderSlideDeckNav(slides) {
    const total = slides.length;
    let chips = '';
    slides.forEach((sl, i) => {
        const title = slideTitleOf(sl);
        chips += `<button type="button" class="slide-chip${i === 0 ? ' slide-chip-active' : ''}"`
            + ` data-slide-index="${i}"`
            + (title ? ` title="${escapeAttrText(title)}"` : '')
            + ` data-on-click="contentViewerNav.goToSlide(this, ${i})">${i + 1}</button>`;
    });
    return `<div class="slide-nav" role="navigation" aria-label="${escapeAttrText(formatterI18n.slidesNavLabel)}">`
        + `<button type="button" class="slide-nav-btn" data-on-click="contentViewerNav.goToSlide(this, -1)" aria-label="${escapeAttrText(formatterI18n.previousSlideLabel)}">&#8249;</button>`
        + `<span class="slide-nav-counter" data-slide-counter>1 / ${total}</span>`
        + `<button type="button" class="slide-nav-btn" data-on-click="contentViewerNav.goToSlide(this, 1)" aria-label="${escapeAttrText(formatterI18n.nextSlideLabel)}">&#8250;</button>`
        + `<div class="slide-chips">${chips}</div>`
        + `</div>`;
}

/** Best-effort slide title: the first text element of the slide. */
function slideTitleOf(slide) {
    if (!slide || !Array.isArray(slide.content)) return '';
    for (const element of slide.content) {
        if (element && element.type === 'text' && element.text && element.text.trim()) {
            const t = element.text.trim();
            return t.length > 80 ? t.slice(0, 77) + '...' : t;
        }
    }
    return '';
}

/** Escape text for use inside a double-quoted HTML attribute. */
function escapeAttrText(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * Format a single slide
 * @param {Array<string|Object>} slideContent - Slide content (lines or structured elements)
 * @param {Object} slideHeader - Slide header info
 * @returns {string} - Formatted HTML
 */
function formatSlide(slideContent, slideHeader, isActive = false) {
    const number = slideHeader && slideHeader.number ? slideHeader.number : '';
    let html = `<div class="formatted-slide${isActive ? ' slide-active' : ''}" data-slide-number="${escapeAttrText(String(number))}">`;
    
    // Slide number chip - a visible anchor the deck navigation scrolls to
    if (number !== '') {
        html += `<div class="slide-number-chip">${escapeHtml(String(number))}</div>`;
    }
    
    html += '<div class="formatted-slide-content">';
    
    // Check if slideContent is structured (new format) or plain lines (legacy)
    if (slideContent.length > 0 && typeof slideContent[0] === 'object' && slideContent[0].type) {
        // New structured format - process elements in order
        slideContent.forEach(element => {
            if (element.type === 'text') {
                html += formatParagraph(element.text);
            } else if (element.type === 'table') {
                html += formatAsTable(element.table, element.header);
            } else if (element.type === 'image') {
                html += `<div class="formatted-slide-image">[Image: ${escapeHtml(element.name)}]</div>`;
            }
        });
    } else {
        // Legacy format - plain text lines
        slideContent.forEach(line => {
            if (typeof line === 'string' && line.trim()) {
                html += formatParagraph(line);
            }
        });
    }
    
    html += '</div>';
    
    html += '</div>';
    return html;
}

/**
 * Format PDF content with pages
 * Preserves original page order and spatial relationships
 * Content is already in spatial order from database storage
 * Hides explanatory metadata (page headers, method info, etc.)
 * @param {string} content - Content text (already in spatial order)
 * @returns {string} - Formatted HTML
 */
function formatPDFContent(content) {
    if (!content || typeof content !== 'string') {
        return '';
    }
    
    // Detect language and direction
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    const lines = content.split('\n');
    let html = `<div class="formatted-pdf-content" dir="${direction}" lang="${lang}">`;
    let currentPage = [];
    let currentPageHeader = null;
    let inPage = false;
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmedLine = line.trim();
        
        if (!trimmedLine) {
            if (inPage && currentPage.length > 0) {
                html += formatPage(currentPage, currentPageHeader);
                currentPage = [];
                currentPageHeader = null;
                inPage = false;
            }
            continue;
        }
        
        // Skip explanatory text lines (metadata markers)
        if (isExplanatoryText(trimmedLine)) {
            // Extract page number from "Page N" for structure, but don't display the marker
            const pageMatch = trimmedLine.match(/^Page\s+(\d+)/i);
            if (pageMatch) {
                if (inPage && currentPage.length > 0) {
                    html += formatPage(currentPage, currentPageHeader);
                    currentPage = [];
                }
                
                currentPageHeader = {
                    number: parseInt(pageMatch[1]),
                    metadata: null  // Don't display metadata
                };
                inPage = true;
            }
            // Skip other metadata lines (Method:, Length:, etc.)
            continue;
        }
        
        // Check for page header
        const pageMatch = trimmedLine.match(/^Page\s+(\d+)(?:\s*\|\s*(.+))?$/i);
        if (pageMatch) {
            if (inPage && currentPage.length > 0) {
                html += formatPage(currentPage, currentPageHeader);
                currentPage = [];
            }
            
            currentPageHeader = {
                number: parseInt(pageMatch[1]),
                metadata: null  // Don't display metadata
            };
            inPage = true;
            continue;
        }
        
        if (inPage) {
            // Remove any remaining explanatory text from page content
            const cleanedLine = removeExplanatoryText(trimmedLine);
            if (cleanedLine && cleanedLine.trim()) {
                currentPage.push(cleanedLine.trim());
            }
        } else {
            // Regular content before first page - skip if it's explanatory
            if (!isExplanatoryText(trimmedLine)) {
                html += formatParagraph(trimmedLine);
            }
        }
    }
    
    if (inPage && currentPage.length > 0) {
        html += formatPage(currentPage, currentPageHeader);
    }
    
    html += '</div>';
    return html;
}

/**
 * Format a single PDF page
 * @param {Array<string>} pageContent - Page content lines
 * @param {Object} pageHeader - Page header info
 * @returns {string} - Formatted HTML
 */
function formatPage(pageContent, pageHeader) {
    const number = pageHeader && pageHeader.number ? pageHeader.number : '';
    let html = '<div class="formatted-page">';
    
    // Visible page divider - mirrors the page structure of the original PDF
    if (number !== '') {
        html += `<div class="page-divider"><span class="page-number-chip">${escapeHtml(String(number))}</span></div>`;
    }
    
    html += '<div class="formatted-page-content">';
    pageContent.forEach(line => {
        if (line.trim()) {
            html += formatParagraph(line);
        }
    });
    html += '</div>';
    
    html += '</div>';
    return html;
}

/**
 * Format email content with proper structure
 * Handles single messages and multiple messages (MBOX/PST)
 * @param {string} content - Content text
 * @returns {string} - Formatted HTML
 */
function formatEmailContent(content) {
    if (!content || typeof content !== 'string') {
        return '';
    }
    
    // Detect language and direction
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    const lines = content.split('\n');
    let html = `<div class="formatted-email-content" dir="${direction}" lang="${lang}">`;
    let currentMessage = [];
    let currentMessageHeader = null;
    let inMessage = false;
    let messageIndex = 0;
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmedLine = line.trim();
        
        if (!trimmedLine) {
            if (inMessage && currentMessage.length > 0) {
                html += formatEmailMessage(currentMessage, currentMessageHeader, messageIndex);
                currentMessage = [];
                currentMessageHeader = null;
                inMessage = false;
                messageIndex++;
            }
            continue;
        }
        
        // Check for message header patterns
        const messageMatch = trimmedLine.match(/^Message\s*#(\d+)/i);
        const fromMatch = trimmedLine.match(/^From:\s*(.+)$/i);
        const subjectMatch = trimmedLine.match(/^Subject:\s*(.+)$/i);
        
        if (messageMatch || (fromMatch && !inMessage)) {
            // Start new message
            if (inMessage && currentMessage.length > 0) {
                html += formatEmailMessage(currentMessage, currentMessageHeader, messageIndex);
                currentMessage = [];
                messageIndex++;
            }
            
            currentMessageHeader = {
                index: messageMatch ? parseInt(messageMatch[1]) : messageIndex + 1
            };
            inMessage = true;
            
            if (fromMatch) {
                currentMessageHeader.from = fromMatch[1].trim();
            }
            if (subjectMatch) {
                currentMessageHeader.subject = subjectMatch[1].trim();
            }
            
            // Continue to collect header fields
            currentMessage.push(trimmedLine);
            continue;
        }
        
        // Collect header fields
        if (inMessage) {
            // From/Subject also appear INSIDE a message that started with a
            // "Message #N" marker (MBOX/PST): capture them there too,
            // otherwise the sender and subject vanished from the header.
            const fromFieldMatch = trimmedLine.match(/^From:\s*(.+)$/i);
            const subjectFieldMatch = trimmedLine.match(/^Subject:\s*(.+)$/i);
            const toMatch = trimmedLine.match(/^To:\s*(.+)$/i);
            const dateMatch = trimmedLine.match(/^Date:\s*(.+)$/i);
            const ccMatch = trimmedLine.match(/^CC:\s*(.+)$/i);
            const bccMatch = trimmedLine.match(/^BCC:\s*(.+)$/i);
            const msgIdMatch = trimmedLine.match(/^Message-ID:\s*(.+)$/i);
            
            if (fromFieldMatch) {
                currentMessageHeader = currentMessageHeader || { index: messageIndex + 1 };
                currentMessageHeader.from = fromFieldMatch[1].trim();
                currentMessage.push(trimmedLine);
            } else if (subjectFieldMatch) {
                currentMessageHeader = currentMessageHeader || { index: messageIndex + 1 };
                currentMessageHeader.subject = subjectFieldMatch[1].trim();
                currentMessage.push(trimmedLine);
            } else if (toMatch) {
                currentMessageHeader = currentMessageHeader || { index: messageIndex + 1 };
                currentMessageHeader.to = toMatch[1].trim();
                currentMessage.push(trimmedLine);
            } else if (dateMatch) {
                currentMessageHeader = currentMessageHeader || { index: messageIndex + 1 };
                currentMessageHeader.date = dateMatch[1].trim();
                currentMessage.push(trimmedLine);
            } else if (ccMatch) {
                currentMessageHeader = currentMessageHeader || { index: messageIndex + 1 };
                currentMessageHeader.cc = ccMatch[1].trim();
                currentMessage.push(trimmedLine);
            } else if (bccMatch) {
                currentMessageHeader = currentMessageHeader || { index: messageIndex + 1 };
                currentMessageHeader.bcc = bccMatch[1].trim();
                currentMessage.push(trimmedLine);
            } else if (msgIdMatch) {
                currentMessageHeader = currentMessageHeader || { index: messageIndex + 1 };
                currentMessageHeader.messageId = msgIdMatch[1].trim();
                currentMessage.push(trimmedLine);
            } else if (trimmedLine.match(/^---\s*Message Content\s*---/i)) {
                // Content separator - keep it but mark content start
                currentMessage.push(trimmedLine);
            } else {
                // Regular content line
                currentMessage.push(trimmedLine);
            }
        } else {
            // Content before first message
            html += formatParagraph(trimmedLine);
        }
    }
    
    if (inMessage && currentMessage.length > 0) {
        html += formatEmailMessage(currentMessage, currentMessageHeader, messageIndex);
    }
    
    html += '</div>';
    return html;
}

/**
 * Format a single email message
 * @param {Array<string>} messageContent - Message content lines
 * @param {Object} messageHeader - Message header info
 * @param {number} messageIndex - Message index
 * @returns {string} - Formatted HTML
 */
/** Split a recipient list (comma/semicolon separated) into badges. */
function renderRecipientBadges(value) {
    return String(value)
        .split(/[,;]\s*/)
        .map(r => r.trim())
        .filter(Boolean)
        .map(r => `<span class="recipient-badge">${escapeHtml(r)}</span>`)
        .join(' ');
}

function formatEmailMessage(messageContent, messageHeader, messageIndex) {
    const direction = detectTextDirection(messageContent.join('\n'));
    const lang = detectLanguage(messageContent.join('\n'));
    
    let html = `<div class="formatted-email-message" dir="${direction}" lang="${lang}">`;
    
    // Message header
    html += '<div class="formatted-email-header">';
    if (messageHeader) {
        if (messageHeader.index) {
            html += `<div class="formatted-email-index">Message #${messageHeader.index}</div>`;
        }
        if (messageHeader.from) {
            // ONE sender
            html += `<div class="formatted-email-field"><strong>From:</strong> <span class="recipient-badge recipient-from">${escapeHtml(messageHeader.from)}</span></div>`;
        }
        // ONE OR MORE recipients: each gets its own badge so multi-recipient
        // messages are unambiguous (sender on one side, every recipient visible)
        if (messageHeader.to) {
            html += `<div class="formatted-email-field"><strong>To:</strong> ${renderRecipientBadges(messageHeader.to)}</div>`;
        }
        if (messageHeader.cc) {
            html += `<div class="formatted-email-field"><strong>CC:</strong> ${renderRecipientBadges(messageHeader.cc)}</div>`;
        }
        if (messageHeader.bcc) {
            html += `<div class="formatted-email-field"><strong>BCC:</strong> ${renderRecipientBadges(messageHeader.bcc)}</div>`;
        }
        if (messageHeader.subject) {
            html += `<div class="formatted-email-subject"><strong>Subject:</strong> ${escapeHtml(messageHeader.subject)}</div>`;
        }
        if (messageHeader.date) {
            html += `<div class="formatted-email-field"><strong>Date:</strong> ${escapeHtml(messageHeader.date)}</div>`;
        }
        if (messageHeader.messageId) {
            html += `<div class="formatted-email-field"><small><strong>Message-ID:</strong> ${escapeHtml(messageHeader.messageId)}</small></div>`;
        }
    }
    html += '</div>';
    
    // Message content
    html += '<div class="formatted-email-body">';
    let inContent = false;
    messageContent.forEach(line => {
        const trimmedLine = line.trim();
        if (trimmedLine.match(/^---\s*Message Content\s*---/i)) {
            inContent = true;
            return;
        }
        if (inContent || (!trimmedLine.match(/^(From|To|CC|BCC|Subject|Date|Message-ID|Message\s*#):/i) && trimmedLine)) {
            html += formatParagraph(trimmedLine);
        }
    });
    html += '</div>';
    
    html += '</div>';
    return html;
}

/**
 * Format HTML content
 * @param {string} content - Content text
 * @returns {string} - Formatted HTML
 */
function formatHTMLContent(content) {
    if (!content || typeof content !== 'string') {
        return '';
    }
    
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    // For HTML, we can display it directly but sanitize it
    // In a real implementation, you might want to use DOMPurify or similar
    let html = `<div class="formatted-html-content" dir="${direction}" lang="${lang}">`;
    html += `<pre class="formatted-text">${escapeHtml(content)}</pre>`;
    html += '</div>';
    return html;
}

/**
 * Format JSON content
 * @param {string} content - Content text
 * @returns {string} - Formatted HTML
 */
function formatJSONContent(content) {
    if (!content || typeof content !== 'string') {
        return '';
    }
    
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    let html = `<div class="formatted-json-content" dir="${direction}" lang="${lang}">`;
    
    try {
        // Try to parse and pretty-print JSON
        const parsed = JSON.parse(content);
        const pretty = JSON.stringify(parsed, null, 2);
        html += `<pre class="formatted-json"><code>${escapeHtml(pretty)}</code></pre>`;
    } catch (e) {
        // If not valid JSON, display as-is
        html += `<pre class="formatted-text">${escapeHtml(content)}</pre>`;
    }
    
    html += '</div>';
    return html;
}

/**
 * Format XML content
 * @param {string} content - Content text
 * @returns {string} - Formatted HTML
 */
function formatXMLContent(content) {
    if (!content || typeof content !== 'string') {
        return '';
    }
    
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    let html = `<div class="formatted-xml-content" dir="${direction}" lang="${lang}">`;
    
    // Simple XML formatting (indent based on tags)
    let formatted = content;
    try {
        // Basic XML indentation
        formatted = content.replace(/(>)(<)(\/*)/g, '$1\n$2$3');
        const lines = formatted.split('\n');
        let indent = 0;
        formatted = lines.map(line => {
            const trimmed = line.trim();
            if (!trimmed) return '';
            if (trimmed.match(/^<\/\w/)) indent--;
            const indented = '  '.repeat(Math.max(0, indent)) + trimmed;
            if (trimmed.match(/^<\w[^>]*[^\/]>.*$/)) indent++;
            return indented;
        }).join('\n');
    } catch (e) {
        formatted = content;
    }
    
    html += `<pre class="formatted-xml"><code>${escapeHtml(formatted)}</code></pre>`;
    html += '</div>';
    return html;
}

/**
 * Format text content with language detection
 * @param {string} content - Content text
 * @returns {string} - Formatted HTML
 */
function formatTextContent(content, fileType = '') {
    if (!content || typeof content !== 'string') {
        return '';
    }
    
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);
    
    const ext = (fileType || '').toLowerCase().replace(/^\./, '');
    if (ext === 'log') {
        return formatLogContent(content, direction, lang);
    }
    
    let html = `<div class="formatted-text-content" dir="${direction}" lang="${lang}">`;
    html += `<pre class="formatted-text">${escapeHtml(content)}</pre>`;
    html += '</div>';
    return html;
}

/**
 * Log viewer: monospace with a line-number gutter. Line numbers are
 * generated (not part of the content) so search/highlight offsets are
 * unaffected.
 */
function formatLogContent(content, direction, lang) {
    const lines = content.split('\n');
    let html = `<div class="formatted-log-content" dir="${direction}" lang="${lang}">`;
    html += lines.map((line, i) =>
        `<div class="log-line">` +
        `<span class="log-line-num">${i + 1}</span>` +
        `<span class="log-line-text">${escapeHtml(line) || '\u00a0'}</span>` +
        `</div>`
    ).join('');
    html += '</div>';
    return html;
}

/* --------------------------------------------------------------------------
   Markdown renderer - README-style files rendered as documentation
   -------------------------------------------------------------------------- */

/**
 * Render Markdown as clean HTML: headings, horizontal rules, block quotes,
 * ordered/unordered lists (one nesting level), fenced + inline code,
 * tables, links (protocol-sanitized), emphasis and strong emphasis.
 * The source is HTML-escaped before any transformation, so raw HTML in the
 * file is displayed, never executed.
 */
function formatMarkdownContent(content) {
    if (!content || typeof content !== 'string') {
        return '';
    }
    const direction = detectTextDirection(content);
    const lang = detectLanguage(content);

    const lines = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    let html = `<div class="formatted-markdown-content" dir="${direction}" lang="${lang}">`;
    let i = 0;

    const isListItem = (t) => /^\s*(-|\*|\+|\d+[.)])\s+/.test(t);
    const listMarkerOf = (t) => /^\s*(\d+[.)])\s+/.test(t) ? 'ol' : 'ul';

    while (i < lines.length) {
        const line = lines[i];
        const trimmed = line.trim();

        // Blank line
        if (!trimmed) { i++; continue; }

        // Fenced code block
        const fence = trimmed.match(/^```\s*(\w*)$/);
        if (fence) {
            const codeLines = [];
            i++;
            while (i < lines.length && !/^```\s*$/.test(lines[i].trim())) {
                codeLines.push(lines[i]);
                i++;
            }
            i++; // closing fence
            html += `<pre class="md-code-block"${fence[1] ? ` data-lang="${escapeAttrText(fence[1])}"` : ''}>${escapeHtml(codeLines.join('\n'))}</pre>`;
            continue;
        }

        // Heading
        const heading = trimmed.match(/^(#{1,6})\s+(.*)$/);
        if (heading) {
            const level = heading[1].length;
            html += `<h${level} class="md-heading md-h${level}">${renderMarkdownInline(heading[2])}</h${level}>`;
            i++;
            continue;
        }

        // Horizontal rule
        if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
            html += '<hr class="md-hr">';
            i++;
            continue;
        }

        // Block quote
        if (trimmed.startsWith('>')) {
            const quoteLines = [];
            while (i < lines.length && lines[i].trim().startsWith('>')) {
                quoteLines.push(lines[i].trim().replace(/^>\s?/, ''));
                i++;
            }
            html += `<blockquote class="md-blockquote">${renderMarkdownInline(quoteLines.join(' '))}</blockquote>`;
            continue;
        }

        // Table: | a | b | followed by |---|---|
        if (/^\|.*\|$/.test(trimmed) && i + 1 < lines.length &&
            /^\|?[\s:|-]+\|?$/.test(lines[i + 1].trim()) && lines[i + 1].includes('-')) {
            const parseRow = (t) => t.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
            const headerCells = parseRow(trimmed);
            i += 2;
            const bodyRows = [];
            while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) {
                bodyRows.push(parseRow(lines[i].trim()));
                i++;
            }
            html += '<table class="md-table"><thead><tr>' +
                headerCells.map(c => `<th>${renderMarkdownInline(c)}</th>`).join('') +
                '</tr></thead><tbody>' +
                bodyRows.map(r => '<tr>' + headerCells.map((_, ci) =>
                    `<td>${renderMarkdownInline(r[ci] || '')}</td>`).join('') + '</tr>').join('') +
                '</tbody></table>';
            continue;
        }

        // List (with one nesting level)
        if (isListItem(trimmed)) {
            const tag = listMarkerOf(trimmed);
            html += `<${tag} class="md-list">`;
            while (i < lines.length && (isListItem(lines[i]) || /^\s{2,}\S/.test(lines[i]))) {
                const itemLine = lines[i];
                const itemTrimmed = itemLine.trim();
                if (isListItem(itemTrimmed)) {
                    const isNested = /^\s{2,}/.test(itemLine);
                    const itemText = itemTrimmed.replace(/^\s*(-|\*|\+|\d+[.)])\s+/, '');
                    if (isNested) {
                        // nested item: open a sub-list under the last <li>
                        const nestedTag = listMarkerOf(itemTrimmed);
                        html += `<${nestedTag} class="md-list md-list-nested"><li>${renderMarkdownInline(itemText)}</li>`;
                        let j = i + 1;
                        while (j < lines.length && isListItem(lines[j]) && /^\s{2,}/.test(lines[j])) {
                            const nestedText = lines[j].trim().replace(/^\s*(-|\*|\+|\d+[.)])\s+/, '');
                            html += `<li>${renderMarkdownInline(nestedText)}</li>`;
                            j++;
                        }
                        html += `</${nestedTag}>`;
                        i = j;
                        continue;
                    }
                    html += `<li>${renderMarkdownInline(itemText)}</li>`;
                    i++;
                } else {
                    // continuation of the previous item
                    html += ' ' + renderMarkdownInline(itemTrimmed);
                    i++;
                }
            }
            html += `</${tag}>`;
            continue;
        }

        // Paragraph: consecutive non-blank, non-structural lines
        const paraLines = [trimmed];
        i++;
        while (i < lines.length && lines[i].trim() &&
               !/^(#{1,6}\s|```|>|\|)/.test(lines[i].trim()) &&
               !isListItem(lines[i].trim()) &&
               !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim())) {
            paraLines.push(lines[i].trim());
            i++;
        }
        html += `<p class="md-paragraph">${renderMarkdownInline(paraLines.join(' '))}</p>`;
    }

    html += '</div>';
    return html;
}

/**
 * Inline Markdown: code spans, links, images (rendered as labeled badges -
 * remote images are never loaded), strong/emphasis/strikethrough.
 * Input is escaped first; code spans are protected by placeholders.
 */
function renderMarkdownInline(text) {
    let out = escapeHtml(text);

    // Inline code first (protect from other inline rules)
    const codeSpans = [];
    out = out.replace(/`([^`\n]+)`/g, (_m, code) => {
        codeSpans.push(`<code class="md-code">${code}</code>`);
        return `\u0000${codeSpans.length - 1}\u0000`;
    });

    // Images: ![alt](url) -> badge (never load remote content)
    out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
        (_m, alt, url) => `<span class="md-image-badge" title="${escapeAttrText(url)}">&#128247; ${alt || url}</span>`);

    // Links: [text](url) with protocol sanitization
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_m, label, url) => {
        const safe = /^([a-z]+:)?\/\//i.test(url) || url.startsWith('#') || url.startsWith('mailto:');
        if (safe) {
            const href = /^(javascript|data|vbscript):/i.test(url) ? '#' : url;
            return `<a class="md-link" href="${escapeAttrText(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
        }
        return label;
    });

    // Emphasis and strong
    out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    out = out.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');

    // Restore code spans
    out = out.replace(/\u0000(\d+)\u0000/g, (_m, idx) => codeSpans[parseInt(idx, 10)] || '');

    return out;
}

/**
 * Escape HTML special characters
 * @param {string} text - Text to escape
 * @returns {string} - Escaped text
 */
function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}


/* --------------------------------------------------------------------------
   In-content navigation (worksheet tabs, slide deck)
   --------------------------------------------------------------------------
   The formatter output is inserted via innerHTML everywhere (File Detail,
   Full Content Reader, search preview modal), so the controls are wired
   through inline handlers against this small global. All sections stay in
   the DOM - navigation only scrolls and toggles active states - so the
   DOM-safe search highlighter always sees the entire document.
   -------------------------------------------------------------------------- */

if (typeof window !== 'undefined' && !window.contentViewerNav) {
    window.contentViewerNav = {
        /** Worksheet tab click: activate the tab and scroll to its section. */
        showSheet(btn) {
            const wrapper = btn.closest('.formatted-excel-content');
            if (!wrapper) return;
            const idx = btn.dataset.sheetIndex;
            wrapper.querySelectorAll('.sheet-tab').forEach(tab => {
                const active = tab === btn;
                tab.classList.toggle('active', active);
                tab.setAttribute('aria-selected', String(active));
            });
            const section = wrapper.querySelector(`.sheet-section[data-sheet-index="${idx}"]`);
            if (section) {
                section.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        },

        /**
         * Slide navigation: target is -1 (previous), 1 (next) or a
         * 0-based slide index. Scrolls to the slide and syncs the active
         * states of chips, counter and slide block.
         */
        goToSlide(btn, target) {
            const deck = btn.closest('.formatted-powerpoint-content');
            if (!deck) return;
            const slideBlocks = Array.from(deck.querySelectorAll('.formatted-slide'));
            if (!slideBlocks.length) return;

            let currentIdx = slideBlocks.findIndex(el => el.classList.contains('slide-active'));
            let idx;
            if (target === -1 || target === 1) {
                idx = currentIdx < 0 ? 0
                    : Math.min(slideBlocks.length - 1, Math.max(0, currentIdx + target));
            } else {
                idx = Math.max(0, Math.min(slideBlocks.length - 1, parseInt(target, 10) || 0));
            }

            slideBlocks.forEach((el, i) => el.classList.toggle('slide-active', i === idx));
            deck.querySelectorAll('.slide-chip').forEach(chip =>
                chip.classList.toggle('slide-chip-active', Number(chip.dataset.slideIndex) === idx));
            const counter = deck.querySelector('[data-slide-counter]');
            if (counter) counter.textContent = `${idx + 1} / ${slideBlocks.length}`;

            slideBlocks[idx].scrollIntoView({ behavior: 'smooth', block: 'start' });
        },
    };
}
