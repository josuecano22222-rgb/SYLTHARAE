/**
 * Comprehensive Dashboard Page JavaScript
 * Extracted from Analysis/comprehensive_dashboard.html
 */

import { enhanceMultiSelects, refreshMultiSelect } from '../modules/ui/multi-select-dropdown.js';

// Load translations from JSON script tag
let translations = {};

// Track if already initialized to prevent double initialization
let initialized = false;

// Initialize page function
function initializeComprehensiveDashboard() {
    // Prevent double initialization
    if (initialized) {
        return;
    }
    initialized = true;
    
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('comprehensive-dashboard-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing comprehensive dashboard page data:', e);
        }
    }
    
    console.log('Comprehensive dashboard page loaded');
    
    // Initialize page functionality
    initializeTabNavigation();
    loadFilterOptions();
    loadLayout('files');
    // Load dashboard summary statistics
    loadDashboardSummary();
}


// ===== GLOBAL STATE =====
const state = {
    charts: {},
    filterData: {
        categories: [],
        sources: [],
        sides: [],
        keywords: [],
        fileTypes: []
    },
    currentLayout: 'files',
    wordsViewMode: 'grid'
};

// ===== CONFIGURATION =====
const CONFIG = {
    colors: {
        // Use theme-aware chart colors (will be initialized after DOM loads)
        get primary() {
            return window.ChartColors ? window.ChartColors.getChartColors(10) : 
                   ['#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444',
                    '#06b6d4', '#8b5cf6', '#ec4899', '#f97316', '#14b8a6'];
        },
        get success() {
            return window.ChartColors ? window.ChartColors.getThemeColors().success : '#10b981';
        },
        get warning() {
            return window.ChartColors ? window.ChartColors.getThemeColors().warning : '#f59e0b';
        },
        get danger() {
            return window.ChartColors ? window.ChartColors.getThemeColors().danger : '#ef4444';
        },
        get unsorted() {
            return window.ChartColors ? window.ChartColors.getThemeColors().unsorted : '#6c757d';
        }
    },
    chartOptions: {
        animation: { duration: 1500, easing: 'easeInOutQuart' },
        responsive: true,
        maintainAspectRatio: false,
        layout: {
            padding: {
                top: 20,
                right: 20,
                bottom: 30, // Extra space for bottom legends
                left: 20
            },
            autoPadding: true // Let Chart.js calculate padding automatically
        },
        plugins: {
            legend: {
                labels: {
                    font: {
                        size: 14,
                        weight: '500'
                    },
                    padding: 15,
                    boxWidth: 16,
                    usePointStyle: true
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
                titleColor: window.ChartColors ? window.ChartColors.getThemeColors().textWhite || '#fff' : '#fff',
                bodyColor: window.ChartColors ? window.ChartColors.getThemeColors().textWhite || '#fff' : '#fff',
                borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                borderWidth: 2,
                cornerRadius: 8
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
                    },
                    padding: {
                        top: 10
                    }
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
                title: {
                    display: true,
                    font: {
                        size: 14,
                        weight: '600'
                    },
                    padding: {
                        bottom: 10
                    }
                }
            }
        }
    },
    filterMappings: {
        files: {
            combined: { category: 'files-filter-category', source: 'files-filter-source', 
                       side: 'files-filter-side', keyword: 'files-filter-keyword' }
        },
        categories: {
            combined: { source: 'categories-filter-source', side: 'categories-filter-side' }
        },
        keywords: {
            combined: { category: 'keywords-filter-category', source: 'keywords-filter-source', 
                       side: 'keywords-filter-side' }
        },
        sources: {
            combined: { filetype: 'sources-filter-filetype', side: 'sources-filter-side', 
                       keyword: 'sources-filter-keyword' }
        },
        sides: {
            combined: { filetype: 'sides-filter-filetype', category: 'sides-filter-category',
                       keyword: 'sides-filter-keyword' }
        },
        words: {
            grid: { category: 'words-filter-category' }
        },
        similar: {
            combined: { threshold: 'similar-filter-threshold', minsize: 'similar-filter-minsize', type: 'similar-filter-type' }
        }
    }
};

// ===== INITIALIZATION =====
// (Initialization is now handled by the exported init function below)

// ===== TAB NAVIGATION =====
function initializeTabNavigation() {
    const tabButtons = document.querySelectorAll('.tab-button');
    
    tabButtons.forEach(button => {
        button.addEventListener('click', (e) => {
            const layout = button.getAttribute('data-layout');
            switchLayout(layout);
        });
        
        // Keyboard navigation
        button.addEventListener('keydown', (e) => {
            handleTabKeyboard(e, tabButtons);
        });
    });
}

function handleTabKeyboard(e, tabButtons) {
    const currentIndex = Array.from(tabButtons).indexOf(e.target);
    let newIndex;
    
    switch(e.key) {
        case 'ArrowRight':
            e.preventDefault();
            newIndex = (currentIndex + 1) % tabButtons.length;
            tabButtons[newIndex].focus();
            break;
        case 'ArrowLeft':
            e.preventDefault();
            newIndex = currentIndex - 1;
            if (newIndex < 0) newIndex = tabButtons.length - 1;
            tabButtons[newIndex].focus();
            break;
        case 'Home':
            e.preventDefault();
            tabButtons[0].focus();
            break;
        case 'End':
            e.preventDefault();
            tabButtons[tabButtons.length - 1].focus();
            break;
        case 'Enter':
        case ' ':
            e.preventDefault();
            e.target.click();
            break;
    }
}

function switchLayout(layoutName) {
    // Update tabs
    document.querySelectorAll('.tab-button').forEach(btn => {
        const isActive = btn.getAttribute('data-layout') === layoutName;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-selected', isActive);
        btn.setAttribute('tabindex', isActive ? '0' : '-1');
    });
    
    // Update layouts
    document.querySelectorAll('.layout-content').forEach(layout => {
        layout.classList.remove('active');
    });
    
    const layoutElement = document.getElementById(`layout-${layoutName}`);
    if (layoutElement) {
        layoutElement.classList.add('active');
        state.currentLayout = layoutName;
        
        // Wait for layout to become visible, then update any existing charts
        requestAnimationFrame(() => {
            updateChartsInLayout(layoutElement);
            // Load data for the layout
            loadLayout(layoutName);
        });
    }
}

function updateChartsInLayout(layoutElement) {
    // Wait a bit for layout to be fully visible
    setTimeout(() => {
        // Find all canvas elements in the layout
        const canvases = layoutElement.querySelectorAll('canvas');
        canvases.forEach(canvas => {
            const chartId = canvas.id;
            if (chartId && state.charts[chartId]) {
                // Update chart to ensure it renders properly now that it's visible
                try {
                    // Check if canvas is visible
                    const isVisible = window.getComputedStyle(canvas).display !== 'none' &&
                                    canvas.offsetWidth > 0 && canvas.offsetHeight > 0;
                    if (isVisible) {
                        state.charts[chartId].resize();
                        state.charts[chartId].update('none');
                    }
                } catch (error) {
                    console.warn(`Error updating chart ${chartId}:`, error);
                }
            }
        });
    }, 100);
}

function loadLayout(layoutName) {
    const [section, mode] = parseLayoutName(layoutName);
    
    switch(section) {
        case 'files':
            loadDataForSection('files', mode);
            break;
        case 'categories':
            loadDataForSection('categories', mode);
            break;
        case 'keywords':
            loadDataForSection('keywords', mode);
            break;
        case 'sources':
            loadDataForSection('sources', mode);
            break;
        case 'sides':
            loadDataForSection('sides', mode);
            break;
        case 'words':
            loadDataForSection('words', 'grid');
            break;
        case 'similar':
            loadDataForSection('similar', 'combined');
            break;
    }
}

function parseLayoutName(layoutName) {
    if (layoutName === 'words') return ['words', 'grid'];
    
    // For combined layouts, the layout name is just the section name
    return [layoutName, 'combined'];
}

// ===== FILTER OPTIONS LOADING =====
async function loadFilterOptions() {
    try {
        await Promise.all([
            loadCategories(),
            loadSources(),
            loadSides(),
            loadKeywords(),
            loadFileTypes()
        ]);
    } catch (error) {
        console.error('Error loading filter options:', error);
    }
}

async function loadCategories() {
    const response = await fetch('/api/categories');
    state.filterData.categories = await response.json();
    
    const selects = [
        'files-filter-category', 'files-chart-filter-category',
        'keywords-filter-category', 'keywords-chart-filter-category',
        'words-filter-category', 'sides-filter-category', 'sides-chart-filter-category'
    ];
    selects.forEach(id => populateSelect(id, state.filterData.categories));
}

async function loadSources() {
    const response = await fetch('/api/sources');
    state.filterData.sources = await response.json();
    
    const selects = [
        'files-filter-source', 'files-chart-filter-source',
        'categories-filter-source', 'categories-chart-filter-source',
        'keywords-filter-source', 'keywords-chart-filter-source'
    ];
    selects.forEach(id => populateSelect(id, state.filterData.sources));
}

async function loadSides() {
    const response = await fetch('/api/sides');
    state.filterData.sides = await response.json();
    
    const selects = [
        'files-filter-side', 'files-chart-filter-side',
        'categories-filter-side', 'categories-chart-filter-side',
        'keywords-filter-side', 'keywords-chart-filter-side',
        'sources-filter-side', 'sources-count-filter-side',
        'sources-size-filter-side', 'sources-rate-filter-side'
    ];
    selects.forEach(id => populateSelect(id, state.filterData.sides));
}

async function loadKeywords() {
    try {
        const response = await fetch('/api/keywords?per_page=100&page=1');
        const data = await response.json();
        
        if (data.success && data.keywords) {
            state.filterData.keywords = data.keywords;
            
            const selects = [
                'files-filter-keyword', 'files-chart-filter-keyword',
                'sources-filter-keyword', 'sources-count-filter-keyword',
                'sources-size-filter-keyword', 'sources-rate-filter-keyword',
                'sides-filter-keyword', 'sides-chart-filter-keyword'
            ];
            selects.forEach(id => populateSelect(id, state.filterData.keywords, 'id', 'text'));
        }
    } catch (error) {
        console.error('Error loading keywords:', error);
        state.filterData.keywords = [];
    }
}

async function loadFileTypes() {
    const response = await fetch('/api/analytics/file-type-distribution');
    const data = await response.json();
    
    if (data.types) {
        state.filterData.fileTypes = data.types;
        
        const selects = [
            'sources-filter-filetype', 'sources-count-filter-filetype',
            'sources-size-filter-filetype', 'sources-rate-filter-filetype',
            'sides-filter-filetype', 'sides-chart-filter-filetype'
        ];
        selects.forEach(id => populateSelect(id, state.filterData.fileTypes, 'type', 'type'));
        enhanceMultiSelects(document, { type: { compact: true } });
    }
}

function populateSelect(selectId, data, valueKey = 'id', textKey = 'name') {
    const select = document.getElementById(selectId);
    if (!select) return;

    const previous = select.multiple
        ? Array.from(select.selectedOptions).map((o) => o.value)
        : [select.value].filter(Boolean);

    const firstOption = (!select.multiple && select.options[0]) ? select.options[0] : null;
    select.innerHTML = '';
    if (firstOption) select.appendChild(firstOption);

    data.forEach(item => {
        const option = document.createElement('option');
        option.value = item[valueKey];
        option.textContent = item[textKey];
        option.selected = previous.includes(String(option.value));
        select.appendChild(option);
    });

    if (select.multiple && (select.classList.contains('msdd-enhance') || select.hasAttribute('data-compact-multi'))) {
        refreshMultiSelect(select);
    }
}

// ===== UNIFIED FILTER & LOAD FUNCTIONS =====
function applyFilters(section, mode) {
    loadDataForSection(section, mode);
}

function resetFilters(section, mode) {
    const filterIds = CONFIG.filterMappings[section][mode];
    Object.values(filterIds).forEach(id => {
        const element = document.getElementById(id);
        if (!element) return;
        if (element.multiple) {
            Array.from(element.options).forEach((opt) => { opt.selected = false; });
            refreshMultiSelect(element);
        } else {
            element.value = '';
        }
    });
    loadDataForSection(section, mode);
}

function getFilterParams(section, mode) {
    const filterIds = CONFIG.filterMappings[section][mode];
    const params = new URLSearchParams();

    Object.entries(filterIds).forEach(([key, id]) => {
        const element = document.getElementById(id);
        if (!element) return;
        let value = '';
        if (element.multiple) {
            value = Array.from(element.selectedOptions).map((o) => o.value).filter(Boolean).join(',');
        } else if (element.value) {
            value = element.value;
        }
        if (!value) return;
        const paramName = key === 'filetype' ? 'file_type' :
                        key === 'keyword' ? 'keyword_id' :
                        key === 'category' ? 'category_id' :
                        key === 'source' ? 'source_id' :
                        key === 'side' ? 'side_id' :
                        key === 'threshold' ? 'similarity_threshold' :
                        key === 'minsize' ? 'min_group_size' :
                        key === 'type' ? 'group_type' : key;
        params.append(paramName, value);
    });
    
    return params;
}

// ===== DATA LOADING =====
async function loadDataForSection(section, mode) {
    const layoutId = `layout-${section}`;
    const layoutElement = document.getElementById(layoutId);
    
    if (!layoutElement || !layoutElement.classList.contains('active')) {
        return;
    }
    
    const params = getFilterParams(section, mode);
    let apiEndpoint;
    
    if (section === 'words') {
        apiEndpoint = `/api/dashboard/words?${params}`;
    } else if (section === 'similar') {
        apiEndpoint = `/api/dashboard/similar-files?${params}`;
    } else {
        apiEndpoint = `/api/dashboard/${section}-filtered?${params}`;
    }
    
    setLoadingState(section, mode, true);
    
    try {
        const response = await fetch(apiEndpoint);
        const data = await response.json();
        
        if (data.success) {
            renderData(section, mode, data);
        } else {
            showError(section, mode, translations.errorLoadingData);
        }
    } catch (error) {
        console.error(`Error loading ${section} ${mode}:`, error);
        showError(section, mode, error.message);
    } finally {
        setLoadingState(section, mode, false);
    }
}

function setLoadingState(section, mode, isLoading) {
    const resultsElement = document.getElementById(`${section}-results`);
    
    if (resultsElement) {
        resultsElement.setAttribute('aria-busy', isLoading);
        if (isLoading) {
            resultsElement.innerHTML = '<div class="skeleton-loader skeleton-table"></div>';
        }
    }
    
    // Set loading for all count elements
    const countIds = [`${section}-count`, `${section}-chart-count`, 
                      `${section}-count-chart-count`, `${section}-size-chart-count`, 
                      `${section}-rate-chart-count`];
    
    countIds.forEach(countId => {
        const countElement = document.getElementById(countId);
        if (countElement) {
            if (isLoading) {
                countElement.textContent = '...';
            }
        }
    });
    
    // Special handling for similar section
    if (section === 'similar') {
        const countElement = document.getElementById('similar-count');
        if (countElement && isLoading) {
            countElement.textContent = '...';
        }
    }
}

function showError(section, mode, message) {
    let resultsId;
    if (section === 'similar') {
        resultsId = 'similar-results';
    } else {
        resultsId = mode === 'table' ? `${section}-results` : null;
    }
    
    if (resultsId) {
        const resultsElement = document.getElementById(resultsId);
        if (resultsElement) {
            resultsElement.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-triangle" aria-hidden="true"></i>
                    <p>${translations.errorLoadingData}: ${message}</p>
                </div>
            `;
        }
    }
}

// ===== DATA RENDERING =====
function renderData(section, mode, data) {
    switch(section) {
        case 'files':
            renderFiles(mode, data);
            break;
        case 'categories':
            renderCategories(mode, data);
            break;
        case 'keywords':
            renderKeywords(mode, data);
            break;
        case 'sources':
            renderSources(mode, data);
            break;
        case 'sides':
            renderSides(mode, data);
            break;
        case 'words':
            renderWords(data);
            break;
        case 'similar':
            renderSimilar(data);
            break;
    }
}

// ===== FILES RENDERING =====
function renderFiles(mode, data) {
    if (!data.file_types || data.file_types.length === 0) {
        showEmptyState('files', mode, translations.noFileTypesFound);
        return;
    }
    
    // Render table
    document.getElementById('files-count').textContent = `${data.file_types.length} ${translations.fileTypesLabel}`;
    document.getElementById('files-results').innerHTML = createTable(
        [translations.fileType, translations.fileCount, translations.totalSize, translations.avgSize],
        data.file_types.map(ft => [
            `<strong>${ft.type || translations.unknown}</strong>`,
            `<span class="badge-count">${ft.count.toLocaleString()}</span>`,
            formatFileSize(ft.total_size),
            formatFileSize(ft.avg_size)
        ])
    );
    
    // Render chart
    document.getElementById('files-chart-count').textContent = `${data.file_types.length} ${translations.fileTypesLabel}`;
    renderBarChart('files-chart', data.file_types.slice(0, 10), {
        labelKey: 'type',
        valueKey: 'count',
        tooltipCallback: (index) => {
            const type = data.file_types[index];
            return [
                `${translations.files}: ${type.count.toLocaleString()}`,
                `${translations.totalSizeLabel}: ${formatFileSize(type.total_size)}`,
                `${translations.avgSizeLabel}: ${formatFileSize(type.avg_size)}`
            ];
        }
    });
}

// ===== CATEGORIES RENDERING =====
function renderCategories(mode, data) {
    if (!data.categories || data.categories.length === 0) {
        showEmptyState('categories', mode, translations.noCategoriesFound);
        return;
    }
    
    // Filter out categories without files (file_count = 0 or null/undefined)
    const validCategories = data.categories.filter(cat => 
        cat && 
        (cat.name !== undefined && cat.name !== null) &&
        (cat.file_count !== undefined && cat.file_count !== null) &&
        (cat.file_count > 0)  // Only show categories that have files
    );
    
    if (validCategories.length === 0) {
        showEmptyState('categories', mode, translations.noCategoriesFound);
        return;
    }
    
    // Sort all categories by file_count descending (largest first)
    const sortedCategories = [...validCategories].sort((a, b) => {
        const fileCountA = a.file_count || 0;
        const fileCountB = b.file_count || 0;
        return fileCountB - fileCountA;  // Descending order
    });
    
    // Render table with sorted categories
    document.getElementById('categories-count').textContent = `${sortedCategories.length} ${translations.categoriesLabel}`;
    document.getElementById('categories-results').innerHTML = createTable(
        [translations.category, translations.fileCount, translations.wordCount, translations.fileDensity],
        sortedCategories.map(cat => [
            `<strong>${cat.name}</strong>`,
            `<span class="badge-count">${cat.file_count.toLocaleString()}</span>`,
            cat.word_count.toLocaleString(),
            `<span class="badge-density">${cat.file_density.toFixed(2)}</span>`
        ])
    );
    
    // Render chart with ALL sorted categories (no limit)
    document.getElementById('categories-chart-count').textContent = `${sortedCategories.length} ${translations.categoriesLabel}`;
    renderBarChart('categories-chart', sortedCategories, {
        labelKey: 'name',
        valueKey: 'file_count',  // Use file_count to show file statistics
        maxLabelLength: 25,
        tooltipCallback: (index) => {
            const cat = sortedCategories[index];
            if (!cat) return [];
            return [
                `${translations.category}: ${cat.name}`,
                `${translations.files}: ${cat.file_count.toLocaleString()}`,
                `${translations.words}: ${cat.word_count.toLocaleString()}`,
                `${translations.density}: ${cat.file_density.toFixed(2)}`
            ];
        }
    });
}

// ===== KEYWORDS RENDERING =====
function renderKeywords(mode, data) {
    if (!data.keywords || data.keywords.length === 0) {
        showEmptyState('keywords', mode, translations.noKeywordsFound);
        // Clear chart if no data
        const chartCanvas = document.getElementById('keywords-chart');
        if (chartCanvas && state.charts['keywords-chart']) {
            state.charts['keywords-chart'].destroy();
            delete state.charts['keywords-chart'];
        }
        return;
    }
    
    // Filter out invalid keywords and keywords without files
    // Only include keywords that have file_count > 0
    const validKeywords = data.keywords.filter(kw => 
        kw && 
        (kw.text !== undefined && kw.text !== null) && 
        (kw.file_count !== undefined && kw.file_count !== null) &&
        (kw.file_count > 0)  // Only show keywords that have files
    );
    
    if (validKeywords.length === 0) {
        showEmptyState('keywords', mode, translations.noKeywordsFound);
        const chartCanvas = document.getElementById('keywords-chart');
        if (chartCanvas && state.charts['keywords-chart']) {
            state.charts['keywords-chart'].destroy();
            delete state.charts['keywords-chart'];
        }
        return;
    }
    
    // Sort all keywords by file_count descending (largest first)
    const sortedKeywords = [...validKeywords].sort((a, b) => {
        const fileCountA = a.file_count || 0;
        const fileCountB = b.file_count || 0;
        return fileCountB - fileCountA;  // Descending order
    });
    
    // Render table with sorted keywords
    document.getElementById('keywords-count').textContent = `${sortedKeywords.length} ${translations.keywordsLabel}`;
    document.getElementById('keywords-results').innerHTML = createTable(
        [translations.keyword, translations.fileCount, translations.wordCount, translations.fileDensity],
        sortedKeywords.map(kw => [
            `<strong>${kw.text || translations.unknown}</strong>`,
            `<span class="badge-count">${(kw.file_count || 0).toLocaleString()}</span>`,
            (kw.word_count || 0).toLocaleString(),
            `<span class="badge-density">${(kw.file_density || 0).toFixed(2)}</span>`
        ])
    );
    
    // Render chart with ALL sorted keywords (no limit)
    if (sortedKeywords.length > 0) {
        document.getElementById('keywords-chart-count').textContent = `${sortedKeywords.length} ${translations.keywordsLabel}`;
        
        // Use file_count for the chart to show file statistics
        renderBarChart('keywords-chart', sortedKeywords, {
            labelKey: 'text',
            valueKey: 'file_count',  // Always use file_count for the chart
            maxLabelLength: 25,
            tooltipCallback: (index) => {
                const kw = sortedKeywords[index];
                if (!kw) return [];
                return [
                    `${translations.keyword}: ${kw.text || translations.unknown}`,
                    `${translations.files}: ${(kw.file_count || 0).toLocaleString()}`,
                    `${translations.words}: ${(kw.word_count || 0).toLocaleString()}`,
                    `${translations.density}: ${(kw.file_density || 0).toFixed(2)}`
                ];
            }
        });
    } else {
        // Clear chart if no valid data
        const chartCanvas = document.getElementById('keywords-chart');
        if (chartCanvas && state.charts['keywords-chart']) {
            state.charts['keywords-chart'].destroy();
            delete state.charts['keywords-chart'];
        }
    }
}

// ===== SOURCES RENDERING =====
function renderSources(mode, data) {
    if (!data.sources || data.sources.length === 0) {
        showEmptyState('sources', mode, translations.noSourcesFound);
        return;
    }
    
    // Render table
    document.getElementById('sources-count').textContent = `${data.sources.length} ${translations.sourcesLabel}`;
    document.getElementById('sources-results').innerHTML = createTable(
        [translations.sourceName, translations.fileCount, translations.totalSize, translations.avgSize, translations.fileTypes, translations.processed, translations.processingRate],
        data.sources.map(src => [
            `<strong>${src.name}</strong>`,
            `<span class="badge-count">${src.file_count.toLocaleString()}</span>`,
            formatFileSize(src.total_size),
            formatFileSize(src.avg_size),
            src.unique_file_types,
            src.processed_files.toLocaleString(),
            `${src.processing_rate.toFixed(1)}%`
        ])
    );
    
    // Render file count chart
    document.getElementById('sources-count-chart-count').textContent = `${data.sources.length} ${translations.sourcesLabel}`;
    renderBarChart('sources-chart-count', data.sources.slice(0, 15), {
        labelKey: 'name',
        valueKey: 'file_count',
        tooltipCallback: (index) => {
            const src = data.sources[index];
            return [
                `${translations.files}: ${src.file_count.toLocaleString()}`,
                `${translations.totalSizeLabel}: ${formatFileSize(src.total_size)}`,
                `${translations.processingRateLabel}: ${src.processing_rate.toFixed(1)}%`
            ];
        }
    });
    
    // Render size chart
    document.getElementById('sources-size-chart-count').textContent = `${data.sources.length} ${translations.sourcesLabel}`;
    renderDoughnutChart('sources-chart-size', data.sources.slice(0, 10), {
        labelKey: 'name',
        valueKey: 'total_size',
        tooltipCallback: (index) => {
            const src = data.sources[index];
            return [
                `${translations.source}: ${src.name}`,
                `${translations.totalSizeLabel}: ${formatFileSize(src.total_size)}`,
                `${translations.files}: ${src.file_count.toLocaleString()}`
            ];
        }
    });
    
    // Render processing rate chart
    document.getElementById('sources-rate-chart-count').textContent = `${data.sources.length} ${translations.sourcesLabel}`;
    const sources = data.sources.slice(0, 15);
    const colors = sources.map(s => 
        s.processing_rate >= 90 ? CONFIG.colors.success :
        s.processing_rate >= 70 ? CONFIG.colors.warning : CONFIG.colors.danger
    );
    renderBarChart('sources-chart-rate', sources, {
        labelKey: 'name',
        valueKey: 'processing_rate',
        customColors: colors,
        yMax: 100,
        yTicksSuffix: '%',
        tooltipCallback: (index) => {
            const src = sources[index];
            return [
                `${translations.processingRateLabel}: ${src.processing_rate.toFixed(1)}%`,
                `${translations.processed}: ${src.processed_files.toLocaleString()} / ${src.file_count.toLocaleString()}`,
                `${translations.totalFiles}: ${src.file_count.toLocaleString()}`
            ];
        }
    });
}

// ===== SIDES RENDERING =====
function renderSides(mode, data) {
    if (!data.sides || data.sides.length === 0) {
        showEmptyState('sides', mode, translations.noSidesFound);
        return;
    }
    
    // Render table
    document.getElementById('sides-count').textContent = `${data.sides.length} ${translations.sidesLabel}`;
    document.getElementById('sides-results').innerHTML = createTable(
        [translations.sideName, translations.importance, translations.fileCount, translations.totalSize, translations.avgSize, translations.fileTypes, translations.sources, translations.processed, translations.processingRate],
        data.sides.map(side => [
            `<strong>${side.name}</strong>`,
            `<span class="badge-count">${side.importance}</span>`,
            `<span class="badge-count">${side.file_count.toLocaleString()}</span>`,
            formatFileSize(side.total_size),
            formatFileSize(side.avg_size),
            side.unique_file_types,
            side.source_count.toLocaleString(),
            side.processed_files.toLocaleString(),
            `${side.processing_rate.toFixed(1)}%`
        ])
    );
    
    // Render chart
    document.getElementById('sides-chart-count').textContent = `${data.sides.length} ${translations.sidesLabel}`;
    renderBarChart('sides-chart', data.sides.slice(0, 15), {
        labelKey: 'name',
        valueKey: 'file_count',
        tooltipCallback: (index) => {
            const side = data.sides[index];
            return [
                `${translations.side}: ${side.name}`,
                `${translations.importance}: ${side.importance}`,
                `${translations.files}: ${side.file_count.toLocaleString()}`,
                `${translations.totalSizeLabel}: ${formatFileSize(side.total_size)}`,
                `${translations.avgSizeLabel}: ${formatFileSize(side.avg_size)}`,
                `${translations.fileTypesLabel2}: ${side.unique_file_types}`,
                `${translations.sources}: ${side.source_count.toLocaleString()}`,
                `${translations.processed}: ${side.processed_files.toLocaleString()} / ${side.file_count.toLocaleString()}`,
                `${translations.processingRateLabel}: ${side.processing_rate.toFixed(1)}%`
            ];
        }
    });
}

// ===== WORDS RENDERING =====
function renderWords(data) {
    if (!data.words || data.words.length === 0) {
        showEmptyState('words', 'grid', translations.noWordsFound);
        return;
    }
    
    if (state.wordsViewMode === 'grid') {
        const wordsGrid = data.words.map(word => 
            `<div class="word-item">
                <div class="word-text">${word.word || word.text || translations.unknown}</div>
                <div class="word-count">${word.file_count || 0} ${translations.filesLabel}</div>
                ${word.category ? `<div class="word-category">${word.category}</div>` : ''}
            </div>`
        ).join('');
        
        document.getElementById('words-results').innerHTML = `
            <div class="words-grid">
                ${wordsGrid}
            </div>
        `;
    } else {
        document.getElementById('words-results').innerHTML = '';
        document.getElementById('words-chart-container').style.display = 'block';
        renderBarChart('words-chart', data.words.slice(0, 20), {
            labelKey: 'word',
            valueKey: 'file_count',
            tooltipCallback: (index) => {
                const word = data.words[index];
                return [`${translations.word}: ${word.word}`, `${translations.files}: ${word.file_count.toLocaleString()}`];
            }
        });
    }
}

function toggleWordsView() {
    state.wordsViewMode = state.wordsViewMode === 'grid' ? 'chart' : 'grid';
    const toggleText = document.getElementById('view-toggle-text');
    const icon = document.querySelector('#toggle-words-view i');
    
    if (state.wordsViewMode === 'chart') {
        toggleText.textContent = translations.gridView;
        icon.className = 'bi bi-grid-3x3-gap';
        document.getElementById('words-results').style.display = 'none';
        document.getElementById('words-chart-container').style.display = 'block';
    } else {
        toggleText.textContent = translations.chartView;
        icon.className = 'bi bi-graph-up';
        document.getElementById('words-results').style.display = 'block';
        document.getElementById('words-chart-container').style.display = 'none';
    }
    
    loadDataForSection('words', 'grid');
}

// ===== SIMILAR FILES RENDERING =====
function renderSimilar(data) {
    if (!data || (!data.hash_groups && !data.title_groups)) {
        showEmptyState('similar', 'combined', translations.noSimilarFilesFound || 'No similar files found');
        return;
    }
    
    const filterType = document.getElementById('similar-filter-type')?.value || '';
    let allGroups = [];
    
    // Add hash groups
    if (data.hash_groups && (!filterType || filterType === 'hash')) {
        allGroups = allGroups.concat(data.hash_groups);
    }
    
    // Add title groups
    if (data.title_groups && (!filterType || filterType === 'title')) {
        allGroups = allGroups.concat(data.title_groups);
    }
    
    if (allGroups.length === 0) {
        showEmptyState('similar', 'combined', translations.noSimilarFilesFound || 'No similar files found');
        return;
    }
    
    // Update count
    const totalGroups = allGroups.length;
    const totalFiles = allGroups.reduce((sum, g) => sum + g.count, 0);
    document.getElementById('similar-count').textContent = `${totalGroups} ${translations.groupsLabel || 'groups'} (${totalFiles} ${translations.filesLabel})`;
    
    // Render groups
    const groupsHtml = allGroups.map(group => {
        const groupTypeLabel = group.group_type === 'hash' 
            ? `<span class="badge badge-hash"><i class="bi bi-hash"></i> ${translations.hashDuplicate || 'Hash Duplicate'}</span>`
            : `<span class="badge badge-title"><i class="bi bi-file-text"></i> ${translations.similarTitle || 'Similar Title'}</span>`;
        
        const groupHeader = group.group_type === 'hash'
            ? `<div class="similar-group-header">
                <div class="similar-group-title">
                    ${groupTypeLabel}
                    <span class="similar-group-count">${group.count} ${translations.filesLabel}</span>
                </div>
                <div class="similar-group-hash">
                    <code>${group.hash ? group.hash.substring(0, 16) + '...' : ''}</code>
                </div>
            </div>`
            : `<div class="similar-group-header">
                <div class="similar-group-title">
                    ${groupTypeLabel}
                    <span class="similar-group-count">${group.count} ${translations.filesLabel}</span>
                    ${group.is_identical ? '<span class="badge badge-identical"><i class="bi bi-check-circle"></i> Identical</span>' : ''}
                </div>
                <div class="similar-group-title-text">"${escapeHtml(group.representative_title || '')}"</div>
            </div>`;
        
        const filesTable = createTable(
            [translations.fileName || 'File Name', translations.fileType || 'Type', translations.fileSize || 'Size', translations.source || 'Source', translations.side || 'Side'],
            group.files.map(file => [
                `<strong>${escapeHtml(file.file_name || file.title || translations.unknown)}</strong>`,
                file.file_type || '-',
                formatFileSize(file.file_size || 0),
                file.source_name || '-',
                file.side_name || '-'
            ])
        );
        
        return `
            <div class="similar-group">
                ${groupHeader}
                ${filesTable}
            </div>
        `;
    }).join('');
    
    document.getElementById('similar-results').innerHTML = `
        <div class="similar-groups-container">
            ${groupsHtml}
        </div>
    `;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ===== CHART HELPERS =====
function renderBarChart(canvasId, data, options = {}) {
    const {
        labelKey = 'name',
        valueKey = 'value',
        customColors = null,
        yMax = null,
        yTicksSuffix = '',
        maxLabelLength = 20,
        tooltipCallback = null
    } = options;
    
    if (state.charts[canvasId]) {
        state.charts[canvasId].destroy();
        delete state.charts[canvasId];
    }
    
    const ctx = document.getElementById(canvasId);
    if (!ctx) {
        console.warn(`Canvas element with id '${canvasId}' not found`);
        return;
    }
    
    // Check if data is empty
    if (!data || data.length === 0) {
        showEmptyChartState(canvasId, translations.noDataAvailable);
        return;
    }
    
    // Ensure canvas is visible (restore if it was hidden by empty state)
    ctx.style.display = '';
    
    // Remove any empty state messages from the container
    const chartContainer = ctx.closest('.chart-container-layout, .chart-container');
    if (chartContainer) {
        const emptyStates = chartContainer.querySelectorAll('.empty-state');
        emptyStates.forEach(state => state.remove());
    }
    
    const isVisible = chartContainer && 
                     window.getComputedStyle(chartContainer).display !== 'none' &&
                     window.getComputedStyle(ctx).display !== 'none';
    
    // If not visible, wait for it to become visible
    if (!isVisible) {
        // Use requestAnimationFrame to wait for next render cycle
        requestAnimationFrame(() => {
            renderBarChart(canvasId, data, options);
        });
        return;
    }
    
    // Ensure canvas has dimensions
    if (ctx.offsetWidth === 0 || ctx.offsetHeight === 0) {
        // Wait a bit for layout to settle
        setTimeout(() => {
            renderBarChart(canvasId, data, options);
        }, 100);
        return;
    }
    
    const labels = data.map(item => truncateLabel(item[labelKey], maxLabelLength));
    // Ensure values are numbers and handle null/undefined
    const values = data.map(item => {
        const val = item[valueKey];
        if (val === null || val === undefined || val === '') return 0;
        const numVal = typeof val === 'string' ? parseFloat(val) : Number(val);
        return isNaN(numVal) ? 0 : numVal;
    });
    const colors = customColors || CONFIG.colors.primary.slice(0, data.length);
    
    // Debug logging for keywords chart
    if (canvasId === 'keywords-chart') {
        console.log('Keywords chart data:', {
            labels: labels.slice(0, 5),
            values: values.slice(0, 5),
            dataLength: data.length,
            valueKey: valueKey
        });
    }
    
    // Check if all values are zero - if so, log a warning
    const maxValue = Math.max(...values);
    if (maxValue === 0 && values.length > 0) {
        console.warn(`All values are zero for chart ${canvasId}. Chart may not display bars.`);
    }
    
    try {
        state.charts[canvasId] = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: valueKey.replace(/_/g, ' ').toUpperCase(),
                    data: values,
                    backgroundColor: colors,
                    borderWidth: 0,
                    borderRadius: 8
                }]
            },
            options: {
                ...CONFIG.chartOptions,
                scales: {
                    x: {
                        ...CONFIG.chartOptions.scales?.x,
                        ticks: {
                            ...CONFIG.chartOptions.scales?.x?.ticks,
                            font: {
                                size: 12,
                                weight: '500'
                            },
                            padding: 10
                        }
                    },
                    y: {
                        beginAtZero: true,
                        max: yMax,
                        ticks: {
                            ...CONFIG.chartOptions.scales?.y?.ticks,
                            callback: (value) => value + yTicksSuffix,
                            font: {
                                size: 12,
                                weight: '500'
                            },
                            padding: 10
                        }
                    }
                },
                plugins: {
                    tooltip: {
                        ...CONFIG.chartOptions.plugins.tooltip,
                        callbacks: {
                            label: (context) => {
                                if (tooltipCallback) {
                                    return tooltipCallback(context.dataIndex);
                                }
                                return `${context.label}: ${context.parsed.y}${yTicksSuffix}`;
                            }
                        }
                    },
                    legend: {
                        display: false
                    }
                }
            }
        });
        
        // Force chart to render properly
        // Use multiple update strategies to ensure rendering
        requestAnimationFrame(() => {
            if (state.charts[canvasId]) {
                try {
                    state.charts[canvasId].resize();
                    state.charts[canvasId].update('none');
                } catch (e) {
                    console.warn(`Error updating chart ${canvasId}:`, e);
                }
            }
        });
        
        // Also update after a short delay to catch any layout changes
        setTimeout(() => {
            if (state.charts[canvasId]) {
                try {
                    state.charts[canvasId].resize();
                    state.charts[canvasId].update('none');
                } catch (e) {
                    console.warn(`Error updating chart ${canvasId} (delayed):`, e);
                }
            }
        }, 200);
        
        // Attach export buttons
        if (window.ChartExport) {
            if (chartContainer) {
                setTimeout(() => {
                    window.ChartExport.attachExportButtonsToCharts(chartContainer);
                }, 100);
            }
        }
    } catch (error) {
        console.error(`Error creating chart for ${canvasId}:`, error);
    }
}

function renderDoughnutChart(canvasId, data, options = {}) {
    const {
        labelKey = 'name',
        valueKey = 'value',
        tooltipCallback = null
    } = options;
    
    if (state.charts[canvasId]) {
        state.charts[canvasId].destroy();
        delete state.charts[canvasId];
    }
    
    const ctx = document.getElementById(canvasId);
    if (!ctx) {
        console.warn(`Canvas element with id '${canvasId}' not found`);
        return;
    }
    
    // Check if data is empty
    if (!data || data.length === 0) {
        showEmptyChartState(canvasId, translations.noDataAvailable);
        return;
    }
    
    // Ensure canvas is visible (restore if it was hidden by empty state)
    ctx.style.display = '';
    
    // Remove any empty state messages from the container
    const chartContainer = ctx.closest('.chart-container-layout, .chart-container');
    if (chartContainer) {
        const emptyStates = chartContainer.querySelectorAll('.empty-state');
        emptyStates.forEach(state => state.remove());
    }
    
    const isVisible = chartContainer && 
                     window.getComputedStyle(chartContainer).display !== 'none' &&
                     window.getComputedStyle(ctx).display !== 'none';
    
    // If not visible, wait for it to become visible
    if (!isVisible) {
        requestAnimationFrame(() => {
            renderDoughnutChart(canvasId, data, options);
        });
        return;
    }
    
    // Ensure canvas has dimensions
    if (ctx.offsetWidth === 0 || ctx.offsetHeight === 0) {
        setTimeout(() => {
            renderDoughnutChart(canvasId, data, options);
        }, 100);
        return;
    }
    
    const labels = data.map(item => item[labelKey]);
    const values = data.map(item => item[valueKey]);
    
    // Determine legend position from options or default to 'right'
    const legendPosition = options.legendPosition || 'right';
    
    // Set data attribute on container for CSS targeting
    if (chartContainer) {
        chartContainer.setAttribute('data-legend-position', legendPosition);
    }
    
    try {
        state.charts[canvasId] = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: CONFIG.colors.primary,
                    borderWidth: 2,
                    borderColor: window.ChartColors ? window.ChartColors.getThemeColors().textWhite || '#fff' : '#fff'
                }]
            },
            options: {
                ...CONFIG.chartOptions,
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: (context) => {
                                if (tooltipCallback) {
                                    return tooltipCallback(context.dataIndex);
                                }
                                return `${context.label}: ${context.parsed}`;
                            }
                        }
                    },
                    legend: {
                        display: true,
                        position: legendPosition,
                        labels: {
                            font: {
                                size: 14,
                                weight: '500'
                            },
                            padding: 15,
                            boxWidth: 16,
                            usePointStyle: true,
                            maxWidth: legendPosition === 'right' ? 250 : undefined // Limit legend width for right position
                        }
                    }
                }
            }
        });
        
        // Update chart after a short delay to ensure proper rendering
        setTimeout(() => {
            if (state.charts[canvasId]) {
                state.charts[canvasId].update('none');
                // After update, check if legend position changed and update container attribute
                const actualLegendPosition = state.charts[canvasId].options.plugins.legend.position;
                if (chartContainer && actualLegendPosition) {
                    chartContainer.setAttribute('data-legend-position', actualLegendPosition);
                }
            }
        }, 50);
        
        // Attach export buttons
        if (window.ChartExport && chartContainer) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(chartContainer);
            }, 100);
        }
    } catch (error) {
        console.error(`Error creating chart for ${canvasId}:`, error);
    }
}

// ===== UTILITY FUNCTIONS =====
function createTable(headers, rows) {
    // If no rows, show empty state
    if (!rows || rows.length === 0) {
        return `
            <div class="empty-state">
                <i class="bi bi-inbox" aria-hidden="true"></i>
                <p>${translations.noDataAvailable}</p>
            </div>
        `;
    }
    
    const headerRow = headers.map(h => `<th>${h}</th>`).join('');
    const bodyRows = rows.map(row => 
        `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`
    ).join('');
    
    return `
        <div class="table-responsive">
            <table class="data-table">
                <thead>
                    <tr>${headerRow}</tr>
                </thead>
                <tbody>
                    ${bodyRows}
                </tbody>
            </table>
        </div>
    `;
}

function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

function truncateLabel(label, maxLength) {
    if (!label) return translations.unknown;
    return label.length > maxLength ? label.substring(0, maxLength) + '...' : label;
}

function showEmptyState(section, mode, message) {
    let resultsId;
    let countId;
    let chartId;
    
    if (section === 'similar') {
        resultsId = 'similar-results';
        countId = 'similar-count';
    } else {
        // For combined mode, we still need to show empty state in the results container
        // The results container exists for all sections regardless of mode
        resultsId = `${section}-results`;
        // Count ID is always just section-count for combined mode
        countId = `${section}-count`;
        
        // For combined views, also handle chart containers
        if (mode === 'combined') {
            // Map section to chart ID
            const chartIdMap = {
                'files': 'files-chart',
                'categories': 'categories-chart',
                'keywords': 'keywords-chart',
                'sources': ['sources-chart-count', 'sources-chart-size', 'sources-chart-rate'],
                'sides': 'sides-chart'
            };
            chartId = chartIdMap[section];
        } else if (section === 'words' && mode === 'grid') {
            // Words section has a chart that might be visible
            chartId = 'words-chart';
        }
    }
    
    // Show empty state in table/results container
    if (resultsId) {
        const resultsElement = document.getElementById(resultsId);
        if (resultsElement) {
            resultsElement.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-inbox" aria-hidden="true"></i>
                    <p>${message}</p>
                </div>
            `;
        }
    }
    
    // Show empty state in chart container(s)
    if (chartId) {
        if (Array.isArray(chartId)) {
            // Handle multiple charts (like sources)
            chartId.forEach(id => showEmptyChartState(id, message));
        } else {
            showEmptyChartState(chartId, message);
        }
    }
    
    // Update count
    const countElement = document.getElementById(countId);
    if (countElement) {
        countElement.textContent = `0 ${translations.itemsLabel || 'items'}`;
    }
}

function showEmptyChartState(canvasId, message) {
    // Destroy existing chart if it exists
    if (state.charts[canvasId]) {
        try {
            state.charts[canvasId].destroy();
        } catch (e) {
            console.warn(`Error destroying chart ${canvasId}:`, e);
        }
        delete state.charts[canvasId];
    }
    
    // Find the canvas and its container
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    
    const chartContainer = canvas.closest('.chart-container-layout, .chart-container');
    if (!chartContainer) return;
    
    // Check if empty state already exists
    const existingEmptyState = chartContainer.querySelector('.empty-state');
    if (existingEmptyState) {
        // Update the message
        const messageElement = existingEmptyState.querySelector('p');
        if (messageElement) {
            messageElement.textContent = message || translations.noDataAvailable;
        }
        return;
    }
    
    // Hide canvas
    canvas.style.display = 'none';
    
    // Create and add empty state message
    const emptyStateDiv = document.createElement('div');
    emptyStateDiv.className = 'empty-state';
    emptyStateDiv.style.cssText = 'display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 500px; padding: 2rem;';
    emptyStateDiv.innerHTML = `
        <i class="bi bi-inbox" style="font-size: 3rem; color: var(--text-muted); margin-bottom: 1rem;" aria-hidden="true"></i>
        <p style="color: var(--text-light); font-size: 1.1rem; margin: 0;">${message || translations.noDataAvailable}</p>
    `;
    
    chartContainer.appendChild(emptyStateDiv);
}

// ===== DASHBOARD SUMMARY =====
async function loadDashboardSummary() {
    console.log('Comprehensive Dashboard: loadDashboardSummary called');
    try {
        console.log('Comprehensive Dashboard: Fetching from /api/analytics/dashboard-summary');
        const response = await fetch('/api/analytics/dashboard-summary');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        console.log('Comprehensive Dashboard: Data received:', data);
        
        // Update summary cards if they exist (comprehensive dashboard may not have these)
        const totalFilesEl = document.getElementById('totalFiles');
        console.log('Comprehensive Dashboard: totalFiles element found:', !!totalFilesEl);
        if (totalFilesEl) {
            totalFilesEl.textContent = (data.totalFiles || 0).toLocaleString();
            console.log('Comprehensive Dashboard: Updated totalFiles');
        } else {
            console.log('Comprehensive Dashboard: Summary statistics cards not found (this is normal for comprehensive dashboard)');
        }
        
        const processedFilesEl = document.getElementById('processedFiles');
        if (processedFilesEl) processedFilesEl.textContent = (data.processedFiles || 0).toLocaleString();
        
        const uniqueTypesEl = document.getElementById('uniqueTypes');
        if (uniqueTypesEl) uniqueTypesEl.textContent = data.uniqueTypes || 0;
        
        const totalWordsEl = document.getElementById('totalWords');
        if (totalWordsEl) totalWordsEl.textContent = (data.totalWords || 0).toLocaleString();
        
        const totalCategoriesEl = document.getElementById('totalCategories');
        if (totalCategoriesEl) totalCategoriesEl.textContent = data.totalCategories || 0;
        
        // Format storage size
        const storageSizeEl = document.getElementById('storageSize');
        if (storageSizeEl) {
            const sizeGB = ((data.totalSize || 0) / (1024 ** 3)).toFixed(2);
            storageSizeEl.textContent = sizeGB + ' GB';
        }
        
        // Update processing rate if element exists
        const processingRateEl = document.getElementById('processingRate');
        if (processingRateEl) {
            processingRateEl.textContent = (data.processingRate || 0).toFixed(1) + '% ' + (translations.processed || 'processed');
        }
        
    } catch (error) {
        console.error('Error loading dashboard summary:', error);
        // Set default values on error
        const totalFilesEl = document.getElementById('totalFiles');
        if (totalFilesEl) totalFilesEl.textContent = '0';
        
        const processedFilesEl = document.getElementById('processedFiles');
        if (processedFilesEl) processedFilesEl.textContent = '0';
        
        const uniqueTypesEl = document.getElementById('uniqueTypes');
        if (uniqueTypesEl) uniqueTypesEl.textContent = '0';
        
        const totalWordsEl = document.getElementById('totalWords');
        if (totalWordsEl) totalWordsEl.textContent = '0';
        
        const totalCategoriesEl = document.getElementById('totalCategories');
        if (totalCategoriesEl) totalCategoriesEl.textContent = '0';
        
        const storageSizeEl = document.getElementById('storageSize');
        if (storageSizeEl) storageSizeEl.textContent = '0 GB';
    }
}

// Export default init function for universal-initializer.js
export default function init() {
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeComprehensiveDashboard);
    } else {
        initializeComprehensiveDashboard();
    }
}

// Expose functions globally for onclick handlers in templates
if (typeof window !== 'undefined') {
    window.toggleWordsView = toggleWordsView;
    window.applyFilters = applyFilters;
    window.resetFilters = resetFilters;
}