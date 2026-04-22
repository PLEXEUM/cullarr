// Plex OAuth authentication for Cullarr
let pollInterval = null;
let currentPinId = null;
let timeoutTimer = null;
let popupWindow = null;
const POLL_INTERVAL_MS = 1000;
const TIMEOUT_MS = 300000; // 5 minutes

async function startPlexOAuth() {
    // Close any existing popup
    if (popupWindow && !popupWindow.closed) {
        popupWindow.close();
    }
    
    // Clear any existing polling
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
    if (timeoutTimer) {
        clearTimeout(timeoutTimer);
        timeoutTimer = null;
    }
    
    try {
        // Create PIN
        const response = await fetch('/api/plex/oauth/pin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) {
            throw new Error('Failed to create PIN');
        }
        
        const data = await response.json();
        currentPinId = data.id;
        
        // Open popup with auth URL
        popupWindow = window.open(data.auth_url, 'PlexAuth', 'width=800,height=600,scrollbars=yes');
        if (!popupWindow) {
            showToast('Popup blocked. Please allow popups for this site.', 'warning');
            return;
        }
        popupWindow.focus();
        
        // Start polling for token
        pollInterval = setInterval(() => pollForToken(currentPinId), POLL_INTERVAL_MS);
        
        // Set timeout
        timeoutTimer = setTimeout(() => {
            cancelPlexOAuth();
            showToast('Plex login timed out after 5 minutes', 'error');
        }, TIMEOUT_MS);
        
        // Monitor popup close
        const checkPopupClosed = setInterval(() => {
            if (popupWindow && popupWindow.closed) {
                clearInterval(checkPopupClosed);
                if (pollInterval) {
                    // Popup closed without authentication
                    cancelPlexOAuth();
                    showToast('Plex login cancelled', 'info');
                }
            }
        }, 500);
        
    } catch (error) {
        console.error('Plex OAuth error:', error);
        showToast('Failed to start Plex login: ' + error.message, 'error');
    }
}

async function pollForToken(pinId) {
    try {
        const response = await fetch(`/api/plex/oauth/pin/${pinId}`);
        const data = await response.json();
        
        if (data.authenticated) {
            // Success! Token received
            cancelPlexOAuth();
            showToast('Plex authentication successful!', 'success');
            
            // Reload page to refresh config
            setTimeout(() => {
                window.location.reload();
            }, 1500);
        }
    } catch (error) {
        console.error('Poll error:', error);
    }
}

function cancelPlexOAuth() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
    if (timeoutTimer) {
        clearTimeout(timeoutTimer);
        timeoutTimer = null;
    }
    if (popupWindow && !popupWindow.closed) {
        popupWindow.close();
        popupWindow = null;
    }
    if (currentPinId) {
        fetch(`/api/plex/oauth/pin/${currentPinId}`, { method: 'DELETE' }).catch(console.error);
        currentPinId = null;
    }
}