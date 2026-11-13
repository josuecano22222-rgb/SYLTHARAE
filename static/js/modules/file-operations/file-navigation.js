/**
 * File Navigation in Modal
 * Handles navigating between files in modal view
 */

import { fileNavigationState, modalState } from '../core/state.js';
import { showFileDetails } from './file-details.js';

/**
 * Navigate to next file in modal
 * @param {number|string} direction - Number (-1 for previous, 1 for next) or string ('next', 'previous')
 */
export function navigateFileInModal(direction) {
    const files = fileNavigationState.currentFiles;
    if (!files || files.length === 0) {
        console.warn('No file list available for navigation');
        return;
    }
    
    const currentIndex = fileNavigationState.currentIndex || 0;
    let newIndex = currentIndex;
    
    // Handle numeric direction (-1 for previous, 1 for next)
    if (typeof direction === 'number') {
        newIndex = currentIndex + direction;
    } else if (direction === 'next' || direction === 1) {
        newIndex = (currentIndex + 1) % files.length;
    } else if (direction === 'previous' || direction === -1) {
        newIndex = (currentIndex - 1 + files.length) % files.length;
    }
    
    // Bounds check
    if (newIndex < 0 || newIndex >= files.length) {
        console.warn('Cannot navigate: index out of bounds');
        return;
    }
    
    const nextFile = files[newIndex];
    if (nextFile && nextFile.id) {
        fileNavigationState.currentIndex = newIndex;
        updateFileNavigationButtons();
        
        // Update title immediately
        const modalTitle = document.getElementById('modalTitle');
        if (modalTitle) {
            modalTitle.textContent = nextFile.name || 'File Details';
        }
        
        // Load new file content
        showFileDetails(nextFile.id, nextFile.name, files, newIndex);
    }
}

/**
 * Update file navigation buttons state
 */
export function updateFileNavigationButtons() {
    const files = fileNavigationState.currentFiles;
    const currentIndex = fileNavigationState.currentIndex || 0;
    const totalFiles = files ? files.length : 0;
    
    const prevBtn = document.getElementById('prevFileBtn');
    const nextBtn = document.getElementById('nextFileBtn');
    const counter = document.getElementById('fileNavCounter');
    
    if (prevBtn) {
        prevBtn.disabled = totalFiles === 0 || currentIndex <= 0;
    }
    
    if (nextBtn) {
        nextBtn.disabled = totalFiles === 0 || currentIndex >= totalFiles - 1;
    }
    
    if (counter && totalFiles > 0) {
        counter.textContent = `${currentIndex + 1} / ${totalFiles}`;
        counter.style.display = 'inline-block';
    } else if (counter) {
        counter.style.display = 'none';
    }
}

