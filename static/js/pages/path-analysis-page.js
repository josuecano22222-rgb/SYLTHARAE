/**
 * Path Analysis Page JavaScript
 * Extracted from Analysis/path_analysis.html
 */

// AUDIT-XSS-01: path segments, words and file names are ingested data.
import { escapeAttribute } from '../modules/core/utils.js';

// Load translations from JSON script tag
let translations = {};

// Helper function to format translation strings with placeholders
function formatTranslation(key, params = {}) {
    let text = translations[key] || key;
    Object.keys(params).forEach(param => {
        text = text.replace(`{${param}}`, params[param]);
    });
    // Handle pluralization
    text = text.replace(/{s}/g, params.count !== undefined && params.count !== 1 ? 's' : '');
    return text;
}

let pathStructure = [];
let selectedPath = null;
let charts = {};

// Destroy any Chart.js instance still bound to this canvas, tracked or orphaned.
// Prevents "Canvas is already in use" when a canvas is reused after its tracked
// reference was dropped (early return, exception, or canvas-element swap).
function releaseCanvas(ctx) {
    try {
        const canvas = ctx && ctx.canvas ? ctx.canvas : ctx;
        const prior = (window.Chart && Chart.getChart) ? Chart.getChart(canvas) : null;
        if (prior) prior.destroy();
    } catch (e) {
        console.warn('Error releasing canvas before chart render:', e);
    }
}
let availableSources = [];
let availableSides = [];
let currentLoadingPath = null; // Track which path is currently being loaded
let abortControllers = {}; // Track AbortControllers for each data type
let expandedPaths = new Set(); // Track expanded paths for persistence

let pathAnalysisBooted = false;

// Soft nav imports this module after DOMContentLoaded has already fired.
function bootPathAnalysisPage() {
    if (pathAnalysisBooted) return;
    if (!document.getElementById('pathTree') && !document.getElementById('path-analysis-page-data')) return;
    pathAnalysisBooted = true;

    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('path-analysis-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing path analysis page data:', e);
        }
    }
    
    console.log('Path Analysis page loaded');
    
    // Load expanded paths state from localStorage BEFORE rendering
    loadExpandedPaths();
    
    // Check if Chart.js is loaded
    if (typeof Chart === 'undefined') {
        console.error('Chart.js is not loaded! Charts will not work.');
        // Don't show alert, just log error - page can still function without charts
    } else {
        console.log('Chart.js loaded successfully, version:', Chart.version);
    }
    
    // 🚀 OPTIMIZED: Load filters first (lightweight)
    loadFilters();
    
    // 🚀 OPTIMIZED: Load path structure after a short delay to allow page to render
    // Tree state will be restored automatically in renderPathTree
    setTimeout(() => {
        loadPathStructure();
    }, 100);
    
    // Add expand/collapse all button handlers
    const expandBtn = document.getElementById('expandAllBtn');
    const collapseBtn = document.getElementById('collapseAllBtn');
    if (expandBtn) expandBtn.addEventListener('click', expandAll);
    if (collapseBtn) collapseBtn.addEventListener('click', collapseAll);
    
    // Add filter change handlers
    const sourceFilter = document.getElementById('sourceFilter');
    const sideFilter = document.getElementById('sideFilter');
    const clearFiltersBtn = document.getElementById('clearFiltersBtn');
    
    if (sourceFilter) sourceFilter.addEventListener('change', handleFilterChange);
    if (sideFilter) sideFilter.addEventListener('change', handleFilterChange);
    if (clearFiltersBtn) clearFiltersBtn.addEventListener('click', clearFilters);
    
    // Add modal close button handlers
    const closeCategoryModalBtn = document.getElementById('closeCategoryModalBtn');
    const closeCategoryWordsModalBtn = document.getElementById('closeCategoryWordsModalBtn');
    if (closeCategoryModalBtn) closeCategoryModalBtn.addEventListener('click', closeCategoryModal);
    if (closeCategoryWordsModalBtn) closeCategoryWordsModalBtn.addEventListener('click', closeCategoryWordsModal);
    
    // Prevent page refresh on path selection - ensure instant updates
    document.addEventListener('click', function(e) {
        const pathItem = e.target.closest('.path-item');
        if (pathItem && !e.target.closest('.expand-btn')) {
            e.preventDefault();
        }
    }, true);

    bootPathAnalysisModals();
}

function bootPathAnalysisModals() {
    const modal = document.getElementById('categoryFilesModal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                closeCategoryModal();
            }
        });
    }
    
    // Close modal with Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeCategoryModal();
            closeCategoryWordsModal();
        }
    });
    
    // Category Words Modal handlers
    const wordsModal = document.getElementById('categoryWordsModal');
    if (wordsModal) {
        wordsModal.addEventListener('click', function(e) {
            if (e.target === wordsModal) {
                closeCategoryWordsModal();
            }
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootPathAnalysisPage, { once: true });
} else if (!window.__swapReinit) {
    // Full page load of a deferred module: DOM is already ready.
    // Soft nav sets __swapReinit before import() and calls export default.
    bootPathAnalysisPage();
}

export default function init() {
    pathAnalysisBooted = false;
    bootPathAnalysisPage();
}

// 🚀 FIXED: Load filters (sources and sides) with proper response handling
async function loadFilters() {
    try {
        // Load sources
        const sourcesResponse = await fetch('/api/analytics/sources');
        if (sourcesResponse.ok) {
            const sourcesData = await sourcesResponse.json();
            // 🚀 FIXED: Handle both array and object response formats
            availableSources = Array.isArray(sourcesData) ? sourcesData : (sourcesData.sources || []);
            
            const sourceSelect = document.getElementById('sourceFilter');
            if (sourceSelect) {
                availableSources.forEach(source => {
                    const option = document.createElement('option');
                    option.value = source.id;
                    option.textContent = `${source.name}${source.country ? ' (' + source.country + ')' : ''}`;
                    sourceSelect.appendChild(option);
                });
                
                console.log(`Loaded ${availableSources.length} sources`);
            }
        }
        
        // Load sides
        const sidesResponse = await fetch('/api/analytics/sides');
        if (sidesResponse.ok) {
            const sidesData = await sidesResponse.json();
            // 🚀 FIXED: Handle both array and object response formats
            availableSides = Array.isArray(sidesData) ? sidesData : (sidesData.sides || []);
            
            const sideSelect = document.getElementById('sideFilter');
            if (sideSelect) {
                availableSides.forEach(side => {
                    const option = document.createElement('option');
                    option.value = side.id;
                    option.textContent = side.name;
                    sideSelect.appendChild(option);
                });
                
                console.log(`Loaded ${availableSides.length} sides`);
            }
        }
    } catch (error) {
        console.error('Error loading filters:', error);
    }
}

// Helper function to get current filter values
function getCurrentFilters() {
    const sourceFilterEl = document.getElementById('sourceFilter');
    const sideFilterEl = document.getElementById('sideFilter');
    return {
        source_id: sourceFilterEl ? sourceFilterEl.value : '',
        side_id: sideFilterEl ? sideFilterEl.value : ''
    };
}

// Helper function to build query string with filters
function buildQueryString(baseParams) {
    const params = new URLSearchParams(baseParams);
    const filters = getCurrentFilters();
    
    if (filters.source_id) {
        params.append('source_id', filters.source_id);
    }
    if (filters.side_id) {
        params.append('side_id', filters.side_id);
    }
    
    return params.toString();
}

// 🚀 FIXED: Handle filter change with null checks
function handleFilterChange() {
    const sourceFilterEl = document.getElementById('sourceFilter');
    const sideFilterEl = document.getElementById('sideFilter');
    
    if (!sourceFilterEl || !sideFilterEl) {
        console.warn('Filter elements not found');
        return;
    }
    
    const sourceFilter = sourceFilterEl.value;
    const sideFilter = sideFilterEl.value;
    
    // Count active filters
    let activeFilterCount = 0;
    if (sourceFilter) activeFilterCount++;
    if (sideFilter) activeFilterCount++;
    
    // Update filter status indicator
    const filterStatus = document.getElementById('filterStatus');
    const filterCount = document.getElementById('filterCount');
    
    if (filterStatus && filterCount) {
        if (activeFilterCount > 0) {
            filterStatus.style.display = 'inline';
            filterCount.textContent = activeFilterCount;
        } else {
            filterStatus.style.display = 'none';
        }
    }
    
    // Show/hide clear button
    const clearBtn = document.getElementById('clearFiltersBtn');
    if (clearBtn) {
        if (sourceFilter || sideFilter) {
            clearBtn.style.display = 'block';
        } else {
            clearBtn.style.display = 'none';
        }
    }
    
    // Reload path structure with filters
    loadPathStructure();
    
    // If a path is currently selected, reload all its data with new filters
    if (selectedPath) {
        const fullPath = selectedPath.fullPath || selectedPath.full_path || selectedPath.name;
        if (fullPath) {
            const queryPath = fullPath.replace(/\\/g, '/').replace(/\/$/, '');
            if (queryPath) {
                // Show loading states
                showLoadingStates();
                
                // Reload all data with new filters
                Promise.all([
                    loadPathAnalytics(queryPath),
                    loadPathWords(queryPath),
                    loadPathFiles(queryPath),
                    loadPathClassifications(queryPath),
                    loadCategoryWordsAnalysis(queryPath)
                ]).catch(error => {
                    console.error('Error reloading path data with filters:', error);
                });
            }
        }
    }
}

// 🚀 FIXED: Clear all filters with null checks
function clearFilters() {
    const sourceFilter = document.getElementById('sourceFilter');
    const sideFilter = document.getElementById('sideFilter');
    const clearBtn = document.getElementById('clearFiltersBtn');
    const filterStatus = document.getElementById('filterStatus');
    
    if (sourceFilter) sourceFilter.value = '';
    if (sideFilter) sideFilter.value = '';
    if (clearBtn) clearBtn.style.display = 'none';
    if (filterStatus) filterStatus.style.display = 'none';
    
    // Reload path structure without filters
    loadPathStructure();
}

// 🚀 FIXED: Load path structure with null checks and error handling
async function loadPathStructure() {
    const tree = document.getElementById('pathTree');
    if (!tree) {
        console.warn('Path tree element not found');
        return;
    }
    
    try {
        console.log('Loading path structure...');
        
        // Get current filter values
        const sourceFilterEl = document.getElementById('sourceFilter');
        const sideFilterEl = document.getElementById('sideFilter');
        const sourceFilter = sourceFilterEl ? sourceFilterEl.value : '';
        const sideFilter = sideFilterEl ? sideFilterEl.value : '';
        
        // Build URL with filters
        let url = '/api/analytics/paths/structure';
        const params = new URLSearchParams();
        
        if (sourceFilter) {
            params.append('source_id', sourceFilter);
        }
        if (sideFilter) {
            params.append('side_id', sideFilter);
        }
        
        if (params.toString()) {
            url += '?' + params.toString();
        }
        
        console.log('Fetching with filters:', url);
        const response = await fetch(url);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        console.log('Path structure loaded:', data);
        
        pathStructure = data.structure || [];
        
        if (pathStructure.length === 0) {
            console.warn('No path structure found in response');
        }
        
        renderPathTree(pathStructure);
        
    } catch (error) {
        console.error('Error loading path structure:', error);
        showError();
    }
}

// 🚀 FIXED: Render path tree with null check and state restoration
function renderPathTree(structure) {
    const tree = document.getElementById('pathTree');
    if (!tree) {
        console.warn('Path tree element not found');
        return;
    }
    
    if (!structure || structure.length === 0) {
        tree.innerHTML = `
            <div class="empty-state">
                <i class="bi bi-inbox"></i>
                <h3>${translations.noPathsFound}</h3>
                <p>${translations.addFilesToSeeStructure}</p>
            </div>
        `;
        return;
    }
    
    // Load expanded paths from localStorage
    loadExpandedPaths();
    
    tree.innerHTML = '';
    structure.forEach(node => {
        const li = createPathNode(node);
        tree.appendChild(li);
    });
    
    // Restore expanded state after rendering
    restoreExpandedState();
    
    // Auto-select the first root path if no path is currently selected
    if (!selectedPath && structure.length > 0) {
        const firstNode = structure[0];
        const firstPathItem = tree.querySelector('.path-item');
        if (firstPathItem && firstNode) {
            console.log('Auto-selecting first path:', firstNode.name);
            // Small delay to ensure DOM is ready
            setTimeout(() => {
                selectPath(firstNode, firstPathItem);
            }, 100);
        }
    }
}

// Create path node with expand/collapse
function createPathNode(node, depth = 0) {
    const li = document.createElement('li');
    li.className = 'path-node';
    
    // Store path identifier for state persistence
    const fullPath = node.fullPath || node.full_path || node.name;
    li.setAttribute('data-path', fullPath);
    
    const item = document.createElement('div');
    item.className = 'path-item';
    
    const hasChildren = node.children && node.children.length > 0;
    
    // Create expand button if node has children
    // Use appropriate chevron icon based on text direction (RTL/LTR)
    let expandBtnHtml = '';
    if (hasChildren) {
        const htmlElement = document.documentElement;
        const isRTL = htmlElement.getAttribute('dir') === 'rtl';
        const chevronIcon = isRTL ? 'bi-chevron-left' : 'bi-chevron-right';
        expandBtnHtml = `<span class="expand-btn" data-path="${fullPath}"><i class="bi ${chevronIcon}"></i></span>`;
    } else {
        expandBtnHtml = `<span style="width: 20px;"></span>`;
    }
    
    // 🚀 FIXED: Handle both camelCase and snake_case property names
    const isArchive = node.isArchive || node.type === 'archive';
    const fileCount = node.fileCount || node.file_count || 0;
    const iconClass = isArchive ? 'bi-file-earmark-zip' : 'bi-folder';
    const iconColor = isArchive ? 'color: var(--warning-color);' : '';  // Orange for archives
    
    item.innerHTML = `
        ${expandBtnHtml}
        <i class="bi ${iconClass} icon" style="${iconColor}"></i>
        <span class="path-name">${escapeHtml(node.name)}</span>
        <span class="path-stats">${fileCount}</span>
    `;
    
    // Add click handler for expand button (if it exists)
    if (hasChildren) {
        const expandBtn = item.querySelector('.expand-btn');
        if (expandBtn) {
            expandBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                toggleNode(e);
            });
        }
    }
    
    // Add click handler for selecting the path (but not on expand button)
    item.addEventListener('click', function(e) {
        if (!e.target.closest('.expand-btn')) {
            e.preventDefault();
            e.stopPropagation();
            selectPath(node, item);
        }
    });
    
    li.appendChild(item);
    
    // Add children if any
    if (hasChildren) {
        const childrenUl = document.createElement('ul');
        childrenUl.className = 'path-children';
        
        // Filter out children that have the same path as the parent (prevents duplicates)
        // Normalize paths by removing trailing slashes and converting to consistent format
        const normalizePath = (path) => {
            if (!path) return '';
            return path.toString().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
        };
        
        const normalizedParentPath = normalizePath(fullPath);
        const filteredChildren = node.children.filter(child => {
            const childPath = child.fullPath || child.full_path || child.name;
            const normalizedChildPath = normalizePath(childPath);
            // Exclude if paths match (after normalization) or if child path is empty/invalid
            return normalizedChildPath && normalizedChildPath !== normalizedParentPath;
        });
        
        filteredChildren.forEach(child => {
            const childLi = createPathNode(child, depth + 1);
            childrenUl.appendChild(childLi);
        });
        
        li.appendChild(childrenUl);
    }
    
    return li;
}

// Toggle node expand/collapse
function toggleNode(event) {
    event.stopPropagation();
    
    const expandBtn = event.currentTarget;
    const pathNode = expandBtn.closest('.path-node');
    const children = pathNode.querySelector('.path-children');
    
    if (children) {
        const isExpanding = !children.classList.contains('expanded');
        children.classList.toggle('expanded');
        expandBtn.classList.toggle('expanded');
        
        // Save state to localStorage
        const path = expandBtn.getAttribute('data-path') || pathNode.getAttribute('data-path');
        if (path) {
            if (isExpanding) {
                expandedPaths.add(path);
            } else {
                expandedPaths.delete(path);
            }
            saveExpandedPaths();
        }
    }
}

// Expand all folders
function expandAll() {
    console.log('Expanding all folders...');
    expandedPaths.clear();
    
    document.querySelectorAll('.path-node').forEach(node => {
        const path = node.getAttribute('data-path');
        const children = node.querySelector('.path-children');
        const expandBtn = node.querySelector('.expand-btn');
        
        if (children && expandBtn) {
            children.classList.add('expanded');
            expandBtn.classList.add('expanded');
            if (path) {
                expandedPaths.add(path);
            }
        }
    });
    
    saveExpandedPaths();
}

// Collapse all folders
function collapseAll() {
    console.log('Collapsing all folders...');
    expandedPaths.clear();
    
    document.querySelectorAll('.path-children').forEach(children => {
        children.classList.remove('expanded');
    });
    document.querySelectorAll('.expand-btn').forEach(btn => {
        btn.classList.remove('expanded');
    });
    
    saveExpandedPaths();
}

// Save expanded paths to localStorage
function saveExpandedPaths() {
    try {
        const pathsArray = Array.from(expandedPaths);
        localStorage.setItem('pathAnalysis_expandedPaths', JSON.stringify(pathsArray));
    } catch (e) {
        console.warn('Failed to save expanded paths to localStorage:', e);
    }
}

// Load expanded paths from localStorage
function loadExpandedPaths() {
    try {
        const saved = localStorage.getItem('pathAnalysis_expandedPaths');
        if (saved) {
            try {
                const pathsArray = JSON.parse(saved);
                expandedPaths = new Set(pathsArray);
            } catch (e) {
                console.warn('Failed to parse saved expanded paths:', e);
                expandedPaths = new Set();
            }
        } else {
            expandedPaths = new Set();
        }
    } catch (e) {
        console.warn('Failed to load expanded paths from localStorage:', e);
        expandedPaths = new Set();
    }
}

// Restore expanded state after tree is rendered
function restoreExpandedState() {
    if (expandedPaths.size === 0) return;
    
    // Small delay to ensure DOM is ready
    setTimeout(() => {
        document.querySelectorAll('.path-node').forEach(node => {
            const path = node.getAttribute('data-path');
            if (path && expandedPaths.has(path)) {
                const children = node.querySelector('.path-children');
                const expandBtn = node.querySelector('.expand-btn');
                
                if (children && expandBtn) {
                    children.classList.add('expanded');
                    expandBtn.classList.add('expanded');
                }
            }
        });
    }, 50);
}

// Select path - INSTANT UPDATE without page refresh
async function selectPath(node, itemElement) {
    // This function is called from click handlers which already prevent default
    // No need to access event here as it's handled by the event listener
    
    // Cancel any pending requests for the previous path
    cancelAllPendingRequests();
    
    // Destroy all existing charts immediately
    destroyAllCharts();
    
    // Set the new selected path and track it
    selectedPath = node;
    const fullPath = node.fullPath || node.full_path || '';
    
    // Normalize path: ensure forward slashes and handle empty paths
    // IMPORTANT: Preserve :: separator for archive paths
    let queryPath = '';
    if (fullPath) {
        // Normalize to forward slashes (API expects this format)
        // But preserve :: separator for archive paths
        queryPath = fullPath.replace(/\\/g, '/');
        
        // For archive paths, only remove trailing slash from the inner path part
        // Regular paths: remove trailing slash
        // Archive paths: preserve :: but remove trailing slash from inner path
        if (queryPath.includes('::')) {
            // Archive path: "base/path/archive.zip::inner/path/"
            const parts = queryPath.split('::');
            if (parts.length === 2) {
                const base = parts[0].replace(/\/$/, '');
                const inner = parts[1].replace(/\/$/, '');
                queryPath = inner ? `${base}::${inner}` : base;
            }
        } else {
            // Regular path: remove trailing slash
            queryPath = queryPath.replace(/\/$/, '');
        }
    } else if (node.name) {
        // Fallback: if fullPath is missing, try to use just the name
        // This should rarely happen if the API provides fullPath correctly
        console.warn('Node missing fullPath, attempting to use name:', node.name);
        queryPath = node.name.replace(/\\/g, '/').replace(/\/$/, '');
    }
    
    // Validate we have a path
    if (!queryPath || queryPath.trim() === '') {
        console.error('Cannot determine path for node:', node);
        return; // Don't proceed if we don't have a valid path
    }
    
    currentLoadingPath = queryPath;
    
    console.log('Selected path node:', node);
    console.log('Full path from node:', fullPath);
    console.log('Normalized query path:', queryPath);
    
    // Update active state IMMEDIATELY (no delay)
    document.querySelectorAll('.path-item').forEach(item => item.classList.remove('active'));
    if (itemElement) {
        itemElement.classList.add('active');
    }
    
    // Show details card, hide empty state IMMEDIATELY
    const pathDetailsCard = document.getElementById('pathDetailsCard');
    const emptyState = document.getElementById('emptyState');
    if (pathDetailsCard) pathDetailsCard.style.display = 'block';
    if (emptyState) emptyState.style.display = 'none';
    
    // 🚀 FIXED: Handle both camelCase and snake_case property names
    const fileCount = node.fileCount || node.file_count || 0;
    const processedCount = node.processedCount || node.processed_count || 0;
    const totalSize = node.totalSize || node.total_size || 0;
    const isArchive = node.isArchive || node.type === 'archive';
    
    // Update path details - show full path hierarchy IMMEDIATELY
    const pathParts = fullPath ? fullPath.split('/') : [node.name];
    const pathTitleEl = document.getElementById('pathTitle');
    if (pathTitleEl) pathTitleEl.textContent = node.name;
    
    // Create breadcrumb with appropriate icon
    const titleIcon = isArchive ? 'bi-file-earmark-zip' : 'bi-folder';
    const titleColor = isArchive ? 'color: var(--warning-color);' : '';
    const htmlElement = document.documentElement;
    const isRTL = htmlElement.getAttribute('dir') === 'rtl';
    const breadcrumbChevron = isRTL ? 'bi-chevron-left' : 'bi-chevron-right';
    let breadcrumbHtml = `<i class="bi ${titleIcon}" style="${titleColor}"></i>`;
    pathParts.forEach((part, index) => {
        if (index > 0) {
            breadcrumbHtml += ` <i class="bi ${breadcrumbChevron}" style="font-size: 0.75rem; opacity: 0.5;"></i> `;
        }
        breadcrumbHtml += `<span style="${index === pathParts.length - 1 ? 'font-weight: 600;' : ''}">${part}</span>`;
    });
    const pathBreadcrumbEl = document.getElementById('pathBreadcrumb');
    if (pathBreadcrumbEl) pathBreadcrumbEl.innerHTML = breadcrumbHtml;
    
    // Update statistics IMMEDIATELY (from node data, no API call needed)
    const pathFileCountEl = document.getElementById('pathFileCount');
    if (pathFileCountEl) pathFileCountEl.textContent = fileCount.toLocaleString();
    const pathProcessedCountEl = document.getElementById('pathProcessedCount');
    if (pathProcessedCountEl) pathProcessedCountEl.textContent = processedCount.toLocaleString();
    const pathTotalSizeEl = document.getElementById('pathTotalSize');
    if (pathTotalSizeEl) pathTotalSizeEl.textContent = formatFileSize(totalSize);
    
    const processingRate = fileCount > 0 ? 
        ((processedCount / fileCount) * 100).toFixed(1) : 0;
    const pathProcessingRateEl = document.getElementById('pathProcessingRate');
    if (pathProcessingRateEl) pathProcessingRateEl.textContent = processingRate + '%';
    
    // Show loading states immediately for all sections (INSTANT FEEDBACK)
    showLoadingStates();
    
    // Load all data for this path (in parallel) - NO PAGE REFRESH
    // These are async and will update the UI as data arrives
    Promise.all([
        loadPathAnalytics(queryPath),
        loadPathWords(queryPath),
        loadPathFiles(queryPath),
        loadPathClassifications(queryPath),
        loadCategoryWordsAnalysis(queryPath)
    ]).catch(error => {
        console.error('Error loading path data:', error);
    });
}

// Build path from node hierarchy (fallback if fullPath is missing)
function buildPathFromNode(node) {
    if (!node) return '';
    
    // If we have a name but no fullPath, try to reconstruct from breadcrumb
    // This is a fallback - the API should always provide fullPath
    if (node.name) {
        console.warn('Node missing fullPath, using name only:', node.name);
        return node.name;
    }
    
    return '';
}

// Cancel all pending requests
function cancelAllPendingRequests() {
    Object.keys(abortControllers).forEach(key => {
        if (abortControllers[key]) {
            abortControllers[key].abort();
            delete abortControllers[key];
        }
    });
}

// Destroy all charts
function destroyAllCharts() {
    Object.keys(charts).forEach(key => {
        if (charts[key]) {
            try {
                charts[key].destroy();
            } catch (e) {
                console.warn('Error destroying chart:', key, e);
            }
            delete charts[key];
        }
    });
}

// Show loading states for all data sections
function showLoadingStates() {
    // Show loading for charts - preserve canvas and use overlay
    const chartConfigs = [
        { id: 'fileTypeChart', height: '600px' },
        { id: 'statusChart', height: '600px' },
        { id: 'timelineChart', height: '700px' },
        { id: 'wordFrequencyChart', height: '600px' },
        { id: 'classificationChart', height: '650px' }
    ];
    
    chartConfigs.forEach(config => {
        const canvas = document.getElementById(config.id);
        const container = canvas ? canvas.parentElement : null;
        
        if (container) {
            // Remove any existing loading overlay
            const existingLoading = container.querySelector('.chart-loading-overlay');
            if (existingLoading) {
                existingLoading.remove();
            }
            
            // Hide canvas but keep it in DOM
            if (canvas) {
                canvas.style.display = 'none';
                canvas.style.opacity = '0';
            }
            
            // Add loading overlay
            const loadingOverlay = document.createElement('div');
            loadingOverlay.className = 'chart-loading-overlay';
            loadingOverlay.style.cssText = `
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                background: var(--bg-section, #fff);
                z-index: 10;
                height: ${config.height};
            `;
            loadingOverlay.innerHTML = `
                <div class="spinner"></div>
                <p style="margin-top: 1rem; color: var(--text-light, #666);">${translations.loading}</p>
            `;
            
            // Don't override CSS with inline styles - use CSS classes instead
            if (!container.classList.contains('chart-container-positioned')) {
                container.classList.add('chart-container-positioned');
            }
            
            container.appendChild(loadingOverlay);
        } else {
            // Canvas doesn't exist, find container and create canvas with loading state
            const chartCards = document.querySelectorAll('.chart-card');
            let chartContainer = null;
            
            // Try to find the right container by looking for chart titles
            const titleMap = {
                'fileTypeChart': 'File Type Distribution',
                'statusChart': 'Processing Status',
                'timelineChart': 'File Creation Timeline',
                'wordFrequencyChart': 'Top Keywords',
                'classificationChart': 'Classification Distribution'
            };
            
            for (let card of chartCards) {
                const h3 = card.querySelector('h3');
                if (h3 && h3.textContent.includes(titleMap[config.id] || '')) {
                    chartContainer = card.querySelector('.chart-container');
                    if (chartContainer) break;
                }
            }
            
            // Fallback: use container by index if title matching fails
            if (!chartContainer && chartCards[chartConfigs.indexOf(config)]) {
                chartContainer = chartCards[chartConfigs.indexOf(config)].querySelector('.chart-container');
            }
            
            if (chartContainer) {
                // Clear any existing content and create canvas with loading overlay
                const canvas = document.createElement('canvas');
                canvas.id = config.id;
                canvas.style.display = 'none';
                canvas.style.opacity = '0';
                
                const loadingOverlay = document.createElement('div');
                loadingOverlay.className = 'chart-loading-overlay';
                // Use CSS classes instead of inline styles for better responsive behavior
                loadingOverlay.className = 'chart-loading-overlay chart-loading-overlay-responsive';
                // Only set essential dynamic styles
                loadingOverlay.style.cssText = `
                    position: absolute;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    background: var(--bg-section, #fff);
                    z-index: 10;
                `;
                loadingOverlay.innerHTML = `
                    <div class="spinner"></div>
                    <p style="margin-top: 1rem; color: var(--text-light, #666);">${translations.loading}</p>
                `;
                
                chartContainer.innerHTML = '';
                chartContainer.appendChild(canvas);
                chartContainer.appendChild(loadingOverlay);
                
                // Don't override CSS with inline styles - use CSS classes instead
                if (!chartContainer.classList.contains('chart-container-positioned')) {
                    chartContainer.classList.add('chart-container-positioned');
                }
            }
        }
    });
    
    // Show loading for word cloud
    const wordCloud = document.getElementById('wordCloud');
    if (wordCloud) {
        wordCloud.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.loadingKeywords}</p></div>`;
    }
    
    // Show loading for files
    const filesGrid = document.getElementById('filesGrid');
    if (filesGrid) {
        filesGrid.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.loadingFiles}</p></div>`;
    }
    
    // Reset classification details - show loading state
    const classificationDetails = document.getElementById('classificationDetails');
    if (classificationDetails) {
        classificationDetails.innerHTML = '<div class="loading-state" style="padding: 2rem;"><div class="spinner"></div><p>' + translations.loading + '</p></div>';
    }
    
    // Reset category words details - show loading state
    const categoryWordsDetails = document.getElementById('categoryWordsDetails');
    if (categoryWordsDetails) {
        categoryWordsDetails.innerHTML = '<div class="loading-state" style="padding: 2rem;"><div class="spinner"></div><p>' + translations.loading + '</p></div>';
    }
    
    // Reset summary counts
    const totalFilesEl = document.getElementById('totalFilesCount');
    if (totalFilesEl) totalFilesEl.textContent = '0';
    const categorizedFilesEl = document.getElementById('categorizedFilesCount');
    if (categorizedFilesEl) categorizedFilesEl.textContent = '0';
    const uncategorizedFilesEl = document.getElementById('uncategorizedFilesCount');
    if (uncategorizedFilesEl) uncategorizedFilesEl.textContent = '0';
    const totalCategoriesEl = document.getElementById('totalCategoriesCount');
    if (totalCategoriesEl) totalCategoriesEl.textContent = '0';
    const totalCategoryWordsEl = document.getElementById('totalCategoryWordsCount');
    if (totalCategoryWordsEl) totalCategoryWordsEl.textContent = '0';
    const totalCategoryFilesEl = document.getElementById('totalCategoryFilesCount');
    if (totalCategoryFilesEl) totalCategoryFilesEl.textContent = '0';
}

// Load analytics for path
async function loadPathAnalytics(pathName) {
    // Check if this request is still relevant
    if (currentLoadingPath !== pathName) {
        console.log('Ignoring stale analytics response for:', pathName);
        return;
    }
    
    // Validate path
    if (!pathName || pathName.trim() === '') {
        console.warn('Empty path provided to loadPathAnalytics');
        showChartError('fileTypeChart');
        showChartError('statusChart');
        showChartError('timelineChart');
        return;
    }
    
    // Cancel previous analytics request if any
    if (abortControllers.analytics) {
        abortControllers.analytics.abort();
    }
    
    // Create new AbortController for this request
    abortControllers.analytics = new AbortController();
    const signal = abortControllers.analytics.signal;
    
    try {
        // Normalize path before sending
        const normalizedPath = pathName.replace(/\\/g, '/').replace(/\/$/, '');
        const queryString = buildQueryString({ path: normalizedPath });
        const url = `/api/analytics/path/analytics?${queryString}`;
        console.log('Fetching analytics from:', url);
        const response = await fetch(url, { signal });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Double-check this is still the current path
        if (currentLoadingPath !== pathName) {
            console.log('Ignoring stale analytics response for:', pathName);
            return;
        }
        
        console.log('Analytics data received for path:', pathName, data);
        
        // Validate response structure
        if (!data || typeof data !== 'object') {
            console.error('Invalid analytics response format:', data);
            showChartError('fileTypeChart');
            showChartError('statusChart');
            showChartError('timelineChart');
            return;
        }
        
        // Check for error in response
        if (data.error) {
            console.error('API returned error:', data.error);
            showChartError('fileTypeChart');
            showChartError('statusChart');
            showChartError('timelineChart');
            return;
        }
        
        // Render file type chart (always call, let function handle empty data)
        const typeDist = data.typeDistribution || [];
        console.log('Rendering file type chart with', typeDist.length, 'types');
        renderFileTypeChart(typeDist);
        
        // Render status chart (always call, let function handle empty data)
        const statusDist = data.statusDistribution || [];
        console.log('Rendering status chart with', statusDist.length, 'statuses');
        renderStatusChart(statusDist);
        
        // Render timeline chart (always call, let function handle empty data)
        const timeline = data.timeline || [];
        console.log('Rendering timeline chart with', timeline.length, 'dates');
        renderTimelineChart(timeline);
        
    } catch (error) {
        // Ignore abort errors
        if (error.name === 'AbortError') {
            console.log('Analytics request cancelled for:', pathName);
            return;
        }
        
        // Only show error if this is still the current path
        if (currentLoadingPath === pathName) {
            console.error('Error loading path analytics:', error);
            // Show error messages in chart areas
            showChartError('fileTypeChart');
            showChartError('statusChart');
            showChartError('timelineChart');
        }
    } finally {
        // Clean up abort controller
        if (abortControllers.analytics && currentLoadingPath === pathName) {
            delete abortControllers.analytics;
        }
    }
}

// Show error in chart area
function showChartError(chartId) {
    const canvas = document.getElementById(chartId);
    const container = canvas ? canvas.parentElement : null;
    if (container) {
        // Remove loading overlay if exists
        const loadingOverlay = container.querySelector('.chart-loading-overlay');
        if (loadingOverlay) {
            loadingOverlay.remove();
        }
        
        const height = chartId === 'timelineChart' ? '700px' : (chartId === 'classificationChart' ? '650px' : '600px');
        container.innerHTML = `
            <div class="empty-state" style="height: ${height};">
                <i class="bi bi-exclamation-triangle"></i>
                <p>${translations.errorLoadingChart}</p>
            </div>
        `;
    }
}

// Render file type chart
function renderFileTypeChart(typeDistribution) {
    // Check if this is still the current path
    if (!selectedPath) {
        return;
    }
    
    // Find or create canvas container
    let canvas = document.getElementById('fileTypeChart');
    let container = canvas ? canvas.parentElement : null;
    
    // If canvas doesn't exist, create it
    if (!canvas || !container) {
        const chartCard = document.querySelector('.chart-card');
        if (chartCard) {
            const chartContainer = chartCard.querySelector('.chart-container');
            if (chartContainer) {
                // Remove loading overlay if exists
                const loadingOverlay = chartContainer.querySelector('.chart-loading-overlay');
                if (loadingOverlay) loadingOverlay.remove();
                
                chartContainer.innerHTML = '<canvas id="fileTypeChart"></canvas>';
                container = chartContainer;
                canvas = document.getElementById('fileTypeChart');
            }
        }
    }
    
    if (!canvas || !container) {
        console.error('File type chart canvas not found');
        return;
    }
    
    // Remove loading overlay
    const loadingOverlay = container.querySelector('.chart-loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.remove();
    }
    
    // Destroy existing chart first
    if (charts.fileType) {
        try {
            charts.fileType.destroy();
        } catch (e) {
            console.warn('Error destroying file type chart:', e);
        }
        charts.fileType = null;
    }
    
    if (!typeDistribution || typeDistribution.length === 0) {
        // Show "no data" message
        container.innerHTML = `<div class="empty-state" style="height: 600px;"><i class="bi bi-pie-chart"></i><p>${translations.noFileTypeData}</p></div>`;
        return;
    }
    
    // Ensure canvas exists in container - recreate if needed
    if (container.querySelector('#fileTypeChart') !== canvas || !canvas) {
        // Remove any existing content except canvas
        const existingCanvas = container.querySelector('#fileTypeChart');
        if (!existingCanvas) {
            container.innerHTML = '<canvas id="fileTypeChart"></canvas>';
            canvas = document.getElementById('fileTypeChart');
        } else {
            canvas = existingCanvas;
        }
        if (!canvas) {
            console.error('Failed to create file type chart canvas');
            return;
        }
    }
    
    // Show canvas
    canvas.style.display = 'block';
    canvas.style.opacity = '1';
    canvas.style.maxWidth = '100%';
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    
    try {
        const ctx = canvas.getContext('2d');
        if (!ctx) {
            console.error('Failed to get 2d context for file type chart');
            return;
        }
        
        // Generate enough colors for all file types using ChartColors utility
        const chartColors = window.ChartColors ? window.ChartColors.getChartColors(Math.max(typeDistribution.length, 10)) : null;
        const colorPalette = chartColors || [
            '#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444',
            '#06b6d4', '#8b5cf6', '#ec4899', '#f97316', '#14b8a6',
            '#3b82f6', '#84cc16', '#f43f5e', '#06b6d4', '#a855f7',
            '#eab308', '#22c55e', '#f97316', '#ec4899', '#6366f1',
            '#8b5cf6', '#ec4899', '#f97316', '#14b8a6', '#3b82f6'
        ];
        
        // Ensure we have enough colors (repeat if needed)
        const colors = [];
        for (let i = 0; i < typeDistribution.length; i++) {
            colors.push(colorPalette[i % colorPalette.length]);
        }
        
        releaseCanvas(ctx);
        charts.fileType = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: typeDistribution.map(t => t.type || translations.unknown),
                datasets: [{
                    data: typeDistribution.map(t => t.count || 0),
                    backgroundColor: colors,
                    borderWidth: 2,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                layout: {
                    padding: {
                        top: 30,
                        right: 30,
                        bottom: 40,
                        left: 30
                    }
                },
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 25,
                            font: {
                                size: 16,
                                weight: '600'
                            },
                            boxWidth: 20,
                            boxHeight: 20
                        }
                    },
                    tooltip: {
                        padding: 12,
                        titleFont: {
                            size: 14,
                            weight: '600'
                        },
                        bodyFont: {
                            size: 13,
                            weight: '500'
                        },
                        backgroundColor: 'rgba(0, 0, 0, 0.85)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                        borderWidth: 2,
                        cornerRadius: 8,
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.parsed || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${label}: ${value} files (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
        
        // Ensure chart is fully rendered and visible
        setTimeout(() => {
            if (charts.fileType) {
                charts.fileType.resize();
                charts.fileType.update('none');
            }
        }, 100);
        
        // Attach export buttons
        if (window.ChartExport && container) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(container);
            }, 100);
        }
    } catch (error) {
        console.error('Error rendering file type chart:', error);
    }
}

// Render status chart
function renderStatusChart(statusDistribution) {
    // Check if this is still the current path
    if (!selectedPath) {
        return;
    }
    
    // Find or create canvas container
    let canvas = document.getElementById('statusChart');
    let container = canvas ? canvas.parentElement : null;
    
    // If canvas doesn't exist, restore it from loading state
    if (!canvas || !container) {
        const chartCards = document.querySelectorAll('.chart-card');
        for (let card of chartCards) {
            const h3 = card.querySelector('h3');
            if (h3 && h3.textContent.includes('Processing Status')) {
                const chartContainer = card.querySelector('.chart-container');
                if (chartContainer) {
                    chartContainer.innerHTML = '<canvas id="statusChart"></canvas>';
                    container = chartContainer;
                    canvas = document.getElementById('statusChart');
                    break;
                }
            }
        }
    }
    
    if (!canvas || !container) {
        console.error('Status chart canvas not found');
        return;
    }
    
    // Remove loading overlay
    const loadingOverlay = container.querySelector('.chart-loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.remove();
    }
    
    if (charts.status) {
        charts.status.destroy();
    }
    
    if (!statusDistribution || statusDistribution.length === 0) {
        // Show "no data" message
        container.innerHTML = `<div class="empty-state" style="height: 600px;"><i class="bi bi-check-circle"></i><p>${translations.noStatusData}</p></div>`;
        return;
    }
    
    // Ensure canvas exists in container
    if (container.querySelector('#statusChart') !== canvas) {
        container.innerHTML = '<canvas id="statusChart"></canvas>';
        canvas = document.getElementById('statusChart');
    }
    
    // Show canvas
    canvas.style.display = 'block';
    canvas.style.opacity = '1';
    canvas.style.maxWidth = '100%';
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    
    try {
        const ctx = canvas.getContext('2d');
        
        // Map status to colors
        const colors = statusDistribution.map(s => {
            const successColor = window.ChartColors ? window.ChartColors.getThemeColors().success : '#10b981';
            const dangerColor = window.ChartColors ? window.ChartColors.getThemeColors().danger : '#ef4444';
            return s.status === translations.read ? successColor : dangerColor;
        });
        
        releaseCanvas(ctx);
        charts.status = new Chart(ctx, {
            type: 'pie',
            data: {
                labels: statusDistribution.map(s => s.status),
                datasets: [{
                    data: statusDistribution.map(s => s.count),
                    backgroundColor: colors,
                    borderWidth: 2,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                layout: {
                    padding: {
                        top: 30,
                        right: 30,
                        bottom: 40,
                        left: 30
                    }
                },
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 25,
                            font: {
                                size: 16,
                                weight: '600'
                            },
                            boxWidth: 20,
                            boxHeight: 20
                        }
                    },
                    tooltip: {
                        padding: 12,
                        titleFont: {
                            size: 14,
                            weight: '600'
                        },
                        bodyFont: {
                            size: 13,
                            weight: '500'
                        },
                        backgroundColor: 'rgba(0, 0, 0, 0.85)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                        borderWidth: 2,
                        cornerRadius: 8,
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.parsed || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${label}: ${value} files (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
        
        // Ensure chart is fully rendered and visible
        setTimeout(() => {
            if (charts.status) {
                charts.status.resize();
                charts.status.update('none');
            }
        }, 100);
        
        // Attach export buttons
        if (window.ChartExport && container) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(container);
            }, 100);
        }
    } catch (error) {
        console.error('Error rendering status chart:', error);
    }
}

// Render timeline chart
function renderTimelineChart(timeline) {
    // Check if this is still the current path
    if (!selectedPath) {
        return;
    }
    
    // Find or create canvas container
    let canvas = document.getElementById('timelineChart');
    let container = canvas ? canvas.parentElement : null;
    
    // If canvas doesn't exist, restore it from loading state
    if (!canvas || !container) {
        const chartCards = document.querySelectorAll('.chart-card');
        for (let card of chartCards) {
            const h3 = card.querySelector('h3');
            if (h3 && h3.textContent.includes('File Creation Timeline')) {
                const chartContainer = card.querySelector('.chart-container');
                if (chartContainer) {
                    chartContainer.innerHTML = '<canvas id="timelineChart"></canvas>';
                    container = chartContainer;
                    canvas = document.getElementById('timelineChart');
                    break;
                }
            }
        }
    }
    
    if (!canvas || !container) {
        console.error('Timeline chart canvas not found');
        return;
    }
    
    // Remove loading overlay
    const loadingOverlay = container.querySelector('.chart-loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.remove();
    }
    
    if (charts.timeline) {
        charts.timeline.destroy();
    }
    
    if (!timeline || timeline.length === 0) {
        // Show "no data" message
        container.innerHTML = `<div class="empty-state" style="height: 700px;"><i class="bi bi-calendar-event"></i><p>${translations.noTimelineData}</p></div>`;
        return;
    }
    
    // Ensure canvas exists in container
    if (container.querySelector('#timelineChart') !== canvas) {
        container.innerHTML = '<canvas id="timelineChart"></canvas>';
        canvas = document.getElementById('timelineChart');
    }
    
    // Show canvas
    canvas.style.display = 'block';
    canvas.style.opacity = '1';
    canvas.style.maxWidth = '100%';
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    
    try {
        const ctx = canvas.getContext('2d');
        
        // Format dates for better display
        const labels = timeline.map(t => {
            const date = new Date(t.date);
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        });
        
        releaseCanvas(ctx);
        charts.timeline = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: translations.filesCreated,
                    data: timeline.map(t => t.count),
                    borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                    backgroundColor: window.ChartColors ? window.ChartColors.getColorWithOpacity(window.ChartColors.getThemeColors().primary, 0.1) : 'rgba(102, 126, 234, 0.1)',
                    tension: 0.4,
                    fill: true,
                    borderWidth: 2,
                    pointRadius: 4,
                    pointBackgroundColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                layout: {
                    padding: {
                        top: 30,
                        right: 30,
                        bottom: 40,
                        left: 30
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: {
                            font: {
                                size: 16,
                                weight: '600'
                            },
                            padding: 20,
                            boxWidth: 20,
                            boxHeight: 20
                        }
                    },
                    tooltip: {
                        padding: 12,
                        titleFont: {
                            size: 14,
                            weight: '600'
                        },
                        bodyFont: {
                            size: 13,
                            weight: '500'
                        },
                        backgroundColor: 'rgba(0, 0, 0, 0.85)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                        borderWidth: 2,
                        cornerRadius: 8,
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            label: function(context) {
                                return `${translations.files}: ${context.parsed.y}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: {
                            font: {
                                size: 12,
                                weight: '500'
                            },
                            padding: 10
                        },
                        title: {
                            display: true,
                            font: {
                                size: 14,
                                weight: '600'
                            }
                        }
                    },
                    y: {
                        beginAtZero: true,
                        ticks: {
                            stepSize: 1,
                            font: {
                                size: 12,
                                weight: '500'
                            },
                            padding: 10
                        },
                        title: {
                            display: true,
                            font: {
                                size: 14,
                                weight: '600'
                            }
                        },
                        grid: {
                            color: 'rgba(0, 0, 0, 0.05)'
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
        
        // Ensure chart is fully rendered and visible
        setTimeout(() => {
            if (charts.timeline) {
                charts.timeline.resize();
                charts.timeline.update('none');
            }
        }, 100);
        
        // Attach export buttons
        if (window.ChartExport && container) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(container);
            }, 100);
        }
    } catch (error) {
        console.error('Error rendering timeline chart:', error);
    }
}

// 🚀 FIXED: Load word frequency for path with null check
async function loadPathWords(pathName) {
    // Check if this request is still relevant
    if (currentLoadingPath !== pathName) {
        console.log('Ignoring stale words response for:', pathName);
        return;
    }
    
    const wordCloud = document.getElementById('wordCloud');
    if (!wordCloud) {
        console.warn('Word cloud element not found');
        return;
    }
    
    // Cancel previous words request if any
    if (abortControllers.words) {
        abortControllers.words.abort();
    }
    
    // Create new AbortController for this request
    abortControllers.words = new AbortController();
    const signal = abortControllers.words.signal;
    
    wordCloud.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.loadingKeywords}</p></div>`;
    
    try {
        // Normalize path before sending
        const normalizedPath = pathName.replace(/\\/g, '/').replace(/\/$/, '');
        console.log('Loading words for path:', normalizedPath);
        const queryString = buildQueryString({ path: normalizedPath, limit: 30 });
        const response = await fetch(`/api/analytics/path/words?${queryString}`, { signal });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Double-check this is still the current path
        if (currentLoadingPath !== pathName) {
            console.log('Ignoring stale words response for:', pathName);
            return;
        }

        // A soft navigation can swap the page out while the request is in
        // flight. The container we started with is then detached, and
        // rendering into it would only produce "container not found" noise.
        if (!wordCloud.isConnected) {
            console.log('Words response arrived after the page was replaced; skipping render for:', pathName);
            return;
        }
        
        console.log('Words data received for path:', pathName, data);
        
        // Validate response structure
        if (!data || typeof data !== 'object') {
            console.error('Invalid words response format:', data);
            wordCloud.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-circle"></i>
                    <h3>${translations.errorLoadingKeywords}</h3>
                    <p>Invalid response format</p>
                </div>
            `;
            return;
        }
        
        // Check for error in response
        if (data.error) {
            console.error('API returned error:', data.error);
            wordCloud.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-circle"></i>
                    <h3>${translations.errorLoadingKeywords}</h3>
                    <p>${data.error}</p>
                </div>
            `;
            return;
        }
        
        if (data.words && data.words.length > 0) {
            console.log('Rendering', data.words.length, 'words');
            renderWordCloud(data.words);
            // Render word frequency chart with top 15 words
            const topWords = data.words.slice(0, 15);
            console.log('Rendering word frequency chart with', topWords.length, 'words');
            renderWordFrequencyChart(topWords);
        } else {
            wordCloud.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-inbox"></i>
                    <h3>${translations.noKeywordsFound}</h3>
                    <p>${translations.noExtractableKeywords}</p>
                </div>
            `;
            // Also clear word frequency chart
            renderWordFrequencyChart([]); // Pass empty array to clear chart
        }
    } catch (error) {
        // Ignore abort errors
        if (error.name === 'AbortError') {
            console.log('Words request cancelled for:', pathName);
            return;
        }
        
        // Only show error if this is still the current path
        if (currentLoadingPath === pathName) {
            console.error('Error loading path words:', error);
            wordCloud.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-circle"></i>
                    <h3>${translations.errorLoadingKeywords}</h3>
                    <p>${error.message}</p>
                </div>
            `;
        }
    } finally {
        // Clean up abort controller
        if (abortControllers.words && currentLoadingPath === pathName) {
            delete abortControllers.words;
        }
    }
}

// 🚀 FIXED: Render word cloud with safety checks
function renderWordCloud(words) {
    const container = document.getElementById('wordCloud');
    if (!container) {
        console.warn('Word cloud container not found');
        return;
    }
    
    if (!words || words.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="bi bi-inbox"></i>
                <h3>${translations.noKeywordsFound}</h3>
            </div>
        `;
        return;
    }
    
    container.innerHTML = '';
    
    // 🚀 FIXED: Handle both camelCase and snake_case property names
    const maxCount = words[0]?.fileCount || words[0]?.file_count || 1;
    
    words.forEach(word => {
        const wordItem = document.createElement('div');
        wordItem.className = 'word-item';
        const fileCount = word.fileCount || word.file_count || 0;
        const wordText = word.word || '';
        
        // Scale font size based on frequency (larger for more frequent words)
        const fontSize = Math.min(0.875 + (fileCount / maxCount) * 0.5, 1.5);
        wordItem.style.fontSize = fontSize + 'rem';
        wordItem.innerHTML = `
            ${escapeHtml(wordText)} <span class="count">(${fileCount})</span>
        `;
        wordItem.onclick = () => {
            // Navigate to search with this word
            if (window.swapNavigate) { window.swapNavigate(`/search?q=${encodeURIComponent(wordText)}`); } else { window.location.href = `/search?q=${encodeURIComponent(wordText)}`; }
        };
        container.appendChild(wordItem);
    });
    
    // Note: Word frequency chart is rendered separately in loadPathWords
}

// 🚀 FIXED: Render word frequency bar chart with property name handling
function renderWordFrequencyChart(words) {
    // Check if this is still the current path
    if (!selectedPath) {
        return;
    }
    
    // Find or create canvas container
    let canvas = document.getElementById('wordFrequencyChart');
    let container = canvas ? canvas.parentElement : null;
    
    // If canvas doesn't exist, restore it from loading state
    if (!canvas || !container) {
        const chartCards = document.querySelectorAll('.chart-card');
        for (let card of chartCards) {
            const h3 = card.querySelector('h3');
            if (h3 && h3.textContent.includes('Top Keywords')) {
                const chartContainer = card.querySelector('.chart-container');
                if (chartContainer) {
                    chartContainer.innerHTML = '<canvas id="wordFrequencyChart"></canvas>';
                    container = chartContainer;
                    canvas = document.getElementById('wordFrequencyChart');
                    break;
                }
            }
        }
    }
    
    if (!canvas || !container) {
        console.error('Word frequency chart canvas not found');
        return;
    }
    
    // Remove loading overlay
    const loadingOverlay = container.querySelector('.chart-loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.remove();
    }
    
    if (charts.wordFrequency) {
        charts.wordFrequency.destroy();
    }
    
    if (!words || words.length === 0) {
        container.innerHTML = `<div class="empty-state" style="height: 600px;"><i class="bi bi-bar-chart"></i><p>${translations.noWordFrequencyData}</p></div>`;
        return;
    }
    
    // Ensure canvas exists in container
    if (container.querySelector('#wordFrequencyChart') !== canvas) {
        container.innerHTML = '<canvas id="wordFrequencyChart"></canvas>';
        canvas = document.getElementById('wordFrequencyChart');
    }
    
    // Show canvas
    canvas.style.display = 'block';
    canvas.style.opacity = '1';
    canvas.style.maxWidth = '100%';
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    
    try {
        const ctx = canvas.getContext('2d');
        // 🚀 FIXED: Handle both camelCase and snake_case property names
        releaseCanvas(ctx);
        charts.wordFrequency = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: words.map(w => w.word || ''),
                datasets: [{
                    label: translations.fileCount,
                    data: words.map(w => w.fileCount || w.file_count || 0),
                    backgroundColor: window.ChartColors ? window.ChartColors.getColorWithOpacity(window.ChartColors.getThemeColors().primary, 0.8) : 'rgba(102, 126, 234, 0.8)',
                    borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                indexAxis: 'y', // Horizontal bars
                layout: {
                    padding: {
                        top: 30,
                        right: 30,
                        bottom: 40,
                        left: 30
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        padding: 12,
                        titleFont: {
                            size: 14,
                            weight: '600'
                        },
                        bodyFont: {
                            size: 13,
                            weight: '500'
                        },
                        backgroundColor: 'rgba(0, 0, 0, 0.85)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                        borderWidth: 2,
                        cornerRadius: 8,
                        callbacks: {
                            label: function(context) {
                                return formatTranslation('appearsInFiles', { count: context.parsed.x });
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: {
                            stepSize: 1,
                            font: {
                                size: 12,
                                weight: '500'
                            },
                            padding: 10
                        },
                        title: {
                            display: true,
                            font: {
                                size: 14,
                                weight: '600'
                            }
                        },
                        grid: {
                            color: 'rgba(0, 0, 0, 0.05)'
                        }
                    },
                    y: {
                        ticks: {
                            font: {
                                size: 12,
                                weight: '500'
                            },
                            padding: 10
                        },
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
        
        // Ensure chart is fully rendered and visible
        setTimeout(() => {
            if (charts.wordFrequency) {
                charts.wordFrequency.resize();
                charts.wordFrequency.update('none');
            }
        }, 100);
        
        // Attach export buttons
        if (window.ChartExport && container) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(container);
            }, 100);
        }
    } catch (error) {
        console.error('Error rendering word frequency chart:', error);
    }
}

// 🚀 FIXED: Load files for a specific path with null check
async function loadPathFiles(pathName) {
    // Check if this request is still relevant
    if (currentLoadingPath !== pathName) {
        console.log('Ignoring stale files response for:', pathName);
        return;
    }
    
    const filesGrid = document.getElementById('filesGrid');
    if (!filesGrid) {
        console.warn('Files grid element not found');
        return;
    }
    
    // Cancel previous files request if any
    if (abortControllers.files) {
        abortControllers.files.abort();
    }
    
    // Create new AbortController for this request
    abortControllers.files = new AbortController();
    const signal = abortControllers.files.signal;
    
    filesGrid.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.loadingFiles}</p></div>`;
    
    try {
        // Normalize path before sending
        const normalizedPath = pathName.replace(/\\/g, '/').replace(/\/$/, '');
        console.log('Loading files for path:', normalizedPath);
        const queryString = buildQueryString({ path: normalizedPath });
        const response = await fetch(`/api/analytics/path/files?${queryString}`, { signal });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Double-check this is still the current path
        if (currentLoadingPath !== pathName) {
            console.log('Ignoring stale files response for:', pathName);
            return;
        }
        
        console.log('Files data received for path:', pathName, data);
        
        // Validate response structure
        if (!data || typeof data !== 'object') {
            console.error('Invalid files response format:', data);
            filesGrid.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-circle"></i>
                    <h3>${translations.errorLoadingFiles}</h3>
                    <p>Invalid response format</p>
                </div>
            `;
            return;
        }
        
        // Check for error in response
        if (data.error) {
            console.error('API returned error:', data.error);
            filesGrid.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-circle"></i>
                    <h3>${translations.errorLoadingFiles}</h3>
                    <p>${data.error}</p>
                </div>
            `;
            return;
        }
        
        if (data.files && data.files.length > 0) {
            console.log(`Rendering ${data.files.length} files`);
            renderFiles(data.files);
        } else {
            console.warn('No files found in this path');
            filesGrid.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-inbox"></i>
                    <h3>${translations.noFilesInPath}</h3>
                    <p>${translations.emptyFolderOrNoMatch}</p>
                </div>
            `;
        }
    } catch (error) {
        // Ignore abort errors
        if (error.name === 'AbortError') {
            console.log('Files request cancelled for:', pathName);
            return;
        }
        
        // Only show error if this is still the current path
        if (currentLoadingPath === pathName) {
            console.error('Error loading path files:', error);
            filesGrid.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-circle"></i>
                    <h3>${translations.errorLoadingFiles}</h3>
                    <p>${error.message}</p>
                </div>
            `;
        }
    } finally {
        // Clean up abort controller
        if (abortControllers.files && currentLoadingPath === pathName) {
            delete abortControllers.files;
        }
    }
}

// Render files
function renderFiles(files) {
    const grid = document.getElementById('filesGrid');
    grid.innerHTML = '';
    
    // Add custom scrollbar styling
    if (!document.getElementById('filesGridScrollbarStyle')) {
        const style = document.createElement('style');
        style.id = 'filesGridScrollbarStyle';
        style.textContent = `
            .files-grid::-webkit-scrollbar {
                width: 8px;
            }
            .files-grid::-webkit-scrollbar-track {
                background: var(--border-light);
                border-radius: 4px;
            }
            .files-grid::-webkit-scrollbar-thumb {
                background: var(--border-dark);
                border-radius: 4px;
            }
            .files-grid::-webkit-scrollbar-thumb:hover {
                background: var(--text-muted);
            }
            .file-card-number {
                position: absolute;
                top: 0.5rem;
                left: 0.5rem;
                background: var(--primary-color);
                color: white;
                width: 24px;
                height: 24px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 0.75rem;
                font-weight: 600;
                z-index: 10;
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            }
            .file-card {
                position: relative;
            }
        `;
        document.head.appendChild(style);
    }
    
    files.forEach((file, index) => {
        const card = document.createElement('div');
        card.className = 'file-card';
        card.style.cursor = 'pointer';
        card.onclick = () => {
            if (window.swapNavigate) { window.swapNavigate(`/file/${file.id}`); } else { window.location.href = `/file/${file.id}`; }
        };
        
        const fileIcon = getFileIcon(file.type);
        const statusClass = file.status === translations.read ? 'read' : 'unread';
        const fileNumber = index + 1;
        
        card.innerHTML = `
            <div class="file-card-number">${fileNumber}</div>
            <div class="file-card-header">
                <div class="file-card-icon">${fileIcon}</div>
                <div class="file-card-info">
                    <div class="file-card-name" title="${escapeAttribute(file.name)}">${escapeHtml(file.name)}</div>
                    <div class="file-card-meta">
                        <span><i class="bi bi-file-earmark"></i> ${escapeHtml(file.type || translations.unknownType)}</span>
                        <span><i class="bi bi-hdd"></i> ${formatFileSize(file.size)}</span>
                    </div>
                </div>
            </div>
            <div class="file-card-meta">
                <span><i class="bi bi-calendar"></i> ${file.created ? new Date(file.created).toLocaleDateString() : translations.nA}</span>
            </div>
            <span class="file-status-badge ${statusClass}">${escapeHtml(file.status)}</span>
        `;
        
        grid.appendChild(card);
    });
    
    // Check if horizontal scrolling is needed and show hint
    setTimeout(() => {
        const filesSection = document.getElementById('filesSection');
        const scrollHint = document.getElementById('scrollHint');
        if (filesSection && scrollHint) {
            const hasScroll = filesSection.scrollWidth > filesSection.clientWidth;
            scrollHint.style.display = hasScroll ? 'block' : 'none';
            
            // Add visual indicator for scrolling
            if (hasScroll) {
                filesSection.classList.add('has-scroll');
            } else {
                filesSection.classList.remove('has-scroll');
            }
        }
    }, 100);
}

// Helper functions
function getFileIcon(type) {
    const iconMap = {
        'pdf': '<i class="bi bi-file-pdf"></i>',
        'doc': '<i class="bi bi-file-word"></i>',
        'docx': '<i class="bi bi-file-word"></i>',
        'xls': '<i class="bi bi-file-excel"></i>',
        'xlsx': '<i class="bi bi-file-excel"></i>',
        'ppt': '<i class="bi bi-file-ppt"></i>',
        'pptx': '<i class="bi bi-file-ppt"></i>',
        'txt': '<i class="bi bi-file-text"></i>',
        'jpg': '<i class="bi bi-file-image"></i>',
        'jpeg': '<i class="bi bi-file-image"></i>',
        'png': '<i class="bi bi-file-image"></i>',
        'gif': '<i class="bi bi-file-image"></i>',
        'mp4': '<i class="bi bi-file-play"></i>',
        'mp3': '<i class="bi bi-file-music"></i>',
        'zip': '<i class="bi bi-file-zip"></i>',
        'rar': '<i class="bi bi-file-zip"></i>'
    };
    return iconMap[type?.toLowerCase()] || '<i class="bi bi-file-earmark"></i>';
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// Load classifications for path
async function loadPathClassifications(pathName) {
    // Check if this request is still relevant
    if (currentLoadingPath !== pathName) {
        console.log('Ignoring stale classifications response for:', pathName);
        return;
    }
    
    // Cancel previous classifications request if any
    if (abortControllers.classifications) {
        abortControllers.classifications.abort();
    }
    
    // Create new AbortController for this request
    abortControllers.classifications = new AbortController();
    const signal = abortControllers.classifications.signal;
    
    try {
        // Normalize path before sending
        const normalizedPath = pathName.replace(/\\/g, '/').replace(/\/$/, '');
        console.log('Loading classifications for path:', normalizedPath);
        const queryString = buildQueryString({ path: normalizedPath });
        const response = await fetch(`/api/analytics/path/classifications?${queryString}`, { signal });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Double-check this is still the current path
        if (currentLoadingPath !== pathName) {
            console.log('Ignoring stale classifications response for:', pathName);
            return;
        }
        
        console.log('Classifications data received for path:', pathName, data);
        
        // Validate response structure
        if (!data || typeof data !== 'object') {
            console.error('Invalid classifications response format:', data);
            const canvas = document.getElementById('classificationChart');
            if (canvas && canvas.parentElement) {
                canvas.parentElement.innerHTML = `
                    <div class="empty-state" style="height: 600px;">
                        <i class="bi bi-exclamation-circle"></i>
                        <p>${translations.errorLoadingClassification}</p>
                    </div>
                `;
            }
            return;
        }
        
        // Check for error in response
        if (data.error) {
            console.error('API returned error:', data.error);
            const canvas = document.getElementById('classificationChart');
            if (canvas && canvas.parentElement) {
                canvas.parentElement.innerHTML = `
                    <div class="empty-state" style="height: 600px;">
                        <i class="bi bi-exclamation-circle"></i>
                        <p>${translations.errorLoadingClassification}: ${data.error}</p>
                    </div>
                `;
            }
            return;
        }
        
        // Update summary
        document.getElementById('totalFilesCount').textContent = data.totalFiles || 0;
        document.getElementById('categorizedFilesCount').textContent = data.categorizedFiles || 0;
        document.getElementById('uncategorizedFilesCount').textContent = data.uncategorizedFiles || 0;
        
        // Always render classification chart (let function handle empty data)
        const classifications = data.classifications || [];
        console.log('Rendering classification chart with', classifications.length, 'classifications');
        renderClassificationChart(classifications);
        
        // Always render classification details table (let function handle empty data)
        renderClassificationDetails(classifications);
    } catch (error) {
        // Ignore abort errors
        if (error.name === 'AbortError') {
            console.log('Classifications request cancelled for:', pathName);
            return;
        }
        
        // Only show error if this is still the current path
        if (currentLoadingPath === pathName) {
            console.error('Error loading classifications:', error);
            const canvas = document.getElementById('classificationChart');
            if (canvas && canvas.parentElement) {
                canvas.parentElement.innerHTML = `
                    <div class="empty-state" style="height: 600px;">
                        <i class="bi bi-exclamation-circle"></i>
                        <p>${translations.errorLoadingClassification}</p>
                    </div>
                `;
            }
        }
    } finally {
        // Clean up abort controller
        if (abortControllers.classifications && currentLoadingPath === pathName) {
            delete abortControllers.classifications;
        }
    }
}

// Render classification chart
function renderClassificationChart(classifications) {
    // Check if this is still the current path
    if (!selectedPath) {
        return;
    }
    
    // Find or create canvas container
    let canvas = document.getElementById('classificationChart');
    let container = canvas ? canvas.parentElement : null;
    
    // If canvas doesn't exist, restore it from loading state
    if (!canvas || !container) {
        const chartCards = document.querySelectorAll('.chart-card');
        for (let card of chartCards) {
            const h3 = card.querySelector('h3');
            if (h3 && h3.textContent.includes('Classification Distribution')) {
                const chartContainer = card.querySelector('.chart-container');
                if (chartContainer) {
                    chartContainer.innerHTML = '<canvas id="classificationChart"></canvas>';
                    container = chartContainer;
                    canvas = document.getElementById('classificationChart');
                    break;
                }
            }
        }
    }
    
    if (!canvas || !container) {
        console.error('Classification chart canvas not found');
        return;
    }
    
    // Remove loading overlay
    const loadingOverlay = container.querySelector('.chart-loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.remove();
    }
    
    if (charts.classification) {
        charts.classification.destroy();
    }
    
    if (!classifications || classifications.length === 0) {
        container.innerHTML = `<div class="empty-state" style="height: 650px;"><i class="bi bi-tags"></i><p>${translations.noClassificationData}</p></div>`;
        return;
    }
    
    // Ensure canvas exists in container
    if (container.querySelector('#classificationChart') !== canvas) {
        container.innerHTML = '<canvas id="classificationChart"></canvas>';
        canvas = document.getElementById('classificationChart');
    }
    
    // Show canvas
    canvas.style.display = 'block';
    canvas.style.opacity = '1';
    canvas.style.maxWidth = '100%';
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    
    try {
        const ctx = canvas.getContext('2d');
        
        // Generate colors for categories using ChartColors utility
        const chartColors = window.ChartColors ? window.ChartColors.getChartColors(Math.max(classifications.length, 10)) : null;
        const colors = chartColors || [
            '#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444',
            '#06b6d4', '#8b5cf6', '#ec4899', '#f97316', '#14b8a6',
            '#3b82f6', '#84cc16', '#f43f5e', '#06b6d4', '#a855f7'
        ];
        
        // Set data attribute for right-side legend
        if (container) {
            container.setAttribute('data-legend-position', 'right');
        }
        
        releaseCanvas(ctx);
        charts.classification = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: classifications.map(c => c.category || translations.unknown),
                datasets: [{
                    data: classifications.map(c => c.fileCount || c.file_count || 0),
                    backgroundColor: colors.slice(0, classifications.length),
                    borderWidth: 2,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                layout: {
                    padding: {
                        top: 30,
                        right: 40,
                        bottom: 40,
                        left: 30
                    }
                },
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            padding: 25,
                            font: {
                                size: 16,
                                weight: '600'
                            },
                            boxWidth: 20,
                            boxHeight: 20,
                            generateLabels: function(chart) {
                                const data = chart.data;
                                return data.labels.map((label, i) => {
                                    const value = data.datasets[0].data[i];
                                    const classification = classifications[i];
                                    const percentage = (classification && classification.percentage) ? classification.percentage.toFixed(1) : '0.0';
                                    return {
                                        text: `${label}: ${value} (${percentage}%)`,
                                        fillStyle: data.datasets[0].backgroundColor[i],
                                        hidden: false,
                                        index: i
                                    };
                                });
                            },
                            maxWidth: 300,
                            textAlign: 'left'
                        }
                    },
                    tooltip: {
                        padding: 12,
                        titleFont: {
                            size: 14,
                            weight: '600'
                        },
                        bodyFont: {
                            size: 13,
                            weight: '500'
                        },
                        backgroundColor: 'rgba(0, 0, 0, 0.85)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                        borderWidth: 2,
                        cornerRadius: 8,
                        callbacks: {
                            label: function(context) {
                                const label = context.label || '';
                                const value = context.parsed || 0;
                                const classification = classifications[context.dataIndex];
                                if (!classification) {
                                    return [`${label}`, `Files: ${value}`];
                                }
                                const totalKeywords = classification.totalKeywords || classification.total_keywords || 0;
                                const percentage = (classification.percentage || 0).toFixed(1);
                                return [
                                    `${label}`,
                                    `Files: ${value}`,
                                    `Keywords: ${totalKeywords}`,
                                    `Percentage: ${percentage}%`
                                ];
                            }
                        }
                    }
                }
            }
        });
        
        // Don't set fixed heights via inline styles - let CSS handle responsive sizing
        // Only resize the chart if needed
        setTimeout(() => {
            if (charts.classification) {
                charts.classification.resize();
            }
        }, 300);
        
        // Attach export buttons
        if (window.ChartExport && container) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(container);
            }, 100);
        }
    } catch (error) {
        console.error('Error rendering classification chart:', error);
    }
}

// Render classification details table
function renderClassificationDetails(classifications) {
    const container = document.getElementById('classificationDetails');
    if (!container) {
        console.warn('Classification details container not found');
        return;
    }
    
    // Handle empty data
    if (!classifications || classifications.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="padding: 2rem; text-align: center;">
                <i class="bi bi-tags" style="font-size: 2rem; color: var(--text-muted); margin-bottom: 1rem;"></i>
                <p style="color: var(--text-light); font-size: 0.875rem;">${translations.noClassificationData}</p>
            </div>
        `;
        return;
    }
    
    let html = `
        <h4 style="margin-bottom: 1rem; color: var(--text-heading); font-size: 1.1rem;">
            <i class="bi bi-list-check"></i> ${translations.detailedBreakdown}
            <span style="font-size: 0.875rem; color: var(--text-light); font-weight: 400; margin-inline-start: 0.5rem;">
                ${translations.clickRowToSeeFiles}
            </span>
        </h4>
        <div style="overflow-x: auto; overflow-y: visible; width: 100%; max-width: 100%; -webkit-overflow-scrolling: touch;">
            <table style="width: 100%; min-width: 600px; border-collapse: collapse; table-layout: auto;">
                <thead>
                    <tr style="background: var(--table-header-bg); border-bottom: 2px solid var(--table-border);">
                        <th style="padding: 0.75rem; text-align: start; font-weight: 600; color: var(--table-header-text);">${translations.category}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.files}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.keywords}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.percentage}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.distribution}</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    classifications.forEach((cat, index) => {
        // Handle both camelCase and snake_case property names
        const categoryName = cat.category || cat.category_name || 'Unknown';
        const fileCount = cat.fileCount || cat.file_count || 0;
        const totalKeywords = cat.totalKeywords || cat.total_keywords || 0;
        const percentage = cat.percentage || 0;
        
        const isUncategorized = categoryName === 'Uncategorized';
        const backgroundColor = index % 2 === 0 ? 'var(--table-row-bg)' : 'var(--bg-section)';
        const categoryColor = isUncategorized ? 'var(--danger-color)' : 'var(--primary-color)';
        
        html += `
            <tr class="category-row-clickable" 
                data-category="${categoryName.replace(/"/g, '&quot;')}"
                style="background: ${backgroundColor}; border-bottom: 1px solid var(--table-border); cursor: pointer; transition: background-color 0.2s;"
                data-on-mouseover="this.style.background='var(--table-row-hover)'"
                data-on-mouseout="this.style.background='${backgroundColor}'">
                <td style="padding: 0.75rem;">
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <i class="bi bi-tag-fill" style="color: ${categoryColor};"></i>
                        <span style="font-weight: 500; word-break: break-word; overflow-wrap: break-word;">${categoryName}</span>
                        <i class="bi bi-box-arrow-up-right" style="font-size: 0.75rem; color: var(--text-muted); margin-inline-start: 0.25rem;"></i>
                    </div>
                </td>
                <td style="padding: 0.75rem; text-align: center; font-weight: 600;">${fileCount}</td>
                <td style="padding: 0.75rem; text-align: center;">${totalKeywords}</td>
                <td style="padding: 0.75rem; text-align: center;">${percentage.toFixed(1)}%</td>
                <td style="padding: 0.75rem;">
                    <div style="background: var(--border-color); border-radius: 0.25rem; height: 20px; overflow: hidden;">
                        <div style="background: ${categoryColor}; height: 100%; width: ${percentage}%; transition: width 0.3s;"></div>
                    </div>
                </td>
            </tr>
        `;
    });
    
    html += `
                </tbody>
            </table>
        </div>
    `;
    
    container.innerHTML = html;
    
    // Add event delegation for category row clicks
    container.querySelectorAll('.category-row-clickable').forEach(row => {
        row.addEventListener('click', function() {
            const category = this.getAttribute('data-category');
            if (category) {
                openCategoryModal(category);
            }
        });
    });
}

// Open category files modal
async function openCategoryModal(category) {
    const modal = document.getElementById('categoryFilesModal');
    const titleElement = document.getElementById('modalCategoryTitle');
    const contentElement = document.getElementById('modalFilesContent');
    
    // Update modal title
    titleElement.innerHTML = formatTranslation('filesInCategory', { category: category });
    
    // Show modal
    modal.classList.add('active');
    
    // Show loading state
    contentElement.innerHTML = `
        <div class="modal-loading">
            <div class="spinner"></div>
            <p>${formatTranslation('loadingFilesFor', { category: escapeHtml(category) })}</p>
        </div>
    `;
    
    try {
        // 🚀 FIXED: Handle both camelCase and snake_case property names
        const currentPath = selectedPath ? (selectedPath.fullPath || selectedPath.full_path || selectedPath.name) : '';
        
        // Fetch files for this category
        const response = await fetch(
            `/api/analytics/path/category-files?path=${encodeURIComponent(currentPath)}&category=${encodeURIComponent(category)}`
        );
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        console.log(`Loaded ${data.files.length} files for category ${category}`);
        
        if (data.files && data.files.length > 0) {
            renderModalFiles(data.files, category);
        } else {
            contentElement.innerHTML = `
                <div class="modal-empty">
                    <i class="bi bi-inbox"></i>
                    <h3>${translations.noFilesFound}</h3>
                    <p>${formatTranslation('noFilesInCategory', { category: escapeHtml(category) })}</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading category files:', error);
        contentElement.innerHTML = `
            <div class="modal-empty">
                <i class="bi bi-exclamation-circle"></i>
                <h3>${translations.errorLoadingFilesModal}</h3>
                <p>${error.message}</p>
            </div>
        `;
    }
}

// Render files in modal
function renderModalFiles(files, category) {
    const contentElement = document.getElementById('modalFilesContent');
    
    let html = `
        <div class="modal-header-info-box">
            <div>
                <div>
                    <strong style="font-size: 1.1rem; color: var(--text-heading);">${files.length}</strong>
                    <span style="color: var(--text-light);"> ${formatTranslation('fileFound', { count: files.length, s: files.length !== 1 ? 's' : '' })}</span>
                </div>
                <div style="font-size: 0.875rem; color: var(--text-light);">
                    ${category === 'Uncategorized' ? 
                        `<i class="bi bi-info-circle"></i> ${translations.theseFilesNoKeywords}` : 
                        `<i class="bi bi-tags"></i> ${translations.classificationBasedOnKeywords}`}
                </div>
            </div>
        </div>
    `;
    
    files.forEach(file => {
        const fileIcon = getFileIcon(file.type);
        const hasKeywords = file.keywords && file.keywords.length > 0;
        
        html += `
            <div class="modal-file-card" data-on-click="window.open('/file/${file.id}', '_blank')">
                <div class="modal-file-header">
                    <div class="modal-file-icon">${fileIcon}</div>
                    <div class="modal-file-info">
                        <div class="modal-file-name">${escapeHtml(file.name)}</div>
                        <div class="modal-file-meta">
                            <span><i class="bi bi-file-earmark"></i> ${escapeHtml(file.type || translations.unknownType)}</span>
                            <span><i class="bi bi-hdd"></i> ${formatFileSize(file.size)}</span>
                            ${file.created ? `<span><i class="bi bi-calendar"></i> ${new Date(file.created).toLocaleDateString()}</span>` : ''}
                            <span><i class="bi bi-circle-fill" style="font-size: 0.5rem; color: ${file.status === translations.read ? 'var(--success-color)' : 'var(--danger-color)'};"></i> ${escapeHtml(file.status)}</span>
                        </div>
                    </div>
                </div>
        `;
        
        if (hasKeywords) {
            html += `
                <div class="modal-keywords">
                    <span style="font-size: 0.875rem; color: var(--text-light); margin-inline-end: 0.5rem;">
                        <i class="bi bi-tags"></i> ${translations.keywords}:
                    </span>
            `;
            
            file.keywords.forEach(keyword => {
                html += `
                    <div class="modal-keyword-badge">
                        ${keyword.word}
                        <span class="modal-keyword-count">(${keyword.count})</span>
                    </div>
                `;
            });
            
            html += `</div>`;
        } else if (category !== 'Uncategorized') {
            html += `
                <div style="padding-top: 0.75rem; border-top: 1px solid var(--border-color); font-size: 0.875rem; color: var(--text-muted);">
                    <i class="bi bi-info-circle"></i> ${translations.noSpecificKeywords}
                </div>
            `;
        }
        
        html += `</div>`;
    });
    
    contentElement.innerHTML = html;
}

// Close category modal
function closeCategoryModal() {
    const modal = document.getElementById('categoryFilesModal');
    if (modal) {
        modal.classList.remove('active');
        modal.style.zIndex = ''; // Reset z-index
    }
}

// Modal wiring is installed from bootPathAnalysisPage() so soft navigation
// (document already complete) still gets the handlers.

// Load category words analysis
async function loadCategoryWordsAnalysis(pathName) {
    // Check if this request is still relevant
    if (currentLoadingPath !== pathName) {
        console.log('Ignoring stale category words response for:', pathName);
        return;
    }
    
    // Cancel previous category words request if any
    if (abortControllers.categoryWords) {
        abortControllers.categoryWords.abort();
    }
    
    // Create new AbortController for this request
    abortControllers.categoryWords = new AbortController();
    const signal = abortControllers.categoryWords.signal;
    
    try {
        // Normalize path before sending
        const normalizedPath = pathName.replace(/\\/g, '/').replace(/\/$/, '');
        console.log('Loading category words analysis for path:', normalizedPath);
        const queryString = buildQueryString({ path: normalizedPath });
        const response = await fetch(`/api/analytics/path/category-words-analysis?${queryString}`, { signal });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Double-check this is still the current path
        if (currentLoadingPath !== pathName) {
            console.log('Ignoring stale category words response for:', pathName);
            return;
        }
        
        console.log('Category words analysis data received for path:', pathName, data);
        
        // Validate response structure
        if (!data || typeof data !== 'object') {
            console.error('Invalid category words response format:', data);
            document.getElementById('categoryWordsDetails').innerHTML = `
                <div class="empty-state" style="padding: 2rem;">
                    <i class="bi bi-exclamation-circle"></i>
                    <p>${translations.errorLoadingCategoryAnalysis}: Invalid response format</p>
                </div>
            `;
            return;
        }
        
        // Check for error in response
        if (data.error) {
            console.error('API returned error:', data.error);
            document.getElementById('categoryWordsDetails').innerHTML = `
                <div class="empty-state" style="padding: 2rem;">
                    <i class="bi bi-exclamation-circle"></i>
                    <p>${translations.errorLoadingCategoryAnalysis}: ${data.error}</p>
                </div>
            `;
            return;
        }
        
        // Update summary
        document.getElementById('totalCategoriesCount').textContent = data.totalCategories || 0;
        document.getElementById('totalCategoryWordsCount').textContent = data.totalWords || 0;
        document.getElementById('totalCategoryFilesCount').textContent = data.totalFiles || 0;
        
        // Always render category words details table (let function handle empty data)
        const categories = data.categories || [];
        console.log('Rendering category words details with', categories.length, 'categories');
        renderCategoryWordsDetails(categories);
    } catch (error) {
        // Ignore abort errors
        if (error.name === 'AbortError') {
            console.log('Category words request cancelled for:', pathName);
            return;
        }
        
        // Only show error if this is still the current path
        if (currentLoadingPath === pathName) {
            console.error('Error loading category words analysis:', error);
            document.getElementById('categoryWordsDetails').innerHTML = `
                <div class="empty-state" style="padding: 2rem;">
                    <i class="bi bi-exclamation-circle"></i>
                    <p>${translations.errorLoadingCategoryAnalysis}</p>
                </div>
            `;
        }
    } finally {
        // Clean up abort controller
        if (abortControllers.categoryWords && currentLoadingPath === pathName) {
            delete abortControllers.categoryWords;
        }
    }
}

// Render category words details table
function renderCategoryWordsDetails(categories) {
    const container = document.getElementById('categoryWordsDetails');
    if (!container) {
        console.warn('Category words details container not found');
        return;
    }
    
    // Handle empty data
    if (!categories || categories.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="padding: 2rem; text-align: center;">
                <i class="bi bi-diagram-3" style="font-size: 2rem; color: var(--text-muted); margin-bottom: 1rem;"></i>
                <p style="color: var(--text-light); font-size: 0.875rem;">${translations.noCategoryData}</p>
            </div>
        `;
        return;
    }
    
    let html = `
        <h4 style="margin-bottom: 1rem; color: var(--text-heading); font-size: 1.1rem;">
            <i class="bi bi-table"></i> ${translations.categoryWordsBreakdown}
            <span style="font-size: 0.875rem; color: var(--text-light); font-weight: 400; margin-inline-start: 0.5rem;">
                ${translations.clickRowToSeeWords}
            </span>
        </h4>
        <div style="overflow-x: auto; overflow-y: visible; width: 100%; max-width: 100%; -webkit-overflow-scrolling: touch;">
            <table style="width: 100%; min-width: 600px; border-collapse: collapse; table-layout: auto;">
                <thead>
                    <tr style="background: var(--table-header-bg); border-bottom: 2px solid var(--table-border);">
                        <th style="padding: 0.75rem; text-align: start; font-weight: 600; color: var(--table-header-text);">${translations.categoryName}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.files}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.words}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.percentage}</th>
                        <th style="padding: 0.75rem; text-align: center; font-weight: 600; color: var(--table-header-text);">${translations.distribution}</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    categories.forEach((cat, index) => {
        // Handle both camelCase and snake_case property names
        const categoryId = cat.categoryId || cat.category_id || 0;
        const categoryName = cat.categoryName || cat.category_name || 'Unknown';
        const fileCount = cat.fileCount || cat.file_count || 0;
        const wordCount = cat.wordCount || cat.word_count || 0;
        const filePercentage = cat.filePercentage || cat.file_percentage || 0;
        
        const backgroundColor = index % 2 === 0 ? 'var(--table-row-bg)' : 'var(--bg-section)';
        const categoryColor = 'var(--primary-color)';
        
        html += `
            <tr class="category-words-row-clickable" 
                data-category-id="${categoryId}"
                data-category-name="${categoryName.replace(/"/g, '&quot;')}"
                style="background: ${backgroundColor}; border-bottom: 1px solid var(--table-border); cursor: pointer; transition: background-color 0.2s;"
                data-on-mouseover="this.style.background='var(--table-row-hover)'"
                data-on-mouseout="this.style.background='${backgroundColor}'">
                <td style="padding: 0.75rem;">
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <i class="bi bi-bookmark-fill" style="color: ${categoryColor};"></i>
                        <span style="font-weight: 500; word-break: break-word; overflow-wrap: break-word;">${categoryName}</span>
                        <i class="bi bi-box-arrow-up-right" style="font-size: 0.75rem; color: var(--text-muted); margin-inline-start: 0.25rem;"></i>
                    </div>
                </td>
                <td style="padding: 0.75rem; text-align: center; font-weight: 600;">${fileCount}</td>
                <td style="padding: 0.75rem; text-align: center;">${wordCount}</td>
                <td style="padding: 0.75rem; text-align: center;">${filePercentage.toFixed(1)}%</td>
                <td style="padding: 0.75rem;">
                    <div style="background: var(--border-color); border-radius: 0.25rem; height: 20px; overflow: hidden;">
                        <div style="background: ${categoryColor}; height: 100%; width: ${filePercentage}%; transition: width 0.3s;"></div>
                    </div>
                </td>
            </tr>
        `;
    });
    
    html += `
                </tbody>
            </table>
        </div>
    `;
    
    container.innerHTML = html;
    
    // Add event delegation for category words row clicks
    container.querySelectorAll('.category-words-row-clickable').forEach(row => {
        row.addEventListener('click', function() {
            const categoryId = this.getAttribute('data-category-id');
            const categoryName = this.getAttribute('data-category-name');
            if (categoryId && categoryName) {
                openCategoryWordsModal(parseInt(categoryId), categoryName);
            }
        });
    });
}

// Store current category context for word files modal
let currentCategoryContext = {
    categoryId: null,
    categoryName: null
};

// Open category words modal
async function openCategoryWordsModal(categoryId, categoryName) {
    const modal = document.getElementById('categoryWordsModal');
    const titleElement = document.getElementById('modalCategoryWordsTitle');
    const contentElement = document.getElementById('modalCategoryWordsContent');
    
    // Store category context for use when opening word files
    currentCategoryContext.categoryId = categoryId;
    currentCategoryContext.categoryName = categoryName;
    
    // Update modal title
    titleElement.innerHTML = formatTranslation('wordsInCategoryTitle', { category: categoryName });
    
    // Show modal
    modal.classList.add('active');
    
    // Set initial z-index for the first modal (default is 10000)
    modal.style.zIndex = '10000';
    
    // Show loading state
    contentElement.innerHTML = `
        <div class="modal-loading">
            <div class="spinner"></div>
            <p>${formatTranslation('loadingWordsFor', { category: escapeHtml(categoryName) })}</p>
        </div>
    `;
    
    try {
        // 🚀 FIXED: Handle both camelCase and snake_case property names
        const currentPath = selectedPath ? (selectedPath.fullPath || selectedPath.full_path || selectedPath.name) : '';
        
        // Fetch words for this category
        const response = await fetch(
            `/api/analytics/path/category-words-detail?category_id=${categoryId}&path=${encodeURIComponent(currentPath)}`
        );
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        console.log(`Loaded ${data.words.length} words for category ${categoryName}`);
        
        if (data.words && data.words.length > 0) {
            renderCategoryWords(data.words, categoryName, categoryId);
        } else {
            contentElement.innerHTML = `
                <div class="modal-empty">
                    <i class="bi bi-inbox"></i>
                    <h3>${translations.noWordsFound}</h3>
                    <p>${formatTranslation('noWordsInCategory', { category: escapeHtml(categoryName) })}</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading category words:', error);
        contentElement.innerHTML = `
            <div class="modal-empty">
                <i class="bi bi-exclamation-circle"></i>
                <h3>${translations.errorLoadingWords}</h3>
                <p>${error.message}</p>
            </div>
        `;
    }
}

// Render category words in modal
function renderCategoryWords(words, categoryName, categoryId) {
    const contentElement = document.getElementById('modalCategoryWordsContent');
    
    let html = `
        <div style="margin-bottom: 1.5rem; padding: 1rem; background: var(--bg-section); border-radius: 0.5rem; border-left: 4px solid var(--primary-color);">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">
                <div>
                    <strong style="font-size: 1.1rem; color: var(--text-heading);">${words.length}</strong>
                    <span style="color: var(--text-light);"> ${formatTranslation('wordsInCategory', { count: words.length, s: words.length !== 1 ? 's' : '' })}</span>
                </div>
                <div style="font-size: 0.875rem; color: var(--text-light);">
                    <i class="bi bi-info-circle"></i> ${translations.clickWordToSeeFiles}
                </div>
            </div>
        </div>
        
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 1rem;">
    `;
    
    words.forEach(word => {
        const usageColor = word.fileCount > 10 ? 'var(--success-color)' : word.fileCount > 5 ? 'var(--warning-color)' : 'var(--text-muted)';
        
        const wordTextEscaped = word.word.replace(/"/g, '&quot;');
        html += `
            <div class="word-card-clickable" 
                 data-word-id="${word.id}"
                 data-word-text="${wordTextEscaped}"
                 data-category-id="${categoryId || ''}"
                 style="background: var(--bg-section); border: 1px solid var(--border-color); border-radius: 0.5rem; padding: 1rem; transition: all 0.2s; cursor: pointer;"
                 data-on-mouseover="this.style.borderColor='var(--primary-color)'; this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 12px color-mix(in srgb, var(--primary-color, rgb(102, 126, 234)) 15%, transparent)';"
                 data-on-mouseout="this.style.borderColor='var(--border-color)'; this.style.transform='translateY(0)'; this.style.boxShadow='none';">
                <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.5rem;">
                    <span style="font-weight: 600; color: var(--text-heading); word-break: break-word;">${word.word}</span>
                    <span style="background: ${usageColor}; color: var(--text-white); padding: 0.125rem 0.5rem; border-radius: 1rem; font-size: 0.75rem; font-weight: 600; flex-shrink: 0; margin-left: 0.5rem;">
                        ${word.fileCount}
                    </span>
                </div>
                <div style="font-size: 0.75rem; color: var(--text-light);">
                    <i class="bi bi-files"></i> ${formatTranslation('foundInFiles', { count: word.fileCount, s: word.fileCount !== 1 ? 's' : '' })}
                </div>
            </div>
        `;
    });
    
    html += `
        </div>
    `;
    
    contentElement.innerHTML = html;
    
    // Add event delegation for word card clicks
    contentElement.querySelectorAll('.word-card-clickable').forEach(card => {
        card.addEventListener('click', function() {
            const wordId = this.getAttribute('data-word-id');
            const wordText = this.getAttribute('data-word-text');
            const catId = this.getAttribute('data-category-id');
            if (wordId && wordText) {
                openWordFilesModal(parseInt(wordId), wordText, catId ? parseInt(catId) : null);
            }
        });
    });
}

// Open word files modal
async function openWordFilesModal(wordId, wordText, categoryId = null) {
    // Use the same modal as category files
    const modal = document.getElementById('categoryFilesModal');
    const titleElement = document.getElementById('modalCategoryTitle');
    const contentElement = document.getElementById('modalFilesContent');
    
    // Check if categoryWordsModal is currently open
    const wordsModal = document.getElementById('categoryWordsModal');
    const isWordsModalOpen = wordsModal && wordsModal.classList.contains('active');
    
    // Use categoryId from parameter or from current context
    const effectiveCategoryId = categoryId || currentCategoryContext.categoryId;
    const categoryName = currentCategoryContext.categoryName || '';
    
    // Update modal title - show category context if available
    if (effectiveCategoryId && categoryName) {
        titleElement.innerHTML = formatTranslation('filesContaining', { word: wordText }) + 
            ` <span style="font-size: 0.875rem; color: var(--text-light); font-weight: 400;">(${translations.inCategory || 'in category'}: ${categoryName})</span>`;
    } else {
        titleElement.innerHTML = formatTranslation('filesContaining', { word: wordText });
    }
    
    // Show modal
    modal.classList.add('active');
    
    // If opening from another modal, set higher z-index to appear above it
    // Default modal z-index is 10000, so we need to set it higher
    if (isWordsModalOpen) {
        // Set the first modal's z-index to 10000 (default)
        wordsModal.style.zIndex = '10000';
        // Set the second modal's z-index higher to appear above
        modal.style.zIndex = '10010';
    } else {
        // Reset to default when not opening from another modal
        modal.style.zIndex = '';
        if (wordsModal) {
            wordsModal.style.zIndex = '';
        }
    }
    
    // Show loading state
    contentElement.innerHTML = `
        <div class="modal-loading">
            <div class="spinner"></div>
            <p>${formatTranslation('loadingFilesContaining', { word: escapeHtml(wordText) })}</p>
        </div>
    `;
    
    try {
        // 🚀 FIXED: Handle both camelCase and snake_case property names
        const currentPath = selectedPath ? (selectedPath.fullPath || selectedPath.full_path || selectedPath.name) : '';
        
        // Build query string with optional category_id
        let queryString = `word_id=${wordId}&path=${encodeURIComponent(currentPath)}`;
        if (effectiveCategoryId) {
            queryString += `&category_id=${effectiveCategoryId}`;
        }
        
        // Fetch files containing this word (with category filter if provided)
        const response = await fetch(
            `/api/analytics/path/word-files?${queryString}`
        );
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        console.log(`Loaded ${data.files.length} files containing "${wordText}"${effectiveCategoryId ? ` (filtered by category ${effectiveCategoryId})` : ''}`);
        
        if (data.files && data.files.length > 0) {
            renderWordFiles(data.files, wordText, data.word, effectiveCategoryId, categoryName);
        } else {
            contentElement.innerHTML = `
                <div class="modal-empty">
                    <i class="bi bi-inbox"></i>
                    <h3>${translations.noFilesFound}</h3>
                    <p>${formatTranslation('noFilesInCategory', { category: escapeHtml(wordText) })}</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading word files:', error);
        contentElement.innerHTML = `
            <div class="modal-empty">
                <i class="bi bi-exclamation-circle"></i>
                <h3>${translations.errorLoadingFilesModal}</h3>
                <p>${error.message}</p>
            </div>
        `;
    }
}

// Render files for a word
function renderWordFiles(files, wordText, wordDisplay, categoryId = null, categoryName = '') {
    const contentElement = document.getElementById('modalFilesContent');
    
    let html = `
        <div class="modal-header-info-box">
            <div>
                <div>
                    <strong style="font-size: 1.1rem; color: var(--text-dark);">${files.length}</strong>
                    <span style="color: var(--text-light);"> ${formatTranslation('filesContain', { count: files.length, s: files.length !== 1 ? 's' : '', word: wordDisplay })}</span>
                    ${categoryId && categoryName ? `
                        <div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;">
                            <i class="bi bi-bookmark-fill" style="color: var(--primary-color);"></i> 
                            ${translations.showingCategoryWords || 'Showing words from category'}: <strong>${categoryName}</strong>
                        </div>
                    ` : ''}
                </div>
                <div style="font-size: 0.875rem; color: var(--text-light);">
                    <i class="bi bi-sort-down"></i> ${translations.sortedByFrequency}
                </div>
            </div>
        </div>
    `;
    
    files.forEach(file => {
        const fileIcon = getFileIcon(file.type);
        
        html += `
            <div class="modal-file-card" data-on-click="window.open('/file/${file.id}', '_blank')">
                <div class="modal-file-header">
                    <div class="modal-file-icon">${fileIcon}</div>
                    <div class="modal-file-info">
                        <div class="modal-file-name">${escapeHtml(file.name)}</div>
                        <div class="modal-file-meta">
                            <span><i class="bi bi-file-earmark"></i> ${escapeHtml(file.type || translations.unknownType)}</span>
                            <span><i class="bi bi-hdd"></i> ${formatFileSize(file.size)}</span>
                            ${file.created ? `<span><i class="bi bi-calendar"></i> ${new Date(file.created).toLocaleDateString()}</span>` : ''}
                            <span><i class="bi bi-circle-fill" style="font-size: 0.5rem; color: ${file.status === translations.read ? 'var(--success-color)' : 'var(--danger-color)'};"></i> ${escapeHtml(file.status)}</span>
                        </div>
                    </div>
                </div>
                <div class="modal-keywords">
                    <span style="font-size: 0.875rem; color: var(--text-light); margin-inline-end: 0.5rem;">
                        <i class="bi bi-file-text"></i> ${formatTranslation('appearsTimesInFile', { count: file.wordCount, s: file.wordCount !== 1 ? 's' : '' })}
                    </span>
                    ${file.words && file.words.length > 0 ? `
                        <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color);">
                            <div style="font-size: 0.75rem; color: var(--text-light); margin-bottom: 0.5rem; font-weight: 500;">
                                <i class="bi bi-tags"></i> 
                                ${categoryId && categoryName ? 
                                    `${translations.categoryWordsInFile || 'Category words in file'}: <strong>${categoryName}</strong>` : 
                                    (translations.wordsInFile || 'Words in file')}:
                            </div>
                            <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
                                ${file.words.slice(0, 20).map(word => `
                                    <span style="display: inline-flex; align-items: center; gap: 0.25rem; padding: 0.25rem 0.5rem; background: var(--border-light); border-radius: 0.375rem; font-size: 0.75rem; color: var(--text-dark);">
                                        <span style="font-weight: 500;">${escapeHtml(word.word)}</span>
                                        <span style="color: var(--text-muted);">(${word.count})</span>
                                    </span>
                                `).join('')}
                                ${file.words.length > 20 ? `<span style="font-size: 0.75rem; color: var(--text-muted); padding: 0.25rem 0.5rem;">+${file.words.length - 20} more</span>` : ''}
                            </div>
                        </div>
                    ` : `
                        <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color); font-size: 0.875rem; color: var(--text-muted);">
                            <i class="bi bi-info-circle"></i> ${translations.noWordsInFile}
                        </div>
                    `}
                </div>
            </div>
        `;
    });
    
    contentElement.innerHTML = html;
}

// Helper function to escape HTML
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Close category words modal
function closeCategoryWordsModal() {
    const modal = document.getElementById('categoryWordsModal');
    if (modal) {
        modal.classList.remove('active');
        modal.style.zIndex = ''; // Reset z-index
    }
}

// Expose functions globally for onclick handlers in templates
if (typeof window !== 'undefined') {
    window.closeCategoryModal = closeCategoryModal;
    window.closeCategoryWordsModal = closeCategoryWordsModal;
}

function showError() {
    const tree = document.getElementById('pathTree');
    if (!tree) return;
    tree.innerHTML = `
        <div class="empty-state">
            <i class="bi bi-exclamation-circle"></i>
            <h3>${translations.errorLoadingPaths}</h3>
            <p>${translations.pleaseTryRefreshing}</p>
        </div>
    `;
}