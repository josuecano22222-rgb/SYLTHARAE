/**
 * Base Page JavaScript - Handles common functionality for all pages
 * Extracted from base.html to keep HTML clean
 */

// Get current endpoint from data attribute or meta tag
function getCurrentEndpoint() {
    const body = document.body;
    const endpoint = body.getAttribute('data-current-endpoint') || 
                     body.getAttribute('data-endpoint') ||
                     (document.querySelector('meta[name="current-endpoint"]')?.getAttribute('content') || '');
    return endpoint;
}

// Initialize base page functionality
document.addEventListener('DOMContentLoaded', function() {
    // Mobile menu toggle with backdrop
    const menuToggle = document.getElementById('menuToggle');
    const sidebar = document.getElementById('sidebar');
    const sidebarBackdrop = document.getElementById('sidebarBackdrop');
    
    function setSidebarOpen(open, returnFocus = false) {
        if (!sidebar) return;
        const desktop = window.innerWidth >= 992;
        const visible = desktop || open;
        sidebar.classList.toggle('show', !desktop && open);
        sidebar.setAttribute('aria-hidden', visible ? 'false' : 'true');
        sidebar.toggleAttribute('inert', !visible);
        if (sidebarBackdrop) sidebarBackdrop.classList.toggle('show', !desktop && open);
        if (menuToggle) {
            const drawerOpen = !desktop && open;
            menuToggle.setAttribute('aria-expanded', drawerOpen ? 'true' : 'false');
            const actionLabel = drawerOpen ? menuToggle.dataset.closeLabel : menuToggle.dataset.openLabel;
            if (actionLabel) menuToggle.setAttribute('aria-label', actionLabel);
        }
        document.body.style.overflow = !desktop && open ? 'hidden' : '';
        if (returnFocus && !desktop && menuToggle) menuToggle.focus();
    }

    function toggleSidebar() {
        if (!sidebar) return;
        const isOpen = sidebar.classList.contains('show');
        setSidebarOpen(!isOpen);
        if (!isOpen && window.innerWidth < 992) {
            sidebar.querySelector('.sidebar-nav-link')?.focus();
        } else if (isOpen && menuToggle) {
            menuToggle.focus();
        }
    }

    function closeSidebar(returnFocus = true) {
        setSidebarOpen(false, returnFocus);
    }

    // The sidebar is persistent navigation on desktop and a hidden drawer on
    // smaller screens. Keep the accessibility tree in sync with that layout.
    setSidebarOpen(sidebar && sidebar.classList.contains('show'));
    
    // Toggle sidebar on menu button click
    if (menuToggle && sidebar) {
        menuToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            toggleSidebar();
        });
    }
    
    // Close sidebar when backdrop is clicked
    if (sidebarBackdrop) {
        sidebarBackdrop.addEventListener('click', function() {
            closeSidebar();
        });
    }
    
    // Close sidebar when clicking on a sidebar link (mobile)
    if (sidebar) {
        const sidebarLinks = sidebar.querySelectorAll('.sidebar-nav-link');
        sidebarLinks.forEach(link => {
            link.addEventListener('click', function() {
                // Only close on mobile/tablet
                if (window.innerWidth < 992) {
                    setTimeout(closeSidebar, 100); // Small delay for visual feedback
                }
            });
        });
    }
    
    // Close sidebar on window resize if it becomes desktop size
    let resizeTimeout;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(function() {
            if (sidebar) {
                const shouldRemainOpen = window.innerWidth < 992 && sidebar.classList.contains('show');
                setSidebarOpen(shouldRemainOpen);
            }
        }, 250);
    });
    
    // Close sidebar on Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && sidebar && sidebar.classList.contains('show')) {
            closeSidebar();
        }
    });
    
    // Maintain sidebar active state
    function updateSidebarActiveState() {
        const currentPath = window.location.pathname;
        const currentEndpoint = getCurrentEndpoint();
        const sidebarLinks = document.querySelectorAll('.sidebar-nav-link:not(.language-switcher .sidebar-nav-link)');

        let matched = null;
        let bestLen = -1;

        if (currentEndpoint) {
            sidebarLinks.forEach((link) => {
                const endpoint = link.getAttribute('data-endpoint');
                if (endpoint && endpoint === currentEndpoint) {
                    matched = link;
                    bestLen = Number.MAX_SAFE_INTEGER;
                }
            });
        }

        if (!matched) {
            sidebarLinks.forEach((link) => {
                const href = link.getAttribute('href');
                if (!href) return;
                try {
                    const hrefPath = new URL(href, window.location.origin).pathname;
                    const isExact = currentPath === hrefPath;
                    const isNested = hrefPath !== '/' && currentPath.startsWith(hrefPath + '/');
                    if ((isExact || isNested) && hrefPath.length > bestLen) {
                        matched = link;
                        bestLen = hrefPath.length;
                    }
                } catch (_error) {
                    if (currentPath === href && href.length > bestLen) {
                        matched = link;
                        bestLen = href.length;
                    }
                }
            });
        }

        sidebarLinks.forEach((link) => {
            const on = link === matched;
            link.classList.toggle('active', on);
            if (on) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        });
    }
    
    // Immediately set clicked link as active and persist it
    const sidebarNav = document.querySelector('.sidebar-nav');
    if (sidebarNav) {
        sidebarNav.addEventListener('click', function(e) {
            const link = e.target.closest('.sidebar-nav-link');
            if (link && link.getAttribute('href') && !link.closest('.language-switcher')) {
                // Immediately set this link as active (visual feedback before navigation)
                const allLinks = document.querySelectorAll('.sidebar-nav-link:not(.language-switcher .sidebar-nav-link)');
                allLinks.forEach(l => l.classList.remove('active'));
                link.classList.add('active');
                
                // Store the clicked link's endpoint for after page reload
                const endpoint = link.getAttribute('data-endpoint');
                if (endpoint) {
                    sessionStorage.setItem('activeSidebarEndpoint', endpoint);
                }
            }
        });
    }
    
    // On page load, ensure the correct button is active
    window.addEventListener('load', function() {
        // First, let server-side template set active state
        // Then verify/update if needed
        setTimeout(function() {
            const storedEndpoint = sessionStorage.getItem('activeSidebarEndpoint');
            const currentEndpoint = getCurrentEndpoint();
            
            // If we have a stored endpoint and it matches current, ensure it's active
            if (storedEndpoint && storedEndpoint === currentEndpoint) {
                const sidebarLinks = document.querySelectorAll('.sidebar-nav-link:not(.language-switcher .sidebar-nav-link)');
                sidebarLinks.forEach(link => {
                    const endpoint = link.getAttribute('data-endpoint');
                    if (endpoint === storedEndpoint) {
                        sidebarLinks.forEach(l => l.classList.remove('active'));
                        link.classList.add('active');
                    }
                });
                // Clear stored state
                sessionStorage.removeItem('activeSidebarEndpoint');
            } else {
                // Update based on current endpoint/path
                updateSidebarActiveState();
            }
        }, 50);
    });
    
    // Also update on popstate (back/forward navigation)
    window.addEventListener('popstate', function() {
        setTimeout(updateSidebarActiveState, 50);
    });
    
    // Auto-hide alerts after 5 seconds
    // ALERT-01: only true flash messages auto-dismiss. The previous selector
    // closed every `.alert` on the page — including hidden error boxes inside
    // modals (removing form validation feedback) and the must-change-password
    // banner (removing its action button).
    const isFlashOnly = (alert) =>
        !alert.closest('.modal') &&
        !alert.classList.contains('d-none') &&
        !Array.from(alert.querySelectorAll('button, a, input, select, form'))
            .some(el => !el.classList.contains('btn-close'));
    const alerts = Array.from(document.querySelectorAll('.alert')).filter(isFlashOnly);
    if (alerts.length > 0) {
        alerts.forEach(alert => {
            setTimeout(() => {
                if (alert && alert.parentNode) {
                    const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
                    bsAlert.close();
                }
            }, 5000);
        });
    }
    
    // Smooth scroll for anchor links
    const anchorLinks = document.querySelectorAll('a[href^="#"]');
    if (anchorLinks.length > 0) {
        anchorLinks.forEach(anchor => {
            anchor.addEventListener('click', function (e) {
                e.preventDefault();
                const href = this.getAttribute('href');
                if (href && href !== '#') {
                    const target = document.querySelector(href);
                    if (target) {
                        target.scrollIntoView({
                            behavior: 'smooth',
                            block: 'start'
                        });
                    }
                }
            });
        });
    }
    
    // Initialize tooltips - only if Bootstrap is loaded
    if (typeof bootstrap !== 'undefined') {
        const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
        if (tooltipTriggerList.length > 0) {
            const tooltipList = Array.from(tooltipTriggerList).map(function (tooltipTriggerEl) {
                return new bootstrap.Tooltip(tooltipTriggerEl);
            });
        }
    }
    
    // Language Switcher is now handled by LanguageSwitcher module
    // The module provides seamless AJAX-based language switching
});

