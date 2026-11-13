/**
 * Root View Loader
 * Extracted from the legacy file-management-system.js
 */

import { sectionDataCache, translations, sectionLabels } from '../core/config.js';
import { updateSidebarActiveState, updateAllSidebarCounts } from '../ui/sidebar.js';
import { updateNavButtons, addToHistory } from '../navigation/history.js';

export function loadRootView() {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) {
        console.error('unifiedContentView element not found!');
        return;
    }

    console.log('Loading root view...');
    updateNavItemCount(0, 0, 0);

    const categoryCount = window.appData?.stats?.totalCategories || (sectionDataCache.category || []).length;
    const keywordsCount = window.appData?.stats?.totalKeywords || (sectionDataCache.keywords || []).length;
    const titlesCount = window.appData?.stats?.totalTitles || (sectionDataCache.titles || []).length;
    const sourcesCount = window.appData?.stats?.totalSources || (sectionDataCache.sources || []).length;
    const sidesCount = window.appData?.stats?.totalSides || (sectionDataCache.sides || []).length;
    const hashCount = window.appData?.stats?.totalHashes || (sectionDataCache.hash || []).length;
    const geolocationCount = window.appData?.stats?.totalGeolocation || 0;
    const filetypesCount = window.appData?.stats?.totalFiletypes || 0;

    console.log('Root view counts:', {
        category: categoryCount,
        keywords: keywordsCount,
        titles: titlesCount,
        sources: sourcesCount,
        sides: sidesCount,
        hash: hashCount,
        geolocation: geolocationCount,
        filetypes: filetypesCount
    });
    
    // Update sidebar counts from stats
    if (window.appData?.stats) {
        updateAllSidebarCounts(window.appData.stats);
    }

    contentView.innerHTML = `
        <div class="explorer-grid" id="contentGrid">
            ${renderRootCard('category', categoryCount, 'folder')}
            ${renderRootCard('keywords', keywordsCount, 'tags')}
            ${renderRootCard('titles', titlesCount, 'file-text')}
            ${renderRootCard('sources', sourcesCount, 'people')}
            ${renderRootCard('sides', sidesCount, 'diagram-3')}
            ${renderRootCard('hash', hashCount, 'hash')}
            ${renderRootCard('geolocation', geolocationCount, 'geo-alt-fill')}
            ${renderRootCard('filetypes', filetypesCount, 'file-earmark-binary')}
        </div>
    `;
    updateSidebarActiveState(null);
    updateNavButtons();
    addToHistory({ type: 'root', section: null, itemId: null, itemName: null });

    contentView.querySelectorAll('.explorer-item').forEach(el => {
        const section = el.getAttribute('data-section');
        const go = () => {
            const nav = window.fms?.navigation?.navigateToSection || window.navigateToSection;
            if (typeof nav === 'function') nav(section);
        };
        el.addEventListener('click', go);
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                go();
            }
        });
    });

    // Soft-nav / tall chrome can leave the explorer below the fold. Bring
    // the first row into view so the root content is immediately usable.
    const grid = contentView.querySelector('.explorer-grid');
    if (grid) {
        const rect = grid.getBoundingClientRect();
        if (rect.top > window.innerHeight * 0.55 || rect.bottom > window.innerHeight) {
            grid.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    }
    
    console.log('Root view loaded successfully');
}

function renderRootCard(section, count, icon) {
    const label = sectionLabels[section] || section;
    return `
        <div class="explorer-item" data-section="${section}" style="cursor: pointer;" role="button" tabindex="0" title="${label}" aria-label="${label}: ${count} ${translations.items || 'items'}">
            <div class="explorer-item-icon"><i class="bi bi-${icon}" aria-hidden="true"></i></div>
            <div class="explorer-item-name">${label}</div>
            <div class="explorer-item-details">${count} ${translations.items || 'items'}</div>
        </div>
    `;
}

function updateNavItemCount(startItem, endItem, total) {
    const navItemCount = document.getElementById('navItemCount');
    if (!navItemCount) return;
    navItemCount.textContent = total ? `${startItem}-${endItem} / ${total}` : '';
}

