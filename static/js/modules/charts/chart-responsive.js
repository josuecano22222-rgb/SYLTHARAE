/**
 * Chart Responsiveness Helper
 * Ensures all Chart.js charts are properly responsive on all screen sizes
 * and support RTL/LTR layouts
 */

(function() {
    'use strict';
    
    // Default responsive configuration for all charts
    const defaultResponsiveConfig = {
        responsive: true,
        maintainAspectRatio: false,
        resizeDelay: 0,
        plugins: {
            legend: {
                labels: {
                    boxWidth: 12,
                    padding: 8,
                    font: {
                        size: 11
                    }
                }
            },
            tooltip: {
                padding: 8,
                titleFont: {
                    size: 12
                },
                bodyFont: {
                    size: 11
                },
                footerFont: {
                    size: 10
                }
            }
        }
    };
    
    // Mobile-specific chart configuration
    const mobileChartConfig = {
        plugins: {
            legend: {
                position: 'bottom',
                labels: {
                    boxWidth: 10,
                    padding: 6,
                    font: {
                        size: 10
                    }
                }
            },
            tooltip: {
                padding: 6,
                titleFont: {
                    size: 11
                },
                bodyFont: {
                    size: 10
                }
            }
        },
        scales: {
            x: {
                ticks: {
                    font: {
                        size: 10
                    },
                    maxRotation: 45,
                    minRotation: 0
                }
            },
            y: {
                ticks: {
                    font: {
                        size: 10
                    }
                }
            }
        }
    };
    
    /**
     * Apply responsive configuration to a chart
     */
    function applyResponsiveConfig(chart, isMobile) {
        if (!chart || !chart.options) return;
        
        // Merge default responsive config
        Object.assign(chart.options, defaultResponsiveConfig);
        
        // Apply mobile-specific config if on mobile
        if (isMobile) {
            if (chart.options.plugins) {
                Object.assign(chart.options.plugins, mobileChartConfig.plugins);
            } else {
                chart.options.plugins = mobileChartConfig.plugins;
            }
            
            if (chart.options.scales) {
                Object.assign(chart.options.scales, mobileChartConfig.scales);
            }
        }
        
        // Ensure responsive is enabled
        chart.options.responsive = true;
        chart.options.maintainAspectRatio = false;
        
        // Update the chart
        chart.update('none');
    }
    
    /**
     * Make a chart container responsive
     */
    function makeChartContainerResponsive(container) {
        if (!container) return;
        
        const canvas = container.querySelector('canvas');
        if (!canvas) return;
        
        // Ensure container has proper styling
        if (!container.classList.contains('chart-container') && 
            !container.classList.contains('chart-container-layout')) {
            container.classList.add('chart-container');
        }
        
        // Set container height based on screen size
        const isMobile = window.innerWidth <= 768;
        const isTablet = window.innerWidth > 768 && window.innerWidth <= 1024;
        
        if (container.classList.contains('large')) {
            container.style.height = isMobile ? '250px' : (isTablet ? '350px' : '400px');
        } else {
            container.style.height = isMobile ? '200px' : (isTablet ? '250px' : '300px');
        }
        
        // Ensure canvas fills container
        canvas.style.width = '100%';
        canvas.style.height = '100%';
    }
    
    /**
     * Initialize responsive behavior for all charts
     */
    function initChartResponsiveness() {
        // Check if Chart.js is loaded
        if (typeof Chart === 'undefined') {
            console.warn('Chart.js not loaded, chart responsiveness will be applied when Chart.js is available');
            return;
        }
        
        // Find all chart containers
        const chartContainers = document.querySelectorAll('.chart-container, .chart-container-layout, [id*="Chart"], [id*="chart"]');
        
        chartContainers.forEach(container => {
            makeChartContainerResponsive(container);
            
            // Find canvas and get chart instance
            const canvas = container.querySelector('canvas');
            if (canvas && canvas.chart) {
                const chart = canvas.chart;
                const isMobile = window.innerWidth <= 768;
                applyResponsiveConfig(chart, isMobile);
            }
        });
    }
    
    /**
     * Handle window resize for charts
     */
    let resizeTimeout;
    function handleResize() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            initChartResponsiveness();
            
            // Resize all existing charts
            if (typeof Chart !== 'undefined' && window.chartInstances) {
                Object.values(window.chartInstances).forEach(chart => {
                    if (!chart || typeof chart.resize !== 'function') return;
                    const canvas = chart.canvas;
                    if (!canvas || !canvas.ownerDocument || !canvas.isConnected) return;
                    chart.resize();
                });
            }
        }, 150);
    }
    
    /**
     * Override Chart.js Chart constructor to automatically apply responsive config
     */
    function enhanceChartConstructor() {
        if (typeof Chart === 'undefined') return;
        
        const OriginalChart = Chart;
        
        // Store original constructor
        const ChartConstructor = function(ctx, config) {
            const isMobile = window.innerWidth <= 768;
            
            // Merge responsive config into provided config
            if (config && config.options) {
                Object.assign(config.options, defaultResponsiveConfig);
                
                if (isMobile) {
                    if (config.options.plugins) {
                        Object.assign(config.options.plugins, mobileChartConfig.plugins);
                    } else {
                        config.options.plugins = mobileChartConfig.plugins;
                    }
                    
                    if (config.options.scales) {
                        Object.assign(config.options.scales, mobileChartConfig.scales);
                    }
                }
            } else if (config) {
                config.options = Object.assign({}, defaultResponsiveConfig);
                if (isMobile) {
                    config.options.plugins = mobileChartConfig.plugins;
                    config.options.scales = mobileChartConfig.scales;
                }
            }
            
            // Call original constructor
            const chart = new OriginalChart(ctx, config);
            
            // Store reference for later resizing
            if (!window.chartInstances) {
                window.chartInstances = {};
            }
            if (ctx && ctx.id) {
                window.chartInstances[ctx.id] = chart;
            }
            
            // Make container responsive
            if (ctx && ctx.parentElement) {
                makeChartContainerResponsive(ctx.parentElement);
            }
            
            return chart;
        };
        
        // Copy static methods and properties
        Object.setPrototypeOf(ChartConstructor, OriginalChart);
        Object.assign(ChartConstructor, OriginalChart);
        ChartConstructor.prototype = OriginalChart.prototype;
        
        // Replace global Chart
        window.Chart = ChartConstructor;
    }
    
    /**
     * Initialize when DOM is ready
     */
    function init() {
        // Wait for Chart.js to load
        if (typeof Chart !== 'undefined') {
            enhanceChartConstructor();
            initChartResponsiveness();
        } else {
            // Try again after a delay
            setTimeout(() => {
                if (typeof Chart !== 'undefined') {
                    enhanceChartConstructor();
                    initChartResponsiveness();
                }
            }, 100);
        }
        
        // Handle window resize
        window.addEventListener('resize', handleResize);
        
        // Handle orientation change
        window.addEventListener('orientationchange', function() {
            setTimeout(handleResize, 200);
        });
    }
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
    // Also initialize after a short delay to catch dynamically loaded charts
    setTimeout(init, 500);
    
    // Export utility functions for manual use
    window.ChartResponsive = {
        applyConfig: applyResponsiveConfig,
        makeContainerResponsive: makeChartContainerResponsive,
        init: initChartResponsiveness,
        defaultConfig: defaultResponsiveConfig,
        mobileConfig: mobileChartConfig
    };
})();

// Export for ES modules (functions are exposed via window.ChartResponsive after IIFE executes)
const ChartResponsiveModule = {
    get applyConfig() { return window.ChartResponsive?.applyConfig; },
    get makeContainerResponsive() { return window.ChartResponsive?.makeContainerResponsive; },
    get init() { return window.ChartResponsive?.init; },
    get defaultConfig() { return window.ChartResponsive?.defaultConfig; },
    get mobileConfig() { return window.ChartResponsive?.mobileConfig; }
};

export default ChartResponsiveModule;

