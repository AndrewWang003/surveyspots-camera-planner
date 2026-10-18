/**
 * Smart Traffic Survey Assistant - Popup Logic
 * All event binding via addEventListener. All async operations use async/await.
 */

/* ---------- DOM Element Cache ---------- */

const dom = {};
let processedPinnedMap = null;
let currentPreviewId = null;
let currentReviewData = [];

const DATA_TAG_OPTIONS = [
  { value: 'site', label: 'Site' },
  { value: 'camera', label: 'Camera' },
  { value: 'direction', label: 'Direction' },
  { value: 'polygon', label: 'Polygon' },
  { value: 'route', label: 'Route' },
  { value: 'atc', label: 'ATC' },
];

/* ---------- Initialization ---------- */

document.addEventListener('DOMContentLoaded', async () => {
  cacheDomElements();
  bindEventListeners();

  // Check cached auth state first.
  try {
    const stored = await chrome.storage.session.get('authState');
    if (stored.authState) {
      updateAuthUI(stored.authState);
    }
  } catch (error) {
    console.error('[Popup] Failed to read cached auth:', error);
  }

});

/**
 * Cache all frequently-accessed DOM elements.
 */
function cacheDomElements() {
  dom.authSection = document.getElementById('auth-section');
  dom.authAuthenticated = document.getElementById('auth-authenticated');
  dom.authUnauthorized = document.getElementById('auth-unauthorized');
  dom.authEmail = document.getElementById('auth-email');
  dom.authEmailUnauth = document.getElementById('auth-email-unauth');
  dom.authIcon = document.getElementById('auth-icon');
  dom.btnSignin = document.getElementById('btn-signin');
  dom.btnSignout = document.getElementById('btn-signout');
  dom.btnUploadPinnedMap = document.getElementById('btn-upload-pinned-map');
  dom.btnDownloadProcessedMap = document.getElementById('btn-download-processed-map');
  dom.btnUpload = document.getElementById('btn-upload');
  dom.pinnedMapInput = document.getElementById('pinned-map-input');
  dom.fileInput = document.getElementById('file-input');
  dom.fileInfo = document.getElementById('file-info');
  dom.generationSummary = document.getElementById('generation-summary');
  dom.summarySiteCount = document.getElementById('summary-site-count');
  dom.summaryCompleteCount = document.getElementById('summary-complete-count');
  dom.summaryPartialCount = document.getElementById('summary-partial-count');
  dom.summaryReviewCount = document.getElementById('summary-review-count');
  dom.summaryCameraCount = document.getElementById('summary-camera-count');
  dom.summarySupplementalCount = document.getElementById('summary-supplemental-count');
  dom.summaryUnmatchedCount = document.getElementById('summary-unmatched-count');
  dom.statusMessage = document.getElementById('status-message');
  dom.loadingOverlay = document.getElementById('loading-overlay');
  // Layer review panel elements
  dom.layerReview = document.getElementById('layer-review');
  dom.layerReviewSubtitle = document.getElementById('layer-review-subtitle');
  dom.layerReviewList = document.getElementById('layer-review-list');
  dom.btnConfirmUpload = document.getElementById('btn-confirm-upload');
  dom.btnCancelUpload = document.getElementById('btn-cancel-upload');
  dom.manualReview = document.getElementById('manual-review');
  dom.manualReviewSubtitle = document.getElementById('manual-review-subtitle');
  dom.manualReviewActions = document.getElementById('manual-review-actions');
  dom.manualReviewList = document.getElementById('manual-review-list');
  dom.btnSubmitReview = document.getElementById('btn-submit-review');
}

/**
 * Bind all event listeners (no inline handlers).
 */
function bindEventListeners() {
  dom.btnSignin.addEventListener('click', handleSignIn);
  dom.btnSignout.addEventListener('click', handleSignOut);
  dom.btnUploadPinnedMap.addEventListener('click', handlePinnedMapUploadClick);
  dom.btnDownloadProcessedMap.addEventListener('click', handleDownloadProcessedMap);
  dom.btnUpload.addEventListener('click', handleUploadClick);
  dom.pinnedMapInput.addEventListener('change', handlePinnedMapSelected);
  dom.fileInput.addEventListener('change', handleFileSelected);
  // Layer review panel events
  dom.btnConfirmUpload.addEventListener('click', handleConfirmUpload);
  dom.btnCancelUpload.addEventListener('click', handleCancelUpload);
  dom.manualReviewActions.addEventListener('click', handleReviewActionClick);
  dom.btnSubmitReview.addEventListener('click', handleSubmitReview);
}

/* ---------- Auth UI ---------- */

/**
 * Updates the authentication UI based on auth data.
 * @param {{ token: string|null, email: string|null, authorized: boolean }} authData
 */
function updateAuthUI(authData) {
  if (!authData) {
    // Not authenticated
    dom.authAuthenticated.hidden = true;
    dom.authUnauthorized.hidden = true;
    dom.btnSignin.hidden = false;
    dom.btnUploadPinnedMap.disabled = true;
    dom.btnDownloadProcessedMap.disabled = true;
    dom.btnUpload.disabled = true;
    return;
  }

  if (authData.token && authData.authorized) {
    // Authenticated and authorized
    dom.authAuthenticated.hidden = false;
    dom.authUnauthorized.hidden = true;
    dom.btnSignin.hidden = true;
    dom.authEmail.textContent = authData.email;
    dom.authIcon.textContent = '✅';
    dom.btnUpload.disabled = false;
    dom.btnUploadPinnedMap.disabled = false;
    dom.btnDownloadProcessedMap.disabled = !processedPinnedMap;
    updateButtonStates();
  } else if (authData.token && !authData.authorized) {
    // Authenticated but unauthorized domain
    dom.authAuthenticated.hidden = true;
    dom.authUnauthorized.hidden = false;
    dom.btnSignin.hidden = true;
    dom.authEmailUnauth.textContent = authData.email;
    dom.btnUploadPinnedMap.disabled = true;
    dom.btnDownloadProcessedMap.disabled = true;
    dom.btnUpload.disabled = true;
  } else {
    // Not authenticated (error or no token)
    dom.authAuthenticated.hidden = true;
    dom.authUnauthorized.hidden = true;
    dom.btnSignin.hidden = false;
    dom.btnUploadPinnedMap.disabled = true;
    dom.btnDownloadProcessedMap.disabled = true;
    dom.btnUpload.disabled = true;
  }
}

/**
 * Enables/disables generated-map download based on auth and processing state.
 */
function updateButtonStates() {
  const isAuthed = !dom.authAuthenticated.hidden;
  dom.btnDownloadProcessedMap.disabled = !(processedPinnedMap && isAuthed);
}

/* ---------- Event Handlers ---------- */

/**
 * Handle sign-in button click.
 */
async function handleSignIn() {
  try {
    dom.btnSignin.disabled = true;
    dom.btnSignin.textContent = 'Signing in…';

    const response = await chrome.runtime.sendMessage({ type: 'GET_AUTH_TOKEN' });
    updateAuthUI(response);

    if (response?.error) {
      showToast(response.error, 'error');
    }
  } catch (error) {
    console.error('[Popup] Sign-in error:', error);
    showToast('Sign-in failed. Please try again.', 'error');
  } finally {
    dom.btnSignin.disabled = false;
    dom.btnSignin.innerHTML =
      '<svg class="google-icon" viewBox="0 0 24 24" width="18" height="18">' +
      '<path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z"/>' +
      '<path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>' +
      '<path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>' +
      '<path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>' +
      '</svg> Sign in with Google';
  }
}

/**
 * Handle sign-out button click.
 */
async function handleSignOut() {
  try {
    await chrome.runtime.sendMessage({ type: 'SIGN_OUT' });
    updateAuthUI(null);
    showToast('Signed out', 'success');
  } catch (error) {
    console.error('[Popup] Sign-out error:', error);
    showToast('Sign-out failed.', 'error');
  }
}

/**
 * Handle upload button click — trigger hidden file input.
 */
function handleUploadClick() {
  dom.fileInput.click();
}

/**
 * Handle pinned map upload button click.
 */
function handlePinnedMapUploadClick() {
  dom.pinnedMapInput.click();
}

/**
 * Handle pinned map KML selection and request processed camera locations.
 * @param {Event} event
 */
async function handlePinnedMapSelected(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.kml') && !file.name.toLowerCase().endsWith('.kmz')) {
    showToast('Select a .kml or .kmz pinned map export.', 'error');
    return;
  }

  dom.fileInfo.textContent = `Pinned map: ${file.name}`;
  dom.fileInfo.hidden = false;

  try {
    setLoading(true);

    const stored = await chrome.storage.session.get('authState');
    const auth = stored.authState;
    if (!auth?.token) {
      throw new Error('Not authenticated. Please sign in first.');
    }

    const formData = new FormData();
    formData.append('file', file, file.name);

    const apiResponse = await fetch(`${CONFIG.API_BASE_URL}/api/v1/pinned-map-reference-kml`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.token}` },
      body: formData
    });

    if (!apiResponse.ok) {
      const responseText = await apiResponse.text();
      const errorData = parseJsonMaybe(responseText);
      throw new Error(errorData?.detail || `Server error: ${apiResponse.status} ${apiResponse.statusText}`);
    }

    const summary = readGenerationSummary(apiResponse.headers);
    if (summary.siteCount === 0) {
      processedPinnedMap = null;
      hideGenerationSummary();
      updateButtonStates();
      showToast('No historical Site matched the uploaded map.', 'error');
      return;
    }

    const processedBlob = await apiResponse.blob();
    processedPinnedMap = {
      blob: processedBlob,
      filename: processedFilename(file.name),
      pointCount: summary.cameraCount,
      summary
    };
    showGenerationSummary(summary);
    updateButtonStates();
    showToast(
      `Processed ${summary.siteCount} Site${summary.siteCount === 1 ? '' : 's'} with ` +
      `${summary.cameraCount} Camera location${summary.cameraCount === 1 ? '' : 's'}.`,
      'success'
    );
  } catch (error) {
    console.error('[Popup] Pinned map processing error:', error);
    processedPinnedMap = null;
    hideGenerationSummary();
    updateButtonStates();
    showToast(readableError(error, 'Pinned map processing failed.'), 'error');
  } finally {
    setLoading(false);
    dom.pinnedMapInput.value = '';
    dom.fileInfo.hidden = true;
  }
}

/**
 * Read normalized generation quality counts from response headers.
 * @param {Headers} headers
 */
function readGenerationSummary(headers) {
  const count = (name) => Number(headers.get(name) || 0);
  return {
    siteCount: count('X-SurveySpots-Canonical-Site-Count'),
    completeCount: count('X-SurveySpots-Complete-Count'),
    partialCount: count('X-SurveySpots-Partial-Count'),
    reviewCount: count('X-SurveySpots-Requires-Review-Count'),
    cameraCount: count('X-SurveySpots-Camera-Count'),
    supplementalCount: count('X-SurveySpots-Supplemental-Camera-Count'),
    unmatchedCount: count('X-SurveySpots-Unmatched-Count')
  };
}

function showGenerationSummary(summary) {
  dom.summarySiteCount.textContent = summary.siteCount;
  dom.summaryCompleteCount.textContent = summary.completeCount;
  dom.summaryPartialCount.textContent = summary.partialCount;
  dom.summaryReviewCount.textContent = summary.reviewCount;
  dom.summaryCameraCount.textContent = summary.cameraCount;
  dom.summarySupplementalCount.textContent = summary.supplementalCount;
  dom.summaryUnmatchedCount.textContent = summary.unmatchedCount;
  dom.generationSummary.hidden = false;
}

function hideGenerationSummary() {
  dom.generationSummary.hidden = true;
}

/**
 * Download the processed KMZ generated from the last pinned map upload.
 */
async function handleDownloadProcessedMap() {
  if (!processedPinnedMap?.blob) {
    showToast('Upload a pinned map first.', 'error');
    return;
  }

  const url = URL.createObjectURL(processedPinnedMap.blob);

  await chrome.downloads.download({
    url,
    filename: processedPinnedMap.filename,
    saveAs: false
  });

  showToast(`Processed KMZ downloaded with ${processedPinnedMap.pointCount} icon${processedPinnedMap.pointCount === 1 ? '' : 's'}.`, 'success');
}

/**
 * Handle file selection from the hidden input.
 * @param {Event} event
 */
async function handleFileSelected(event) {
  let files = [...(event.target.files || [])];

  // Filter out any hidden files or files that are not .kml or .kmz (e.g. .DS_Store)
  files = files.filter(f => f.name.toLowerCase().endsWith('.kml') || f.name.toLowerCase().endsWith('.kmz'));

  if (files.length === 0) {
    showToast('No valid .kml or .kmz files selected.', 'error');
    return;
  }

  // Show selected filenames
  dom.fileInfo.textContent = files.length === 1
    ? `Selected: ${files[0].name}`
    : `Selected: ${files.length} files`;
  dom.fileInfo.hidden = false;

  try {
    setLoading(true);

    const stored = await chrome.storage.session.get('authState');
    const auth = stored.authState;
    if (!auth?.token) {
      throw new Error('Not authenticated. Please sign in first.');
    }

    const formData = new FormData();
    for (const file of files) {
      const buffer = await file.arrayBuffer();
      const blob = new Blob([buffer], { type: file.type || 'application/vnd.google-earth.kml+xml' });
      formData.append('files', blob, file.name);
    }

    // Step 1: Send to preview endpoint (parse only, no DB commit)
    const apiResponse = await fetch(`${CONFIG.API_BASE_URL}/api/v1/preview-upload`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.token}` },
      body: formData
    });

    const data = await apiResponse.json().catch(() => null);
    if (!apiResponse.ok) {
      throw new Error(data?.detail || `Server error: ${apiResponse.status}`);
    }

    // Store preview ID and show the layer review panel
    currentPreviewId = data.preview_id;
    showLayerReview(data);

  } catch (error) {
    console.error('[Popup] Preview error:', error);
    showToast(error.message || 'Preview failed. Please try again.', 'error');
  } finally {
    setLoading(false);
    dom.fileInput.value = '';
    dom.fileInfo.hidden = true;
  }
}

/* ---------- Layer Review ---------- */

/**
 * Display the layer review panel with parsed file data.
 * @param {object} previewData - PreviewResponse from the API
 */
function showLayerReview(previewData) {
  dom.layerReviewList.innerHTML = '';

  const totalPlacemarks = previewData.files.reduce(
    (sum, file) => sum + (file.total_placemarks || file.total_points),
    0
  );
  dom.layerReviewSubtitle.textContent =
    `${totalPlacemarks} Placemarks found in ${previewData.files.length} file(s).`;

  for (const file of previewData.files) {
    // File group header (only shown for multi-file uploads)
    if (previewData.files.length > 1) {
      const fileHeader = document.createElement('div');
      fileHeader.className = 'layer-review__file-group';
      fileHeader.innerHTML = `<div class="layer-review__file-name">📄 ${escapeHtml(file.filename)}</div>`;
      dom.layerReviewList.appendChild(fileHeader);
    }

    for (const layer of file.layers) {
      dom.layerReviewList.appendChild(
        buildLayerRow(layer, file.file_id, file.filename)
      );
    }
  }

  dom.layerReview.hidden = false;
  dom.layerReview.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * Build a single layer row element for the review panel.
 * @param {object} layer - LayerPreview object
 * @param {string} fileId - Stable preview file identity
 * @param {string} filename - Parent filename
 * @returns {HTMLElement}
 */
function buildLayerRow(layer, fileId, filename) {
  const row = document.createElement('div');
  row.className = 'layer-row';
  row.dataset.family = layer.inferred_family;
  row.dataset.layerName = layer.layer_name;
  row.dataset.fileId = fileId;
  row.dataset.filename = filename;

  const supportedFamilies = ['ic', 'od', 'ipod', 'hik', 'atc', 'site'];
  const isAutoSelected = supportedFamilies.includes(layer.inferred_family);
  if (isAutoSelected) row.classList.add('layer-row--selected');

  const placemarkCount = layer.placemark_count || layer.point_count;
  const suggestedTags = inferReviewTags(layer);
  const tagControls = DATA_TAG_OPTIONS.map((tag) => `
      <label class="layer-row__tag">
        <input type="checkbox" value="${tag.value}" ${suggestedTags.includes(tag.value) ? 'checked' : ''}>
        <span>${tag.label}</span>
      </label>
    `).join('');

  row.innerHTML = `
    <input type="checkbox" class="layer-row__checkbox" ${isAutoSelected ? 'checked' : ''}
           aria-label="Select layer ${escapeHtml(layer.layer_name)}">
    <div class="layer-row__info">
      <div class="layer-row__name">${escapeHtml(layer.layer_name)}</div>
    </div>
    <span class="layer-row__count" title="Placemark count">${placemarkCount}</span>
    <div class="layer-row__review-controls">
      <select class="layer-row__type-select" aria-label="Task family">
        <option value="ic" ${layer.inferred_family === 'ic' ? 'selected' : ''}>IC</option>
        <option value="od" ${layer.inferred_family === 'od' ? 'selected' : ''}>OD</option>
        <option value="ipod" ${layer.inferred_family === 'ipod' ? 'selected' : ''}>IP OD</option>
        <option value="hik" ${layer.inferred_family === 'hik' ? 'selected' : ''}>HIK</option>
        <option value="atc" ${layer.inferred_family === 'atc' ? 'selected' : ''}>ATC</option>
        <option value="site" ${layer.inferred_family === 'site' ? 'selected' : ''}>Site</option>
        <option value="unknown" ${!supportedFamilies.includes(layer.inferred_family) ? 'selected' : ''}>Other</option>
      </select>
      <div class="layer-row__tags" aria-label="Allowed data tags">
        ${tagControls}
      </div>
    </div>
  `;

  // Toggle selected class on checkbox change
  const checkbox = row.querySelector('.layer-row__checkbox');
  checkbox.addEventListener('change', () => {
    row.classList.toggle('layer-row--selected', checkbox.checked);
  });

  // Update data-family on type change
  const select = row.querySelector('.layer-row__type-select');
  select.addEventListener('change', () => {
    row.dataset.family = select.value;
    // Auto-check the row when a type is selected
    if (!checkbox.checked) {
      checkbox.checked = true;
      row.classList.add('layer-row--selected');
    }
  });

  row.querySelectorAll('.layer-row__tag input').forEach((tagInput) => {
    tagInput.addEventListener('change', () => {
      if (!checkbox.checked) {
        checkbox.checked = true;
        row.classList.add('layer-row--selected');
      }
    });
  });

  return row;
}

function inferReviewTags(layer) {
  const tags = new Set();
  const roleCounts = layer.business_role_counts || {};
  const geometryTypes = new Set(layer.geometry_types || []);

  if (roleCounts.site) tags.add('site');
  if (roleCounts.camera_location) tags.add('camera');
  if (roleCounts.camera_direction) tags.add('direction');
  if (roleCounts.ignored_historical_polygon || geometryTypes.has('Polygon')) {
    tags.add('polygon');
  }
  if (roleCounts.route) tags.add('route');
  if (roleCounts.atc_location || roleCounts.atc_tube_line || layer.inferred_family === 'atc') {
    tags.add('atc');
  }

  if (tags.size === 0 && geometryTypes.has('LineString')) {
    tags.add('direction');
  }
  if (tags.size === 0 && geometryTypes.has('Point')) {
    tags.add('site');
  }
  return [...tags];
}

function formatBusinessRole(role) {
  const labels = {
    site: 'Site',
    camera_location: 'Camera',
    camera_direction: 'Direction',
    atc_location: 'ATC logger',
    atc_tube_line: 'ATC tube line',
    route: 'Route',
    reference_point: 'Unclear point',
    ignored_historical_polygon: 'Ignored polygon',
    unknown: 'Unknown',
  };
  return labels[role] || role;
}

/**
 * Handle confirm upload — collect selections and call confirm endpoint.
 */
async function handleConfirmUpload() {
  if (!currentPreviewId) {
    showToast('No preview to confirm.', 'error');
    return;
  }

  const rows = dom.layerReviewList.querySelectorAll('.layer-row');
  const selections = [];

  for (const row of rows) {
    const checkbox = row.querySelector('.layer-row__checkbox');
    if (!checkbox.checked) continue;

    const select = row.querySelector('.layer-row__type-select');
    const reviewTags = [...row.querySelectorAll('.layer-row__tag input:checked')]
      .map((input) => input.value);
    selections.push({
      file_id: row.dataset.fileId,
      filename: row.dataset.filename,
      layer_name: row.dataset.layerName,
      selected_family: select.value,
      review_tags: reviewTags,
    });
  }

  if (selections.length === 0) {
    showToast('No layers selected. Check at least one layer to import.', 'error');
    return;
  }

  try {
    setLoading(true);

    const stored = await chrome.storage.session.get('authState');
    const auth = stored.authState;
    if (!auth?.token) {
      throw new Error('Not authenticated. Please sign in first.');
    }

    const apiResponse = await fetch(`${CONFIG.API_BASE_URL}/api/v1/confirm-upload`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${auth.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        preview_id: currentPreviewId,
        selections: selections,
      }),
    });

    const data = await apiResponse.json().catch(() => null);
    if (!apiResponse.ok) {
      throw new Error(data?.detail || `Server error: ${apiResponse.status}`);
    }

    const msg = data.message || `Imported ${data.points_inserted} points`;
    showToast(msg, data.normalized_placemarks > 0 ? 'success' : 'error');
    await refreshManualReview(data.details || []);

  } catch (error) {
    console.error('[Popup] Confirm upload error:', error);
    showToast(error.message || 'Import failed. Please try again.', 'error');
  } finally {
    setLoading(false);
    hideLayerReview();
  }
}

/**
 * Handle cancel — hide the review panel without importing.
 */
function handleCancelUpload() {
  hideLayerReview();
  showToast('Upload cancelled.', 'error');
}

/**
 * Hide the layer review panel and reset state.
 */
function hideLayerReview() {
  dom.layerReview.hidden = true;
  dom.layerReviewList.innerHTML = '';
  currentPreviewId = null;
}

async function refreshManualReview(uploadDetails) {
  const uploadIds = uploadDetails
    .map((detail) => detail.raw_upload_id)
    .filter(Boolean);
  if (uploadIds.length === 0) {
    hideManualReview();
    return;
  }

  const stored = await chrome.storage.session.get('authState');
  const auth = stored.authState;
  if (!auth?.token) {
    hideManualReview();
    return;
  }

  const reviewResponses = [];
  for (const rawUploadId of uploadIds) {
    const response = await fetch(
      `${CONFIG.API_BASE_URL}/api/v1/review-items?raw_upload_id=${encodeURIComponent(rawUploadId)}`,
      { headers: { Authorization: `Bearer ${auth.token}` } }
    );
    if (!response.ok) continue;
    const reviewData = await response.json();
    if (reviewData.review_count > 0) {
      reviewResponses.push(reviewData);
    }
  }

  currentReviewData = reviewResponses;
  renderManualReview();
}

function renderManualReview() {
  dom.manualReviewActions.innerHTML = '';
  dom.manualReviewList.innerHTML = '';

  const totalItems = currentReviewData.reduce(
    (sum, upload) => sum + upload.review_count,
    0
  );
  if (totalItems === 0) {
    hideManualReview();
    return;
  }

  dom.manualReviewSubtitle.textContent =
    `${totalItems} item${totalItems === 1 ? '' : 's'} need review. Download the review KML to match IDs on the map.`;

  for (const upload of currentReviewData) {
    const button = document.createElement('button');
    button.className = 'btn btn-review-map';
    button.type = 'button';
    button.dataset.rawUploadId = upload.raw_upload_id;
    button.dataset.sourceFile = upload.source_file;
    button.textContent = `Download review KML: ${upload.source_file}`;
    dom.manualReviewActions.appendChild(button);

    const fileLabel = document.createElement('div');
    fileLabel.className = 'layer-review__file-name';
    fileLabel.textContent = upload.source_file;
    dom.manualReviewList.appendChild(fileLabel);

    for (const item of upload.items) {
      dom.manualReviewList.appendChild(buildReviewItem(item));
    }
  }

  dom.manualReview.hidden = false;
  dom.manualReview.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function buildReviewItem(item) {
  const row = document.createElement('div');
  row.className = 'review-item';
  row.dataset.reviewId = item.review_id;
  row.dataset.rawUploadId = item.raw_upload_id;
  row.dataset.cameraObservationId = item.camera_observation_id || '';

  const currentFamily = item.current_site_task_family || item.camera_task_family || 'unknown';
  const familyOptions = ['ic', 'od', 'ipod', 'hik', 'atc', 'unknown']
    .map((family) => (
      `<option value="${family}" ${family === currentFamily ? 'selected' : ''}>${family.toUpperCase()}</option>`
    ))
    .join('');
  const siteOptions = (item.candidate_sites || [])
    .map((site) => {
      const selected = site.is_recommended || site.site_observation_id === item.current_site_observation_id;
      const distance = site.distance_m == null ? '' : ` (${site.distance_m}m)`;
      return `<option value="${site.site_observation_id}" ${selected ? 'selected' : ''}>${site.task_family.toUpperCase()} | ${escapeHtml(site.name)}${distance}</option>`;
    })
    .join('');
  const relationshipOptions = ['primary', 'fallback', 'unassigned']
    .map((role) => (
      `<option value="${role}" ${role === item.suggested_relationship_role ? 'selected' : ''}>${formatRelationshipRole(role)}</option>`
    ))
    .join('');

  row.innerHTML = `
    <div class="review-item__top">
      <span class="review-item__id">${escapeHtml(item.review_id)}</span>
      <span class="review-item__name" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</span>
      <span class="review-item__type">${escapeHtml(formatReviewType(item.item_type))}</span>
    </div>
    <p class="review-item__reason">${escapeHtml(item.reason)}</p>
    <div class="review-item__controls">
      <select class="review-item__family" aria-label="Task family">${familyOptions}</select>
      <select class="review-item__site" aria-label="Candidate Site">${siteOptions}</select>
      <select class="review-item__role" aria-label="Relationship role">${relationshipOptions}</select>
      <label class="review-item__ignore">
        <input class="review-item__ignore-input" type="checkbox">
        <span>Ignore</span>
      </label>
    </div>
  `;
  return row;
}

async function handleReviewActionClick(event) {
  const button = event.target.closest('.btn-review-map');
  if (!button) return;
  await downloadReviewKml(button.dataset.rawUploadId, button.dataset.sourceFile);
}

async function downloadReviewKml(rawUploadId, sourceFile) {
  try {
    setLoading(true);
    const stored = await chrome.storage.session.get('authState');
    const auth = stored.authState;
    if (!auth?.token) {
      throw new Error('Not authenticated. Please sign in first.');
    }
    const response = await fetch(
      `${CONFIG.API_BASE_URL}/api/v1/review-kml?raw_upload_id=${encodeURIComponent(rawUploadId)}`,
      { headers: { Authorization: `Bearer ${auth.token}` } }
    );
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(data?.detail || `Server error: ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = reviewFilename(sourceFile);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast('Review KML downloaded.', 'success');
  } catch (error) {
    console.error('[Popup] Review KML download error:', error);
    showToast(error.message || 'Review KML download failed.', 'error');
  } finally {
    setLoading(false);
  }
}

async function handleSubmitReview() {
  const rows = [...dom.manualReviewList.querySelectorAll('.review-item')]
    .filter((row) => row.dataset.cameraObservationId);
  if (rows.length === 0) {
    showToast('No review items to submit.', 'error');
    return;
  }

  const grouped = new Map();
  for (const row of rows) {
    const rawUploadId = row.dataset.rawUploadId;
    const decision = {
      camera_observation_id: row.dataset.cameraObservationId,
      selected_site_observation_id: row.querySelector('.review-item__site')?.value || null,
      selected_task_family: row.querySelector('.review-item__family')?.value || 'unknown',
      relationship_role: row.querySelector('.review-item__role')?.value || 'primary',
      ignored: Boolean(row.querySelector('.review-item__ignore-input')?.checked),
    };
    if (!grouped.has(rawUploadId)) grouped.set(rawUploadId, []);
    grouped.get(rawUploadId).push(decision);
  }

  try {
    setLoading(true);
    const stored = await chrome.storage.session.get('authState');
    const auth = stored.authState;
    if (!auth?.token) {
      throw new Error('Not authenticated. Please sign in first.');
    }

    let saved = 0;
    let dismissed = 0;
    for (const [rawUploadId, decisions] of grouped.entries()) {
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/v1/submit-review`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${auth.token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          raw_upload_id: rawUploadId,
          decisions,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(data?.detail || `Server error: ${response.status}`);
      }
      saved += data.saved_count || 0;
      dismissed += data.dismissed_count || 0;
    }

    showToast(`Saved ${saved} review decision${saved === 1 ? '' : 's'} and dismissed ${dismissed}.`, 'success');
    await refreshManualReview(
      [...grouped.keys()].map((rawUploadId) => ({ raw_upload_id: rawUploadId }))
    );
  } catch (error) {
    console.error('[Popup] Submit review error:', error);
    showToast(error.message || 'Review submission failed.', 'error');
  } finally {
    setLoading(false);
  }
}

function hideManualReview() {
  dom.manualReview.hidden = true;
  dom.manualReviewActions.innerHTML = '';
  dom.manualReviewList.innerHTML = '';
  currentReviewData = [];
}

function formatReviewType(type) {
  if (type === 'camera_count_conflict') return 'Count';
  if (type === 'camera_site') return 'Camera';
  return type;
}

function formatRelationshipRole(role) {
  const labels = {
    primary: 'Primary',
    fallback: 'Fallback',
    unassigned: 'Unassigned',
  };
  return labels[role] || role;
}

function reviewFilename(sourceName) {
  const base = sourceName.replace(/\.(kml|kmz)$/i, '') || 'historical_upload';
  return `${base}_review_items.kml`;
}

/**
 * Escape HTML special characters for safe innerHTML insertion.
 * @param {string} text
 * @returns {string}
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/* ---------- Helpers ---------- */

/**
 * Parse JSON text when an error response is JSON.
 * @param {string} text
 * @returns {object | null}
 */
function parseJsonMaybe(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * Convert unknown thrown values into readable UI text.
 * @param {unknown} error
 * @param {string} fallback
 * @returns {string}
 */
function readableError(error, fallback) {
  if (error instanceof TypeError && /fetch/i.test(error.message)) {
    return 'Cannot reach the local server at 127.0.0.1:8000. Start the backend, then try again.';
  }
  if (error && typeof error === 'object' && 'message' in error) {
    return String(error.message || fallback);
  }
  return fallback;
}

/**
 * Build the download filename for a processed pinned map.
 * @param {string} sourceName
 * @returns {string}
 */
function processedFilename(sourceName) {
  const base = sourceName.replace(/\.(kml|kmz)$/i, '') || 'pinned_map';
  return `${base}_processed_camera_locations.kmz`;
}

/**
 * Shows a toast notification at the bottom of the popup.
 * @param {string} message
 * @param {'success' | 'error'} type
 */
function showToast(message, type) {
  dom.statusMessage.textContent = message;
  dom.statusMessage.className = `status-message status-message--${type}`;
  dom.statusMessage.hidden = false;

  // Clear any previous timeout
  if (dom._toastTimeout) {
    clearTimeout(dom._toastTimeout);
  }

  dom._toastTimeout = setTimeout(() => {
    dom.statusMessage.hidden = true;
  }, type === 'error' ? 12000 : 5000);
}

/**
 * Show or hide the loading overlay.
 * @param {boolean} isLoading
 */
function setLoading(isLoading) {
  dom.loadingOverlay.hidden = !isLoading;
}
