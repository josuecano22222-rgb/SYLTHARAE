/**
 * Chart Export Utility
 * Provides functionality to export Chart.js charts in multiple formats:
 * - PNG (native Chart.js support)
 * - JPG (converted from PNG)
 * - SVG (converted from canvas)
 * - PDF (using jsPDF if available)
 */

(function() {
    'use strict';

    /**
     * Export a chart to the specified format
     * @param {Chart} chart - Chart.js chart instance
     * @param {string} format - Export format: 'png', 'jpg', 'svg', 'pdf'
     * @param {string} filename - Optional filename (without extension)
     */
    function exportChart(chart, format, filename) {
        if (!chart || !chart.canvas) {
            console.error('Invalid chart instance');
            return;
        }

        const canvas = chart.canvas;
        const defaultFilename = filename || `chart_${new Date().toISOString().split('T')[0]}`;

        switch (format.toLowerCase()) {
            case 'png':
                exportAsPNG(canvas, defaultFilename);
                break;
            case 'jpg':
            case 'jpeg':
                exportAsJPG(canvas, defaultFilename);
                break;
            case 'svg':
                exportAsSVG(canvas, defaultFilename);
                break;
            case 'pdf':
                exportAsPDF(canvas, defaultFilename);
                break;
            default:
                console.error('Unsupported format:', format);
        }
    }

    /**
     * Export chart as PNG
     */
    function exportAsPNG(canvas, filename) {
        try {
            // Use Chart.js toBase64Image method if available, otherwise use canvas.toDataURL
            const dataURL = canvas.toDataURL('image/png');
            downloadImage(dataURL, `${filename}.png`, 'image/png');
        } catch (error) {
            console.error('Error exporting PNG:', error);
            const msg = (window.t && window.t('Error exporting chart as PNG')) || 
                       (window.appTranslations && window.appTranslations['Error exporting chart as PNG']) ||
                       'Error exporting chart as PNG';
            if (window.MessageSystem) {
                window.MessageSystem.show(msg, 'error');
            } else {
                alert(msg);
            }
        }
    }

    /**
     * Export chart as JPG
     */
    function exportAsJPG(canvas, filename) {
        try {
            // Convert to JPG with quality 0.95
            const dataURL = canvas.toDataURL('image/jpeg', 0.95);
            downloadImage(dataURL, `${filename}.jpg`, 'image/jpeg');
        } catch (error) {
            console.error('Error exporting JPG:', error);
            const msg = (window.t && window.t('Error exporting chart as JPG')) || 
                       (window.appTranslations && window.appTranslations['Error exporting chart as JPG']) ||
                       'Error exporting chart as JPG';
            if (window.MessageSystem) {
                window.MessageSystem.show(msg, 'error');
            } else {
                alert(msg);
            }
        }
    }

    /**
     * Export chart as SVG
     */
    function exportAsSVG(canvas, filename) {
        try {
            const svg = canvasToSVG(canvas);
            const blob = new Blob([svg], { type: 'image/svg+xml' });
            const url = URL.createObjectURL(blob);
            downloadFile(url, `${filename}.svg`);
            URL.revokeObjectURL(url);
        } catch (error) {
            console.error('Error exporting SVG:', error);
            const msg = (window.t && window.t('Error exporting chart as SVG')) || 
                       (window.appTranslations && window.appTranslations['Error exporting chart as SVG']) ||
                       'Error exporting chart as SVG';
            if (window.MessageSystem) {
                window.MessageSystem.show(msg, 'error');
            } else {
                alert(msg);
            }
        }
    }

    /**
     * Export chart as PDF
     */
    function exportAsPDF(canvas, filename) {
        try {
            // Check if jsPDF is available
            if (typeof window.jspdf === 'undefined' && typeof window.jsPDF === 'undefined') {
                // Fallback to PNG download with PDF extension suggestion
                const msg = (window.t && window.t('PDF export requires jsPDF library. Downloading as PNG instead.')) || 
                           (window.appTranslations && window.appTranslations['PDF export requires jsPDF library. Downloading as PNG instead.']) ||
                           'PDF export requires jsPDF library. Downloading as PNG instead.';
                if (window.MessageSystem) {
                    window.MessageSystem.show(msg, 'warning');
                } else {
                    alert(msg);
                }
                exportAsPNG(canvas, filename);
                return;
            }

            const jsPDF = window.jsPDF || window.jspdf.jsPDF;
            const imgData = canvas.toDataURL('image/png');
            
            // Calculate dimensions
            const imgWidth = canvas.width;
            const imgHeight = canvas.height;
            const pdfWidth = jsPDF.internal.pageSize.getWidth();
            const pdfHeight = (imgHeight * pdfWidth) / imgWidth;
            
            const pdf = new jsPDF({
                orientation: pdfHeight > pdfWidth ? 'portrait' : 'landscape',
                unit: 'px',
                format: [pdfWidth, pdfHeight]
            });
            
            pdf.addImage(imgData, 'PNG', 0, 0, pdfWidth, pdfHeight);
            pdf.save(`${filename}.pdf`);
        } catch (error) {
            console.error('Error exporting PDF:', error);
            // Fallback to PNG
            const msg = (window.t && window.t('Error exporting PDF. Downloading as PNG instead.')) || 
                       (window.appTranslations && window.appTranslations['Error exporting PDF. Downloading as PNG instead.']) ||
                       'Error exporting PDF. Downloading as PNG instead.';
            if (window.MessageSystem) {
                window.MessageSystem.show(msg, 'error');
            } else {
                alert(msg);
            }
            exportAsPNG(canvas, filename);
        }
    }

    /**
     * Convert canvas to SVG
     */
    function canvasToSVG(canvas) {
        const imgData = canvas.toDataURL('image/png');
        const svg = `
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" 
     width="${canvas.width}" height="${canvas.height}">
    <image width="${canvas.width}" height="${canvas.height}" 
           xlink:href="${imgData}"/>
</svg>`.trim();
        return svg;
    }

    /**
     * Download image file
     */
    function downloadImage(dataURL, filename, mimeType) {
        const link = document.createElement('a');
        link.download = filename;
        link.href = dataURL;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    /**
     * Download file from URL
     */
    function downloadFile(url, filename) {
        const link = document.createElement('a');
        link.download = filename;
        link.href = url;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    /**
     * Change chart type dynamically
     * @param {Chart} chart - Chart.js chart instance
     * @param {string} newType - New chart type: 'bar', 'line', 'pie', 'doughnut', 'radar', 'polarArea', 'scatter', 'bubble'
     */
    function changeChartType(chart, newType) {
        if (!chart) {
            console.error('Invalid chart instance');
            return null;
        }

        // Store canvas reference BEFORE destroying (important!)
        const canvas = chart.canvas;
        if (!canvas) {
            console.error('Chart canvas not found');
            return null;
        }

        // Store original data and options
        const originalData = JSON.parse(JSON.stringify(chart.data));
        const originalOptions = JSON.parse(JSON.stringify(chart.options));
        const originalType = chart.config ? chart.config.type : chart.type || 'bar';
        
        // Get container before destroying
        const container = canvas.closest('.chart-container, .chart-card, .chart-container-layout, .section-card, [class*="chart"]');
        
        // Destroy existing chart
        try {
            if (chart.destroy && typeof chart.destroy === 'function') {
                chart.destroy();
            }
        } catch (e) {
            console.warn('Error destroying chart:', e);
        }
        
        // Clear canvas
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Adjust options based on chart type
        const adjustedOptions = adjustOptionsForChartType(originalOptions, newType);
        
        // Create new chart with new type
        let newChart;
        try {
            newChart = new Chart(ctx, {
                type: newType,
                data: originalData,
                options: adjustedOptions
            });
        } catch (e) {
            console.error('Error creating new chart:', e);
            // Try to restore original chart type if new type fails
            try {
                newChart = new Chart(ctx, {
                    type: originalType,
                    data: originalData,
                    options: originalOptions
                });
            } catch (e2) {
                console.error('Error restoring original chart:', e2);
                return null;
            }
        }
        
        // Update chart reference in canvas (Chart.js stores it here)
        canvas.__chartjs__ = newChart;
        
        // Also register with Chart.js if available
        if (window.Chart && Chart.registry) {
            // Chart.js automatically registers charts, but ensure it's accessible
            try {
                const chartId = newChart.id;
                if (chartId && window.Chart.instances) {
                    window.Chart.instances[chartId] = newChart;
                }
            } catch (e) {
                // Ignore registration errors
            }
        }
        
        // Update export buttons to use new chart instance
        if (container) {
            const exportWrapper = container.querySelector('.chart-controls-wrapper');
            if (exportWrapper) {
                // Update chart reference in export buttons
                const exportButtons = exportWrapper.querySelectorAll('.chart-export-btn');
                exportButtons.forEach(btn => {
                    btn.onclick = function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        const format = btn.dataset.format;
                        const chartName = btn.dataset.chartName;
                        exportChart(newChart, format, chartName);
                    };
                });
            }
        }
        
        return newChart;
    }

    /**
     * Adjust chart options based on chart type
     */
    function adjustOptionsForChartType(options, chartType) {
        const adjusted = JSON.parse(JSON.stringify(options));
        
        // Ensure plugins object exists
        if (!adjusted.plugins) {
            adjusted.plugins = {};
        }
        
        // For line and bar charts, ensure scales are present
        if (chartType === 'line' || chartType === 'bar') {
            if (!adjusted.scales) {
                adjusted.scales = {
                    y: { beginAtZero: true },
                    x: {}
                };
            }
            // Ensure legend settings
            if (!adjusted.plugins.legend) {
                adjusted.plugins.legend = { display: false };
            }
        } else if (chartType === 'pie' || chartType === 'doughnut') {
            // For pie and doughnut, remove scales
            delete adjusted.scales;
            // Show legend by default
            if (!adjusted.plugins.legend) {
                adjusted.plugins.legend = { 
                    display: true,
                    position: 'bottom'
                };
            } else if (adjusted.plugins.legend.display === undefined) {
                adjusted.plugins.legend.display = true;
            }
        } else if (chartType === 'radar' || chartType === 'polarArea') {
            // For radar and polarArea, ensure appropriate settings
            if (!adjusted.scales) {
                adjusted.scales = {};
            }
            if (!adjusted.plugins.legend) {
                adjusted.plugins.legend = { display: true };
            }
        } else {
            // For scatter, bubble, etc.
            if (!adjusted.scales) {
                adjusted.scales = {
                    y: { beginAtZero: true },
                    x: { beginAtZero: true }
                };
            }
            if (!adjusted.plugins.legend) {
                adjusted.plugins.legend = { display: true };
            }
        }
        
        // Ensure responsive is set
        if (adjusted.responsive === undefined) {
            adjusted.responsive = true;
        }
        
        // Ensure maintainAspectRatio is set
        if (adjusted.maintainAspectRatio === undefined) {
            adjusted.maintainAspectRatio = false;
        }
        
        return adjusted;
    }

    /**
     * Create export buttons and chart type selector for a chart container
     * @param {HTMLElement} container - Chart container element
     * @param {Chart} chart - Chart.js chart instance
     * @param {string} chartName - Optional chart name for filename
     */
    function createChartControls(container, chart, chartName) {
        // Get canvas element directly first to get its ID
        const canvas = chart.canvas;
        if (!canvas) {
            console.error('Canvas not found for chart controls');
            return;
        }
        
        // Store canvas ID for later reference
        const canvasId = canvas.id;
        
        // IMPORTANT: Check if controls already exist SPECIFICALLY for THIS chart (by canvas ID)
        // This allows multiple charts in the same container to each have their own controls
        if (canvasId) {
            // Check if controls exist for this specific chart by canvas ID
            const existingControls = document.querySelector(`.chart-controls-wrapper[data-chart-id="${canvasId}"]`);
            if (existingControls) {
                return; // Controls already exist for this specific chart
            }
            
            // Also check if the chart type selector for this chart exists
            const existingSelector = document.getElementById(`chartTypeSelect_${canvasId}`);
            if (existingSelector) {
                return; // Controls already exist for this chart
            }
        }
        
        // Fallback: Check if controls exist in the container itself (only if no canvas ID)
        // But only if there's no canvas ID to identify the chart uniquely
        if (!canvasId && container.querySelector('.chart-controls-wrapper')) {
            // Only skip if we can't uniquely identify this chart
            console.warn('Chart has no ID, cannot create unique controls');
            return;
        }

        // Store original chart type in container data attribute
        const originalChartType = chart && chart.config ? chart.config.type : (chart.type || 'bar');
        container.setAttribute('data-original-chart-type', originalChartType);
        container.setAttribute('data-current-chart-type', originalChartType);
        
        // Create wrapper for all chart controls (at top)
        const controlsWrapper = document.createElement('div');
        controlsWrapper.className = 'chart-controls-wrapper';
        // Mark this controls wrapper with the chart ID for identification
        if (canvasId) {
            controlsWrapper.setAttribute('data-chart-id', canvasId);
        }
        
        // Create controls container
        const controlsContainer = document.createElement('div');
        controlsContainer.className = 'chart-controls-container';
        
        // Create chart type selector
        const typeSelectorWrapper = document.createElement('div');
        typeSelectorWrapper.className = 'chart-type-selector-wrapper';
        
        // Use canvas ID for unique identification, fallback to chartName
        const uniqueId = canvasId || chartName || 'chart';
        
        const typeLabel = document.createElement('label');
        typeLabel.className = 'chart-type-label';
        typeLabel.innerHTML = '<i class="bi bi-graph-up"></i> <span>Chart Type:</span>';
        typeLabel.setAttribute('for', `chartTypeSelect_${uniqueId}`);
        
        const typeSelect = document.createElement('select');
        typeSelect.id = `chartTypeSelect_${uniqueId}`;
        typeSelect.className = 'chart-type-select';
        typeSelect.setAttribute('aria-label', 'Select chart type');
        
        // Available chart types
        const chartTypes = [
            { value: 'bar', label: 'Bar' },
            { value: 'line', label: 'Line' },
            { value: 'pie', label: 'Pie' },
            { value: 'doughnut', label: 'Doughnut' },
            { value: 'radar', label: 'Radar' },
            { value: 'polarArea', label: 'Polar Area' },
            { value: 'scatter', label: 'Scatter' },
            { value: 'bubble', label: 'Bubble' }
        ];
        
        // Get current chart type (use the one already stored in container or get from chart)
        const currentChartType = container.getAttribute('data-original-chart-type') || 
                                 (chart && chart.config ? chart.config.type : (chart.type || 'bar'));
        
        chartTypes.forEach(({ value, label }) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            // Set current chart type as selected
            if (currentChartType === value) {
                option.selected = true;
            }
            typeSelect.appendChild(option);
        });
        
        // Chart type change handler - always get current chart from canvas
        typeSelect.addEventListener('change', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const newType = this.value;
            
            // Get canvas element directly (by ID or from container)
            let canvasElement = null;
            if (canvasId) {
                canvasElement = document.getElementById(canvasId);
            }
            if (!canvasElement) {
                // Fallback: find canvas in container
                canvasElement = container.querySelector('canvas');
            }
            
            if (!canvasElement) {
                console.error('Canvas not found');
                const originalType = container.getAttribute('data-original-chart-type') || 'bar';
                this.value = originalType;
                return;
            }
            
            // Always get the CURRENT chart instance from the canvas (not from closure)
            let currentChart = null;
            
            // Try Chart.js getChart method first (most reliable)
            if (window.Chart && Chart.getChart) {
                currentChart = Chart.getChart(canvasElement);
            }
            
            // Fallback to canvas internal reference
            if (!currentChart && canvasElement.__chartjs__) {
                currentChart = canvasElement.__chartjs__;
            }
            
            if (!currentChart) {
                console.error('Current chart instance not found on canvas');
                const originalType = container.getAttribute('data-original-chart-type') || 'bar';
                this.value = originalType;
                return;
            }
            
            const newChart = changeChartType(currentChart, newType);
            
            // Update chart reference
            if (newChart) {
                // Update the chart in the global charts object if it exists
                if (canvasElement && canvasElement.id) {
                    // Try to find and update chart in various chart storage objects
                    if (window.charts) {
                        // Try different property names
                        Object.keys(window.charts).forEach(key => {
                            if (window.charts[key] === currentChart) {
                                window.charts[key] = newChart;
                            }
                        });
                        // Also try by canvas id
                        if (window.charts[canvasElement.id]) {
                            window.charts[canvasElement.id] = newChart;
                        }
                        // Try common property names
                        const commonNames = ['fileType', 'status', 'timeline', 'wordFrequency', 'classification', 
                                           'storage', 'storageTimeline', 'successRate', 'processingSpeed', 
                                           'contentCoverage', 'topCategories', 'timeline', 'processing', 
                                           'categoryPie', 'categoryBar', 'categoriesChart', 'sourcesChart', 
                                           'distributionChart'];
                        commonNames.forEach(name => {
                            if (window.charts[name] === currentChart) {
                                window.charts[name] = newChart;
                            }
                        });
                    }
                    if (window.state && window.state.charts) {
                        Object.keys(window.state.charts).forEach(key => {
                            if (window.state.charts[key] === currentChart) {
                                window.state.charts[key] = newChart;
                            }
                        });
                        if (window.state.charts[canvasElement.id]) {
                            window.state.charts[canvasElement.id] = newChart;
                        }
                    }
                    // Check chartInstances
                    if (window.chartInstances) {
                        Object.keys(window.chartInstances).forEach(key => {
                            if (window.chartInstances[key] === currentChart) {
                                window.chartInstances[key] = newChart;
                            }
                        });
                    }
                }
                
                // Update export button handlers to use new chart
                const exportButtons = buttonContainer.querySelectorAll('.chart-export-btn');
                exportButtons.forEach(btn => {
                    btn.onclick = function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        const format = btn.dataset.format;
                        const chartName = btn.dataset.chartName;
                        exportChart(newChart, format, chartName);
                    };
                });
                
                // Update selector to reflect new chart type (should already be set, but ensure it)
                typeSelect.value = newType;
                
                // Update container data attribute
                container.setAttribute('data-current-chart-type', newType);
                
                // Verify the chart was created successfully
                console.log('Chart type changed to:', newType, 'Chart instance:', newChart);
            } else {
                // If chart creation failed, reset selector to current type
                const originalType = container.getAttribute('data-original-chart-type') || 'bar';
                const currentType = currentChart && currentChart.config ? currentChart.config.type : (currentChart && currentChart.type ? currentChart.type : originalType);
                this.value = currentType;
                container.setAttribute('data-current-chart-type', currentType);
                console.error('Failed to change chart type, reverted to:', currentType);
            }
        });
        
        typeSelectorWrapper.appendChild(typeLabel);
        typeSelectorWrapper.appendChild(typeSelect);
        
        // Create export controls
        const exportWrapper = document.createElement('div');
        exportWrapper.className = 'chart-export-wrapper';
        
        // Create toggle button to show/hide export options
        const toggleButton = document.createElement('button');
        toggleButton.className = 'chart-export-toggle';
        toggleButton.type = 'button';
        toggleButton.title = 'Show export options';
        toggleButton.innerHTML = '<i class="bi bi-download"></i>';
        toggleButton.setAttribute('aria-label', 'Show export options');
        
        // Create button container (hidden by default)
        const buttonContainer = document.createElement('div');
        buttonContainer.className = 'chart-export-buttons';
        buttonContainer.style.display = 'none';
        
        // Create buttons for each format
        const formats = [
            { format: 'png', icon: 'bi-file-earmark-image', label: 'PNG' },
            { format: 'jpg', icon: 'bi-file-earmark-image', label: 'JPG' },
            { format: 'svg', icon: 'bi-file-earmark-code', label: 'SVG' },
            { format: 'pdf', icon: 'bi-file-earmark-pdf', label: 'PDF' }
        ];

        formats.forEach(({ format, icon, label }) => {
            const button = document.createElement('button');
            button.className = `chart-export-btn chart-export-${format}`;
            button.type = 'button';
            button.title = `Export as ${label}`;
            button.dataset.format = format;
            button.dataset.chartName = chartName;
            button.dataset.canvasId = canvasId; // Store canvas ID for fallback lookup
            button.innerHTML = `<i class="bi ${icon}"></i> <span>${label}</span>`;
            button.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                // Get current chart instance dynamically (chart may have changed type)
                // Try multiple methods to get the canvas and chart
                let canvas = null;
                let currentChart = null;
                
                // Method 1: Try from chart reference (if still valid)
                if (chart && chart.canvas) {
                    canvas = chart.canvas;
                }
                
                // Method 2: Try to find canvas by ID (stored in button data)
                if (!canvas && canvasId) {
                    canvas = document.getElementById(canvasId);
                }
                
                // Method 3: Try to find canvas in container
                if (!canvas && container) {
                    canvas = container.querySelector('canvas');
                }
                
                if (!canvas) {
                    console.error('Canvas not found, cannot export');
                    return;
                }
                
                // Try to get chart instance from canvas
                if (window.Chart && Chart.getChart) {
                    currentChart = Chart.getChart(canvas);
                }
                
                // Fallback to canvas internal reference
                if (!currentChart && canvas.__chartjs__) {
                    currentChart = canvas.__chartjs__;
                }
                
                // Last resort: use original chart reference (if still valid)
                if (!currentChart && chart && chart.canvas === canvas) {
                    currentChart = chart;
                }
                
                if (!currentChart) {
                    console.error('Current chart instance not found, cannot export');
                    return;
                }
                
                const filename = chartName ? `${chartName}_${format}` : undefined;
                exportChart(currentChart, format, filename);
            });
            buttonContainer.appendChild(button);
        });

        // Toggle button click handler
        let closeHandler = null;
        toggleButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const isVisible = buttonContainer.style.display !== 'none';
            buttonContainer.style.display = isVisible ? 'none' : 'flex';
            toggleButton.setAttribute('aria-expanded', !isVisible);
            toggleButton.title = isVisible ? 'Show export options' : 'Hide export options';
            
            // Update icon
            if (isVisible) {
                toggleButton.innerHTML = '<i class="bi bi-download"></i>';
                // Remove close handler when closing
                if (closeHandler) {
                    document.removeEventListener('click', closeHandler);
                    closeHandler = null;
                }
            } else {
                toggleButton.innerHTML = '<i class="bi bi-x-circle"></i>';
                // Add close handler when opening
                if (!closeHandler) {
                    closeHandler = function(e) {
                        if (!exportWrapper.contains(e.target) && buttonContainer.style.display !== 'none') {
                            buttonContainer.style.display = 'none';
                            toggleButton.innerHTML = '<i class="bi bi-download"></i>';
                            toggleButton.setAttribute('aria-expanded', 'false');
                            toggleButton.title = 'Show export options';
                            document.removeEventListener('click', closeHandler);
                            closeHandler = null;
                        }
                    };
                    // Use setTimeout to avoid immediate closure
                    setTimeout(() => {
                        document.addEventListener('click', closeHandler);
                    }, 10);
                }
            }
        });

        // Assemble export wrapper
        exportWrapper.appendChild(toggleButton);
        exportWrapper.appendChild(buttonContainer);
        
        // Assemble controls container
        controlsContainer.appendChild(typeSelectorWrapper);
        controlsContainer.appendChild(exportWrapper);
        
        // Assemble main wrapper
        controlsWrapper.appendChild(controlsContainer);
        
        // Attach customization button if customizer is available
        if (window.ChartCustomizer && typeof window.ChartCustomizer.attachCustomizationButton === 'function') {
            try {
                // Use setTimeout to ensure chart is fully initialized
                setTimeout(() => {
                    // The chart may have been replaced (type change) or its
                    // canvas removed before the timer fired: skip, do not throw.
                    if (window.ChartCustomizer.isAttachable && !window.ChartCustomizer.isAttachable(chart)) return;
                    window.ChartCustomizer.attachCustomizationButton(container, chart, chartName);
                }, 150);
            } catch (e) {
                console.warn('Could not attach chart customizer:', e);
            }
        }

        // CRITICAL: Always insert controls INSIDE the chart-container, BEFORE the canvas
        // This ensures controls appear above the chart and each chart gets its own controls
        
        // Find the actual chart container that contains the canvas
        let actualChartContainer = null;
        
        // Method 1: If container is already a chart-container, use it
        if (container.classList.contains('chart-container')) {
            actualChartContainer = container;
        }
        
        // Method 2: Find chart-container by canvas ID
        if (!actualChartContainer && canvasId) {
            const canvasElement = document.getElementById(canvasId);
            if (canvasElement) {
                actualChartContainer = canvasElement.closest('.chart-container');
            }
        }
        
        // Method 3: Find chart-container in the container
        if (!actualChartContainer) {
            actualChartContainer = container.querySelector('.chart-container');
        }
        
        // Method 4: If canvas exists, find its parent chart-container
        if (!actualChartContainer && canvas) {
            actualChartContainer = canvas.closest('.chart-container');
        }
        
        // If we found a chart-container, insert controls at the beginning (before canvas)
        if (actualChartContainer) {
            // Find the canvas in this container to insert before it
            const canvasInContainer = actualChartContainer.querySelector('canvas');
            if (canvasInContainer) {
                // Insert controls right before the canvas
                actualChartContainer.insertBefore(controlsWrapper, canvasInContainer);
            } else {
                // No canvas found, insert at the beginning
                actualChartContainer.insertBefore(controlsWrapper, actualChartContainer.firstChild);
            }
            return; // Exit early - controls are now in the chart container above the canvas
        }
        
        // If no chart-container found, create one or use canvas parent
        if (canvas && canvas.parentElement) {
            // Insert controls before canvas in its parent
            canvas.parentElement.insertBefore(controlsWrapper, canvas);
            return;
        }
        
        // Fallback: Insert wrapper after title but before chart container
        // This ensures buttons are above the chart without offsetting issues
        // Find the parent card (chart-card or section-card) to place controls correctly
        const parentCard = container.closest('.chart-card, .section-card, .file-details-analysis-section, .classification-charts-container') || container;
        const title = parentCard.querySelector('h3, h4, .chart-title, .section-header, .file-details-section-header h3');
        const chartContainer = parentCard.querySelector('.chart-container') || 
                               parentCard.querySelector('.chart-container-layout') ||
                               parentCard.querySelector('.chart-and-table-container') ||
                               (parentCard.querySelector('canvas') ? parentCard.querySelector('canvas').closest('.chart-container') || parentCard.querySelector('canvas').parentElement : null);
        
        // Special handling for file-details-analysis-section and classification-charts-container structure
        const isFileAnalysisSection = container.closest('.file-details-analysis-section') || 
                                     container.id === 'fileAnalysisSection' ||
                                     container.closest('.classification-charts-container');
        const classificationContainer = container.closest('.classification-charts-container') || 
                                       container.querySelector('.classification-charts-container');
        
        if (isFileAnalysisSection || classificationContainer) {
            // For classification charts, place buttons below the filter input
            const targetContainer = classificationContainer || container;
            const filterContainer = targetContainer.querySelector('.chart-filter-container');
            const chartAndTableContainer = targetContainer.querySelector('.chart-and-table-container') || 
                                          targetContainer.querySelector('.chart-container');
            
            if (filterContainer) {
                // Insert after the filter container (below the filter)
                const parent = filterContainer.parentElement;
                if (parent) {
                    // Insert right after filter container
                    if (filterContainer.nextSibling) {
                        parent.insertBefore(controlsWrapper, filterContainer.nextSibling);
                    } else {
                        parent.appendChild(controlsWrapper);
                    }
                } else {
                    // Fallback: insert in targetContainer after filter
                    targetContainer.insertBefore(controlsWrapper, filterContainer.nextSibling);
                }
                return; // Exit early for this case
            } else if (chartAndTableContainer) {
                // Fallback: Insert before the chart-and-table-container if no filter found
                const parent = chartAndTableContainer.parentElement;
                if (parent) {
                    parent.insertBefore(controlsWrapper, chartAndTableContainer);
                } else {
                    targetContainer.insertBefore(controlsWrapper, chartAndTableContainer);
                }
                return; // Exit early for this case
            }
        }
        
        // Ensure we don't create a new section - place controls in existing structure
        if (title && chartContainer && title.parentNode === parentCard && chartContainer.parentNode === parentCard) {
            // Insert between title and chart container in the same parent card
            parentCard.insertBefore(controlsWrapper, chartContainer);
        } else if (title && title.parentNode === parentCard) {
            // Insert after title in parent card
            const titleNext = title.nextSibling;
            if (titleNext && titleNext.classList && titleNext.classList.contains('chart-container')) {
                parentCard.insertBefore(controlsWrapper, titleNext);
            } else {
                parentCard.insertBefore(controlsWrapper, title.nextSibling);
            }
        } else if (chartContainer && chartContainer.parentNode === parentCard) {
            // For multiple charts in same section, insert controls INSIDE the chart container
            // This ensures each chart gets its own controls
            if (chartContainer.classList.contains('chart-container')) {
                // Insert at the beginning of the chart container (before canvas)
                const canvas = chartContainer.querySelector('canvas');
                if (canvas) {
                    chartContainer.insertBefore(controlsWrapper, canvas);
                } else {
                    chartContainer.insertBefore(controlsWrapper, chartContainer.firstChild);
                }
            } else {
                // Insert before chart container in parent card
                parentCard.insertBefore(controlsWrapper, chartContainer);
            }
        } else if (container !== parentCard) {
            // If container is not the parent card, try to find the right place in container
            const containerTitle = container.querySelector('h3, h4, .chart-title, .file-details-section-header h3');
            const containerChart = container.querySelector('.chart-container, .chart-and-table-container, canvas');
            if (containerTitle && containerChart) {
                container.insertBefore(controlsWrapper, containerChart);
            } else if (containerTitle) {
                container.insertBefore(controlsWrapper, containerTitle.nextSibling);
            } else if (containerChart) {
                container.insertBefore(controlsWrapper, containerChart);
            } else {
                container.insertBefore(controlsWrapper, container.firstChild);
            }
        } else {
            // Fallback: insert at start of parent card
            parentCard.insertBefore(controlsWrapper, parentCard.firstChild);
        }
    }

    /**
     * Auto-attach export buttons to all charts in a container
     * @param {HTMLElement|string} container - Container element or selector
     */
    // Track containers that are being processed to prevent duplicate calls
    // Use WeakSet to avoid memory leaks, but fallback to Set for string IDs
    const processingContainers = new WeakSet();
    const processingContainerIds = new Set();
    
    function attachExportButtonsToCharts(container) {
        // Prevent infinite recursion - check if we're already in a call stack
        if (attachExportButtonsToCharts._processing) {
            console.warn('attachExportButtonsToCharts already processing, skipping recursive call');
            return;
        }
        
        attachExportButtonsToCharts._processing = true;
        
        try {
            const containerEl = typeof container === 'string' 
                ? document.querySelector(container) 
                : container;
            
            if (!containerEl) {
                console.warn('Container not found for chart export buttons');
                return;
            }
            
            // Prevent duplicate processing of the same container
            // Check if this container is already being processed
            if (processingContainers.has(containerEl)) {
                return; // Already processing this container
            }
            
            // Also check by ID/class to catch cases where same container is passed differently
            const containerId = containerEl.id || 
                           (containerEl.className ? containerEl.className.split(' ')[0] : null) ||
                           'unknown_' + Date.now();
            if (processingContainerIds.has(containerId)) {
                return; // Already processing this container
            }
            
            // Mark as processing
            processingContainers.add(containerEl);
            processingContainerIds.add(containerId);
            
            // Clear the flags after processing (with a delay to prevent rapid re-processing)
            setTimeout(() => {
                processingContainerIds.delete(containerId);
                // Note: WeakSet doesn't have delete, but entries are automatically garbage collected
            }, 2000);

            // Find all canvas elements that might be charts
            const canvases = containerEl.querySelectorAll('canvas');
            
            if (canvases.length === 0) {
                return;
            }
            
            canvases.forEach(canvas => {
                // Try to find the chart instance - try multiple methods
                let chart = null;
                
                // Method 1: Chart.js getChart method (most reliable)
                if (window.Chart && Chart.getChart) {
                    chart = Chart.getChart(canvas);
                }
                
                // Method 2: Direct property access
                if (!chart && canvas.__chartjs__) {
                    chart = canvas.__chartjs__;
                }
                
                // Method 3: Check if chart was just created (might not be registered yet)
                if (!chart) {
                    // Wait a bit and try again for dynamically created charts
                    setTimeout(() => {
                        const delayedChart = (window.Chart && Chart.getChart && Chart.getChart(canvas)) || canvas.__chartjs__;
                        if (delayedChart) {
                            attachChartControlsToCanvas(canvas, delayedChart, containerEl);
                        }
                    }, 100);
                }
                
                if (chart) {
                    attachChartControlsToCanvas(canvas, chart, containerEl);
                }
            });
        } finally {
            // Always clear the processing flag
            attachExportButtonsToCharts._processing = false;
        }
    }
    
    /**
     * Helper function to attach controls to a specific canvas/chart
     */
    function attachChartControlsToCanvas(canvas, chart, containerEl) {
        // Find the chart container (prefer chart-container class)
        let chartContainer = canvas.closest('.chart-container');
        
        // If no chart-container found, try other container types
        if (!chartContainer) {
            chartContainer = canvas.closest('.chart-card, .chart-container-layout, .file-details-analysis-section, .classification-charts-container, .chart-and-table-container, [class*="chart"]');
        }
        
        // Last resort: use parent element
        if (!chartContainer) {
            chartContainer = canvas.parentElement;
        }
        
        if (chartContainer && chart) {
            // Use canvas ID to identify the specific chart
            const canvasId = canvas.id;
            const chartName = canvasId || 
                            chartContainer.querySelector('h3, h4, .file-details-section-header h3')?.textContent?.trim().toLowerCase().replace(/\s+/g, '_') ||
                            'chart_' + Date.now(); // Use timestamp as fallback to ensure uniqueness
            
            // Check if controls exist specifically for this chart by canvas ID
            // This is critical - we check for THIS specific chart, not just any chart
            const controlsForThisChart = canvasId ? (
                document.querySelector(`.chart-controls-wrapper[data-chart-id="${canvasId}"]`) ||
                document.querySelector(`#chartTypeSelect_${canvasId}`)
            ) : null;
            
            if (!controlsForThisChart) {
                // Create controls for this specific chart
                // Always pass the chart-container if found, otherwise the container
                createChartControls(chartContainer, chart, chartName);
            }
            
            // Also ensure customization button is attached (even if controls already exist)
            if (window.ChartCustomizer && typeof window.ChartCustomizer.attachCustomizationButton === 'function') {
                setTimeout(() => {
                    // Same rule as above: a replaced or detached chart is skipped.
                    if (window.ChartCustomizer.isAttachable && !window.ChartCustomizer.isAttachable(chart)) return;
                    try {
                        window.ChartCustomizer.attachCustomizationButton(chartContainer, chart, chartName);
                    } catch (e) {
                        console.warn('Could not attach customization button:', e);
                    }
                }, 150);
            }
        }
    }

    /**
     * Initialize export buttons for all charts on the page
     */
    function initChartExports() {
        // Function to attach controls to all charts
        function attachToAllCharts() {
            // Find all canvas elements that might be charts
            const canvases = document.querySelectorAll('canvas');
            canvases.forEach(canvas => {
                // Try to find the chart instance - use multiple methods
                let chart = null;
                if (window.Chart && Chart.getChart) {
                    chart = Chart.getChart(canvas);
                }
                if (!chart && canvas.__chartjs__) {
                    chart = canvas.__chartjs__;
                }
                
                if (chart) {
                    // Find the chart container - include all possible container types
                    let chartContainer = canvas.closest('.chart-container, .chart-card, .chart-container-layout, .section-card, .file-details-analysis-section, .classification-charts-container, .chart-and-table-container, [class*="chart"]');
                    if (!chartContainer) {
                        chartContainer = canvas.parentElement;
                    }
                    
                    if (chartContainer) {
                        // Check if controls exist SPECIFICALLY for THIS chart (by canvas ID)
                        // This allows multiple charts to each have their own controls
                        const canvasId = canvas.id;
                        const controlsExist = canvasId ? (
                            document.querySelector(`.chart-controls-wrapper[data-chart-id="${canvasId}"]`) ||
                            document.querySelector(`#chartTypeSelect_${canvasId}`)
                        ) : null;
                        
                        if (!controlsExist) {
                            // Get chart name from container or canvas id
                            const chartName = canvasId || 
                                            chartContainer.querySelector('h3, h4, .chart-title, .file-details-section-header h3')?.textContent?.trim().toLowerCase().replace(/\s+/g, '_') ||
                                            'chart_' + Date.now();
                            
                            // Always create controls - the function will check for duplicates internally
                            createChartControls(chartContainer, chart, chartName);
                        }
                    }
                }
            });
        }

        // Initial attachment - wait for charts to be rendered
        setTimeout(() => {
            attachToAllCharts();
        }, 500);

        // Also listen for dynamically added charts
        // Use debouncing to prevent multiple rapid calls
        let mutationCheckTimeout = null;
        const observer = new MutationObserver((mutations) => {
            let hasCanvas = false;
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) { // Element node
                        // Check if a canvas was added, or if a container with canvas was added
                        if (node.matches && (node.matches('canvas') || node.querySelector('canvas'))) {
                            hasCanvas = true;
                        } else if (node.querySelector && node.querySelector('canvas')) {
                            hasCanvas = true;
                        }
                    }
                });
            });
            
            if (hasCanvas) {
                // Debounce to prevent duplicate calls
                clearTimeout(mutationCheckTimeout);
                mutationCheckTimeout = setTimeout(() => {
                    attachToAllCharts();
                }, 300);
            }
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
        
        // Also periodically check for new charts (fallback) - but less frequently to avoid duplicates
        // Only check charts that don't already have controls
        setInterval(() => {
            const canvases = document.querySelectorAll('canvas');
            let needsCheck = false;
            canvases.forEach(canvas => {
                const chart = (window.Chart && Chart.getChart && Chart.getChart(canvas)) || canvas.__chartjs__;
                if (chart) {
                    const container = canvas.closest('.chart-container, .chart-card, .section-card, .file-details-analysis-section, .classification-charts-container');
                    if (container && !container.querySelector('.chart-controls-wrapper')) {
                        needsCheck = true;
                    }
                }
            });
            if (needsCheck) {
                attachToAllCharts();
            }
        }, 5000); // Reduced frequency from 2000ms to 5000ms to prevent duplicates
        
        // Special handling for fileAnalysisSection - check when it's populated
        // Use a debounced function to prevent multiple rapid calls
        let fileAnalysisSectionCheckTimeout = null;
        const fileAnalysisSectionObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.addedNodes.length > 0) {
                    // Check if a canvas was added to fileAnalysisSection
                    const fileAnalysisSection = document.getElementById('fileAnalysisSection');
                    if (fileAnalysisSection && fileAnalysisSection.querySelector('canvas')) {
                        // Debounce to prevent duplicate calls
                        clearTimeout(fileAnalysisSectionCheckTimeout);
                        fileAnalysisSectionCheckTimeout = setTimeout(() => {
                            attachToAllCharts();
                        }, 400);
                    }
                }
            });
        });
        
        // Set up observer for fileAnalysisSection if it exists, or wait for it
        function setupFileAnalysisObserver() {
            const fileAnalysisSection = document.getElementById('fileAnalysisSection');
            if (fileAnalysisSection) {
                fileAnalysisSectionObserver.observe(fileAnalysisSection, {
                    childList: true,
                    subtree: true
                });
            } else {
                // Retry if not found yet (might be created dynamically)
                setTimeout(setupFileAnalysisObserver, 500);
            }
        }
        setupFileAnalysisObserver();
    }

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initChartExports);
    } else {
        initChartExports();
    }

    // Export public API
    window.ChartExport = {
        exportChart: exportChart,
        changeChartType: changeChartType,
        createChartControls: createChartControls,
        attachExportButtonsToCharts: attachExportButtonsToCharts,
        init: initChartExports
    };
    
    // Store direct references in closure for module exports (prevents recursion)
    window.__ChartExportFunctions = {
        exportChart: exportChart,
        changeChartType: changeChartType,
        createChartControls: createChartControls,
        attachExportButtonsToCharts: attachExportButtonsToCharts,
        init: initChartExports
    };

})();

// Export for ES modules (functions are exposed via window.ChartExport after IIFE executes)
// Use direct function references from closure to prevent infinite recursion
function getChartExportFunction(name) {
    // First, try to get from the closure variable (set by IIFE) - this avoids recursion
    if (window.__ChartExportFunctions && typeof window.__ChartExportFunctions[name] === 'function') {
        return window.__ChartExportFunctions[name];
    }
    
    // Fallback: try window.ChartExport, but check if it's a getter to avoid recursion
    if (window.ChartExport) {
        const descriptor = Object.getOwnPropertyDescriptor(window.ChartExport, name);
        // If it's a getter, we're in recursion - don't access it
        if (descriptor && descriptor.get) {
            console.warn(`ChartExport.${name} is a getter - possible infinite recursion. Use window.__ChartExportFunctions or wait for IIFE.`);
            return undefined;
        }
        // It's a direct function property - safe to return
        if (typeof window.ChartExport[name] === 'function') {
            return window.ChartExport[name];
        }
    }
    
    // If not available yet, return undefined (caller should wait for IIFE)
    return undefined;
}

const ChartExportModule = {
    get exportChart() { return getChartExportFunction('exportChart'); },
    get changeChartType() { return getChartExportFunction('changeChartType'); },
    get createChartControls() { return getChartExportFunction('createChartControls'); },
    get attachExportButtonsToCharts() { return getChartExportFunction('attachExportButtonsToCharts'); },
    get init() { return getChartExportFunction('init'); }
};

export default ChartExportModule;

