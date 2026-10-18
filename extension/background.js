/**
 * Smart Traffic Survey Assistant - Background Service Worker
 * Handles authentication and historical upload support.
 */
importScripts('config.js');

/* ---------- Message Handler ---------- */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      switch (message.type) {

        /* ---- GET_AUTH_TOKEN ---- */
        case 'GET_AUTH_TOKEN': {
          try {
            // DEV MODE: Bypass real Google Auth and return dummy credentials immediately
            console.log('[SurveyAssistant] Dev mode: Bypassing Google OAuth');
            
            const authState = { 
              token: 'dev-dummy-token-12345', 
              email: 'dev@company.com.au', 
              authorized: true 
            };

            await chrome.storage.session.set({ authState });
            sendResponse(authState);
          } catch (error) {
            console.error('[SurveyAssistant] Auth error:', error);
            sendResponse({ token: null, email: null, authorized: false, error: error.message });
          }
          break;
        }

        /* ---- SIGN_OUT ---- */
        case 'SIGN_OUT': {
          try {
            const data = await chrome.storage.session.get('authState');
            if (data.authState?.token) {
              await chrome.identity.removeCachedAuthToken({ token: data.authState.token });
            }
            await chrome.storage.session.remove('authState');
            sendResponse({ success: true });
          } catch (error) {
            console.error('[SurveyAssistant] Sign-out error:', error);
            sendResponse({ success: false, error: error.message });
          }
          break;
        }

        /* ---- UPLOAD_KML_BATCH ---- */
        case 'UPLOAD_KML_BATCH': {
          try {
            const authData = await chrome.storage.session.get('authState');
            const auth = authData.authState;

            if (!auth?.token) {
              throw new Error('Not authenticated. Please sign in first.');
            }

            const files = message.files || [];
            if (!files.length) {
              throw new Error('No file data provided.');
            }

            // Build multipart/form-data
            const formData = new FormData();
            for (const file of files) {
              const blob = new Blob([file.bytes], {
                type: file.type || 'application/vnd.google-earth.kml+xml'
              });
              formData.append('files', blob, file.name);
            }

            const response = await fetch(`${CONFIG.API_BASE_URL}/api/v1/upload-kml-batch`, {
              method: 'POST',
              headers: { Authorization: `Bearer ${auth.token}` },
              body: formData
            });

            if (!response.ok) {
              throw new Error(`Server error: ${response.status} ${response.statusText}`);
            }

            const result = await response.json();
            sendResponse({ success: true, data: result });
          } catch (error) {
            console.error('[SurveyAssistant] Upload error:', error);
            sendResponse({ success: false, error: error.message });
          }
          break;
        }

        default:
          sendResponse({ error: `Unknown message type: ${message.type}` });
      }
    } catch (error) {
      console.error('[SurveyAssistant] Unexpected error:', error);
      sendResponse({ error: error.message });
    }
  })();

  // Return true to keep the message channel open for async sendResponse
  return true;
});
