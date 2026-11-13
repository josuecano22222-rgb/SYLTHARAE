/**
 * Settings UI Controller - Frontend
 * File: static/js/settings/settings-ui.js
 * 
 * Handles all UI interactions for settings page
 * Clean architecture - no legacy code
 */

import settingsState from './settings-state.js';

async function refreshSidebarAfterSettingsChange() {
    if (typeof window.refreshSidebarNavigation !== 'function') return false;
    return window.refreshSidebarNavigation();
}

async function refreshSidebarBrandingAfterSettingsChange() {
    if (typeof window.refreshSidebarBranding !== 'function') return false;
    return window.refreshSidebarBranding();
}

class SettingsUI {
    constructor() {
        this.state = settingsState;
        this.saveButtons = new Map();
        this.initialized = false;
        
        // Wait for DOM
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.init());
        } else {
            this.init();
        }
    }
    
    async init() {
        if (this.initialized) return;
        this.initialized = true;
        
        // Subscribe to state changes
        this.state.subscribe((event, data) => this.handleStateChange(event, data));
        
        // Setup UI elements
        this.setupInputHandlers();
        this.setupSaveButtons();
        this.setupActionButtons();
        
        // Populate form with current values
        await this.populateForm();
        
        // Expose global functions for inline handlers (backward compatibility)
        this.exposeGlobalFunctions();
        
        console.log('✅ Settings UI initialized');
    }
    
    /**
     * Expose global functions for inline event handlers in templates
     */
    exposeGlobalFunctions() {
        const self = this; // Capture 'this' for use in arrow functions
        
        // toggleSystemSetting - for system settings dropdowns and checkboxes
        window.toggleSystemSetting = (key, value) => {
            // Convert value types
            if (typeof value === 'string' && (value === 'true' || value === 'false')) {
                value = value === 'true';
            } else if (typeof value === 'string' && !isNaN(value) && value.trim() !== '') {
                // Try to parse as number if it looks like one
                const numValue = parseFloat(value);
                if (!isNaN(numValue)) {
                    value = numValue;
                }
            }
            
            // Set the setting using dot notation
            self.state.set(`system.${key}`, value);
            self.updateSaveButtons(); // Update save buttons
        };
        
        // toggleUserSetting - for user/display/search/processing/notifications settings
        window.toggleUserSetting = (category, key, value) => {
            // Convert value types
            if (typeof value === 'string' && (value === 'true' || value === 'false')) {
                value = value === 'true';
            } else if (typeof value === 'string' && !isNaN(value) && value.trim() !== '') {
                // Try to parse as number if it looks like one
                const numValue = parseFloat(value);
                if (!isNaN(numValue)) {
                    value = numValue;
                }
            }
            
            // Set the setting using dot notation
            self.state.set(`${category}.${key}`, value);
            self.updateSaveButtons(); // Update save buttons
        };
        
        // updateThemeColor - for theme color pickers
        window.updateThemeColor = (key, value) => {
            // Normalize color value
            if (!value.startsWith('#') && !value.startsWith('rgb')) {
                value = '#' + value;
            }
            
            // Map kebab-case to snake_case for settings key
            const settingKey = key.replace(/-/g, '_');
            
            // Set theme color
            self.state.set(`theme.${settingKey}`, value);
            
            // Apply live preview
            self.applyLivePreview(`theme.${settingKey}`, value);
            
            // Update save buttons
            self.updateSaveButtons();
        };
        
        // updateColorSwatch - for color swatch updates
        window.updateColorSwatch = (inputId, value) => {
            const colorInput = document.getElementById(inputId);
            const textInput = document.getElementById(inputId + 'Text');
            
            if (colorInput) {
                colorInput.value = value;
            }
            if (textInput) {
                textInput.value = value;
            }
        };
        
        // saveThemeColors - save all theme color changes
        window.saveThemeColors = async () => {
            try {
                await self.saveSettings();
                // Broadcast changes to other tabs/pages
                if (typeof BroadcastChannel !== 'undefined') {
                    const channel = new BroadcastChannel('settings_changes');
                    channel.postMessage({
                        type: 'settings_saved',
                        changes: self.state.getChanges()
                    });
                }
            } catch (error) {
                console.error('Failed to save theme colors:', error);
                self.showError(`Failed to save: ${error.message}`);
            }
        };
        
        // resetThemeColor - reset a single theme color to default
        window.resetThemeColor = (key) => {
            // Map kebab-case to snake_case for settings key
            const settingKey = key.replace(/-/g, '_');
            
            // Default values mapping
            const defaults = {
                'primary_color': '#4f46e5',
                'secondary_color': '#06b6d4',
                'success_color': '#10b981',
                'danger_color': '#ef4444',
                'warning_color': '#f59e0b',
                'info_color': '#3b82f6',
                'bg_light': '#f8fafc',
                'bg_white': '#ffffff',
                'bg_section': '#f8fafc',
                'text_dark': '#1e293b',
                'text_light': '#64748b',
                'text_muted': '#94a3b8',
                'border_color': '#e2e8f0',
                'border_light': '#f1f5f9',
                'border_dark': '#cbd5e1',
                'sidebar_bg': '#1e293b',
                'sidebar_text': '#e2e8f0',
                'sidebar_active': '#4f46e5',
                'sidebar_hover': '#334155',
                'btn_primary': '#4f46e5',
                'btn_primary_hover': '#4338ca',
                'btn_secondary': '#06b6d4',
                'btn_secondary_hover': '#0891b2',
                'gradient_start': '#4f46e5',
                'gradient_end': '#06b6d4',
                'gradient_direction': '135deg'
            };
            
            const defaultValue = defaults[settingKey];
            if (defaultValue) {
                self.state.set(`theme.${settingKey}`, defaultValue);
                self.applyLivePreview(`theme.${settingKey}`, defaultValue);
                
                // Update UI inputs
                const colorInputId = key.charAt(0).toUpperCase() + key.slice(1).replace(/-([a-z])/g, (g) => g[1].toUpperCase());
                const colorInput = document.getElementById(colorInputId) || document.getElementById(key.replace(/-/g, ''));
                const textInput = document.getElementById(colorInputId + 'Text') || document.getElementById(key.replace(/-/g, '') + 'Text');
                
                if (colorInput) {
                    colorInput.value = defaultValue;
                }
                if (textInput) {
                    textInput.value = defaultValue;
                }
            }
        };
        
        // resetAllThemeColors - reset all theme colors to defaults
        window.resetAllThemeColors = async () => {
            if (!confirm('Are you sure you want to reset all theme colors to defaults?')) {
                return;
            }
            
            const defaults = {
                'primary_color': '#4f46e5',
                'secondary_color': '#06b6d4',
                'success_color': '#10b981',
                'danger_color': '#ef4444',
                'warning_color': '#f59e0b',
                'bg_light': '#f8fafc',
                'bg_white': '#ffffff',
                'bg_section': '#f8fafc',
                'text_dark': '#1e293b',
                'text_light': '#64748b',
                'text_muted': '#94a3b8',
                'border_color': '#e2e8f0',
                'border_light': '#f1f5f9',
                'border_dark': '#cbd5e1',
                'sidebar_bg': '#1e293b',
                'sidebar_text': '#e2e8f0',
                'sidebar_active': '#4f46e5',
                'sidebar_hover': '#334155',
                'btn_primary': '#4f46e5',
                'btn_primary_hover': '#4338ca',
                'btn_secondary': '#06b6d4',
                'btn_secondary_hover': '#0891b2',
                'gradient_start': '#4f46e5',
                'gradient_end': '#06b6d4',
                'gradient_direction': '135deg'
            };
            
            // Set all defaults
            for (const [key, value] of Object.entries(defaults)) {
                self.state.set(`theme.${key}`, value);
                self.applyLivePreview(`theme.${key}`, value);
            }
            
            // Reload form to update UI
            await self.populateForm();
            
            self.showSuccess('All theme colors reset to defaults');
        };
        
        // previewThemeColors - preview theme colors (already applied live)
        window.previewThemeColors = () => {
            self.showSuccess('Colors are applied in real-time. Scroll to see changes throughout the page.');
        };
        
        // openGlobalCssEditor - open CSS editor modal
        window.openGlobalCssEditor = () => {
            const modal = document.getElementById('globalCssModal');
            if (modal) {
                const bsModal = new bootstrap.Modal(modal);
                bsModal.show();
            }
        };
        
        // saveInterfaceSettings - save interface visibility changes
        window.saveInterfaceSettings = async () => {
            try {
                // Get all interface toggles
                const toggles = document.querySelectorAll('.interface-toggle');
                const updates = {};
                
                toggles.forEach(toggle => {
                    const interfaceId = toggle.dataset.interfaceId;
                    if (interfaceId) {
                        updates[`interfaces.${interfaceId}.enabled`] = toggle.checked;
                    }
                });
                
                // Save via batch update
                const response = await fetch('/api/settings/batch', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': self.getCSRFToken()
                    },
                    body: JSON.stringify({ updates })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    // Reload settings state
                    await self.state.load();
                    
                    // Format changes for broadcast (nested structure)
                    const broadcastChanges = {
                        interfaces: {}
                    };
                    Object.keys(updates).forEach(key => {
                        const parts = key.split('.');
                        if (parts.length >= 3 && parts[0] === 'interfaces') {
                            const interfaceId = parts[1];
                            const attr = parts[2];
                            if (!broadcastChanges.interfaces[interfaceId]) {
                                broadcastChanges.interfaces[interfaceId] = {};
                            }
                            broadcastChanges.interfaces[interfaceId][attr] = updates[key];
                        }
                    });
                    
                    // Broadcast changes
                    if (typeof BroadcastChannel !== 'undefined') {
                        const channel = new BroadcastChannel('settings_changes');
                        channel.postMessage({
                            type: 'settings_saved',
                            changes: broadcastChanges
                        });
                    }
                    
                    const sidebarUpdated = await refreshSidebarAfterSettingsChange();
                    if (sidebarUpdated) {
                        self.showSuccess('Interface settings saved and the sidebar updated.');
                    } else {
                        self.showError('Settings were saved, but the sidebar could not be updated. Retry the change or navigate again.');
                    }
                } else {
                    throw new Error(data.error || 'Failed to save');
                }
            } catch (error) {
                console.error('Failed to save interface settings:', error);
                self.showError(`Failed to save: ${error.message}`);
            }
        };
        
        // resetInterfaceSettings - reset all interfaces to defaults
        window.resetInterfaceSettings = async () => {
            if (!confirm('Are you sure you want to reset all interface settings to defaults?')) {
                return;
            }
            
            try {
                const response = await fetch('/api/settings/interfaces/reset', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': self.getCSRFToken()
                    }
                });
                
                const data = await response.json();
                
                if (data.success) {
                    // Reload settings state
                    await self.state.load();
                    
                    const sidebarUpdated = await refreshSidebarAfterSettingsChange();
                    if (sidebarUpdated) {
                        self.showSuccess('Interface settings reset and the sidebar updated.');
                    } else {
                        self.showError('Settings were reset, but the sidebar could not be updated. Retry the change or navigate again.');
                    }
                } else {
                    throw new Error(data.error || 'Failed to reset');
                }
            } catch (error) {
                console.error('Failed to reset interface settings:', error);
                self.showError(`Failed to reset: ${error.message}`);
            }
        };
        
        // removeLogo - remove application logo
        window.removeLogo = async () => {
            if (!confirm('Are you sure you want to remove the logo?')) {
                return;
            }
            
            try {
                const response = await fetch('/api/settings/logo/remove', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': self.getCSRFToken()
                    }
                });
                
                const data = await response.json();
                
                if (data.success) {
                    const sidebarUpdated = await refreshSidebarBrandingAfterSettingsChange();
                    if (sidebarUpdated) self.showSuccess('Logo removed successfully');
                    else self.showError('Logo removed, but the sidebar could not be updated. Retry the change or navigate again.');
                } else {
                    throw new Error(data.error || 'Failed to remove logo');
                }
            } catch (error) {
                console.error('Failed to remove logo:', error);
                self.showError(`Failed to remove logo: ${error.message}`);
            }
        };
        
        // openIconPicker - open icon picker
        window.openIconPicker = () => {
            const modal = document.getElementById('iconPickerModal');
            if (modal) {
                const bsModal = new bootstrap.Modal(modal);
                bsModal.show();
            }
        };
        
        // selectIcon - select an icon from the picker
        window.selectIcon = (iconClass) => {
            const iconInput = document.getElementById('appIcon');
            if (iconInput) {
                iconInput.value = iconClass;
                window.updateAppBranding('app_icon', iconClass);
            }
            
            // Close modal
            const modal = bootstrap.Modal.getInstance(document.getElementById('iconPickerModal'));
            if (modal) {
                modal.hide();
            }
        };
        
        // filterIcons - filter icons in the picker
        window.filterIcons = (searchTerm) => {
            const iconItems = document.querySelectorAll('.icon-item');
            const term = searchTerm.toLowerCase();
            
            iconItems.forEach(item => {
                const iconName = item.dataset.icon || '';
                const text = item.textContent.toLowerCase();
                
                if (iconName.includes(term) || text.includes(term)) {
                    item.style.display = '';
                } else {
                    item.style.display = 'none';
                }
            });
        };
        
        // handleLogoUpload - handle logo file upload
        window.handleLogoUpload = async (input) => {
            const file = input.files[0];
            if (!file) return;
            
            // Validate file size (5MB max)
            if (file.size > 5 * 1024 * 1024) {
                self.showError('File size must be less than 5MB');
                input.value = '';
                return;
            }
            
            // Validate file type
            const validTypes = ['image/png', 'image/jpeg', 'image/jpg', 'image/svg+xml', 'image/gif', 'image/webp', 'image/x-icon', 'image/vnd.microsoft.icon'];
            if (!validTypes.includes(file.type)) {
                self.showError('Invalid file type. Please upload PNG, JPG, SVG, GIF, WebP, or ICO');
                input.value = '';
                return;
            }
            
            try {
                const formData = new FormData();
                formData.append('logo', file);
                
                const response = await fetch('/api/settings/logo/upload', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': self.getCSRFToken()
                    },
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success) {
                    const sidebarUpdated = await refreshSidebarBrandingAfterSettingsChange();
                    if (sidebarUpdated) self.showSuccess('Logo uploaded successfully');
                    else self.showError('Logo uploaded, but the sidebar could not be updated. Retry the change or navigate again.');
                } else {
                    throw new Error(data.error || 'Failed to upload logo');
                }
            } catch (error) {
                console.error('Failed to upload logo:', error);
                self.showError(`Failed to upload logo: ${error.message}`);
                input.value = '';
            }
        };
        
        // updateThemeSpacing - update theme spacing/typography values
        window.updateThemeSpacing = (key, value) => {
            // Map kebab-case to snake_case for settings key
            const settingKey = key.replace(/-/g, '_');
            
            // Set theme spacing/typography
            self.state.set(`theme.${settingKey}`, value);
            
            // Apply live preview if it's a CSS variable
            const cssVar = key.replace(/_/g, '-');
            document.documentElement.style.setProperty(`--${cssVar}`, value);
            
            // Update save buttons
            self.updateSaveButtons();
        };
        
        // clearSearchCache - clear search cache
        window.clearSearchCache = async () => {
            if (!confirm('Are you sure you want to clear the search cache?')) {
                return;
            }
            
            try {
                const response = await fetch('/api/cache/clear', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': self.getCSRFToken()
                    }
                });
                
                const data = await response.json();
                
                if (data.success) {
                    self.showSuccess('Search cache cleared successfully');
                } else {
                    throw new Error(data.error || 'Failed to clear cache');
                }
            } catch (error) {
                console.error('Failed to clear cache:', error);
                self.showError(`Failed to clear cache: ${error.message}`);
            }
        };
        
        // viewCacheStats - view cache statistics
        window.viewCacheStats = async () => {
            const statsDiv = document.getElementById('cacheStats');
            const contentDiv = document.getElementById('cacheStatsContent');
            
            if (!statsDiv || !contentDiv) return;
            
            try {
                const response = await fetch('/api/cache/stats');
                const data = await response.json();
                
                if (data.success && data.stats) {
                    const stats = data.stats;
                    contentDiv.innerHTML = `
                        <div class="row g-3">
                            <div class="col-md-6">
                                <div class="card">
                                    <div class="card-body">
                                        <h6>Cache Size</h6>
                                        <div class="h4">${stats.size || 'N/A'}</div>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="card">
                                    <div class="card-body">
                                        <h6>Entries</h6>
                                        <div class="h4">${stats.entries || 0}</div>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="card">
                                    <div class="card-body">
                                        <h6>Hit Rate</h6>
                                        <div class="h4">${stats.hit_rate || '0%'}</div>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="card">
                                    <div class="card-body">
                                        <h6>Average Age</h6>
                                        <div class="h4">${stats.average_age || 'N/A'}</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;
                    statsDiv.style.display = 'block';
                } else {
                    throw new Error(data.error || 'Failed to load cache stats');
                }
            } catch (error) {
                console.error('Failed to load cache stats:', error);
                contentDiv.innerHTML = `<div class="alert alert-danger">Failed to load cache statistics: ${error.message}</div>`;
                statsDiv.style.display = 'block';
            }
        };
        
        // updateAppBrandingPreview - preview app branding changes
        window.updateAppBrandingPreview = () => {
            const appName = document.getElementById('appName')?.value || '';
            const appIcon = document.getElementById('appIcon')?.value || '';
            
            // Update preview in sidebar if available
            const sidebarTitle = document.querySelector('.sidebar-brand, .app-title');
            if (sidebarTitle && appName) {
                sidebarTitle.textContent = appName;
            }
            
            // Update icon if available
            const sidebarIcon = document.querySelector('.sidebar-brand i, .app-icon');
            if (sidebarIcon && appIcon) {
                sidebarIcon.className = `bi ${appIcon}`;
            }
        };
        
        // updateAppBranding - save app branding changes
        window.updateAppBranding = async (key, value) => {
            try {
                self.state.set(`system.${key}`, value);
                
                // Apply preview immediately
                window.updateAppBrandingPreview();
                
                // Update save buttons
                self.updateSaveButtons();
                
                // Auto-save if enabled
                if (self.state.get('system.auto_save') !== false) {
                    await self.saveSettings();
                }
            } catch (error) {
                console.error('Failed to update app branding:', error);
            }
        };
        
        // saveGlobalCss - save global CSS
        window.saveGlobalCss = async () => {
            const textarea = document.getElementById('globalCssTextarea');
            if (!textarea) return;
            
            const css = textarea.value;
            
            try {
                self.state.set('theme.custom_css', css);
                await self.saveSettings();
                
                // Apply CSS immediately
                const styleEl = document.getElementById('global-custom-css');
                if (styleEl) {
                    styleEl.textContent = css;
                }
                
                self.showSuccess('Global CSS saved successfully');
                
                // Close modal
                const modal = bootstrap.Modal.getInstance(document.getElementById('globalCssModal'));
                if (modal) {
                    modal.hide();
                }
            } catch (error) {
                console.error('Failed to save global CSS:', error);
                self.showError(`Failed to save: ${error.message}`);
            }
        };
        
        // resetGlobalCss - reset global CSS
        window.resetGlobalCss = () => {
            if (!confirm('Are you sure you want to clear all global CSS?')) {
                return;
            }
            
            const textarea = document.getElementById('globalCssTextarea');
            if (textarea) {
                textarea.value = '';
                
                // Update character count
                const charCount = document.getElementById('globalCssCharCount');
                if (charCount) {
                    charCount.textContent = '0';
                }
                
                // Clear applied CSS
                const styleEl = document.getElementById('global-custom-css');
                if (styleEl) {
                    styleEl.textContent = '';
                }
                
                self.showSuccess('Global CSS cleared');
            }
        };
        
        // applyGlobalCssPreview - preview global CSS without saving
        window.applyGlobalCssPreview = () => {
            const textarea = document.getElementById('globalCssTextarea');
            if (!textarea) return;
            
            const css = textarea.value;
            
            // Apply CSS immediately for preview
            const styleEl = document.getElementById('global-custom-css');
            if (styleEl) {
                styleEl.textContent = css;
            }
            
            self.showSuccess('CSS preview applied (not saved)');
        };

        // saveDatabaseSettings - save database settings including password
        window.saveDatabaseSettings = async () => {
            try {
                const data = {};
                
                // Get all database settings
                const hostField = document.getElementById('dbHost');
                const portField = document.getElementById('dbPort');
                const databaseField = document.getElementById('dbDatabase');
                const userField = document.getElementById('dbUser');
                const passwordField = document.getElementById('dbPassword');
                const poolMinConnField = document.getElementById('dbPoolMinConn');
                const poolMaxConnField = document.getElementById('dbPoolMaxConn');
                const poolTimeoutField = document.getElementById('dbPoolTimeout');
                const queryTimeoutField = document.getElementById('dbQueryTimeout');
                const batchSizeField = document.getElementById('dbBatchSize');
                const chunkSizeField = document.getElementById('dbChunkSize');
                
                if (hostField) data.host = hostField.value;
                if (portField) data.port = parseInt(portField.value);
                if (databaseField) data.database = databaseField.value;
                if (userField) data.user = userField.value;
                if (passwordField && passwordField.value.trim()) {
                    data.password = passwordField.value;
                }
                if (poolMinConnField) data.pool_min_conn = parseInt(poolMinConnField.value);
                if (poolMaxConnField) data.pool_max_conn = parseInt(poolMaxConnField.value);
                if (poolTimeoutField) data.pool_timeout = parseInt(poolTimeoutField.value);
                if (queryTimeoutField) data.query_timeout = parseInt(queryTimeoutField.value);
                if (batchSizeField) data.batch_size = parseInt(batchSizeField.value);
                if (chunkSizeField) data.chunk_size = parseInt(chunkSizeField.value);
                
                // OPS-02: the backend validates the syntax, opens a real connection
                // with the proposed credentials and only then persists. Until
                // it reports success nothing has been saved, so the UI must
                // show the testing state first and must never claim success
                // early.
                const submitBtn = document.querySelector('[data-on-click*="saveDatabaseSettings"]');
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.dataset.originalLabel = submitBtn.innerHTML;
                }

                self.showNotification('Testing database connection…', 'info');

                let response;
                try {
                    response = await fetch('/api/settings/database', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': self.getCSRFToken()
                        },
                        body: JSON.stringify(data)
                    });
                } catch (networkError) {
                    self.showError('Unable to reach the server. Your existing configuration was not changed.');
                    return;
                } finally {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                    }
                }

                let result = {};
                try {
                    result = await response.json();
                } catch (parseError) {
                    self.showError('The server returned an unexpected response. Your existing configuration was not changed.');
                    return;
                }

                if (result.success) {
                    // Clear password field
                    if (passwordField) {
                        passwordField.value = '';
                    }

                    // Reload settings state
                    await self.state.load();

                    self.showSuccess(result.message || 'Connection successful. Configuration saved.');
                } else {
                    // The server returns short, classified, credential-free
                    // explanations. They are escaped before being handed to
                    // showNotification(), which renders through innerHTML, so
                    // no server-supplied text can become markup.
                    const message = self.escapeHtml(
                        result.error || 'Unable to connect to the database. Your existing configuration was not changed.'
                    );

                    if (Array.isArray(result.errors) && result.errors.length) {
                        const detail = result.errors.map(e => self.escapeHtml(String(e))).join('<br>');
                        self.showError(`Please correct the following:<br>${detail}`);
                    } else {
                        self.showError(message);
                    }
                }
            } catch (error) {
                // Never surface raw exception text to the user, and never log
                // the form payload - it can contain the database password.
                console.error('Failed to save database settings');
                self.showError('Unable to connect to the database. Your existing configuration was not changed.');
            }
        };
    }
    
    /**
     * Get CSRF token from meta tag
     */
    getCSRFToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * Escape text before it is rendered through innerHTML.
     *
     * showNotification() builds its toast with innerHTML, so any server
     * supplied string must be escaped first. The database settings endpoint
     * returns short, classified messages, but escaping here keeps the UI safe
     * even if a future message contains metacharacters.
     */
    escapeHtml(value) {
        if (value === null || value === undefined) {
            return '';
        }
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    
    /**
     * Handle state changes
     */
    handleStateChange(event, data) {
        switch (event) {
            case 'loaded':
                this.populateForm();
                break;
            
            case 'changed':
                this.updateSaveButtons();
                this.applyLivePreview(data.key, data.value);
                break;
            
            case 'saved':
                this.showSuccess(`Saved ${data.count} setting(s)`);
                this.updateSaveButtons();
                break;
            
            case 'cleared':
                this.updateSaveButtons();
                break;
            
            case 'error':
                this.showError(data.error.message);
                break;
        }
    }
    
    /**
     * Setup input change handlers
     */
    setupInputHandlers() {
        // Text inputs
        document.querySelectorAll('input[data-setting]').forEach(input => {
            const key = input.dataset.setting;
            const debounced = this.debounce((e) => {
                const value = input.type === 'checkbox' ? input.checked : 
                             input.type === 'number' ? parseFloat(input.value) :
                             input.value;
                
                this.state.set(key, value);
                this.updateSaveButtons(); // Update save buttons when input changes
            }, 300);
            
            input.addEventListener('change', debounced);
            if (input.type !== 'checkbox') {
                input.addEventListener('input', debounced);
            }
        });
        
        // Select dropdowns
        document.querySelectorAll('select[data-setting]').forEach(select => {
            const key = select.dataset.setting;
            
            select.addEventListener('change', (e) => {
                this.state.set(key, select.value);
                this.updateSaveButtons(); // Update save buttons when select changes
            });
        });
        
        // Color pickers
        document.querySelectorAll('input[type="color"][data-setting]').forEach(input => {
            const key = input.dataset.setting;
            
            input.addEventListener('change', (e) => {
                this.state.set(key, input.value);
                
                // Also update text input if exists
                const textInput = document.getElementById(input.id + 'Text');
                if (textInput) {
                    textInput.value = input.value;
                }
                
                this.updateSaveButtons(); // Update save buttons when color changes
            });
        });
        
        // Handle database password field separately (special handling)
        const dbPasswordField = document.getElementById('dbPassword');
        if (dbPasswordField) {
            dbPasswordField.addEventListener('input', () => {
                this.updateSaveButtons();
            });
        }

        // Also handle inputs without data-setting (for backward compatibility)
        // These are handled by inline handlers, but we still want to show save buttons
        document.querySelectorAll('input:not([data-setting]), select:not([data-setting])').forEach(element => {
            if (element.id && (element.id.includes('appName') || element.id.includes('appIcon') || 
                element.id.includes('globalCss'))) {
                element.addEventListener('change', () => {
                    this.updateSaveButtons();
                });
            }
        });
        
        // Handle interface toggles - auto-save on change for immediate effect
        const self = this; // Capture 'this' for use in event handler
        document.querySelectorAll('.interface-toggle').forEach(toggle => {
            toggle.addEventListener('change', async (e) => {
                const interfaceId = toggle.dataset.interfaceId;
                const enabled = toggle.checked;
                
                if (!interfaceId) return;
                
                // Update the card visual state immediately
                const card = toggle.closest('.interface-card');
                if (card) {
                    if (enabled) {
                        card.classList.remove('disabled', 'border-secondary');
                        card.classList.add('border-success');
                    } else {
                        card.classList.remove('border-success');
                        card.classList.add('disabled', 'border-secondary');
                    }
                }
                
                // Auto-save the change immediately
                try {
                    const response = await fetch(`/api/settings/interfaces/${interfaceId}`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': self.getCSRFToken()
                        },
                        body: JSON.stringify({ enabled: enabled })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        // Get interface name for better message
                        const interfaceName = card ? card.querySelector('.card-title')?.textContent?.trim() || interfaceId : interfaceId;
                        
                        // The interface registry changed: synchronize the
                        // sidebar in place, leaving its outer shell untouched.
                        
                        // Broadcast change to other tabs
                        if (typeof BroadcastChannel !== 'undefined') {
                            const channel = new BroadcastChannel('settings_changes');
                            channel.postMessage({
                                type: 'settings_saved',
                                changes: {
                                    interfaces: {
                                        [interfaceId]: { enabled: enabled }
                                    }
                                }
                            });
                        }
                        
                        const sidebarUpdated = await refreshSidebarAfterSettingsChange();
                        if (sidebarUpdated) {
                            self.showSuccess(`${interfaceName} ${enabled ? 'enabled' : 'disabled'}; the sidebar is updated.`);
                        } else {
                            self.showError(`${interfaceName} was updated, but the sidebar could not be refreshed. Retry the change or navigate again.`);
                        }
                    } else {
                        // Revert toggle on error
                        toggle.checked = !enabled;
                        if (card) {
                            if (enabled) {
                                card.classList.remove('border-success');
                                card.classList.add('disabled', 'border-secondary');
                            } else {
                                card.classList.remove('disabled', 'border-secondary');
                                card.classList.add('border-success');
                            }
                        }
                        self.showError(data.error || 'Failed to update interface setting');
                    }
                } catch (error) {
                    // Revert toggle on error
                    toggle.checked = !enabled;
                    if (card) {
                        if (enabled) {
                            card.classList.remove('border-success');
                            card.classList.add('disabled', 'border-secondary');
                        } else {
                            card.classList.remove('disabled', 'border-secondary');
                            card.classList.add('border-success');
                        }
                    }
                    console.error('Failed to save interface setting:', error);
                    self.showError(`Failed to save: ${error.message}`);
                }
            });
        });
    }
    
    /**
     * Setup save buttons
     */
    setupSaveButtons() {
        // Support both data-save-tab and data-save-settings attributes
        document.querySelectorAll('[data-save-tab], [data-save-settings]').forEach(button => {
            const tabId = button.dataset.saveTab || button.dataset.saveSettings;
            if (tabId) {
                this.saveButtons.set(tabId, button);
                
                // Only add event listener if not already added
                if (!button.hasAttribute('data-handler-attached')) {
                    button.setAttribute('data-handler-attached', 'true');
                    button.addEventListener('click', async () => {
                        await this.saveSettings();
                    });
                }
            }
        });
        
        // Hide all initially
        this.updateSaveButtons();
        
        // Log for debugging
        console.log(`✅ Save buttons initialized: ${this.saveButtons.size} buttons found`);
    }
    
    /**
     * Setup action buttons (reset, export, etc.)
     */
    setupActionButtons() {
        // Reset button
        const resetBtn = document.querySelector('[data-action="reset"]');
        if (resetBtn) {
            resetBtn.addEventListener('click', async () => {
                try {
                    await this.state.reset();
                    this.showSuccess('Settings reset to defaults');
                } catch (error) {
                    this.showError(`Reset failed: ${error.message}`);
                }
            });
        }
        
        // Export button
        const exportBtn = document.querySelector('[data-action="export"]');
        if (exportBtn) {
            exportBtn.addEventListener('click', async () => {
                try {
                    await this.state.export();
                    this.showSuccess('Settings exported');
                } catch (error) {
                    this.showError(`Export failed: ${error.message}`);
                }
            });
        }
        
        // Import button
        const importBtn = document.querySelector('[data-action="import"]');
        if (importBtn) {
            importBtn.addEventListener('click', () => {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = '.json';
                
                input.onchange = async (e) => {
                    const file = e.target.files[0];
                    if (file) {
                        try {
                            await this.state.import(file);
                            this.showSuccess('Settings imported');
                        } catch (error) {
                            this.showError(`Import failed: ${error.message}`);
                        }
                    }
                };
                
                input.click();
            });
        }
    }
    
    /**
     * Update save button visibility
     */
    updateSaveButtons() {
        const changeCount = this.state.getChangeCount();
        
        // Re-scan for buttons in case they weren't found during init
        document.querySelectorAll('[data-save-settings], [data-save-tab]').forEach(button => {
            const tabId = button.dataset.saveSettings || button.dataset.saveTab;
            if (tabId && !this.saveButtons.has(tabId)) {
                // Add to map if not already there
                this.saveButtons.set(tabId, button);
                
                // Set up click handler if not already set
                if (!button.hasAttribute('data-handler-attached')) {
                    button.setAttribute('data-handler-attached', 'true');
                    button.addEventListener('click', async () => {
                        await this.saveSettings();
                    });
                }
            }
        });
        
        // Show all save buttons if there are any changes
        this.saveButtons.forEach((button, tabId) => {
            if (!button || !button.parentElement) {
                // Button was removed from DOM, remove from map
                this.saveButtons.delete(tabId);
                return;
            }
            
            if (changeCount > 0) {
                button.style.display = '';
                button.style.visibility = 'visible';
                // Update badge with change count
                const badge = button.querySelector('.badge');
                if (badge) {
                    badge.textContent = changeCount;
                } else {
                    // If badge doesn't exist, update innerHTML
                    const icon = button.querySelector('i') ? button.querySelector('i').outerHTML : '<i class="bi bi-save me-2"></i>';
                    button.innerHTML = `
                        ${icon}
                        Save Changes
                        <span class="badge bg-warning ms-2">${changeCount}</span>
                    `;
                }
            } else {
                button.style.display = 'none';
            }
        });
    }
    
    /**
     * Save all pending changes
     */
    async saveSettings() {
        const saveBtn = Array.from(this.saveButtons.values())[0];
        if (!saveBtn) return;
        
        // Check if this is database settings tab - use special handler
        const tabId = saveBtn.dataset.saveSettings || saveBtn.dataset.saveTab;
        if (tabId === 'database') {
            if (window.saveDatabaseSettings) {
                await window.saveDatabaseSettings();
            }
            return;
        }
        
        const originalHTML = saveBtn.innerHTML;
        
        try {
            // Show loading
            saveBtn.disabled = true;
            saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';
            
            // Validate first
            const validation = await this.state.validate();
            if (!validation.valid) {
                throw new Error(`Validation failed:\n${validation.errors.join('\n')}`);
            }
            
            // Save
            await this.state.save();
            
            // Show success
            saveBtn.innerHTML = '<i class="bi bi-check-circle me-2"></i>Saved!';
            saveBtn.classList.add('btn-success');
            saveBtn.classList.remove('btn-primary');
            
            setTimeout(() => {
                saveBtn.innerHTML = originalHTML;
                saveBtn.classList.remove('btn-success');
                saveBtn.classList.add('btn-primary');
                saveBtn.disabled = false;
            }, 2000);
            
        } catch (error) {
            this.showError(`Save failed: ${error.message}`);
            saveBtn.innerHTML = originalHTML;
            saveBtn.disabled = false;
        }
    }
    
    /**
     * Populate form with current values
     */
    async populateForm() {
        if (!this.state.current) return;
        
        // Iterate through all inputs with data-setting attribute
        document.querySelectorAll('[data-setting]').forEach(element => {
            const key = element.dataset.setting;
            const value = this.state.get(key);
            
            if (value !== undefined) {
                if (element.type === 'checkbox') {
                    element.checked = Boolean(value);
                } else if (element.type === 'color') {
                    element.value = value;
                    // Also update text input
                    const textInput = document.getElementById(element.id + 'Text');
                    if (textInput) {
                        textInput.value = value;
                    }
                } else {
                    element.value = value;
                }
            }
        });
        
        // Map element IDs to setting keys (for elements without data-setting attribute)
        const idToSettingMap = {
            // System settings
            'autoSave': 'system.auto_save',
            'confirmActions': 'system.confirm_actions',
            'animationsEnabled': 'system.animations_enabled',
            'breadcrumbsEnabled': 'system.show_breadcrumbs',
            'compactMode': 'system.compact_mode',
            'showPageTips': 'interfaces.page_tips.enabled',
            'notificationsEnabled': 'system.notifications_enabled',
            'theme': 'system.theme',
            'itemsPerPage': 'system.items_per_page',
            'defaultSort': 'system.default_sort',
            'sortDirection': 'system.sort_direction',
            'appName': 'system.app_name',
            'appIcon': 'system.app_icon',
            
            // Display settings
            'compactView': 'display.compact_view',
            'showFilePreview': 'display.show_file_preview',
            'showMetadata': 'display.show_metadata',
            'resultsPerPage': 'display.results_per_page',
            'displayDefaultSort': 'display.default_sort',
            'displaySortDirection': 'display.sort_direction',
            
            // Search settings
            'defaultSearchType': 'search.default_search_type',
            'caseSensitive': 'search.case_sensitive',
            'highlightResults': 'search.highlight_results',
            'searchInContent': 'search.search_in_content',
            'searchInFilename': 'search.search_in_filename',
            'searchInMetadata': 'search.search_in_metadata',
            'maxResults': 'search.max_results',
            
            // Processing settings
            'autoProcessUploads': 'processing.auto_process_uploads',
            'extractArchives': 'processing.extract_archives',
            'extractAttachments': 'processing.extract_attachments',
            'processNested': 'processing.process_nested',
            'calculateHashes': 'processing.calculate_hashes',
            'extractText': 'processing.extract_text',
            'extractMetadata': 'processing.extract_metadata',
            
            // Notification settings
            'emailNotifications': 'notifications.email_notifications',
            'processingComplete': 'notifications.processing_complete',
            'batchComplete': 'notifications.batch_complete',
            'errorsOnly': 'notifications.errors_only',
            'similarFilesEnabled': 'notifications.similar_files_enabled',
            'futureDatesEnabled': 'notifications.future_dates_enabled',
            'autoAnalyzeFiles': 'notifications.auto_analyze_files',
            'upcomingEventsDays': 'notifications.upcoming_events_days',
        };
        
        // Populate elements by ID
        for (const [elementId, settingKey] of Object.entries(idToSettingMap)) {
            const element = document.getElementById(elementId);
            if (!element) continue;
            
            const value = this.state.get(settingKey);
            if (value !== undefined) {
                if (element.type === 'checkbox') {
                    element.checked = Boolean(value);
                } else if (element.type === 'select-one' || element.tagName === 'SELECT') {
                    // For select elements, try to find matching option
                    const option = Array.from(element.options).find(opt => {
                        if (element.type === 'number' || settingKey.includes('items_per_page') || settingKey.includes('max_results') || settingKey.includes('upcoming_events_days')) {
                            return parseInt(opt.value) === parseInt(value);
                        }
                        return opt.value === String(value);
                    });
                    if (option) {
                        element.value = option.value;
                    }
                } else if (element.type === 'number') {
                    element.value = value;
                } else {
                    element.value = value;
                }
            }
        }
        
        // Populate theme color inputs
        const themeColorMap = {
            'primaryColor': 'theme.primary_color',
            'secondaryColor': 'theme.secondary_color',
            'successColor': 'theme.success_color',
            'dangerColor': 'theme.danger_color',
            'warningColor': 'theme.warning_color',
            'infoColor': 'theme.info_color',
            'bgLight': 'theme.bg_light',
            'bgWhite': 'theme.bg_white',
            'bgSection': 'theme.bg_section',
            'textDark': 'theme.text_dark',
            'textLight': 'theme.text_light',
            'textMuted': 'theme.text_muted',
            'borderColor': 'theme.border_color',
            'borderLight': 'theme.border_light',
            'borderDark': 'theme.border_dark',
            'sidebarBg': 'theme.sidebar_bg',
            'sidebarText': 'theme.sidebar_text',
            'sidebarActive': 'theme.sidebar_active',
            'sidebarHover': 'theme.sidebar_hover',
            'btnPrimary': 'theme.btn_primary',
            'btnPrimaryHover': 'theme.btn_primary_hover',
            'btnSecondary': 'theme.btn_secondary',
            'btnSecondaryHover': 'theme.btn_secondary_hover',
            'gradientStart': 'theme.gradient_start',
            'gradientEnd': 'theme.gradient_end',
            'gradientDirection': 'theme.gradient_direction',
        };
        
        for (const [elementId, settingKey] of Object.entries(themeColorMap)) {
            const colorInput = document.getElementById(elementId);
            const textInput = document.getElementById(elementId + 'Text');
            const value = this.state.get(settingKey);
            
            if (value !== undefined) {
                if (colorInput) {
                    colorInput.value = value;
                }
                if (textInput) {
                    textInput.value = value;
                }
            }
        }
    }
    
    /**
     * Apply live preview of changes
     */
    applyLivePreview(key, value) {
        // Parse key
        const parts = key.split('.');
        const category = parts[0];
        const setting = parts.length > 1 ? parts[parts.length - 1] : '';
        
        // Apply based on category
        if (category === 'theme') {
            this.applyThemePreview(setting, value);
        } else if (category === 'system') {
            this.applySystemPreview(setting, value);
        } else if (category === 'interfaces' && parts.length >= 3 && parts[1] === 'page_tips' && parts[2] === 'enabled') {
            // Handle interfaces.page_tips.enabled
            if (window.pageTipsManager) {
                window.pageTipsManager.setEnabled(value);
            }
        }
    }
    
    /**
     * Apply theme color preview
     */
    applyThemePreview(setting, value) {
        // Convert snake_case to kebab-case for CSS variables
        const cssVar = setting.replace(/_/g, '-');
        document.documentElement.style.setProperty(`--${cssVar}`, value);
        
        // Update color swatch if exists
        const swatch = document.querySelector(`[data-swatch="${setting}"]`);
        if (swatch) {
            swatch.style.backgroundColor = value;
        }
    }
    
    /**
     * Apply system setting preview
     */
    applySystemPreview(setting, value) {
        switch (setting) {
            case 'animations_enabled':
                document.body.classList.toggle('no-animations', !value);
                break;
            
            case 'compact_mode':
                document.body.classList.toggle('compact-mode', value);
                break;
            
            case 'show_breadcrumbs':
                document.querySelectorAll('.breadcrumb, .page-navigation-bar')
                    .forEach(el => el.style.display = value ? '' : 'none');
                break;
            
            case 'page_tips':
                if (window.pageTipsManager && value && typeof value === 'object' && value.enabled !== undefined) {
                    window.pageTipsManager.setEnabled(value.enabled);
                } else if (window.pageTipsManager) {
                    window.pageTipsManager.setEnabled(value);
                }
                break;
        }
    }
    
    /**
     * Show success message
     */
    showSuccess(message) {
        this.showNotification(message, 'success');
    }
    
    /**
     * Show error message
     */
    showError(message) {
        this.showNotification(message, 'danger');
    }
    
    /**
     * Show notification
     */
    showNotification(message, type = 'info') {
        // Use notification system if available
        if (window.notifications && typeof window.notifications.show === 'function') {
            window.notifications.show(message, type, { duration: 4000 });
            return;
        }
        
        // Fallback: Create toast
        const toast = document.createElement('div');
        toast.className = `alert alert-${type} position-fixed top-0 start-50 translate-middle-x mt-3`;
        toast.style.zIndex = '9999';
        toast.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.remove();
        }, 4000);
    }
    
    /**
     * Debounce helper
     */
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
}

// Initialize
const settingsUI = new SettingsUI();

// Export
export default settingsUI;

// Global access
window.settingsUI = settingsUI;

