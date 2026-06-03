import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './index.css';
import './shared/i18n';
import { initWebAPI } from './lib/api-adapter';
import { initializeGitHubListeners } from './stores/github';

// Initialize web API adapter (replaces window.API)
initWebAPI();

// Initialize global GitHub event listeners (PR review progress/complete/error)
// Must be called after initWebAPI() so window.API is available
initializeGitHubListeners();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
