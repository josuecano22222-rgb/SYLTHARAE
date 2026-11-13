/**
 * Page Tips/Documentation Manager
 * Manages the visibility and behavior of page tips based on user settings
 */

class PageTipsManager {
    constructor() {
        this.tipsEnabled = true; // Default to enabled (visible)
        this.tipsVisible = false; // Default to collapsed (closed)
        this.broadcastChannel = null; // Store broadcast channel
        this.init();
    }

    init() {
        // Wait a bit for DOM to be ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                this.initializeTips();
            });
        } else {
            this.initializeTips();
        }
    }

    initializeTips() {
        // Check if tips are enabled from settings
        this.loadSettings();
        
        // Set up event listeners
        this.setupEventListeners();
        
        // Apply initial state
        this.applyVisibility();
        
        // Initialize collapsed state (tips start closed by default)
        this.applyCollapsedState();
    }

    loadSettings() {
        try {
            // STRICT RULE: Read from server-side setting
            // The template only renders the container if interfaces.page_tips.enabled is True
            // If container doesn't exist, it means the interface is disabled or page doesn't use the macro
            const tipsContainer = document.getElementById('pageTipsContainer');
            
            if (!tipsContainer) {
                this.tipsEnabled = false;
                return;
            }
            
            // Get the value from the data attribute (set by template from JSON)
            const enabledAttr = tipsContainer.getAttribute('data-page-tips-enabled');
            
            if (enabledAttr !== null) {
                // STRICT: Only 'true' means enabled, everything else is disabled
                this.tipsEnabled = enabledAttr === 'true';
                
                // Sync localStorage with server-side setting to keep them in sync
                try {
                    localStorage.setItem('tips_enabled', this.tipsEnabled.toString());
                } catch (e) {
                    // Ignore localStorage errors
                }
                return; // Use server-side setting (source of truth)
            }
            
            // Fallback: Check localStorage (for immediate UI updates during settings changes)
            const stored = localStorage.getItem('tips_enabled');
            if (stored !== null) {
                this.tipsEnabled = stored === 'true';
            } else {
                // Default to enabled (visible) to match system default
                this.tipsEnabled = true;
            }
        } catch (e) {
            // Default to enabled on error to match system default
            this.tipsEnabled = true;
        }
    }

    setupEventListeners() {
        if (this._listenersBound) {
            // Soft-nav: only rebind the tips toggle against the new button.
            this.bindToggleButton();
            return;
        }
        this._listenersBound = true;

        // Listen for settings changes (from broadcast or direct updates)
        document.addEventListener('tipsSettingChanged', (e) => {
            this.tipsEnabled = e.detail.enabled;
            this.applyVisibility();
        });

        // Listen for broadcast messages from other tabs (for automatic updates when settings are saved)
        if (typeof BroadcastChannel !== 'undefined') {
            this.broadcastChannel = new BroadcastChannel('settings_changes');
            this.broadcastChannel.onmessage = (event) => {
                // Check for interface changes (new path)
                if (event.data.type === 'settings_saved' && event.data.changes.interfaces) {
                    const pageTipsConfig = event.data.changes.interfaces.page_tips;
                    if (pageTipsConfig && pageTipsConfig.enabled !== undefined) {
                        // STRICT RULE: Update immediately when settings are saved
                        this.setEnabled(pageTipsConfig.enabled);
                    }
                }
                // Backward compatibility: check old system path
                else if (event.data.type === 'settings_saved' && event.data.changes.system) {
                    const tipsValue = event.data.changes.system.show_page_tips;
                    if (tipsValue !== undefined) {
                        // STRICT RULE: Update immediately when settings are saved
                        this.setEnabled(tipsValue);
                    }
                }
            };
        }

        this.bindToggleButton();
    }

    bindToggleButton() {
        const toggleBtn = document.getElementById('toggleTipsBtn');
        if (!toggleBtn || toggleBtn.dataset.tipsBound === '1') return;
        toggleBtn.dataset.tipsBound = '1';
        toggleBtn.addEventListener('click', () => {
            this.toggleCollapse();
        });
    }

    toggleCollapse() {
        this.tipsVisible = !this.tipsVisible;
        this.applyCollapsedState();
    }

    applyCollapsedState() {
        const content = document.getElementById('pageTipsContent');
        const icon = document.getElementById('tipsToggleIcon');
        
        if (!content || !icon) return;
        
        if (this.tipsVisible) {
            content.classList.remove('collapsed');
            icon.classList.remove('rotated');
        } else {
            content.classList.add('collapsed');
            icon.classList.add('rotated');
        }
    }

    applyVisibility() {
        const container = document.getElementById('pageTipsContainer');
        if (!container) {
            return;
        }

        // STRICT RULE: If enabled, show; if disabled, hide completely
        if (this.tipsEnabled) {
            // ENABLED: Show tips - mark as initialized and remove ALL hiding styles and attributes
            container.classList.add('tips-initialized');
            container.style.display = '';
            container.style.visibility = '';
            container.removeAttribute('hidden');
            container.removeAttribute('style');
            container.setAttribute('data-tips-hidden', 'false');
            container.classList.remove('tips-disabled');
        } else {
            // DISABLED: Hide completely - use multiple methods to ensure it's completely hidden
            // Don't add tips-initialized class to keep it hidden
            container.style.display = 'none';
            container.style.visibility = 'hidden';
            container.setAttribute('hidden', 'true');
            container.setAttribute('data-tips-hidden', 'true');
            container.classList.add('tips-disabled');
            container.classList.remove('tips-initialized');
            // Also set inline style as final fallback
            container.style.setProperty('display', 'none', 'important');
        }
    }

    setEnabled(enabled) {
        this.tipsEnabled = enabled;
        try {
            localStorage.setItem('tips_enabled', enabled.toString());
        } catch (e) {
            // Ignore localStorage errors
        }
        this.applyVisibility();
        
        // Dispatch event for other components
        document.dispatchEvent(new CustomEvent('tipsSettingChanged', {
            detail: { enabled: enabled }
        }));
    }
}

// Initialize on DOM ready
let pageTipsManager;

// Wait for DOM to be ready before initializing
function initPageTipsManager() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            pageTipsManager = new PageTipsManager();
            window.pageTipsManager = pageTipsManager;
        });
    } else {
        // DOM already ready, initialize immediately
        pageTipsManager = new PageTipsManager();
        window.pageTipsManager = pageTipsManager;
    }
}

// Initialize immediately
initPageTipsManager();

// Soft-nav swaps #mainContent (and tips markup) without a full reload.
// Re-apply settings + collapsed default and rebind the toggle against the
// fresh DOM so tips do not stay expanded and steal the explorer viewport.
document.addEventListener('syltharae:soft-nav', () => {
    if (pageTipsManager) {
        pageTipsManager.loadSettings();
        pageTipsManager.applyVisibility();
        pageTipsManager.tipsVisible = false;
        pageTipsManager.applyCollapsedState();
        pageTipsManager.setupEventListeners();
    } else {
        initPageTipsManager();
    }
});

// Add diagnostic function for manual debugging
window.debugPageTips = function() {
    console.log('🔍 [Page Tips] Manual Diagnostic Check');
    console.log('='.repeat(60));
    
    const container = document.getElementById('pageTipsContainer');
    if (!container) {
        console.error('❌ Container NOT FOUND in DOM');
        console.log('\nPossible reasons:');
        console.log('1. Template condition failed: user_settings.is_interface_enabled("page_tips") returned false');
        console.log('2. Template not rendered: Check if page uses {% call page_tips(enabled=is_interface_enabled("page_tips") if is_interface_enabled is defined else (user_settings.is_interface_enabled("page_tips") if user_settings else False)) %} macro');
        console.log('3. Settings not loaded: Check Settings → Interfaces → Page Tips');
        console.log('4. Flask app needs restart: Settings might be cached');
        return;
    }
    
    console.log('✅ Container FOUND in DOM');
    console.log(`   → ID: ${container.id}`);
    console.log(`   → Classes: ${container.className || '(none)'}`);
    console.log(`   → data-page-tips-enabled: ${container.getAttribute('data-page-tips-enabled') || 'NOT SET'}`);
    console.log(`   → data-tips-hidden: ${container.getAttribute('data-tips-hidden') || 'NOT SET'}`);
    
    const computed = window.getComputedStyle(container);
    console.log(`\nCSS Computed Styles:`);
    console.log(`   → display: ${computed.display}`);
    console.log(`   → visibility: ${computed.visibility}`);
    console.log(`   → opacity: ${computed.opacity}`);
    console.log(`   → height: ${computed.height}`);
    console.log(`   → width: ${computed.width}`);
    
    console.log(`\nClasses:`);
    console.log(`   → tips-initialized: ${container.classList.contains('tips-initialized')}`);
    console.log(`   → tips-disabled: ${container.classList.contains('tips-disabled')}`);
    
    if (pageTipsManager) {
        console.log(`\nManager State:`);
        console.log(`   → tipsEnabled: ${pageTipsManager.tipsEnabled}`);
        console.log(`   → tipsVisible: ${pageTipsManager.tipsVisible}`);
    }
    
    console.log('='.repeat(60));
};

// Export for global access
window.PageTipsManager = PageTipsManager;

