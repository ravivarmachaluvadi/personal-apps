// Personal Apps — configuration. This file is public (it is served with the pages); a Google OAuth
// client ID is not a secret, it only works from the origins you authorised in Google Cloud Console.
window.APP_CONFIG = {
  // Paste the "Web application" OAuth client ID from Google Cloud Console → APIs & Services → Credentials.
  // Leave empty until then: the pages still work, saving only in the browser.
  googleClientId: '',

  // Folder in My Drive that holds one JSON file per app (created on first save).
  driveFolderName: 'Personal Apps'
};
