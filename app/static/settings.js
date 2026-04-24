// Settings page JavaScript for Cullarr

// DOM Elements
let ageSlider, sizeSlider, ratingSlider, qualitySlider, watchedSlider;
let ageVal, sizeVal, ratingVal, qualityVal, watchedVal;
let totalSpan, warningSpan;
let ageMaxDays, sizeMaxGb;
let minScoreThreshold;
let debounceTimer = null;

// Store current weights for auto-balancing
let currentWeights = {
    age: 25,
    size: 25,
    rating: 15,
    quality: 15,
    watched: 10
};

// Preset configurations (sum to 100)
const PRESETS = {
    balanced: { age: 25, size: 25, rating: 15, quality: 15, watched: 10 },
    spaceSaver: { age: 30, size: 40, rating: 10, quality: 10, watched: 10 },
    qualityKeeper: { age: 15, size: 15, rating: 30, quality: 30, watched: 10 },
    freshness: { age: 40, size: 20, rating: 15, quality: 15, watched: 10 }
};

// Load all settings on page load
document.addEventListener('DOMContentLoaded', () => {
    // Initialize DOM references
    ageSlider = document.getElementById('age-weight');
    sizeSlider = document.getElementById('size-weight');
    ratingSlider = document.getElementById('rating-weight');
    qualitySlider = document.getElementById('quality-weight');
    watchedSlider = document.getElementById('watched-weight');
    
    ageVal = document.getElementById('age-weight-val');
    sizeVal = document.getElementById('size-weight-val');
    ratingVal = document.getElementById('rating-weight-val');
    qualityVal = document.getElementById('quality-weight-val');
    watchedVal = document.getElementById('watched-weight-val');
    
    totalSpan = document.getElementById('total-weight');
    warningSpan = document.getElementById('weight-warning');
    ageMaxDays = document.getElementById('age-max-days');
    sizeMaxGb = document.getElementById('size-max-gb');
    minScoreThreshold = document.getElementById('min-score-threshold');
    
    // Load data
    loadRadarrConfig();
    loadPlexConfig();
    loadWeights();
    loadSettings();
    
    // Setup event listeners
    setupEventListeners();
});

function setupEventListeners() {
    // Radarr
    document.getElementById('radarr-test-btn').addEventListener('click', testAndSaveRadarr);
    document.getElementById('radarr-clear-btn').addEventListener('click', clearRadarr);
    
    // Plex
    document.getElementById('plex-auth-btn').addEventListener('click', authenticateAndSavePlex);
    document.getElementById('plex-clear-btn').addEventListener('click', clearPlex);
    document.getElementById('plex-save-label-btn').addEventListener('click', savePlexLabel);
    
    // Weight sliders with auto-balance
    const sliders = [ageSlider, sizeSlider, ratingSlider, qualitySlider, watchedSlider];
    sliders.forEach(slider => {
        if (slider) {
            slider.addEventListener('input', (e) => {
                updateWeightDisplay(e.target.id);
                handleAutoBalance(e.target.id, parseInt(e.target.value));
                scheduleLivePreview();
            });
        }
    });
    
    // Preset buttons
    document.getElementById('preset-balanced')?.addEventListener('click', () => applyPreset('balanced'));
    document.getElementById('preset-space-saver')?.addEventListener('click', () => applyPreset('spaceSaver'));
    document.getElementById('preset-quality-keeper')?.addEventListener('click', () => applyPreset('qualityKeeper'));
    document.getElementById('preset-freshness')?.addEventListener('click', () => applyPreset('freshness'));
    
    // Save buttons
    document.getElementById('save-weights-btn').addEventListener('click', saveWeights);
    document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
    
    // Advanced section toggle
    const advancedToggle = document.getElementById('advanced-toggle');
    const advancedContent = document.getElementById('advanced-content');
    const advancedIcon = document.getElementById('advanced-icon');
    if (advancedToggle) {
        advancedToggle.addEventListener('click', () => {
            const isHidden = advancedContent.classList.toggle('hidden');
            advancedIcon.textContent = isHidden ? '▶' : '▼';
        });
    }
    
    // Recalibrate button
    document.getElementById('recalibrate-btn')?.addEventListener('click', recalibrateAdvanced);
}

// Auto-balance sliders to keep 100% total
function handleAutoBalance(changedId, newValue) {
    // Get current values
    const oldValues = {
        age: parseInt(ageSlider.value),
        size: parseInt(sizeSlider.value),
        rating: parseInt(ratingSlider.value),
        quality: parseInt(qualitySlider.value),
        watched: parseInt(watchedSlider.value)
    };
    
    // Calculate delta
    const oldChanged = oldValues[changedId.replace('-weight', '')];
    const delta = newValue - oldChanged;
    
    if (delta === 0) return;
    
    // Sum of other sliders
    const otherSliders = ['age', 'size', 'rating', 'quality', 'watched'].filter(id => id !== changedId.replace('-weight', ''));
    const otherSum = otherSliders.reduce((sum, id) => sum + oldValues[id], 0);
    
    if (otherSum === 0) return;
    
    // Distribute delta proportionally
    const newValues = { ...oldValues };
    newValues[changedId.replace('-weight', '')] = newValue;
    
    for (const id of otherSliders) {
        const proportion = oldValues[id] / otherSum;
        let adjustment = delta * proportion;
        let newVal = oldValues[id] - adjustment;
        
        // Clamp to 0-100
        newVal = Math.max(0, Math.min(100, Math.round(newVal)));
        newValues[id] = newVal;
    }
    
    // Ensure total is exactly 100 (adjust if rounding caused issues)
    let total = Object.values(newValues).reduce((a, b) => a + b, 0);
    if (total !== 100 && total > 0) {
        // Find the largest slider to adjust
        const largestId = Object.keys(newValues).reduce((a, b) => newValues[a] > newValues[b] ? a : b);
        newValues[largestId] += (100 - total);
    }
    
    // Apply new values without triggering auto-balance again
    if (newValues.age !== oldValues.age) ageSlider.value = newValues.age;
    if (newValues.size !== oldValues.size) sizeSlider.value = newValues.size;
    if (newValues.rating !== oldValues.rating) ratingSlider.value = newValues.rating;
    if (newValues.quality !== oldValues.quality) qualitySlider.value = newValues.quality;
    if (newValues.watched !== oldValues.watched) watchedSlider.value = newValues.watched;
    
    // Update displays
    updateWeightDisplay('age-weight');
    updateWeightDisplay('size-weight');
    updateWeightDisplay('rating-weight');
    updateWeightDisplay('quality-weight');
    updateWeightDisplay('watched-weight');
    
    updateTotalWeight();
}

function updateWeightDisplay(id) {
    const val = document.getElementById(id).value;
    const displayId = id.replace('-weight', '-weight-val');
    const display = document.getElementById(displayId);
    if (display) display.textContent = `${val}%`;
}

function updateTotalWeight() {
    const age = parseInt(ageSlider.value) || 0;
    const size = parseInt(sizeSlider.value) || 0;
    const rating = parseInt(ratingSlider.value) || 0;
    const quality = parseInt(qualitySlider.value) || 0;
    const watched = parseInt(watchedSlider.value) || 0;
    const total = age + size + rating + quality + watched;
    
    if (totalSpan) totalSpan.textContent = total;
    
    // Auto-balance keeps total at 100, but show warning if not
    if (warningSpan) {
        if (total !== 100) {
            warningSpan.classList.remove('hidden');
        } else {
            warningSpan.classList.add('hidden');
        }
    }
}

function applyPreset(presetName) {
    const preset = PRESETS[presetName];
    if (!preset) return;
    
    // Apply preset values
    ageSlider.value = preset.age;
    sizeSlider.value = preset.size;
    ratingSlider.value = preset.rating;
    qualitySlider.value = preset.quality;
    watchedSlider.value = preset.watched;
    
    // Update displays
    updateWeightDisplay('age-weight');
    updateWeightDisplay('size-weight');
    updateWeightDisplay('rating-weight');
    updateWeightDisplay('quality-weight');
    updateWeightDisplay('watched-weight');
    
    updateTotalWeight();
    scheduleLivePreview();
    showToast(`Preset "${presetName}" applied`, 'info');
}

// Debounced live preview
function scheduleLivePreview() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => updateLivePreview(), 300);
}

async function updateLivePreview() {
    const previewDiv = document.getElementById('live-preview-content');
    if (!previewDiv) return;
    
    previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">Updating preview...</div>';
    
    try {
        // Get current weights
        const weights = {
            age_weight: parseInt(ageSlider.value),
            size_weight: parseInt(sizeSlider.value),
            rating_weight: parseInt(ratingSlider.value),
            quality_weight: parseInt(qualitySlider.value),
            watched_weight: parseInt(watchedSlider.value),
            age_max_days: parseInt(ageMaxDays.value),
            size_max_gb: parseFloat(sizeMaxGb.value)
        };
        
        // Call preview endpoint (we'll need to add this to backend)
        const res = await fetch('/api/settings/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(weights)
        });
        
        if (!res.ok) {
            previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">Preview unavailable — run a score cycle first</div>';
            return;
        }
        
        const data = await res.json();
        
        if (!data.movie) {
            previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">No movies found in library</div>';
            return;
        }
        
        renderPreview(data.movie);
        
    } catch (e) {
        console.error('Preview failed:', e);
        previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">Preview unavailable</div>';
    }
}

function renderPreview(movie) {
    const previewDiv = document.getElementById('live-preview-content');
    if (!previewDiv) return;
    
    const score = (movie.raw_score * 100).toFixed(1);
    const wouldQueue = movie.raw_score * 100 > (parseInt(minScoreThreshold?.value) || 0);
    const queueBadge = wouldQueue 
        ? '<span class="badge" style="background: var(--success-bg); color: var(--success);">✓ Would queue</span>'
        : '<span class="badge" style="background: var(--danger-bg); color: var(--danger);">✗ Below threshold</span>';
    
    // Build factor bars
    const factors = movie.factors || [];
    const factorHtml = factors.map(f => {
        const pct = (f.contribution * 100).toFixed(1);
        const barWidth = Math.min(Math.round(f.raw_score * 100), 100);
        return `
            <div class="mb-2">
                <div class="flex justify-between text-xs mb-1">
                    <span>${f.name}</span>
                    <span style="color: var(--text-secondary);">${f.details || ''}</span>
                </div>
                <div class="flex items-center gap-2">
                    <div class="flex-1 rounded-full h-1.5" style="background: var(--border-color)">
                        <div class="h-1.5 rounded-full" style="width: ${barWidth}%; background: var(--accent);"></div>
                    </div>
                    <span class="text-xs font-mono w-10 text-right" style="color: var(--text-secondary);">${pct}%</span>
                </div>
            </div>
        `;
    }).join('');
    
    previewDiv.innerHTML = `
        <div class="space-y-3">
            <div class="flex justify-between items-start">
                <div>
                    <div class="font-semibold">${escapeHtml(movie.movie_title)} (${movie.movie_year || 'N/A'})</div>
                    <div class="text-xs" style="color: var(--text-secondary);">${movie.size_gb?.toFixed(1) || 0} GB • ${movie.age_days || 0} days old</div>
                </div>
                ${queueBadge}
            </div>
            <div class="border-t pt-2" style="border-color: var(--border-color);">
                ${factorHtml}
            </div>
            <div class="border-t pt-2 flex justify-between items-center" style="border-color: var(--border-color);">
                <span class="text-sm font-semibold">TOTAL SCORE:</span>
                <span class="text-xl font-bold" style="color: var(--accent);">${score}</span>
            </div>
        </div>
    `;
}

async function recalibrateAdvanced() {
    const btn = document.getElementById('recalibrate-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Analyzing library...';
    
    try {
        const res = await fetch('/api/settings/recalibrate', { method: 'POST' });
        const data = await res.json();
        
        if (res.ok) {
            ageMaxDays.value = data.age_max_days;
            sizeMaxGb.value = data.size_max_gb;
            showToast(data.message, 'success');
            // Refresh live preview with new values
            scheduleLivePreview();
        } else {
            showToast(data.detail || 'Recalibration failed', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ----- Existing functions (keep as is, with modifications) -----

async function loadRadarrConfig() {
    try {
        const res = await fetch('/api/radarr/config');
        const data = await res.json();
        if (data.configured) {
            document.getElementById('radarr-url').value = data.url || '';
            document.getElementById('radarr-status').innerHTML = '<span style="color: var(--success);">✅ Configured</span>';
        } else {
            document.getElementById('radarr-status').innerHTML = '<span style="color: var(--warning);">⚠ Not configured</span>';
        }
    } catch (e) {
        console.error('Failed to load Radarr config:', e);
    }
}

async function testAndSaveRadarr() {
    const url = document.getElementById('radarr-url').value;
    const apiKey = document.getElementById('radarr-api-key').value;
    
    if (!url || !apiKey) {
        showToast('Please enter both URL and API key', 'error');
        return;
    }
    
    const btn = document.getElementById('radarr-test-btn');
    btn.disabled = true;
    btn.textContent = 'Testing...';
    
    try {
        const testRes = await fetch('/api/radarr/config/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: apiKey })
        });
        
        if (!testRes.ok) {
            const err = await testRes.json();
            showToast(err.detail || 'Connection failed', 'error');
            return;
        }
        
        const saveRes = await fetch('/api/radarr/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: apiKey })
        });
        
        if (saveRes.ok) {
            showToast('Radarr configured successfully', 'success');
            loadRadarrConfig();
        } else {
            showToast('Failed to save configuration', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Test & Save';
    }
}

async function clearRadarr() {
    if (!confirm('Clear Radarr configuration?')) return;
    try {
        const res = await fetch('/api/radarr/config', { method: 'DELETE' });
        if (res.ok) {
            showToast('Radarr configuration cleared', 'success');
            document.getElementById('radarr-url').value = '';
            document.getElementById('radarr-api-key').value = '';
            loadRadarrConfig();
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// Plex OAuth (keep existing implementation)
let plexPopupWindow = null;
let plexPollInterval = null;
let plexTimeoutTimer = null;

async function loadPlexConfig() {
    try {
        const res = await fetch('/api/plex/config');
        const data = await res.json();
        
        document.getElementById('plex-url').value = data.url || '';
        document.getElementById('plex-label').value = data.label_text || 'Movies Leaving Soon';
        
        const statusDiv = document.getElementById('plex-status');
        if (data.configured && data.url && data.api_key === '[REDACTED]') {
            statusDiv.innerHTML = '<span style="color: var(--success);">✅ Configured</span>';
        } else if (data.api_key === '[REDACTED]') {
            statusDiv.innerHTML = '<span style="color: var(--warning);">⚠ Authenticated (token saved)</span>';
        } else {
            statusDiv.innerHTML = '<span style="color: var(--warning);">⚠ Not configured</span>';
        }
    } catch (e) {
        console.error('Failed to load Plex config:', e);
    }
}

async function authenticateAndSavePlex() {
    const url = document.getElementById('plex-url').value;
    const labelText = document.getElementById('plex-label').value;
    
    if (!url) {
        showToast('Please enter Plex server URL', 'error');
        return;
    }
    
    if (plexPopupWindow && !plexPopupWindow.closed) {
        plexPopupWindow.close();
    }
    
    if (plexPollInterval) {
        clearInterval(plexPollInterval);
        plexPollInterval = null;
    }
    if (plexTimeoutTimer) {
        clearTimeout(plexTimeoutTimer);
        plexTimeoutTimer = null;
    }
    
    const btn = document.getElementById('plex-auth-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Authenticating...';
    
    try {
        const response = await fetch('/api/plex/oauth/pin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) {
            throw new Error('Failed to start Plex login');
        }
        
        const data = await response.json();
        
        plexPopupWindow = window.open(data.auth_url, 'PlexAuth', 'width=800,height=600,scrollbars=yes');
        if (!plexPopupWindow) {
            showToast('Popup blocked. Please allow popups for this site.', 'warning');
            btn.disabled = false;
            btn.textContent = originalText;
            return;
        }
        plexPopupWindow.focus();
        
        let pollCount = 0;
        plexPollInterval = setInterval(async () => {
            pollCount++;
            try {
                const pollRes = await fetch(`/api/plex/oauth/pin/${data.id}`);
                const pollData = await pollRes.json();
                
                if (pollData.authenticated) {
                    clearInterval(plexPollInterval);
                    clearTimeout(plexTimeoutTimer);
                    if (plexPopupWindow && !plexPopupWindow.closed) {
                        plexPopupWindow.close();
                    }
                    
                    const saveRes = await fetch('/api/plex/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: url, label_text: labelText, enabled: true })
                    });
                    
                    if (saveRes.ok) {
                        showToast('Plex configured successfully!', 'success');
                        await loadPlexConfig();
                    } else {
                        showToast('Token saved but failed to save URL/label', 'error');
                    }
                    
                    btn.disabled = false;
                    btn.textContent = originalText;
                } else if (pollCount > 300) {
                    clearInterval(plexPollInterval);
                    clearTimeout(plexTimeoutTimer);
                    showToast('Plex login timed out', 'error');
                    btn.disabled = false;
                    btn.textContent = originalText;
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }, 1000);
        
        plexTimeoutTimer = setTimeout(() => {
            if (plexPollInterval) {
                clearInterval(plexPollInterval);
                showToast('Plex login timed out after 5 minutes', 'error');
                btn.disabled = false;
                btn.textContent = originalText;
            }
        }, 300000);
        
    } catch (error) {
        console.error('Plex setup error:', error);
        showToast('Failed to start Plex login: ' + error.message, 'error');
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

async function savePlexLabel() {
    const url = document.getElementById('plex-url').value;
    const labelText = document.getElementById('plex-label').value;
    
    if (!url) {
        showToast('Please enter Plex server URL first', 'error');
        return;
    }
    
    try {
        const res = await fetch('/api/plex/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url, label_text: labelText, enabled: true })
        });
        
        if (res.ok) {
            showToast('Label saved successfully', 'success');
            await loadPlexConfig();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to save label', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function clearPlex() {
    if (!confirm('Clear Plex configuration? This will remove URL, token, and label.')) return;
    
    try {
        const res = await fetch('/api/plex/config', { method: 'DELETE' });
        if (res.ok) {
            showToast('Plex configuration cleared', 'success');
            document.getElementById('plex-url').value = '';
            document.getElementById('plex-label').value = 'Movies Leaving Soon';
            await loadPlexConfig();
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// Weights
async function loadWeights() {
    try {
        const res = await fetch('/api/settings/weights');
        const data = await res.json();
        
        ageSlider.value = data.age_weight || 25;
        sizeSlider.value = data.size_weight || 25;
        ratingSlider.value = data.rating_weight || 15;
        qualitySlider.value = data.quality_weight || 15;
        watchedSlider.value = data.watched_weight || 10;
        
        updateWeightDisplay('age-weight');
        updateWeightDisplay('size-weight');
        updateWeightDisplay('rating-weight');
        updateWeightDisplay('quality-weight');
        updateWeightDisplay('watched-weight');
        
        ageMaxDays.value = data.age_max_days || 365;
        sizeMaxGb.value = data.size_max_gb || 100;
        
        updateTotalWeight();
        scheduleLivePreview();
    } catch (e) {
        console.error('Failed to load weights:', e);
    }
}

async function saveWeights() {
    const total = parseInt(totalSpan.textContent);
    if (total !== 100) {
        showToast('Weights must sum to 100%', 'error');
        return;
    }
    
    const payload = {
        age_weight: parseInt(ageSlider.value),
        size_weight: parseInt(sizeSlider.value),
        rating_weight: parseInt(ratingSlider.value),
        quality_weight: parseInt(qualitySlider.value),
        watched_weight: parseInt(watchedSlider.value),
        age_max_days: parseInt(ageMaxDays.value),
        size_max_gb: parseFloat(sizeMaxGb.value),
        monitored_weight: 0  // Send 0 for compatibility
    };
    
    try {
        const res = await fetch('/api/settings/weights', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            showToast('Weights saved successfully', 'success');
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to save weights', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// Settings
async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        
        document.getElementById('cullarr-enabled').checked = data.enabled || false;
        document.getElementById('score-cron').value = data.score_cron || '0 3 * * 0';
        document.getElementById('cull-cron').value = data.cull_cron || '0 2 * * *';
        document.getElementById('max-queued').value = data.max_queued || 20;
        document.getElementById('delete-after-days').value = data.delete_after_days || 7;
        document.getElementById('protection-days').value = data.protection_days || 30;
        document.getElementById('collection-grouping').checked = data.collection_grouping || false;
        if (minScoreThreshold) minScoreThreshold.value = data.min_score_threshold || 0;
    } catch (e) {
        console.error('Failed to load settings:', e);
    }
}

async function saveSettings() {
    const payload = {
        enabled: document.getElementById('cullarr-enabled').checked,
        score_cron: document.getElementById('score-cron').value,
        cull_cron: document.getElementById('cull-cron').value,
        max_queued: parseInt(document.getElementById('max-queued').value),
        delete_after_days: parseInt(document.getElementById('delete-after-days').value),
        protection_days: parseInt(document.getElementById('protection-days').value),
        collection_grouping: document.getElementById('collection-grouping').checked,
        min_score_threshold: parseInt(minScoreThreshold?.value || 0)
    };
    
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            showToast('Settings saved successfully', 'success');
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to save settings', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[m] || m);
}