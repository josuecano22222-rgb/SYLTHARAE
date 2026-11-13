/**
 * Settings State Manager - Frontend
 * File: static/js/settings/settings-state.js
 * 
 * Clean, modern state management for settings
 * No legacy code - built from scratch
 */

class SettingsState {
    constructor() {
        // Current state (loaded from server)
        this.current = null;
        
        // Pending changes (not yet saved)
        this.pending = new Map();
        
        // Listeners for state changes
        this.listeners = new Set();
        
        // Lock for preventing concurrent operations
        this.saving = false;
        
        // Initialize
        this.init();
    }
    
    async init() {
        try {
            await this.load();
            this.notifyListeners('initialized');
        } catch (error) {
            console.error('Failed to initialize settings:', error);
        }
    }
    
    /**
     * Load all settings from server
     */
    async load() {
        try {
            const response = await fetch('/api/settings/');
            const data = await response.json();
            
            if (data.success) {
                this.current = data.settings;
                this.pending.clear();
                this.notifyListeners('loaded', this.current);
                return this.current;
            } else {
                throw new Error(data.error || 'Failed to load settings');
            }
        } catch (error) {
            console.error('Load error:', error);
            throw error;
        }
    }
    
    /**
     * Get a setting value (checks pending first, then current)
     */
    get(key) {
        // Check pending changes first
        if (this.pending.has(key)) {
            return this.pending.get(key);
        }
        
        // Parse dot notation
        const parts = key.split('.');
        let value = this.current;
        
        for (const part of parts) {
            if (value && typeof value === 'object' && part in value) {
                value = value[part];
            } else {
                return undefined;
            }
        }
        
        return value;
    }
    
    /**
     * Set a pending change (doesn't save immediately)
     */
    set(key, value) {
        this.pending.set(key, value);
        this.notifyListeners('changed', { key, value });
    }
    
    /**
     * Check if there are unsaved changes
     */
    hasChanges() {
        return this.pending.size > 0;
    }
    
    /**
     * Get all pending changes
     */
    getChanges() {
        return Object.fromEntries(this.pending);
    }
    
    /**
     * Get count of pending changes
     */
    getChangeCount() {
        return this.pending.size;
    }
    
    /**
     * Clear pending changes
     */
    clearChanges() {
        this.pending.clear();
        this.notifyListeners('cleared');
    }
    
    /**
     * Save all pending changes to server
     */
    async save() {
        if (this.saving) {
            throw new Error('Save already in progress');
        }
        
        if (!this.hasChanges()) {
            return { success: true, message: 'No changes to save' };
        }
        
        this.saving = true;
        
        try {
            const updates = this.getChanges();
            
            const { response, data } = await this.postJSONWithCSRF('/api/settings/batch', { updates });
            
            if (data.success) {
                // Reload current state
                await this.load();
                
                // Broadcast changes to other tabs/pages
                this.broadcastChanges(updates);
                
                this.notifyListeners('saved', {
                    count: Object.keys(updates).length
                });
                
                return data;
            } else {
                throw new Error(data.error || 'Save failed');
            }
        } catch (error) {
            this.notifyListeners('error', { error });
            throw error;
        } finally {
            this.saving = false;
        }
    }
    
    /**
     * Validate pending changes without saving
     */
    async validate() {
        if (!this.hasChanges()) {
            return { valid: true, errors: [] };
        }
        
        try {
            const updates = this.getChanges();
            
            const response = await fetch('/api/settings/validate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCSRFToken()
                },
                body: JSON.stringify({ updates })
            });
            
            const data = await response.json();
            
            return {
                valid: data.success,
                errors: data.errors || []
            };
        } catch (error) {
            return {
                valid: false,
                errors: [error.message]
            };
        }
    }
    
    /**
     * Reset all settings to defaults
     */
    async reset() {
        if (!confirm('Reset all settings to defaults? This cannot be undone.')) {
            return;
        }
        
        try {
            const response = await fetch('/api/settings/reset', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCSRFToken()
                },
                body: JSON.stringify({ confirm: true })
            });
            
            const data = await response.json();
            
            if (data.success) {
                await this.load();
                this.notifyListeners('reset');
                return data;
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            console.error('Reset error:', error);
            throw error;
        }
    }
    
    /**
     * Export settings
     */
    async export() {
        try {
            const response = await fetch('/api/settings/export');
            const data = await response.json();
            
            if (data.success) {
                // Trigger download
                const blob = new Blob(
                    [JSON.stringify(data.settings, null, 2)],
                    { type: 'application/json' }
                );
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = data.filename;
                a.click();
                URL.revokeObjectURL(url);
                
                return data;
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            console.error('Export error:', error);
            throw error;
        }
    }
    
    /**
     * Import settings from file
     */
    async import(file) {
        try {
            const text = await file.text();
            const settings = JSON.parse(text);
            
            const response = await fetch('/api/settings/import', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCSRFToken()
                },
                body: JSON.stringify(settings)
            });
            
            const data = await response.json();
            
            if (data.success) {
                await this.load();
                this.notifyListeners('imported');
                return data;
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            console.error('Import error:', error);
            throw error;
        }
    }
    
    /**
     * Subscribe to state changes
     */
    subscribe(listener) {
        this.listeners.add(listener);
        
        // Return unsubscribe function
        return () => {
            this.listeners.delete(listener);
        };
    }
    
    /**
     * Notify all listeners of state change
     */
    notifyListeners(event, data = null) {
        this.listeners.forEach(listener => {
            try {
                listener(event, data);
            } catch (error) {
                console.error('Listener error:', error);
            }
        });
    }
    
    /**
     * Broadcast changes to other tabs/pages
     */
    broadcastChanges(changes) {
        if (typeof BroadcastChannel !== 'undefined') {
            try {
                const channel = new BroadcastChannel('settings_changes');
                channel.postMessage({
                    type: 'settings_saved',
                    changes: changes,
                    timestamp: Date.now()
                });
            } catch (error) {
                console.warn('Could not broadcast settings changes:', error);
            }
        }
        
        // Also dispatch custom event for same-tab listeners
        document.dispatchEvent(new CustomEvent('settingsChanged', {
            detail: { changes }
        }));
    }
    
    /**
     * Get CSRF token from meta tag
     */
    getCSRFToken() {
        // Prefer the shared provider (window.CSRF) so pages whose session was
        // rotated (re-login in another tab, logout, expiry) can still obtain a
        // fresh token via CSRF.refreshToken() instead of failing with 400.
        if (window.CSRF && typeof window.CSRF.getToken === 'function') {
            return window.CSRF.getToken();
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    /**
     * Save with one automatic CSRF recovery round: on a stale-token 400 the
     * token is refreshed from the server and the request retried once.
     */
    async postJSONWithCSRF(url, body) {
        const doFetch = () => fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCSRFToken()
            },
            body: JSON.stringify(body)
        });
        let response = await doFetch();
        let data = null;
        try { data = await response.json(); } catch (_) { data = null; }
        const staleToken = response.status === 400 && data &&
            /csrf|token/i.test(String(data.error || ''));
        if (staleToken && window.CSRF && typeof window.CSRF.refreshToken === 'function') {
            await window.CSRF.refreshToken();
            response = await doFetch();
            try { data = await response.json(); } catch (_) { data = null; }
        }
        return { response, data: data || {} };
    }
}

// Create global singleton
const settingsState = new SettingsState();

// Export for modules
export default settingsState;

// Also make available globally for inline handlers
window.settingsState = settingsState;

