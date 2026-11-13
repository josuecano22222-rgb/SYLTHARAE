/**
 * Charts Dashboard Page JavaScript - Fixed Version
 * Resolved filtering issues and improved functionality
 */

import { enhanceMultiSelects, refreshMultiSelect } from '../modules/ui/multi-select-dropdown.js';

// Load translations from JSON script tag
let translations = {};

function loadChartsTranslations() {
    const pageDataEl = document.getElementById('charts-dashboard-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing charts dashboard page data:', e);
        }
    }
}

document.addEventListener('DOMContentLoaded', function() {
    loadChartsTranslations();
    console.log('Charts dashboard page loaded');
});


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
    loadingStates: {} // Track loading state per section
};

// ===== CONFIGURATION =====
const CONFIG = {
    colors: {
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
                bottom: 30,
                left: 20
            },
            autoPadding: true
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
            charts: { 
                category: 'files-filter-category', 
                source: 'files-filter-source', 
                side: 'files-filter-side', 
                keyword: 'files-filter-keyword' 
            }
        },
        categories: {
            charts: { 
                source: 'categories-filter-source', 
                side: 'categories-filter-side' 
            }
        },
        keywords: {
            charts: { 
                category: 'keywords-filter-category', 
                source: 'keywords-filter-source', 
                side: 'keywords-filter-side' 
            }
        },
        sources: {
            charts: { 
                filetype: 'sources-filter-filetype', 
                side: 'sources-filter-side', 
                keyword: 'sources-filter-keyword' 
            }
        },
        sides: {
            charts: { 
                filetype: 'sides-filter-filetype', 
                category: 'sides-filter-category',
                keyword: 'sides-filter-keyword' 
            }
        },
        words: {
            charts: { 
                category: 'words-filter-category' 
            }
        }
    }
};

// ===== RUN TOKEN =====
// Every visit imports this module under a new URL, so several copies can be
// alive at once, each with its own `state`. Their requests outlive the page
// they were started for. The run token lives on `window`, is shared by every
// copy, and changes whenever a new page root is booted. A response that
// arrives after its run has been replaced is dropped rather than rendered into
// the current page (which is what produced "Cannot set properties of null").
function chartsRunRoot() {
    return document.getElementById('section-words') || document.body;
}

function startChartsRun() {
    const shared = window.__chartsDashboardRun || (window.__chartsDashboardRun = { generation: 0, root: null });
    const root = chartsRunRoot();
    if (shared.root === root && root.isConnected) return null; // this DOM already booted
    shared.generation += 1;
    shared.root = root;
    // Drop Chart.js instances whose canvases belonged to the previous page.
    if (window.Chart && window.Chart.instances) {
        Object.values(window.Chart.instances).forEach((chart) => {
            if (!chart.canvas || !chart.canvas.isConnected) {
                try { chart.destroy(); } catch (e) { /* already gone */ }
            }
        });
    }
    return { generation: shared.generation, root };
}

function currentChartsRun() {
    const shared = window.__chartsDashboardRun;
    return shared ? { generation: shared.generation, root: shared.root } : null;
}

function isStaleChartsRun(run) {
    const shared = window.__chartsDashboardRun;
    return !run || !shared || run.generation !== shared.generation || !run.root.isConnected;
}

// ===== INITIALIZATION =====
function bootChartsDashboard() {
    const run = startChartsRun();
    if (!run) {
        console.log('Charts dashboard already booted on this page; skipping');
        return;
    }
    console.log('Initializing charts dashboard...');
    loadChartsTranslations();

    // Load filter options first
    loadFilterOptions(run).then(() => {
        if (isStaleChartsRun(run)) return;
        console.log('Filter options loaded, loading initial data...');
        // Load all sections on page load
        ['files', 'categories', 'keywords', 'sources', 'sides', 'words'].forEach((section) => {
            loadDataForSection(section, 'charts', run);
        });
    }).catch(error => {
        if (!isStaleChartsRun(run)) console.error('Error during initialization:', error);
    });
}

document.addEventListener('DOMContentLoaded', function() {
    bootChartsDashboard();
});

export default function init() {
    bootChartsDashboard();
}

// ===== FILTER OPTIONS LOADING =====
async function loadFilterOptions(run = currentChartsRun()) {
    try {
        await Promise.all([
            loadCategories(run),
            loadSources(run),
            loadSides(run),
            loadKeywords(run),
            loadFileTypes(run)
        ]);
        console.log('All filter options loaded successfully');
    } catch (error) {
        console.error('Error loading filter options:', error);
        throw error;
    }
}

async function loadCategories(run = currentChartsRun()) {
    try {
        const response = await fetch('/api/categories');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        
        const data = await response.json();
        if (isStaleChartsRun(run)) return;
        state.filterData.categories = Array.isArray(data) ? data : [];
        
        const selects = [
            'files-filter-category', 
            'keywords-filter-category',
            'words-filter-category', 
            'sides-filter-category'
        ];
        selects.forEach(id => populateSelect(id, state.filterData.categories));
        console.log(`Loaded ${state.filterData.categories.length} categories`);
    } catch (error) {
        if (isStaleChartsRun(run)) return;
        console.error('Error loading categories:', error);
        state.filterData.categories = [];
    }
}

async function loadSources(run = currentChartsRun()) {
    try {
        const response = await fetch('/api/sources');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        
        const data = await response.json();
        if (isStaleChartsRun(run)) return;
        state.filterData.sources = Array.isArray(data) ? data : [];
        
        const selects = [
            'files-filter-source', 
            'categories-filter-source',
            'keywords-filter-source'
        ];
        selects.forEach(id => populateSelect(id, state.filterData.sources));
        console.log(`Loaded ${state.filterData.sources.length} sources`);
    } catch (error) {
        if (isStaleChartsRun(run)) return;
        console.error('Error loading sources:', error);
        state.filterData.sources = [];
    }
}

async function loadSides(run = currentChartsRun()) {
    try {
        const response = await fetch('/api/sides');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        
        const data = await response.json();
        if (isStaleChartsRun(run)) return;
        state.filterData.sides = Array.isArray(data) ? data : [];
        
        const selects = [
            'files-filter-side', 
            'categories-filter-side',
            'keywords-filter-side', 
            'sources-filter-side'
            // Note: the Sides section intentionally has no "Side" filter — it
            // would be self-referential (CHART-01: previously listed the
            // non-existent 'sides-filter-side', logging a console warning).
        ];
        selects.forEach(id => populateSelect(id, state.filterData.sides));
        console.log(`Loaded ${state.filterData.sides.length} sides`);
    } catch (error) {
        if (isStaleChartsRun(run)) return;
        console.error('Error loading sides:', error);
        state.filterData.sides = [];
    }
}

async function loadKeywords(run = currentChartsRun()) {
    try {
        const response = await fetch('/api/keywords?per_page=100&page=1');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        
        const data = await response.json();
        if (isStaleChartsRun(run)) return;
        
        if (data.success && Array.isArray(data.keywords)) {
            state.filterData.keywords = data.keywords;
            
            const selects = [
                'files-filter-keyword', 
                'sources-filter-keyword',
                'sides-filter-keyword'
            ];
            selects.forEach(id => populateSelect(id, state.filterData.keywords, 'id', 'text'));
            console.log(`Loaded ${state.filterData.keywords.length} keywords`);
        } else {
            throw new Error('Invalid keywords data format');
        }
    } catch (error) {
        if (isStaleChartsRun(run)) return;
        console.error('Error loading keywords:', error);
        state.filterData.keywords = [];
    }
}

async function loadFileTypes(run = currentChartsRun()) {
    try {
        const response = await fetch('/api/analytics/file-type-distribution');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        
        const data = await response.json();
        if (isStaleChartsRun(run)) return;
        
        if (data.types && Array.isArray(data.types)) {
            state.filterData.fileTypes = data.types;
            
            const selects = [
                'sources-filter-filetype', 
                'sides-filter-filetype'
            ];
            selects.forEach(id => populateSelect(id, state.filterData.fileTypes, 'type', 'type'));
            enhanceMultiSelects(document, { type: { compact: true } });
            console.log(`Loaded ${state.filterData.fileTypes.length} file types`);
        } else {
            throw new Error('Invalid file types data format');
        }
    } catch (error) {
        if (isStaleChartsRun(run)) return;
        console.error('Error loading file types:', error);
        state.filterData.fileTypes = [];
    }
}

function populateSelect(selectId, data, valueKey = 'id', textKey = 'name') {
    const select = document.getElementById(selectId);
    if (!select) {
        console.warn(`Select element '${selectId}' not found`);
        return;
    }

    const previous = select.multiple
        ? Array.from(select.selectedOptions).map((o) => o.value)
        : [select.value].filter(Boolean);

    // Preserve the first option for single-select (typically "All ...")
    const firstOption = (!select.multiple && select.options[0]) ? select.options[0] : null;
    select.innerHTML = '';
    if (firstOption) select.appendChild(firstOption);

    if (Array.isArray(data)) {
        data.forEach(item => {
            const option = document.createElement('option');
            option.value = item[valueKey] || '';
            option.textContent = item[textKey] || 'Unknown';
            option.selected = previous.includes(option.value);
            select.appendChild(option);
        });
    }

    if (select.multiple && (select.classList.contains('msdd-enhance') || select.hasAttribute('data-compact-multi'))) {
        refreshMultiSelect(select);
    }
}

// ===== FILTER FUNCTIONS =====
function applyFilters(section, mode) {
    console.log(`Applying filters for section: ${section}, mode: ${mode}`);
    loadDataForSection(section, mode);
}

function resetFilters(section, mode) {
    console.log(`Resetting filters for section: ${section}, mode: ${mode}`);
    
    const filterIds = CONFIG.filterMappings[section]?.[mode];
    if (filterIds) {
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
    }
    
    loadDataForSection(section, mode);
}

function getFilterParams(section, mode) {
    const filterIds = CONFIG.filterMappings[section]?.[mode];
    if (!filterIds) {
        console.warn(`No filter mappings found for section: ${section}, mode: ${mode}`);
        return new URLSearchParams();
    }
    
    const params = new URLSearchParams();
    
    Object.entries(filterIds).forEach(([key, id]) => {
        const element = document.getElementById(id);
        if (!element) return;
        let value = '';
        if (element.multiple) {
            value = Array.from(element.selectedOptions).map((o) => o.value).filter(Boolean).join(',');
        } else if (element.value && element.value.trim() !== '') {
            value = element.value.trim();
        }
        if (!value) return;
        const paramName = key === 'filetype' ? 'file_type' :
                        key === 'keyword' ? 'keyword_id' :
                        key === 'category' ? 'category_id' :
                        key === 'source' ? 'source_id' :
                        key === 'side' ? 'side_id' : key;
        params.append(paramName, value);
    });
    
    console.log(`Filter params for ${section}:`, params.toString());
    return params;
}

// ===== DATA LOADING =====
async function loadDataForSection(section, mode, run = currentChartsRun()) {
    // Prevent duplicate loading
    const loadKey = `${section}-${mode}`;
    if (state.loadingStates[loadKey]) {
        console.log(`Already loading ${section}, skipping...`);
        return;
    }
    
    state.loadingStates[loadKey] = true;
    const params = getFilterParams(section, mode);
    let apiEndpoint;
    
    // Construct API endpoint based on section
    if (section === 'words') {
        apiEndpoint = `/api/dashboard/words?${params}`;
    } else {
        apiEndpoint = `/api/dashboard/${section}-filtered?${params}`;
    }
    
    console.log(`Loading data for ${section} from: ${apiEndpoint}`);
    setLoadingState(section, true);
    
    try {
        const response = await fetch(apiEndpoint);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        if (isStaleChartsRun(run)) return;
        console.log(`Received data for ${section}:`, data);
        
        if (data.success) {
            renderData(section, mode, data);
        } else {
            const errorMsg = data.message || translations.errorLoadingData || 'Error loading data';
            showError(section, errorMsg);
            console.error(`API returned success=false for ${section}:`, data);
        }
    } catch (error) {
        if (isStaleChartsRun(run)) return;
        console.error(`Error loading ${section} ${mode}:`, error);
        showError(section, error.message || translations.errorLoadingData || 'Error loading data');
    } finally {
        if (!isStaleChartsRun(run)) setLoadingState(section, false);
        state.loadingStates[loadKey] = false;
    }
}

function setLoadingState(section, isLoading) {
    const countIds = {
        'files': ['files-chart-count'],
        'categories': ['categories-chart-count'],
        'keywords': ['keywords-chart-count'],
        'sources': ['sources-count-chart-count', 'sources-size-chart-count', 'sources-rate-chart-count'],
        'sides': ['sides-chart-count'],
        'words': ['words-chart-count']
    };
    
    const ids = countIds[section] || [];
    ids.forEach(countId => {
        const countElement = document.getElementById(countId);
        if (countElement) {
            countElement.textContent = isLoading ? '...' : '0';
        }
    });
}

function showError(section, message) {
    const chartIds = {
        'files': ['files-chart'],
        'categories': ['categories-chart'],
        'keywords': ['keywords-chart'],
        'sources': ['sources-chart-count', 'sources-chart-size', 'sources-chart-rate'],
        'sides': ['sides-chart'],
        'words': ['words-chart']
    };
    
    const ids = chartIds[section] || [];
    ids.forEach(chartId => {
        showEmptyChartState(chartId, message);
    });
    
    // Reset counts
    setLoadingState(section, false);
}

// ===== DATA RENDERING =====
function renderData(section, mode, data) {
    console.log(`Rendering ${section} data:`, data);
    
    switch(section) {
        case 'files':
            renderFiles(data);
            break;
        case 'categories':
            renderCategories(data);
            break;
        case 'keywords':
            renderKeywords(data);
            break;
        case 'sources':
            renderSources(data);
            break;
        case 'sides':
            renderSides(data);
            break;
        case 'words':
            renderWords(data);
            break;
        default:
            console.warn(`Unknown section: ${section}`);
    }
}

function renderFiles(data) {
    if (!data.file_types || !Array.isArray(data.file_types) || data.file_types.length === 0) {
        showEmptyChartState('files-chart', translations.noFileTypesFound || 'No file types found');
        document.getElementById('files-chart-count').textContent = '0 ' + (translations.fileTypesLabel || 'file types');
        return;
    }
    
    const count = data.file_types.length;
    document.getElementById('files-chart-count').textContent = `${count} ${translations.fileTypesLabel || 'file types'}`;
    
    renderBarChart('files-chart', data.file_types.slice(0, 10), {
        labelKey: 'type',
        valueKey: 'count',
        tooltipCallback: (index) => {
            const type = data.file_types[index];
            if (!type) return [];
            return [
                `${translations.files || 'Files'}: ${(type.count || 0).toLocaleString()}`,
                `${translations.totalSizeLabel || 'Total Size'}: ${formatFileSize(type.total_size || 0)}`,
                `${translations.avgSizeLabel || 'Avg Size'}: ${formatFileSize(type.avg_size || 0)}`
            ];
        }
    });
}

function renderCategories(data) {
    if (!data.categories || !Array.isArray(data.categories) || data.categories.length === 0) {
        showEmptyChartState('categories-chart', translations.noCategoriesFound || 'No categories found');
        document.getElementById('categories-chart-count').textContent = '0 ' + (translations.categoriesLabel || 'categories');
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
        showEmptyChartState('categories-chart', translations.noCategoriesFound || 'No categories found');
        document.getElementById('categories-chart-count').textContent = '0 ' + (translations.categoriesLabel || 'categories');
        return;
    }
    
    // Sort all categories by file_count descending (largest first)
    const sortedCategories = [...validCategories].sort((a, b) => {
        const fileCountA = a.file_count || 0;
        const fileCountB = b.file_count || 0;
        return fileCountB - fileCountA;  // Descending order
    });
    
    const count = sortedCategories.length;
    document.getElementById('categories-chart-count').textContent = `${count} ${translations.categoriesLabel || 'categories'}`;
    
    // Render chart with ALL sorted categories (no limit)
    renderBarChart('categories-chart', sortedCategories, {
        labelKey: 'name',
        valueKey: 'file_count',  // Use file_count to show file statistics
        maxLabelLength: 25,
        tooltipCallback: (index) => {
            const cat = sortedCategories[index];
            if (!cat) return [];
            return [
                `${translations.category || 'Category'}: ${cat.name}`,
                `${translations.files || 'Files'}: ${(cat.file_count || 0).toLocaleString()}`,
                `${translations.words || 'Words'}: ${(cat.word_count || 0).toLocaleString()}`,
                `${translations.density || 'Density'}: ${(cat.file_density || 0).toFixed(2)}`
            ];
        }
    });
}

function renderKeywords(data) {
    if (!data.keywords || !Array.isArray(data.keywords) || data.keywords.length === 0) {
        showEmptyChartState('keywords-chart', translations.noKeywordsFound || 'No keywords found');
        document.getElementById('keywords-chart-count').textContent = '0 ' + (translations.keywordsLabel || 'keywords');
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
        showEmptyChartState('keywords-chart', translations.noKeywordsFound || 'No keywords found');
        document.getElementById('keywords-chart-count').textContent = '0 ' + (translations.keywordsLabel || 'keywords');
        return;
    }
    
    // Sort all keywords by file_count descending (largest first)
    const sortedKeywords = [...validKeywords].sort((a, b) => {
        const fileCountA = a.file_count || 0;
        const fileCountB = b.file_count || 0;
        return fileCountB - fileCountA;  // Descending order
    });
    
    document.getElementById('keywords-chart-count').textContent = `${sortedKeywords.length} ${translations.keywordsLabel || 'keywords'}`;
    
    // Render chart with ALL sorted keywords (no limit)
    renderBarChart('keywords-chart', sortedKeywords, {
        labelKey: 'text',
        valueKey: 'file_count',  // Always use file_count for the chart
        maxLabelLength: 25,
        tooltipCallback: (index) => {
            const kw = sortedKeywords[index];
            if (!kw) return [];
            return [
                `${translations.keyword || 'Keyword'}: ${kw.text || translations.unknown || 'Unknown'}`,
                `${translations.files || 'Files'}: ${(kw.file_count || 0).toLocaleString()}`,
                `${translations.words || 'Words'}: ${(kw.word_count || 0).toLocaleString()}`,
                `${translations.density || 'Density'}: ${(kw.file_density || 0).toFixed(2)}`
            ];
        }
    });
}

function renderSources(data) {
    if (!data.sources || !Array.isArray(data.sources) || data.sources.length === 0) {
        showEmptyChartState('sources-chart-count', translations.noSourcesFound || 'No sources found');
        showEmptyChartState('sources-chart-size', translations.noSourcesFound || 'No sources found');
        showEmptyChartState('sources-chart-rate', translations.noSourcesFound || 'No sources found');
        ['sources-count-chart-count', 'sources-size-chart-count', 'sources-rate-chart-count'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = '0 ' + (translations.sourcesLabel || 'sources');
        });
        return;
    }
    
    const count = data.sources.length;
    const label = translations.sourcesLabel || 'sources';
    document.getElementById('sources-count-chart-count').textContent = `${count} ${label}`;
    document.getElementById('sources-size-chart-count').textContent = `${count} ${label}`;
    document.getElementById('sources-rate-chart-count').textContent = `${count} ${label}`;
    
    // File Count Chart
    renderBarChart('sources-chart-count', data.sources.slice(0, 15), {
        labelKey: 'name',
        valueKey: 'file_count',
        tooltipCallback: (index) => {
            const src = data.sources[index];
            if (!src) return [];
            return [
                `${translations.files || 'Files'}: ${(src.file_count || 0).toLocaleString()}`,
                `${translations.totalSizeLabel || 'Total Size'}: ${formatFileSize(src.total_size || 0)}`,
                `${translations.processingRateLabel || 'Processing Rate'}: ${(src.processing_rate || 0).toFixed(1)}%`
            ];
        }
    });
    
    // Size Distribution Chart (Doughnut)
    renderDoughnutChart('sources-chart-size', data.sources.slice(0, 10), {
        labelKey: 'name',
        valueKey: 'total_size',
        tooltipCallback: (index) => {
            const src = data.sources[index];
            if (!src) return [];
            return [
                `${translations.source || 'Source'}: ${src.name || 'Unknown'}`,
                `${translations.totalSizeLabel || 'Total Size'}: ${formatFileSize(src.total_size || 0)}`,
                `${translations.files || 'Files'}: ${(src.file_count || 0).toLocaleString()}`
            ];
        }
    });
    
    // Processing Rate Chart
    const sources = data.sources.slice(0, 15);
    const colors = sources.map(s => {
        const rate = s.processing_rate || 0;
        return rate >= 90 ? CONFIG.colors.success :
               rate >= 70 ? CONFIG.colors.warning : CONFIG.colors.danger;
    });
    
    renderBarChart('sources-chart-rate', sources, {
        labelKey: 'name',
        valueKey: 'processing_rate',
        customColors: colors,
        yMax: 100,
        yTicksSuffix: '%',
        tooltipCallback: (index) => {
            const src = sources[index];
            if (!src) return [];
            return [
                `${translations.processingRateLabel || 'Processing Rate'}: ${(src.processing_rate || 0).toFixed(1)}%`,
                `${translations.processed || 'Processed'}: ${(src.processed_files || 0).toLocaleString()} / ${(src.file_count || 0).toLocaleString()}`,
                `${translations.totalFiles || 'Total Files'}: ${(src.file_count || 0).toLocaleString()}`
            ];
        }
    });
}

function renderSides(data) {
    if (!data.sides || !Array.isArray(data.sides) || data.sides.length === 0) {
        showEmptyChartState('sides-chart', translations.noSidesFound || 'No sides found');
        document.getElementById('sides-chart-count').textContent = '0 ' + (translations.sidesLabel || 'sides');
        return;
    }
    
    const count = data.sides.length;
    document.getElementById('sides-chart-count').textContent = `${count} ${translations.sidesLabel || 'sides'}`;
    
    renderBarChart('sides-chart', data.sides.slice(0, 15), {
        labelKey: 'name',
        valueKey: 'file_count',
        tooltipCallback: (index) => {
            const side = data.sides[index];
            if (!side) return [];
            return [
                `${translations.side || 'Side'}: ${side.name || 'Unknown'}`,
                `${translations.importance || 'Importance'}: ${side.importance || 'N/A'}`,
                `${translations.files || 'Files'}: ${(side.file_count || 0).toLocaleString()}`,
                `${translations.totalSizeLabel || 'Total Size'}: ${formatFileSize(side.total_size || 0)}`,
                `${translations.avgSizeLabel || 'Avg Size'}: ${formatFileSize(side.avg_size || 0)}`
            ];
        }
    });
}

function renderWords(data) {
    if (!data.words || !Array.isArray(data.words) || data.words.length === 0) {
        showEmptyChartState('words-chart', translations.noWordsFound || 'No words found');
        document.getElementById('words-chart-count').textContent = '0 ' + (translations.itemsLabel || 'items');
        return;
    }
    
    // Get selected category name if available
    const categorySelect = document.getElementById('words-filter-category');
    const selectedCategoryId = categorySelect ? categorySelect.value : '';
    let categoryName = '';
    
    if (selectedCategoryId && data.words.length > 0 && data.words[0].category) {
        categoryName = data.words[0].category;
    }
    
    // Update chart header to show category if selected
    const chartHeader = document.querySelector('#section-words .chart-header h3');
    if (chartHeader) {
        if (categoryName) {
            chartHeader.textContent = `${translations.words || 'Words'} - ${categoryName}`;
        } else {
            chartHeader.textContent = translations.wordsDistribution || 'Words Distribution';
        }
    }
    
    const count = data.words.length;
    document.getElementById('words-chart-count').textContent = `${count} ${translations.itemsLabel || 'items'}`;
    
    renderBarChart('words-chart', data.words.slice(0, 20), {
        labelKey: 'word',
        valueKey: 'file_count',
        tooltipCallback: (index) => {
            const word = data.words[index];
            if (!word) return [];
            const tooltip = [
                `${translations.word || 'Word'}: ${word.word || 'Unknown'}`, 
                `${translations.files || 'Files'}: ${(word.file_count || 0).toLocaleString()}`
            ];
            if (word.category) {
                tooltip.push(`${translations.category || 'Category'}: ${word.category}`);
            }
            return tooltip;
        }
    });
}

// ===== CHART HELPERS =====

/**
 * Properly destroy a chart instance, clearing both our state and Chart.js registry
 */
function destroyChartInstance(canvasId) {
    // Destroy from our state
    if (state.charts[canvasId]) {
        try {
            state.charts[canvasId].destroy();
        } catch (e) {
            console.warn(`Error destroying chart ${canvasId} from state:`, e);
        }
        delete state.charts[canvasId];
    }
    
    // Also check Chart.js internal registry and destroy if exists
    const canvas = document.getElementById(canvasId);
    if (canvas && typeof Chart !== 'undefined') {
        try {
            // Chart.js v4+ uses Chart.getChart()
            const chartInstance = Chart.getChart(canvas);
            if (chartInstance) {
                chartInstance.destroy();
            }
        } catch (e) {
            // Chart.js v3 or earlier, or error accessing registry
            // Try to access Chart.instances if available
            if (Chart.instances && Chart.instances[canvasId]) {
                try {
                    Chart.instances[canvasId].destroy();
                    delete Chart.instances[canvasId];
                } catch (e2) {
                    console.warn(`Error destroying chart ${canvasId} from Chart.instances:`, e2);
                }
            }
        }
    }
}

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
    
    // Destroy existing chart properly
    destroyChartInstance(canvasId);
    
    const ctx = document.getElementById(canvasId);
    if (!ctx) {
        console.warn(`Canvas element with id '${canvasId}' not found`);
        return;
    }
    
    if (!data || !Array.isArray(data) || data.length === 0) {
        showEmptyChartState(canvasId, translations.noDataAvailable || 'No data available');
        return;
    }
    
    // Show canvas and remove empty state
    ctx.style.display = '';
    const chartContainer = ctx.closest('.chart-container-layout');
    if (chartContainer) {
        const emptyStates = chartContainer.querySelectorAll('.empty-state');
        emptyStates.forEach(state => state.remove());
    }
    
    // Check if visible
    const isVisible = chartContainer && 
                     window.getComputedStyle(chartContainer).display !== 'none' &&
                     window.getComputedStyle(ctx).display !== 'none';
    
    if (!isVisible) {
        requestAnimationFrame(() => {
            renderBarChart(canvasId, data, options);
        });
        return;
    }
    
    if (ctx.offsetWidth === 0 || ctx.offsetHeight === 0) {
        setTimeout(() => {
            renderBarChart(canvasId, data, options);
        }, 100);
        return;
    }
    
    // Prepare data
    const labels = data.map(item => truncateLabel(item[labelKey], maxLabelLength));
    const values = data.map(item => {
        const val = item[valueKey];
        if (val === null || val === undefined || val === '') return 0;
        const numVal = typeof val === 'string' ? parseFloat(val) : Number(val);
        return isNaN(numVal) ? 0 : numVal;
    });
    const colors = customColors || CONFIG.colors.primary.slice(0, data.length);
    
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
        
        // Force resize and update
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
    
    // Destroy existing chart properly
    destroyChartInstance(canvasId);
    
    const ctx = document.getElementById(canvasId);
    if (!ctx) {
        console.warn(`Canvas element with id '${canvasId}' not found`);
        return;
    }
    
    if (!data || !Array.isArray(data) || data.length === 0) {
        showEmptyChartState(canvasId, translations.noDataAvailable || 'No data available');
        return;
    }
    
    // Show canvas and remove empty state
    ctx.style.display = '';
    const chartContainer = ctx.closest('.chart-container-layout');
    if (chartContainer) {
        const emptyStates = chartContainer.querySelectorAll('.empty-state');
        emptyStates.forEach(state => state.remove());
    }
    
    // Check if visible
    const isVisible = chartContainer && 
                     window.getComputedStyle(chartContainer).display !== 'none' &&
                     window.getComputedStyle(ctx).display !== 'none';
    
    if (!isVisible) {
        requestAnimationFrame(() => {
            renderDoughnutChart(canvasId, data, options);
        });
        return;
    }
    
    if (ctx.offsetWidth === 0 || ctx.offsetHeight === 0) {
        setTimeout(() => {
            renderDoughnutChart(canvasId, data, options);
        }, 100);
        return;
    }
    
    // Prepare data
    const labels = data.map(item => item[labelKey] || 'Unknown');
    const values = data.map(item => item[valueKey] || 0);
    
    try {
        state.charts[canvasId] = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: CONFIG.colors.primary,
                    borderWidth: 2,
                    borderColor: 'var(--text-white)'
                }]
            },
            options: {
                ...CONFIG.chartOptions,
                plugins: {
                    tooltip: {
                        ...CONFIG.chartOptions.plugins.tooltip,
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
                        position: 'right',
                        labels: {
                            font: {
                                size: 14,
                                weight: '500'
                            },
                            padding: 15,
                            boxWidth: 16,
                            usePointStyle: true,
                            maxWidth: 250
                        }
                    }
                }
            }
        });
        
        setTimeout(() => {
            if (state.charts[canvasId]) {
                try {
                    state.charts[canvasId].update('none');
                } catch (e) {
                    console.warn(`Error updating chart ${canvasId}:`, e);
                }
            }
        }, 50);
    } catch (error) {
        console.error(`Error creating chart for ${canvasId}:`, error);
    }
}

// ===== UTILITY FUNCTIONS =====
function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

function truncateLabel(label, maxLength) {
    if (!label) return translations.unknown || 'Unknown';
    const str = String(label);
    return str.length > maxLength ? str.substring(0, maxLength) + '...' : str;
}

function showEmptyChartState(canvasId, message) {
    // Destroy existing chart
    if (state.charts[canvasId]) {
        try {
            state.charts[canvasId].destroy();
        } catch (e) {
            console.warn(`Error destroying chart ${canvasId}:`, e);
        }
        delete state.charts[canvasId];
    }
    
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    
    const chartContainer = canvas.closest('.chart-container-layout');
    if (!chartContainer) return;
    
    // Check if empty state already exists
    const existingEmptyState = chartContainer.querySelector('.empty-state');
    if (existingEmptyState) {
        const messageElement = existingEmptyState.querySelector('p');
        if (messageElement) {
            messageElement.textContent = message || translations.noDataAvailable || 'No data available';
        }
        return;
    }
    
    // Hide canvas
    canvas.style.display = 'none';
    
    // Create empty state
    const emptyStateDiv = document.createElement('div');
    emptyStateDiv.className = 'empty-state';
    emptyStateDiv.style.cssText = 'display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 500px; padding: 2rem;';
    emptyStateDiv.innerHTML = `
        <i class="bi bi-inbox" style="font-size: 3rem; color: var(--text-muted); margin-bottom: 1rem;" aria-hidden="true"></i>
        <p style="color: var(--text-light); font-size: 1.1rem; margin: 0;">${message || translations.noDataAvailable || 'No data available'}</p>
    `;
    
    chartContainer.appendChild(emptyStateDiv);
}

// Make functions globally available
window.applyFilters = applyFilters;
window.resetFilters = resetFilters;