// Plex OAuth PIN authentication for Cullarr
let pollInterval = null;
let currentPinId = null;
let timeoutTimer = null;
const POLL_INTERVAL_MS = 1000;
const TIMEOUT_MS = 300000; // 5 minutes

async function startPlexOAuth() {
    // Close any existing popup
    if (window.plexPopup && !window.plexPopup.closed) {
        window.plexPopup.close();
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
        const pinCode = data.code;
        
        // Show PIN dialog
        showPlexPinDialog(pinCode);
        
        // Start polling
        pollInterval = setInterval(() => pollForToken(currentPinId), POLL_INTERVAL_MS);
        
        // Set timeout
        timeoutTimer = setTimeout(() => {
            cancelPlexOAuth();
            showToast('Plex login timed out after 5 minutes', 'error');
        }, TIMEOUT_MS);
        
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
            closePlexPinDialog();
            showToast('Plex authentication successful!', 'success');
            
            // Reload Plex config display
            setTimeout(() => {
                if (typeof loadPlexConfig === 'function') {
                    loadPlexConfig();
                }
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
    if (window.plexPopup && !window.plexPopup.closed) {
        window.plexPopup.close();
    }
    if (currentPinId) {
        fetch(`/api/plex/oauth/pin/${currentPinId}`, { method: 'DELETE' }).catch(console.error);
        currentPinId = null;
    }
}

function showPlexPinDialog(pinCode) {
    // Remove existing dialog if present
    const existingDialog = document.getElementById('plex-pin-dialog');
    if (existingDialog) {
        existingDialog.remove();
    }
    
    // Create dialog
    const dialog = document.createElement('div');
    dialog.id = 'plex-pin-dialog';
    dialog.className = 'fixed inset-0 flex items-center justify-center z-50';
    dialog.style.background = 'rgba(0,0,0,0.7)';
    dialog.innerHTML = `
        <div class="card rounded-xl p-6 max-w-md w-full mx-4 text-center">
            <div class="mb-4">
                <svg class="w-12 h-12 mx-auto text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
                </svg>
            </div>
            <h3 class="text-xl font-bold mb-2">Link Your Plex Account</h3>
            <p class="text-sm mb-4" style="color: var(--text-secondary)">
                1. Go to <strong class="text-indigo-400">plex.tv/link</strong><br>
                2. Enter this code:
            </p>
            <div class="text-4xl font-mono font-bold tracking-wider bg-gray-800 py-3 px-6 rounded-lg inline-block mb-4">
                ${pinCode}
            </div>
            <p class="text-xs mb-4" style="color: var(--text-secondary)">
                The code expires in 5 minutes. A popup window will open automatically.
            </p>
            <div class="flex gap-3">
                <button onclick="openPlexAuthPopup()" class="flex-1 py-2 rounded-lg text-sm font-medium text-white btn-raise" style="background: var(--accent);">
                    Open Plex
                </button>
                <button onclick="cancelPlexOAuth(); closePlexPinDialog();" class="flex-1 py-2 rounded-lg text-sm border btn-raise" style="border-color: var(--border-color);">
                    Cancel
                </button>
            </div>
        </div>
    `;
    
    document.body.appendChild(dialog);
}

function closePlexPinDialog() {
    const dialog = document.getElementById('plex-pin-dialog');
    if (dialog) {
        dialog.remove();
    }
}

function openPlexAuthPopup() {
    if (currentPinId && active_pin_code) {
        // Get the current PIN code from the dialog
        const pinCode = document.querySelector('#plex-pin-dialog .text-4xl')?.textContent;
        if (pinCode) {
            const authUrl = `https://app.plex.tv/auth#!?clientID=Cullarr&code=${pinCode}`;
            window.plexPopup = window.open(authUrl, 'PlexAuth', 'width=600,height=700,scrollbars=yes');
            if (window.plexPopup) {
                window.plexPopup.focus();
            } else {
                showToast('Popup blocked. Please allow popups for this site.', 'warning');
                window.open('https://plex.tv/link', '_blank');
            }
        }
    }
}

// Store active pin code for popup
let active_pin_code = null;