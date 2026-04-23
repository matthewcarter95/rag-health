import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

// BFF Pattern: No Auth0Provider needed
// Authentication is handled via HTTP-only cookies and backend sessions
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
