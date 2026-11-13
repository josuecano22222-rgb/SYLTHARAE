/**
 * Chart Customizer Module
 * Provides comprehensive appearance customization for Chart.js charts
 * Each chart can be customized individually while preserving all data labels
 */

(function() {
    'use strict';

    // Store customization settings per chart
    const chartSettings = new Map();
    
    // Track charts that failed to attach (to prevent infinite retries)
    const failedCharts = new Set();
    const retryAttempts = new Map(); // Track retry count per chart
    const MAX_RETRY_ATTEMPTS = 3; // Maximum number of retry attempts per chart

    /**
     * Convert rgba/rgb color to hex
     */
    function rgbaToHex(rgba) {
        if (!rgba) return '#000000';
        
        // If already hex, return as is
        if (rgba.startsWith('#')) {
            return rgba;
        }
        
        // Parse rgba/rgb
        const match = rgba.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/);
        if (match) {
            const r = parseInt(match[1]);
            const g = parseInt(match[2]);
            const b = parseInt(match[3]);
            return '#' + [r, g, b].map(x => {
                const hex = x.toString(16);
                return hex.length === 1 ? '0' + hex : hex;
            }).join('');
        }
        
        return '#000000';
    }

    /**
     * Get default customization settings
     * Optimized for proper data display
     */
    function getDefaultSettings() {
        return {
            // Colors
            backgroundColor: '#ffffff',
            borderColor: '#e0e0e0',
            textColor: '#333333',
            gridColor: '#f0f0f0',
            chartColors: null, // Will use theme colors if null
            
            // Fonts
            fontFamily: 'Inter, system-ui, sans-serif',
            fontSize: 12,
            fontStyle: 'normal',
            fontWeight: 'normal',
            
            // Legend
            legendPosition: 'top',
            legendDisplay: true,
            legendStyle: 'normal', // normal, box, circle
            
            // Grid
            gridDisplay: true,
            gridColor: '#e0e0e0',
            gridLineWidth: 1,
            
            // Animation
            animationDuration: 1000,
            animationEasing: 'easeOutQuart',
            animationEnabled: true,
            
            // Tooltip
            tooltipEnabled: true,
            tooltipBackgroundColor: 'rgba(0, 0, 0, 0.8)',
            tooltipTextColor: '#ffffff',
            
            // Chart specific - optimized for data display
            maintainAspectRatio: false, // false allows chart to use full container height
            responsive: true // true ensures chart adapts to container size
        };
    }
    
    /**
     * Ensure chart displays all data properly
     * Called when attaching customization to a chart
     * CRITICAL: Preserves all labels, data, and callbacks
     */
    function ensureChartDataDisplay(chart) {
        if (!chart || !chart.config) return;
        
        const config = chart.config;
        
        // CRITICAL: Preserve all original data and labels
        const originalLabels = config.data && config.data.labels ? [...config.data.labels] : null;
        const originalDatasets = config.data && config.data.datasets ? 
            config.data.datasets.map(ds => ({
                label: ds.label,
                data: Array.isArray(ds.data) ? [...ds.data] : ds.data,
                // Preserve ALL properties including callbacks
                ...Object.keys(ds).reduce((acc, key) => {
                    acc[key] = ds[key];
                    return acc;
                }, {})
            })) : null;
        
        // Ensure responsive and maintainAspectRatio are set correctly
        if (!config.options) {
            config.options = {};
        }
        
        config.options.responsive = config.options.responsive !== undefined ? config.options.responsive : true;
        config.options.maintainAspectRatio = config.options.maintainAspectRatio !== undefined ? 
            config.options.maintainAspectRatio : false;
        
        // Ensure layout padding for better data visibility
        if (!config.options.layout) {
            config.options.layout = {};
        }
        if (!config.options.layout.padding) {
            config.options.layout.padding = {
                top: 10,
                right: 10,
                bottom: 10,
                left: 10
            };
        }
        
        // Ensure scales show all data - preserve existing callbacks
        if (config.options.scales) {
            Object.keys(config.options.scales).forEach(scaleKey => {
                const scale = config.options.scales[scaleKey];
                if (scale && scale.ticks) {
                    // Preserve existing callbacks
                    const originalCallback = scale.ticks.callback;
                    const originalFormatter = scale.ticks.format;
                    const originalAfterBuildTicks = scale.afterBuildTicks;
                    
                    // Ensure ticks are displayed
                    if (scale.ticks.display === undefined) {
                        scale.ticks.display = true;
                    }
                    // For y-axis, ensure beginAtZero is set for better visibility
                    if (scaleKey === 'y' && scale.beginAtZero === undefined) {
                        scale.beginAtZero = true;
                    }
                    
                    // Restore callbacks
                    if (originalCallback && !scale.ticks.callback) {
                        scale.ticks.callback = originalCallback;
                    }
                    if (originalFormatter && !scale.ticks.format) {
                        scale.ticks.format = originalFormatter;
                    }
                    if (originalAfterBuildTicks && !scale.afterBuildTicks) {
                        scale.afterBuildTicks = originalAfterBuildTicks;
                    }
                }
            });
        }
        
        // Ensure plugins are configured - preserve all callbacks
        if (!config.options.plugins) {
            config.options.plugins = {};
        }
        
        // Ensure tooltip shows all data - preserve callbacks
        if (!config.options.plugins.tooltip) {
            config.options.plugins.tooltip = {};
        }
        if (config.options.plugins.tooltip.enabled === undefined) {
            config.options.plugins.tooltip.enabled = true;
        }
        
        // Preserve tooltip callbacks
        const originalTooltipCallbacks = config.options.plugins.tooltip.callbacks ? 
            {...config.options.plugins.tooltip.callbacks} : null;
        if (originalTooltipCallbacks) {
            if (!config.options.plugins.tooltip.callbacks) {
                config.options.plugins.tooltip.callbacks = {};
            }
            Object.keys(originalTooltipCallbacks).forEach(key => {
                if (!config.options.plugins.tooltip.callbacks[key]) {
                    config.options.plugins.tooltip.callbacks[key] = originalTooltipCallbacks[key];
                }
            });
        }
        
        // Ensure legend shows all labels - preserve callbacks
        if (config.options.plugins.legend) {
            if (config.options.plugins.legend.display === undefined) {
                config.options.plugins.legend.display = true;
            }
            
            // Preserve legend label callbacks
            if (config.options.plugins.legend.labels) {
                const originalGenerateLabels = config.options.plugins.legend.labels.generateLabels;
                const originalFilter = config.options.plugins.legend.labels.filter;
                if (originalGenerateLabels && !config.options.plugins.legend.labels.generateLabels) {
                    config.options.plugins.legend.labels.generateLabels = originalGenerateLabels;
                }
                if (originalFilter && !config.options.plugins.legend.labels.filter) {
                    config.options.plugins.legend.labels.filter = originalFilter;
                }
            }
        }
        
        // Restore original data if it was lost
        if (originalLabels && config.data && (!config.data.labels || config.data.labels.length === 0)) {
            config.data.labels = originalLabels;
        }
        if (originalDatasets && config.data && config.data.datasets) {
            originalDatasets.forEach((originalDs, index) => {
                if (config.data.datasets[index]) {
                    // Restore data array
                    if (originalDs.data) {
                        config.data.datasets[index].data = originalDs.data;
                    }
                    // Restore label
                    if (originalDs.label) {
                        config.data.datasets[index].label = originalDs.label;
                    }
                    // Restore all other properties including callbacks
                    Object.keys(originalDs).forEach(key => {
                        if (!['data', 'label', 'backgroundColor', 'borderColor'].includes(key)) {
                            config.data.datasets[index][key] = originalDs[key];
                        }
                    });
                }
            });
        }
        
        // Force chart to resize and update
        try {
            if (chart.resize && typeof chart.resize === 'function') {
                setTimeout(() => {
                    try {
                        chart.resize();
                        chart.update('none');
                    } catch (e) {
                        // Ignore errors
                    }
                }, 100);
            }
        } catch (e) {
            // Ignore errors
        }
    }

    /**
     * Get settings for a specific chart
     */
    function getChartSettings(chartId) {
        if (!chartSettings.has(chartId)) {
            // Try to load from localStorage
            const saved = localStorage.getItem(`chart_settings_${chartId}`);
            if (saved) {
                try {
                    chartSettings.set(chartId, JSON.parse(saved));
                } catch (e) {
                    chartSettings.set(chartId, getDefaultSettings());
                }
            } else {
                chartSettings.set(chartId, getDefaultSettings());
            }
        }
        return chartSettings.get(chartId);
    }

    /**
     * Save settings for a chart
     */
    function saveChartSettings(chartId, settings) {
        chartSettings.set(chartId, settings);
        try {
            localStorage.setItem(`chart_settings_${chartId}`, JSON.stringify(settings));
        } catch (e) {
            console.warn('Could not save chart settings to localStorage:', e);
        }
    }

    /**
     * Apply customization settings to a chart
     * Preserves all data labels, statistics names, and callbacks
     * FIXED: Added validation to prevent null reference errors
     */
    function applyCustomization(chart, settings) {
        // Validate chart instance
        if (!chart) {
            return; // Invalid chart instance
        }
        
        // Validate chart config
        if (!chart.config) {
            return; // Chart config missing
        }
        
        // Validate canvas exists and is in DOM
        if (!chart.canvas) {
            return; // Canvas missing
        }
        
        // Check if canvas is in the DOM
        if (!chart.canvas.parentElement || !document.body.contains(chart.canvas)) {
            return; // Canvas not in DOM
        }
        
        // Check if chart has been destroyed
        if (chart.destroyed === true) {
            return; // Chart is destroyed
        }

        const config = chart.config;
        
        // CRITICAL: Preserve all data - never modify config.data.labels, datasets.data, or any callbacks
        // Store original data structure to ensure nothing is lost
        const originalLabels = config.data && config.data.labels ? [...config.data.labels] : null;
        const originalDatasets = config.data && config.data.datasets ? 
            config.data.datasets.map(ds => ({
                label: ds.label,
                data: Array.isArray(ds.data) ? [...ds.data] : ds.data,
                // Preserve all other properties
                ...Object.keys(ds).reduce((acc, key) => {
                    if (!['backgroundColor', 'borderColor'].includes(key)) {
                        acc[key] = ds[key];
                    }
                    return acc;
                }, {})
            })) : null;
        
        // Apply font settings
        const fontConfig = {
            family: settings.fontFamily,
            size: settings.fontSize,
            style: settings.fontStyle,
            weight: settings.fontWeight
        };

        // Ensure options object exists
        if (!config.options) {
            config.options = {};
        }
        
        // Ensure plugins object exists
        if (!config.options.plugins) {
            config.options.plugins = {};
        }

        // Apply to scales (preserves all labels and callbacks)
        if (config.options.scales) {
            Object.keys(config.options.scales).forEach(scaleKey => {
                const scale = config.options.scales[scaleKey];
                if (!scale) return;
                
                if (scale.ticks) {
                    // Preserve existing callbacks and formatters
                    const originalCallback = scale.ticks.callback;
                    const originalFormatter = scale.ticks.format;
                    
                    scale.ticks.font = {...(scale.ticks.font || {}), ...fontConfig};
                    scale.ticks.color = settings.textColor || '#333333';
                    
                    // Restore callbacks if they existed
                    if (originalCallback && !scale.ticks.callback) {
                        scale.ticks.callback = originalCallback;
                    }
                    if (originalFormatter && !scale.ticks.format) {
                        scale.ticks.format = originalFormatter;
                    }
                }
                if (scale.title) {
                    if (!scale.title.font) {
                        scale.title.font = {};
                    }
                    scale.title.font = {...scale.title.font, ...fontConfig};
                    scale.title.color = settings.textColor || '#333333';
                }
                if (scale.grid) {
                    scale.grid.display = settings.gridDisplay !== undefined ? settings.gridDisplay : true;
                    scale.grid.color = settings.gridColor || '#e0e0e0';
                    scale.grid.lineWidth = settings.gridLineWidth !== undefined ? settings.gridLineWidth : 1;
                }
            });
        }

        // Apply to legend (preserves all label text and callbacks)
        if (config.options.plugins.legend) {
            const legend = config.options.plugins.legend;
            const originalLabelCallback = legend.labels && legend.labels.generateLabels;
            const originalLabelFormatter = legend.labels && legend.labels.filter;
            
            legend.display = settings.legendDisplay !== undefined ? settings.legendDisplay : true;
            legend.position = settings.legendPosition || 'top';
            
            if (!legend.labels) {
                legend.labels = {};
            }
            
            legend.labels.font = {...(legend.labels.font || {}), ...fontConfig};
            legend.labels.color = settings.textColor || '#333333';
            
            // Preserve label callbacks and text
            if (originalLabelCallback && !legend.labels.generateLabels) {
                legend.labels.generateLabels = originalLabelCallback;
            }
            if (originalLabelFormatter && !legend.labels.filter) {
                legend.labels.filter = originalLabelFormatter;
            }
            
            if (settings.legendStyle === 'box') {
                legend.labels.usePointStyle = false;
                legend.labels.boxWidth = 20;
            } else if (settings.legendStyle === 'circle') {
                legend.labels.usePointStyle = true;
            } else {
                legend.labels.usePointStyle = legend.labels.usePointStyle !== undefined ? legend.labels.usePointStyle : true;
            }
        } else {
            // Create legend config if it doesn't exist
            config.options.plugins.legend = {
                display: settings.legendDisplay !== undefined ? settings.legendDisplay : true,
                position: settings.legendPosition || 'top',
                labels: {
                    font: fontConfig,
                    color: settings.textColor || '#333333',
                    usePointStyle: settings.legendStyle === 'circle' ? true : (settings.legendStyle === 'box' ? false : true),
                    boxWidth: settings.legendStyle === 'box' ? 20 : undefined
                }
            };
        }

        // Apply to tooltip (preserves all data in tooltips and callbacks)
        if (config.options.plugins.tooltip) {
            const tooltip = config.options.plugins.tooltip;
            const originalCallbacks = tooltip.callbacks ? {...tooltip.callbacks} : null;
            
            tooltip.enabled = settings.tooltipEnabled !== undefined ? settings.tooltipEnabled : true;
            
            // Always set tooltip colors if provided
            tooltip.backgroundColor = settings.tooltipBackgroundColor || 'rgba(0, 0, 0, 0.8)';
            tooltip.titleColor = settings.tooltipTextColor || '#ffffff';
            tooltip.bodyColor = settings.tooltipTextColor || '#ffffff';
            
            // Apply font settings
            if (!tooltip.titleFont) {
                tooltip.titleFont = {};
            }
            if (!tooltip.bodyFont) {
                tooltip.bodyFont = {};
            }
            tooltip.titleFont = {...tooltip.titleFont, ...fontConfig};
            tooltip.bodyFont = {...tooltip.bodyFont, ...fontConfig};
            
            // Restore all tooltip callbacks
            if (originalCallbacks && tooltip.callbacks) {
                Object.keys(originalCallbacks).forEach(key => {
                    if (!tooltip.callbacks[key]) {
                        tooltip.callbacks[key] = originalCallbacks[key];
                    }
                });
            }
        } else {
            // Create tooltip config if it doesn't exist
            config.options.plugins.tooltip = {
                enabled: settings.tooltipEnabled !== undefined ? settings.tooltipEnabled : true,
                backgroundColor: settings.tooltipBackgroundColor || 'rgba(0, 0, 0, 0.8)',
                titleColor: settings.tooltipTextColor || '#ffffff',
                bodyColor: settings.tooltipTextColor || '#ffffff',
                titleFont: fontConfig,
                bodyFont: fontConfig
            };
        }

        // Apply animation
        if (!config.options.animation) {
            config.options.animation = {};
        }
        config.options.animation.duration = settings.animationEnabled ? (settings.animationDuration || 1000) : 0;
        config.options.animation.easing = settings.animationEasing || 'easeOutQuart';

        // Apply chart colors if specified (only colors, not data)
        if (settings.chartColors && Array.isArray(settings.chartColors) && config.data && config.data.datasets) {
            config.data.datasets.forEach((dataset, index) => {
                if (settings.chartColors[index]) {
                    // Only modify colors, preserve all data values
                    if (Array.isArray(dataset.backgroundColor)) {
                        dataset.backgroundColor = dataset.backgroundColor.map((_, i) => 
                            settings.chartColors[i] || settings.chartColors[index] || dataset.backgroundColor[i]
                        );
                    } else if (dataset.backgroundColor !== undefined) {
                        dataset.backgroundColor = settings.chartColors[index];
                    }
                    if (Array.isArray(dataset.borderColor)) {
                        dataset.borderColor = dataset.borderColor.map((_, i) => 
                            settings.chartColors[i] || settings.chartColors[index] || dataset.borderColor[i]
                        );
                    } else if (dataset.borderColor !== undefined) {
                        dataset.borderColor = settings.chartColors[index];
                    }
                }
            });
        }

        // Apply responsive and aspect ratio - ensure proper display
        config.options.responsive = settings.responsive !== undefined ? settings.responsive : true;
        config.options.maintainAspectRatio = settings.maintainAspectRatio !== undefined ? settings.maintainAspectRatio : false;
        
        // Apply background color to chart canvas and container
        if (settings.backgroundColor && chart.canvas) {
            // Apply to canvas parent container
            const chartContainer = chart.canvas.closest('.chart-container');
            if (chartContainer) {
                chartContainer.style.backgroundColor = settings.backgroundColor;
            }
            
            // Also apply to canvas parent if different
            const canvasParent = chart.canvas.parentElement;
            if (canvasParent && canvasParent !== chartContainer) {
                canvasParent.style.backgroundColor = settings.backgroundColor;
            }
            
            // Set canvas background color in options if supported
            if (config.options.plugins) {
                if (!config.options.plugins.background) {
                    config.options.plugins.background = {};
                }
                config.options.plugins.background.color = settings.backgroundColor;
            }
        }
        
        // Ensure chart displays all data properly
        if (config.options.layout) {
            config.options.layout.padding = config.options.layout.padding || {
                top: 10,
                right: 10,
                bottom: 10,
                left: 10
            };
        }
        
        // Verify data integrity - restore if needed
        if (originalLabels && config.data && !config.data.labels) {
            config.data.labels = originalLabels;
        }
        if (originalDatasets && config.data && config.data.datasets) {
            originalDatasets.forEach((originalDs, index) => {
                if (config.data.datasets[index]) {
                    // Preserve data array
                    if (originalDs.data) {
                        config.data.datasets[index].data = originalDs.data;
                    }
                    // Preserve label
                    if (originalDs.label) {
                        config.data.datasets[index].label = originalDs.label;
                    }
                }
            });
        }

        // Update chart with 'none' mode to preserve all data and avoid animation issues
        // CRITICAL: Validate chart and canvas before updating to prevent null reference errors
        try {
            // Check if chart is valid and canvas exists
            if (!chart || !chart.canvas) {
                return; // Chart is invalid, skip update
            }
            
            // Check if canvas is still in the DOM
            if (!chart.canvas.parentElement || !document.body.contains(chart.canvas)) {
                return; // Canvas is not in DOM, skip update
            }
            
            // Check if chart has been destroyed (Chart.js sets destroyed flag)
            if (chart.destroyed === true) {
                return; // Chart is destroyed, skip update
            }
            
            // Check if chart.config still exists
            if (!chart.config) {
                return; // Chart config is missing, skip update
            }
            
            // Only update if chart is in a valid state
            chart.update('none');
            
            // Force resize to ensure all data is visible
            if (chart.resize && typeof chart.resize === 'function') {
                setTimeout(() => {
                    try {
                        // Validate again before resize
                        if (chart && chart.canvas && chart.canvas.parentElement && 
                            document.body.contains(chart.canvas) && chart.destroyed !== true) {
                            chart.resize();
                        }
                    } catch (e) {
                        // Ignore resize errors
                    }
                }, 50);
            }
        } catch (e) {
            // Only log if it's not a null reference (which we now prevent)
            if (e.message && !e.message.includes('null') && !e.message.includes('Cannot read properties')) {
                console.warn('Error updating chart:', e);
            }
            // Silently ignore null reference errors as they're now prevented
        }
    }

    /**
     * Create customization controls UI
     */
    function createCustomizationControls(chart, chartName, container) {
        const chartId = chart.canvas.id;
        const settings = getChartSettings(chartId);

        // Create customization panel
        const customizerPanel = document.createElement('div');
        customizerPanel.className = 'chart-customizer-panel';
        customizerPanel.style.display = 'none';
        customizerPanel.innerHTML = `
            <div class="customizer-header">
                <h4><i class="bi bi-palette"></i> Customize Chart Appearance</h4>
                <button class="customizer-close" type="button" aria-label="Close customizer">
                    <i class="bi bi-x"></i>
                </button>
            </div>
            <div class="customizer-content">
                <div class="customizer-section">
                    <h5><i class="bi bi-paint-bucket"></i> Colors</h5>
                    <div class="customizer-group">
                        <label>Background Color:</label>
                        <input type="color" id="bgColor_${chartId}" value="${settings.backgroundColor}">
                    </div>
                    <div class="customizer-group">
                        <label>Text Color:</label>
                        <input type="color" id="textColor_${chartId}" value="${settings.textColor}">
                    </div>
                    <div class="customizer-group">
                        <label>Grid Color:</label>
                        <input type="color" id="gridColor_${chartId}" value="${settings.gridColor}">
                    </div>
                    <div class="customizer-group">
                        <label>Tooltip Background:</label>
                        <input type="color" id="tooltipBg_${chartId}" value="${rgbaToHex(settings.tooltipBackgroundColor)}">
                        <input type="text" id="tooltipBgText_${chartId}" value="${settings.tooltipBackgroundColor}" 
                               class="form-control form-control-sm mt-1" 
                               placeholder="rgba(0, 0, 0, 0.8)" 
                               title="For rgba colors, use the text input below">
                    </div>
                </div>

                <div class="customizer-section">
                    <h5><i class="bi bi-type"></i> Fonts</h5>
                    <div class="customizer-group">
                        <label>Font Family:</label>
                        <select id="fontFamily_${chartId}">
                            <option value="Inter, system-ui, sans-serif" ${settings.fontFamily.includes('Inter') ? 'selected' : ''}>Inter</option>
                            <option value="Arial, sans-serif" ${settings.fontFamily.includes('Arial') ? 'selected' : ''}>Arial</option>
                            <option value="'Times New Roman', serif" ${settings.fontFamily.includes('Times') ? 'selected' : ''}>Times New Roman</option>
                            <option value="'Courier New', monospace" ${settings.fontFamily.includes('Courier') ? 'selected' : ''}>Courier New</option>
                            <option value="Georgia, serif" ${settings.fontFamily.includes('Georgia') ? 'selected' : ''}>Georgia</option>
                        </select>
                    </div>
                    <div class="customizer-group">
                        <label>Font Size:</label>
                        <input type="number" id="fontSize_${chartId}" value="${settings.fontSize}" min="8" max="24" step="1">
                    </div>
                    <div class="customizer-group">
                        <label>Font Weight:</label>
                        <select id="fontWeight_${chartId}">
                            <option value="normal" ${settings.fontWeight === 'normal' ? 'selected' : ''}>Normal</option>
                            <option value="bold" ${settings.fontWeight === 'bold' ? 'selected' : ''}>Bold</option>
                            <option value="300" ${settings.fontWeight === '300' ? 'selected' : ''}>Light</option>
                            <option value="600" ${settings.fontWeight === '600' ? 'selected' : ''}>Semi-Bold</option>
                        </select>
                    </div>
                </div>

                <div class="customizer-section">
                    <h5><i class="bi bi-list-ul"></i> Legend</h5>
                    <div class="customizer-group">
                        <label>Show Legend:</label>
                        <input type="checkbox" id="legendDisplay_${chartId}" ${settings.legendDisplay ? 'checked' : ''}>
                    </div>
                    <div class="customizer-group">
                        <label>Legend Position:</label>
                        <select id="legendPosition_${chartId}">
                            <option value="top" ${settings.legendPosition === 'top' ? 'selected' : ''}>Top</option>
                            <option value="bottom" ${settings.legendPosition === 'bottom' ? 'selected' : ''}>Bottom</option>
                            <option value="left" ${settings.legendPosition === 'left' ? 'selected' : ''}>Left</option>
                            <option value="right" ${settings.legendPosition === 'right' ? 'selected' : ''}>Right</option>
                        </select>
                    </div>
                    <div class="customizer-group">
                        <label>Legend Style:</label>
                        <select id="legendStyle_${chartId}">
                            <option value="normal" ${settings.legendStyle === 'normal' ? 'selected' : ''}>Normal</option>
                            <option value="box" ${settings.legendStyle === 'box' ? 'selected' : ''}>Box</option>
                            <option value="circle" ${settings.legendStyle === 'circle' ? 'selected' : ''}>Circle</option>
                        </select>
                    </div>
                </div>

                <div class="customizer-section">
                    <h5><i class="bi bi-grid-3x3"></i> Grid</h5>
                    <div class="customizer-group">
                        <label>Show Grid:</label>
                        <input type="checkbox" id="gridDisplay_${chartId}" ${settings.gridDisplay ? 'checked' : ''}>
                    </div>
                    <div class="customizer-group">
                        <label>Grid Line Width:</label>
                        <input type="number" id="gridLineWidth_${chartId}" value="${settings.gridLineWidth}" min="0" max="5" step="0.5">
                    </div>
                </div>

                <div class="customizer-section">
                    <h5><i class="bi bi-lightning"></i> Animation</h5>
                    <div class="customizer-group">
                        <label>Enable Animation:</label>
                        <input type="checkbox" id="animationEnabled_${chartId}" ${settings.animationEnabled ? 'checked' : ''}>
                    </div>
                    <div class="customizer-group">
                        <label>Animation Duration (ms):</label>
                        <input type="number" id="animationDuration_${chartId}" value="${settings.animationDuration}" min="0" max="5000" step="100">
                    </div>
                    <div class="customizer-group">
                        <label>Animation Easing:</label>
                        <select id="animationEasing_${chartId}">
                            <option value="linear" ${settings.animationEasing === 'linear' ? 'selected' : ''}>Linear</option>
                            <option value="easeInQuad" ${settings.animationEasing === 'easeInQuad' ? 'selected' : ''}>Ease In Quad</option>
                            <option value="easeOutQuad" ${settings.animationEasing === 'easeOutQuad' ? 'selected' : ''}>Ease Out Quad</option>
                            <option value="easeInOutQuad" ${settings.animationEasing === 'easeInOutQuad' ? 'selected' : ''}>Ease In Out Quad</option>
                            <option value="easeOutQuart" ${settings.animationEasing === 'easeOutQuart' ? 'selected' : ''}>Ease Out Quart</option>
                        </select>
                    </div>
                </div>

                <div class="customizer-actions">
                    <button class="btn-apply" type="button">Apply Changes</button>
                    <button class="btn-reset" type="button">Reset to Default</button>
                </div>
            </div>
        `;

        // Attach event listeners
        attachCustomizerEventListeners(customizerPanel, chart, chartId);
        
        // Sync tooltip background color and text inputs
        const tooltipBgEl = customizerPanel.querySelector(`#tooltipBg_${chartId}`);
        const tooltipBgTextEl = customizerPanel.querySelector(`#tooltipBgText_${chartId}`);
        if (tooltipBgEl && tooltipBgTextEl) {
            // When color input changes, update text input (convert hex to rgba approximation)
            tooltipBgEl.addEventListener('input', () => {
                const hex = tooltipBgEl.value;
                // Convert hex to rgba (with 0.8 alpha for tooltip)
                const r = parseInt(hex.slice(1, 3), 16);
                const g = parseInt(hex.slice(3, 5), 16);
                const b = parseInt(hex.slice(5, 7), 16);
                tooltipBgTextEl.value = `rgba(${r}, ${g}, ${b}, 0.8)`;
            });
            // When text input changes, update color input if it's hex
            tooltipBgTextEl.addEventListener('input', () => {
                const value = tooltipBgTextEl.value;
                if (value.startsWith('#')) {
                    tooltipBgEl.value = value;
                }
            });
        }

        return customizerPanel;
    }

    /**
     * Attach event listeners to customizer panel
     * Can be called on existing panels (static or dynamic)
     */
    function attachCustomizerEventListeners(panel, chart, chartId) {
        if (!panel) return;

        // Remove existing listeners by cloning (clean slate)
        const applyBtn = panel.querySelector('.btn-apply');
        const resetBtn = panel.querySelector('.btn-reset');
        const closeBtn = panel.querySelector('.customizer-close');

        if (applyBtn) {
            // Remove old listeners by replacing the button
            const newApplyBtn = applyBtn.cloneNode(true);
            applyBtn.parentNode.replaceChild(newApplyBtn, applyBtn);
            newApplyBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                applyCustomizationFromUI(chart, chartId, panel);
            });
        }

        if (resetBtn) {
            const newResetBtn = resetBtn.cloneNode(true);
            resetBtn.parentNode.replaceChild(newResetBtn, resetBtn);
            newResetBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                resetCustomization(chart, chartId, panel);
            });
        }

        if (closeBtn) {
            const newCloseBtn = closeBtn.cloneNode(true);
            closeBtn.parentNode.replaceChild(newCloseBtn, closeBtn);
            newCloseBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                panel.style.display = 'none';
            });
        }
    }

    /**
     * Apply customization from UI inputs
     */
    function applyCustomizationFromUI(chart, chartId, panel) {
        try {
            // Get chart instance if not provided
            if (!chart && chartId) {
                const canvas = document.getElementById(chartId);
                if (canvas) {
                    if (window.Chart && Chart.getChart) {
                        chart = Chart.getChart(canvas);
                    } else if (canvas.__chartjs__) {
                        chart = canvas.__chartjs__;
                    }
                }
            }

            if (!chart) {
                console.error('Chart instance not found for chartId:', chartId);
                alert('Error: Chart not found. Please refresh the page.');
                return;
            }

            if (!panel) {
                console.error('Customizer panel not found');
                return;
            }

            // Get all input values with error handling
            const bgColorEl = panel.querySelector(`#bgColor_${chartId}`);
            const textColorEl = panel.querySelector(`#textColor_${chartId}`);
            const gridColorEl = panel.querySelector(`#gridColor_${chartId}`);
            const tooltipBgEl = panel.querySelector(`#tooltipBg_${chartId}`);
            const tooltipBgTextEl = panel.querySelector(`#tooltipBgText_${chartId}`);
            const fontFamilyEl = panel.querySelector(`#fontFamily_${chartId}`);
            const fontSizeEl = panel.querySelector(`#fontSize_${chartId}`);
            const fontWeightEl = panel.querySelector(`#fontWeight_${chartId}`);
            const legendDisplayEl = panel.querySelector(`#legendDisplay_${chartId}`);
            const legendPositionEl = panel.querySelector(`#legendPosition_${chartId}`);
            const legendStyleEl = panel.querySelector(`#legendStyle_${chartId}`);
            const gridDisplayEl = panel.querySelector(`#gridDisplay_${chartId}`);
            const gridLineWidthEl = panel.querySelector(`#gridLineWidth_${chartId}`);
            const animationEnabledEl = panel.querySelector(`#animationEnabled_${chartId}`);
            const animationDurationEl = panel.querySelector(`#animationDuration_${chartId}`);
            const animationEasingEl = panel.querySelector(`#animationEasing_${chartId}`);

            // Check if all required elements exist
            if (!bgColorEl || !textColorEl || !gridColorEl || !tooltipBgEl) {
                console.error('Required customizer input elements not found for chartId:', chartId);
                console.log('Available elements:', panel.querySelectorAll('[id*="' + chartId + '"]'));
                alert('Error: Customizer controls not properly initialized. Please refresh the page.');
                return;
            }

            // Get tooltip background color - prefer text input (for rgba) over color input (hex only)
            let tooltipBgColor = tooltipBgEl.value;
            if (tooltipBgTextEl && tooltipBgTextEl.value && 
                (tooltipBgTextEl.value.startsWith('rgba') || tooltipBgTextEl.value.startsWith('rgb'))) {
                tooltipBgColor = tooltipBgTextEl.value;
            }

            const settings = {
                backgroundColor: bgColorEl.value,
                textColor: textColorEl.value,
                gridColor: gridColorEl.value,
                tooltipBackgroundColor: tooltipBgColor,
                fontFamily: fontFamilyEl ? fontFamilyEl.value : 'Inter, system-ui, sans-serif',
                fontSize: fontSizeEl ? parseInt(fontSizeEl.value) || 12 : 12,
                fontWeight: fontWeightEl ? fontWeightEl.value : 'normal',
                legendDisplay: legendDisplayEl ? legendDisplayEl.checked : true,
                legendPosition: legendPositionEl ? legendPositionEl.value : 'top',
                legendStyle: legendStyleEl ? legendStyleEl.value : 'normal',
                gridDisplay: gridDisplayEl ? gridDisplayEl.checked : true,
                gridLineWidth: gridLineWidthEl ? parseFloat(gridLineWidthEl.value) || 1 : 1,
                animationEnabled: animationEnabledEl ? animationEnabledEl.checked : true,
                animationDuration: animationDurationEl ? parseInt(animationDurationEl.value) || 1000 : 1000,
                animationEasing: animationEasingEl ? animationEasingEl.value : 'easeOutQuart',
                tooltipEnabled: true,
                tooltipTextColor: '#ffffff',
                maintainAspectRatio: false,
                responsive: true,
                chartColors: null
            };

            saveChartSettings(chartId, settings);
            applyCustomization(chart, settings);
            
            // Show success feedback
            const applyBtn = panel.querySelector('.btn-apply');
            if (applyBtn) {
                const originalText = applyBtn.textContent;
                applyBtn.innerHTML = '<i class="bi bi-check-lg me-1" aria-hidden="true"></i>' + (window.t ? window.t('Applied') : 'Applied!');
                applyBtn.style.backgroundColor = '#10b981';
                setTimeout(() => {
                    applyBtn.textContent = originalText;
                    applyBtn.style.backgroundColor = '';
                }, 2000);
            }
        } catch (error) {
            console.error('Error applying customization:', error);
            alert('Error applying customization: ' + error.message);
        }
    }

    /**
     * Reset customization to default
     */
    function resetCustomization(chart, chartId, panel) {
        const defaultSettings = getDefaultSettings();
        saveChartSettings(chartId, defaultSettings);
        
        // Update UI
        panel.querySelector(`#bgColor_${chartId}`).value = defaultSettings.backgroundColor;
        panel.querySelector(`#textColor_${chartId}`).value = defaultSettings.textColor;
        panel.querySelector(`#gridColor_${chartId}`).value = defaultSettings.gridColor;
        const tooltipBgEl = panel.querySelector(`#tooltipBg_${chartId}`);
        const tooltipBgTextEl = panel.querySelector(`#tooltipBgText_${chartId}`);
        if (tooltipBgEl) tooltipBgEl.value = rgbaToHex(defaultSettings.tooltipBackgroundColor);
        if (tooltipBgTextEl) tooltipBgTextEl.value = defaultSettings.tooltipBackgroundColor;
        panel.querySelector(`#fontFamily_${chartId}`).value = defaultSettings.fontFamily;
        panel.querySelector(`#fontSize_${chartId}`).value = defaultSettings.fontSize;
        panel.querySelector(`#fontWeight_${chartId}`).value = defaultSettings.fontWeight;
        panel.querySelector(`#legendDisplay_${chartId}`).checked = defaultSettings.legendDisplay;
        panel.querySelector(`#legendPosition_${chartId}`).value = defaultSettings.legendPosition;
        panel.querySelector(`#legendStyle_${chartId}`).value = defaultSettings.legendStyle;
        panel.querySelector(`#gridDisplay_${chartId}`).checked = defaultSettings.gridDisplay;
        panel.querySelector(`#gridLineWidth_${chartId}`).value = defaultSettings.gridLineWidth;
        panel.querySelector(`#animationEnabled_${chartId}`).checked = defaultSettings.animationEnabled;
        panel.querySelector(`#animationDuration_${chartId}`).value = defaultSettings.animationDuration;
        panel.querySelector(`#animationEasing_${chartId}`).value = defaultSettings.animationEasing;
        
        applyCustomization(chart, defaultSettings);
    }

    /**
     * True when a chart can still be customised: it exists, it has a canvas,
     * and that canvas is still on the page. Chart.js clears `canvas` when a
     * chart is destroyed (for example when its type is changed and it is
     * rebuilt), and a soft navigation can remove the canvas. A deferred caller
     * checks this when its timer fires, so a replaced chart is skipped instead
     * of raising "Chart canvas is required".
     */
    function isAttachable(chart) {
        return !!(chart && chart.canvas && chart.canvas.isConnected && chart.config);
    }

    /**
     * Attach customization button to chart controls
     * Now creates controls if they don't exist
     * FIXED: Better validation and error handling
     */
    function attachCustomizationButton(container, chart, chartName) {
        // Validate inputs
        if (!chart) {
            throw new Error('Chart instance is required');
        }
        
        if (!chart.canvas) {
            throw new Error('Chart canvas is required');
        }
        
        if (!container) {
            throw new Error('Container element is required');
        }
        
        const chartId = chart.canvas.id;
        
        if (!chartId) {
            throw new Error('Chart canvas must have an ID');
        }
        
        // Validate chart.config exists
        if (!chart.config) {
            throw new Error('Chart config is required');
        }
        
        // Check if button already exists for THIS specific chart (by checking chart ID)
        const existingButton = chartId ? 
            container.querySelector(`.chart-customize-toggle[data-chart-id="${chartId}"]`) ||
            document.querySelector(`.chart-customize-toggle[data-chart-id="${chartId}"]`) :
            container.querySelector('.chart-customize-toggle');
        
        if (existingButton) {
            return; // Button already exists for this chart
        }
        
        // Find or create the controls wrapper
        let controlsWrapper = container.querySelector('.chart-controls-wrapper[data-chart-id="' + chartId + '"]') ||
                            container.closest('.chart-controls-wrapper') ||
                            container.querySelector('.chart-controls-wrapper');
        
        // If no controls wrapper exists, try to create chart controls first
        if (!controlsWrapper && window.ChartExport && typeof window.ChartExport.createChartControls === 'function') {
            try {
                // Try to create controls for this chart
                window.ChartExport.createChartControls(container, chart, chartName || chartId);
                // Try to find the controls wrapper again
                controlsWrapper = container.querySelector('.chart-controls-wrapper[data-chart-id="' + chartId + '"]') ||
                                container.closest('.chart-controls-wrapper') ||
                                container.querySelector('.chart-controls-wrapper');
            } catch (e) {
                console.warn('Could not create chart controls:', e);
            }
        }
        
        // If still no controls wrapper, create a minimal one
        if (!controlsWrapper) {
            controlsWrapper = document.createElement('div');
            controlsWrapper.className = 'chart-controls-wrapper';
            controlsWrapper.setAttribute('data-chart-id', chartId);
            
            const controlsContainer = document.createElement('div');
            controlsContainer.className = 'chart-controls-container';
            controlsWrapper.appendChild(controlsContainer);
            
            // Insert at the beginning of the chart container
            const chartContainer = chart.canvas.closest('.chart-container') || container;
            if (chartContainer) {
                chartContainer.insertBefore(controlsWrapper, chartContainer.firstChild);
            } else {
                container.appendChild(controlsWrapper);
            }
        }
        
        // Find or create export wrapper
        let exportWrapper = controlsWrapper.querySelector('.chart-export-wrapper');
        
        if (!exportWrapper) {
            // Create a minimal export wrapper structure
            const controlsContainer = controlsWrapper.querySelector('.chart-controls-container') || 
                                    document.createElement('div');
            if (!controlsContainer.classList.contains('chart-controls-container')) {
                controlsContainer.className = 'chart-controls-container';
                controlsWrapper.appendChild(controlsContainer);
            }
            
            exportWrapper = document.createElement('div');
            exportWrapper.className = 'chart-export-wrapper';
            controlsContainer.appendChild(exportWrapper);
        }

        // Create customize wrapper
        const customizeWrapper = document.createElement('div');
        customizeWrapper.className = 'chart-customize-wrapper';
        
        const toggleButton = document.createElement('button');
        toggleButton.className = 'chart-customize-toggle';
        toggleButton.type = 'button';
        toggleButton.title = 'Customize chart appearance';
        toggleButton.innerHTML = '<i class="bi bi-palette"></i>';
        toggleButton.setAttribute('aria-label', 'Customize chart appearance');
        // Mark button with chart ID to identify which chart it belongs to
        if (chartId) {
            toggleButton.setAttribute('data-chart-id', chartId);
        }
        
        // Find the chart container for the panel and button
        const chartContainerForPanel = chart.canvas.closest('.chart-container') || container;
        const chartContainerForButton = chart.canvas.closest('.chart-container-layout, .chart-container, .chart-wrapper, .chart-card') || 
                              chart.canvas.parentElement;
        
        // Check if panel already exists - try multiple methods
        let customizerPanel = chartContainerForPanel.querySelector(`.chart-customizer-panel[data-chart-id="${chartId}"]`);
        
        // If not found by data attribute, try finding by input IDs
        if (!customizerPanel) {
            const testInput = chartContainerForPanel.querySelector(`#bgColor_${chartId}`);
            if (testInput) {
                customizerPanel = testInput.closest('.chart-customizer-panel');
            }
        }
        
        // If still not found, look for any customizer panel in the container
        if (!customizerPanel) {
            customizerPanel = chartContainerForPanel.querySelector('.chart-customizer-panel');
        }
        
        if (!customizerPanel) {
            // Create customization panel
            customizerPanel = createCustomizationControls(chart, chartName, chartContainerForPanel);
            customizerPanel.setAttribute('data-chart-id', chartId);
            // Append to body for fixed positioning to work correctly
            document.body.appendChild(customizerPanel);
        } else {
            // Panel exists (might be static HTML) - ensure event listeners are attached
            if (!customizerPanel.hasAttribute('data-chart-id')) {
                customizerPanel.setAttribute('data-chart-id', chartId);
            }
            // If panel is not in body, move it there for proper fixed positioning
            if (customizerPanel.parentElement !== document.body) {
                document.body.appendChild(customizerPanel);
            }
            attachCustomizerEventListeners(customizerPanel, chart, chartId);
        }
        
        /**
         * Position the customizer panel intelligently to ensure it's always fully visible
         */
        function positionPanel(panel, button) {
            if (!panel || !button) return;
            
            const buttonRect = button.getBoundingClientRect();
            const panelRect = panel.getBoundingClientRect();
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;
            const panelWidth = panelRect.width || 400;
            const panelHeight = panelRect.height || 600;
            const spacing = 8; // Space between button and panel
            
            let top = 0;
            let left = 0;
            let right = 'auto';
            
            // Calculate horizontal position (prefer right-aligned, but adjust if needed)
            const spaceOnRight = viewportWidth - buttonRect.right;
            const spaceOnLeft = buttonRect.left;
            
            if (spaceOnRight >= panelWidth) {
                // Enough space on right - align to button's right edge
                left = buttonRect.right - panelWidth;
            } else if (spaceOnLeft >= panelWidth) {
                // Not enough space on right, but enough on left
                left = buttonRect.left;
            } else {
                // Not enough space on either side - center it
                left = Math.max(16, (viewportWidth - panelWidth) / 2);
            }
            
            // Ensure panel doesn't go off-screen horizontally
            left = Math.max(16, Math.min(left, viewportWidth - panelWidth - 16));
            
            // Calculate vertical position (prefer above, but use below if not enough space)
            const spaceAbove = buttonRect.top;
            const spaceBelow = viewportHeight - buttonRect.bottom;
            
            if (spaceAbove >= panelHeight + spacing) {
                // Enough space above - position above button
                top = buttonRect.top - panelHeight - spacing;
            } else if (spaceBelow >= panelHeight + spacing) {
                // Not enough space above, but enough below
                top = buttonRect.bottom + spacing;
            } else {
                // Not enough space above or below - use available space
                if (spaceAbove > spaceBelow) {
                    // More space above - position above with max height
                    top = Math.max(16, buttonRect.top - Math.min(panelHeight, spaceAbove - spacing));
                } else {
                    // More space below - position below with max height
                    top = Math.min(buttonRect.bottom + spacing, viewportHeight - Math.min(panelHeight, spaceBelow - spacing) - 16);
                }
            }
            
            // Ensure panel doesn't go off-screen vertically
            top = Math.max(16, Math.min(top, viewportHeight - Math.min(panelHeight, viewportHeight - 32) - 16));
            
            // Apply positioning
            panel.style.position = 'fixed';
            panel.style.top = top + 'px';
            panel.style.left = left + 'px';
            panel.style.right = 'auto';
            panel.style.bottom = 'auto';
            panel.style.maxHeight = Math.min(panelHeight, viewportHeight - top - 16) + 'px';
        }
        
        // Toggle panel visibility
        toggleButton.addEventListener('click', (e) => {
            e.stopPropagation();
            const isVisible = customizerPanel.style.display !== 'none';
            
            if (isVisible) {
                // Hide panel
                customizerPanel.style.display = 'none';
                toggleButton.classList.remove('active');
            } else {
                // Show panel - position it intelligently
                customizerPanel.style.display = 'block';
                
                // Use requestAnimationFrame to ensure panel is rendered before positioning
                requestAnimationFrame(() => {
                    positionPanel(customizerPanel, toggleButton);
                });
                
                toggleButton.classList.add('active');
                
                // Close other panels
                document.querySelectorAll('.chart-customizer-panel').forEach(panel => {
                    if (panel !== customizerPanel && panel.style.display !== 'none') {
                        panel.style.display = 'none';
                        const otherButton = document.querySelector(`.chart-customize-toggle[data-chart-id="${panel.getAttribute('data-chart-id')}"]`);
                        if (otherButton) {
                            otherButton.classList.remove('active');
                        }
                    }
                });
                
                // Add click-outside handler to close panel
                const clickOutsideHandler = (event) => {
                    if (!customizerPanel.contains(event.target) && 
                        !toggleButton.contains(event.target) &&
                        customizerPanel.style.display !== 'none') {
                        customizerPanel.style.display = 'none';
                        toggleButton.classList.remove('active');
                        document.removeEventListener('click', clickOutsideHandler);
                    }
                };
                
                // Use setTimeout to avoid immediate closure
                setTimeout(() => {
                    document.addEventListener('click', clickOutsideHandler);
                }, 100);
            }
        });
        
        // Reposition panel on window resize or scroll
        let resizeTimeout;
        const handleResize = () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                if (customizerPanel.style.display !== 'none') {
                    positionPanel(customizerPanel, toggleButton);
                }
            }, 100);
        };
        
        window.addEventListener('resize', handleResize);
        window.addEventListener('scroll', handleResize, true);
        
        customizeWrapper.appendChild(toggleButton);
        
        // CRITICAL: Insert customize button ABOVE the chart, not beside other controls
        
        if (chartContainerForButton) {
            // Create a separate wrapper for the customize button ABOVE the chart
            // Check if a customize button wrapper already exists
            let customizeButtonWrapper = chartContainerForButton.querySelector('.chart-customize-button-wrapper[data-chart-id="' + chartId + '"]');
            
            if (!customizeButtonWrapper) {
                customizeButtonWrapper = document.createElement('div');
                customizeButtonWrapper.className = 'chart-customize-button-wrapper';
                customizeButtonWrapper.setAttribute('data-chart-id', chartId);
                customizeButtonWrapper.style.cssText = 'margin-bottom: 0.75rem; display: flex; justify-content: flex-end;';
                
                // Insert BEFORE the chart canvas or chart-container-layout
                const canvas = chart.canvas;
                const chartLayout = canvas.closest('.chart-container-layout');
                
                if (chartLayout) {
                    // Insert before the chart layout container
                    chartLayout.parentElement.insertBefore(customizeButtonWrapper, chartLayout);
                } else {
                    // Insert before the canvas
                    canvas.parentElement.insertBefore(customizeButtonWrapper, canvas);
                }
            }
            
            // Add the customize button to the wrapper
            customizeButtonWrapper.innerHTML = '';
            customizeButtonWrapper.appendChild(customizeWrapper);
        } else {
            // Fallback: Insert in controls container if chart container not found
            const controlsContainer = controlsWrapper.querySelector('.chart-controls-container');
            if (controlsContainer) {
                controlsContainer.appendChild(customizeWrapper);
            } else if (exportWrapper.parentNode) {
                exportWrapper.parentNode.insertBefore(customizeWrapper, exportWrapper.nextSibling);
            }
        }
        
        // Ensure chart displays all data properly
        ensureChartDataDisplay(chart);
        
        // Apply saved settings on load
        const settings = getChartSettings(chartId);
        // Apply settings immediately and also after a short delay to ensure chart is fully initialized
        applyCustomization(chart, settings);
        setTimeout(() => {
            applyCustomization(chart, settings);
        }, 100);
    }
    
    /**
     * Attach customization buttons to all charts on the page
     * This ensures every chart gets a customization button
     * CRITICAL: This runs for ALL charts, not just one
     * FIXED: Prevents infinite retry loops
     */
    function attachCustomizationButtonsToAllCharts(container) {
        const containerEl = typeof container === 'string' 
            ? document.querySelector(container) 
            : container || document;
        
        if (!containerEl) {
            console.warn('Container not found for chart customization buttons');
            return;
        }
        
        // Find all canvas elements that might be charts
        const canvases = containerEl.querySelectorAll('canvas');
        
        if (canvases.length === 0) {
            return;
        }
        
        // Track which charts we've already processed to avoid duplicates
        const processedCharts = new Set();
        const chartsToRetry = [];
        
        canvases.forEach(canvas => {
            // Skip if canvas is hidden
            if (canvas.style.display === 'none') {
                return;
            }
            
            const canvasId = canvas.id;
            
            // Skip if no ID (might not be a chart)
            if (!canvasId) {
                return;
            }
            
            // Skip if we've already processed this chart successfully
            if (processedCharts.has(canvasId)) {
                return;
            }
            
            // Skip if this chart has failed too many times
            if (failedCharts.has(canvasId)) {
                return;
            }
            
            // Check if button already exists
            const existingButton = document.querySelector(`.chart-customize-toggle[data-chart-id="${canvasId}"]`);
            if (existingButton) {
                processedCharts.add(canvasId);
                return; // Already has button, skip
            }
            
            // Try to find the chart instance
            let chart = null;
            
            // Method 1: Chart.js getChart method (most reliable)
            if (window.Chart && Chart.getChart) {
                try {
                    chart = Chart.getChart(canvas);
                } catch (e) {
                    // Chart not ready or doesn't exist
                }
            }
            
            // Method 2: Check canvas.__chartjs__
            if (!chart && canvas.__chartjs__) {
                chart = canvas.__chartjs__;
            }
            
            // Method 3: Check for Chart instance in global registry
            if (!chart && window.Chart && Chart.registry) {
                if (canvasId && window.Chart.instances && window.Chart.instances[canvasId]) {
                    chart = window.Chart.instances[canvasId];
                }
            }
            
            // Validate chart instance
            if (chart && chart.canvas && chart.config) {
                // Valid chart found - mark as processed and attach button
                processedCharts.add(canvasId);
                
                // Find the chart container
                const chartContainer = canvas.closest('.chart-container, .chart-card, .chart-container-layout, .chart-wrapper, .section-card') || 
                                     canvas.parentElement;
                
                if (chartContainer) {
                    const chartName = canvasId || 
                                    chartContainer.querySelector('h2, h3, h4')?.textContent?.trim() ||
                                    'chart';
                    
                    // Attach customization button with error handling
                    try {
                        attachCustomizationButton(chartContainer, chart, chartName);
                        // Reset retry count on success
                        retryAttempts.delete(canvasId);
                    } catch (e) {
                        // Increment retry count
                        const currentRetries = retryAttempts.get(canvasId) || 0;
                        retryAttempts.set(canvasId, currentRetries + 1);
                        
                        // Only log if we haven't exceeded max retries
                        if (currentRetries < MAX_RETRY_ATTEMPTS) {
                            console.warn(`Error attaching customization button to chart "${canvasId}" (attempt ${currentRetries + 1}/${MAX_RETRY_ATTEMPTS}):`, e);
                            chartsToRetry.push({ canvasId, chartContainer, chart, chartName });
                        } else {
                            // Mark as failed after max retries
                            failedCharts.add(canvasId);
                            console.warn(`Chart "${canvasId}" failed to attach customization button after ${MAX_RETRY_ATTEMPTS} attempts. Skipping.`);
                        }
                    }
                } else {
                    // No container found - mark for retry if not exceeded
                    const currentRetries = retryAttempts.get(canvasId) || 0;
                    if (currentRetries < MAX_RETRY_ATTEMPTS) {
                        chartsToRetry.push({ canvasId, chartContainer: null, chart, chartName: canvasId });
                    } else {
                        failedCharts.add(canvasId);
                    }
                }
            } else {
                // Chart not found - might not be ready yet
                // Only retry if we haven't exceeded max attempts
                const currentRetries = retryAttempts.get(canvasId) || 0;
                if (currentRetries < MAX_RETRY_ATTEMPTS) {
                    chartsToRetry.push({ canvasId, chartContainer: null, chart: null, chartName: canvasId });
                } else {
                    // Mark as failed - probably not a Chart.js chart
                    failedCharts.add(canvasId);
                }
            }
        });
        
        // Retry failed charts once more after a delay (only if under retry limit)
        if (chartsToRetry.length > 0) {
            setTimeout(() => {
                chartsToRetry.forEach(({ canvasId, chartContainer, chart, chartName }) => {
                    const currentRetries = retryAttempts.get(canvasId) || 0;
                    if (currentRetries < MAX_RETRY_ATTEMPTS && !failedCharts.has(canvasId)) {
                        // Increment retry count
                        retryAttempts.set(canvasId, currentRetries + 1);
                        
                        // Try to find chart again
                        const canvas = document.getElementById(canvasId);
                        if (canvas) {
                            let foundChart = null;
                            if (window.Chart && Chart.getChart) {
                                try {
                                    foundChart = Chart.getChart(canvas);
                                } catch (e) {
                                    // Ignore
                                }
                            }
                            
                            if (foundChart && foundChart.canvas && foundChart.config) {
                                const container = chartContainer || canvas.closest('.chart-container, .chart-card, .chart-container-layout, .chart-wrapper, .section-card') || canvas.parentElement;
                                if (container) {
                                    try {
                                        attachCustomizationButton(container, foundChart, chartName);
                                        retryAttempts.delete(canvasId);
                                    } catch (e) {
                                        // Will be handled in next retry or marked as failed
                                    }
                                }
                            }
                        }
                    }
                });
            }, 500);
        }
    }

    /**
     * Initialize customization buttons for all charts on page load
     * Works on ANY HTML page with charts
     * FIXED: Prevents infinite loops and excessive retries
     */
    function initChartCustomization() {
        let initializationCount = 0;
        const MAX_INITIALIZATION_ATTEMPTS = 3;
        
        // Function to initialize all charts
        const initializeAll = () => {
            if (initializationCount >= MAX_INITIALIZATION_ATTEMPTS) {
                return; // Stop after max attempts
            }
            
            initializationCount++;
            attachListenersToAllPanels(); // Attach listeners to any existing static panels
            attachCustomizationButtonsToAllCharts(document);
        };
        
        // Wait for DOM to be ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                setTimeout(initializeAll, 500);
                // Run again after a delay to catch late-loading charts (only if under limit)
                setTimeout(() => {
                    if (initializationCount < MAX_INITIALIZATION_ATTEMPTS) {
                        initializeAll();
                    }
                }, 1500);
            });
        } else {
            setTimeout(initializeAll, 500);
            // Run again after a delay to catch late-loading charts (only if under limit)
            setTimeout(() => {
                if (initializationCount < MAX_INITIALIZATION_ATTEMPTS) {
                    initializeAll();
                }
            }, 1500);
        }
        
        // Also listen for dynamically added charts with debouncing
        let mutationTimeout = null;
        const observer = new MutationObserver((mutations) => {
            let shouldCheck = false;
            
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) { // Element node
                        // Check if it's a canvas or contains canvases or chart containers
                        if (node.tagName === 'CANVAS' || 
                            node.querySelector('canvas') ||
                            node.classList.contains('chart-container') ||
                            node.classList.contains('chart-container-layout') ||
                            node.classList.contains('chart-wrapper')) {
                            shouldCheck = true;
                        }
                    }
                });
            });
            
            if (shouldCheck) {
                // Debounce to avoid too many calls
                if (mutationTimeout) {
                    clearTimeout(mutationTimeout);
                }
                mutationTimeout = setTimeout(() => {
                    attachCustomizationButtonsToAllCharts(document);
                }, 300);
            }
        });
        
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    /**
     * Attach event listeners to all existing customizer panels
     * Useful when panels exist as static HTML
     */
    function attachListenersToAllPanels() {
        document.querySelectorAll('.chart-customizer-panel').forEach(panel => {
            const chartId = panel.getAttribute('data-chart-id');
            if (!chartId) {
                // Try to extract chart ID from input IDs
                const firstInput = panel.querySelector('[id*="_"]');
                if (firstInput && firstInput.id) {
                    const parts = firstInput.id.split('_');
                    if (parts.length > 1) {
                        const extractedId = parts.slice(1).join('_');
                        panel.setAttribute('data-chart-id', extractedId);
                        const canvas = document.getElementById(extractedId);
                        if (canvas) {
                            let chart = null;
                            if (window.Chart && Chart.getChart) {
                                chart = Chart.getChart(canvas);
                            } else if (canvas.__chartjs__) {
                                chart = canvas.__chartjs__;
                            }
                            if (chart) {
                                attachCustomizerEventListeners(panel, chart, extractedId);
                            }
                        }
                    }
                }
            } else {
                const canvas = document.getElementById(chartId);
                if (canvas) {
                    let chart = null;
                    if (window.Chart && Chart.getChart) {
                        chart = Chart.getChart(canvas);
                    } else if (canvas.__chartjs__) {
                        chart = canvas.__chartjs__;
                    }
                    if (chart) {
                        attachCustomizerEventListeners(panel, chart, chartId);
                    }
                }
            }
        });
    }

    /**
     * Reset failed charts tracking (useful for debugging or manual retry)
     */
    function resetFailedCharts() {
        failedCharts.clear();
        retryAttempts.clear();
    }
    
    /**
     * Manually retry attaching button to a specific chart
     */
    function retryChart(chartId) {
        if (!chartId) return false;
        
        // Remove from failed list
        failedCharts.delete(chartId);
        retryAttempts.delete(chartId);
        
        // Try to attach
        const canvas = document.getElementById(chartId);
        if (canvas) {
            let chart = null;
            if (window.Chart && Chart.getChart) {
                try {
                    chart = Chart.getChart(canvas);
                } catch (e) {
                    return false;
                }
            }
            
            if (chart && chart.canvas && chart.config) {
                const chartContainer = canvas.closest('.chart-container, .chart-card, .chart-container-layout, .chart-wrapper, .section-card') || 
                                     canvas.parentElement;
                if (chartContainer) {
                    try {
                        attachCustomizationButton(chartContainer, chart, chartId);
                        return true;
                    } catch (e) {
                        return false;
                    }
                }
            }
        }
        return false;
    }

    // Export functions
    window.ChartCustomizer = {
        attachCustomizationButton: attachCustomizationButton,
        isAttachable: isAttachable,
        attachCustomizationButtonsToAllCharts: attachCustomizationButtonsToAllCharts,
        attachCustomizerEventListeners: attachCustomizerEventListeners,
        attachListenersToAllPanels: attachListenersToAllPanels,
        applyCustomization: applyCustomization,
        applyCustomizationFromUI: applyCustomizationFromUI,
        getChartSettings: getChartSettings,
        saveChartSettings: saveChartSettings,
        ensureChartDataDisplay: ensureChartDataDisplay,
        resetFailedCharts: resetFailedCharts,
        retryChart: retryChart,
        init: initChartCustomization
    };
    
    // Auto-initialize on load
    initChartCustomization();
})();

