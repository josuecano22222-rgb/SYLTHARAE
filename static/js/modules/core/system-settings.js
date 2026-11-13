/**
 * System Settings Manager
 * Applies system-wide settings throughout the application
 */

import { apiGet } from '../api/api-client.js';

// Resolves once the server settings have been loaded and applied (or the
// defaults have been applied after a failure). A page that reads a per-page
// select must wait for it: applyAllSettings writes the saved page size into
// those selects, and a read before that point is a read of a stale value.
let markSettingsReady;
export const settingsReady = new Promise((resolve) => { markSettingsReady = resolve; });

class SystemSettings {
    constructor() {
        this.settings = {};
        this.init();
    }

    async init() {
        try {
            // Load settings from server
            await this.loadSettings();
            
            // Apply settings immediately
            this.applyAllSettings();
            
            // Watch for setting changes
            this.watchSettings();
        } catch (error) {
            console.error('Error initializing system settings:', error);
            // Use defaults on error
            this.settings = this.getDefaultSettings();
            this.applyAllSettings();
        } finally {
            markSettingsReady();
        }
    }

    async loadSettings() {
        try {
            // Try to get system settings from new API
            const data = await apiGet('/api/settings/system');
            if (data.success && data.settings) {
                this.settings = data.settings;
            } else {
                // Fallback: get all settings and extract system
                const allData = await apiGet('/api/settings/');
                if (allData.success && allData.settings && allData.settings.system) {
                    this.settings = allData.settings.system;
                } else {
                    throw new Error(data.error || allData.error || 'Failed to load settings');
                }
            }
            
            // Merge with localStorage for client-side persistence
            const storedItemsPerPage = localStorage.getItem('items_per_page');
            if (storedItemsPerPage && !this.settings.items_per_page) {
                this.settings.items_per_page = parseInt(storedItemsPerPage);
            }
        } catch (error) {
            console.warn('Could not load system settings:', error);
            // Use defaults
            this.settings = this.getDefaultSettings();
        }
    }

    getDefaultSettings() {
        return {
            animations_enabled: true,
            notifications_enabled: true,
            auto_save: true,
            confirm_actions: true,
            show_breadcrumbs: true,
            compact_mode: false,
            items_per_page: 50,
            language: 'en',
            timezone: 'UTC',
            date_format: 'YYYY-MM-DD',
            time_format: '24h'
        };
    }

    applyAllSettings() {
        // Apply animations
        if (this.settings.animations_enabled !== undefined) {
            document.body.classList.toggle('no-animations', !this.settings.animations_enabled);
        }

        // Apply breadcrumbs
        if (this.settings.show_breadcrumbs !== undefined) {
            const breadcrumb = document.querySelector('.page-navigation-bar');
            if (breadcrumb) {
                breadcrumb.style.display = this.settings.show_breadcrumbs ? '' : 'none';
            }
        }

        // Apply compact mode
        if (this.settings.compact_mode !== undefined) {
            document.body.classList.toggle('compact-mode', this.settings.compact_mode);
        }

        // Apply items per page to pagination
        if (this.settings.items_per_page) {
            const paginationSelects = document.querySelectorAll('select[name*="per_page"], select[id*="perPage"]');
            paginationSelects.forEach(select => {
                if (select.querySelector(`option[value="${this.settings.items_per_page}"]`)) {
                    select.value = this.settings.items_per_page;
                }
            });
        }
    }

    watchSettings() {
        // Listen for setting changes
        document.addEventListener('systemSettingChanged', (e) => {
            const { key, value } = e.detail;
            this.settings[key] = value;
            this.applySetting(key, value);
        });
        
        // Listen to new settings broadcast channel
        if (typeof BroadcastChannel !== 'undefined') {
            const channel = new BroadcastChannel('settings_changes');
            channel.addEventListener('message', (event) => {
                if (event.data.type === 'settings_saved' && event.data.changes) {
                    // Extract system setting changes
                    for (const [key, value] of Object.entries(event.data.changes)) {
                        if (key.startsWith('system.')) {
                            const settingKey = key.replace('system.', '');
                            this.settings[settingKey] = value;
                            this.applySetting(settingKey, value);
                        }
                    }
                }
            });
        }
        
        // Listen to custom event for same-tab changes
        document.addEventListener('settingsChanged', (event) => {
            if (event.detail && event.detail.changes) {
                for (const [key, value] of Object.entries(event.detail.changes)) {
                    if (key.startsWith('system.')) {
                        const settingKey = key.replace('system.', '');
                        this.settings[settingKey] = value;
                        this.applySetting(settingKey, value);
                    }
                }
            }
        });
    }

    applySetting(key, value) {
        switch(key) {
            case 'animations_enabled':
                document.body.classList.toggle('no-animations', !value);
                // Apply to all elements
                document.querySelectorAll('*').forEach(el => {
                    if (value) {
                        el.style.transition = '';
                    } else {
                        el.style.transition = 'none';
                    }
                });
                break;
            case 'show_breadcrumbs':
                const breadcrumb = document.querySelector('.page-navigation-bar');
                if (breadcrumb) {
                    breadcrumb.style.display = value ? '' : 'none';
                }
                // Apply to all breadcrumb elements
                document.querySelectorAll('.breadcrumb, .page-navigation-bar').forEach(el => {
                    el.style.display = value ? '' : 'none';
                });
                break;
            case 'compact_mode':
                document.body.classList.toggle('compact-mode', value);
                // Apply compact spacing
                if (value) {
                    document.documentElement.style.setProperty('--spacing-unit', '0.5rem');
                } else {
                    document.documentElement.style.setProperty('--spacing-unit', '1rem');
                }
                break;
            case 'items_per_page':
                // Update pagination if present
                const paginationSelects = document.querySelectorAll('select[name*="per_page"], select[id*="perPage"], select[name*="limit"]');
                paginationSelects.forEach(select => {
                    if (select.querySelector(`option[value="${value}"]`)) {
                        select.value = value;
                        // Trigger change event if needed
                        select.dispatchEvent(new Event('change'));
                    }
                });
                // Store in localStorage for persistence
                localStorage.setItem('items_per_page', value);
                break;
            case 'language':
                // Language change requires page reload
                if (window.showInfo) {
                    window.showInfo('Language setting will be applied after page reload');
                }
                break;
            case 'timezone':
            case 'date_format':
            case 'time_format':
                // These require page reload
                if (window.showInfo) {
                    window.showInfo('Setting will be applied after page reload');
                }
                break;
            case 'notifications_enabled':
                // Notifications are handled by the notification system itself
                if (!value && window.notifications) {
                    window.notifications.clearAll();
                }
                break;
        }
        
        // Dispatch global event for other components
        document.dispatchEvent(new CustomEvent('globalSettingChanged', {
            detail: { key, value, settings: this.settings }
        }));
    }

    getSetting(key, defaultValue = null) {
        return this.settings[key] !== undefined ? this.settings[key] : defaultValue;
    }

    shouldConfirmAction() {
        return this.settings.confirm_actions !== false;
    }

    shouldAutoSave() {
        return this.settings.auto_save !== false;
    }

    shouldShowNotifications() {
        return this.settings.notifications_enabled !== false;
    }
}

// Initialize system settings
let systemSettings;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        systemSettings = new SystemSettings();
        window.systemSettings = systemSettings;
    });
} else {
    systemSettings = new SystemSettings();
    window.systemSettings = systemSettings;
}

// Export for use in other modules
export default systemSettings;

