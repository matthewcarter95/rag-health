import React from 'react';
import ReactDOM from 'react-dom/client';
import { Auth0Provider } from '@auth0/auth0-react';
import App from './App';
import './index.css';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <Auth0Provider
      domain="violet-hookworm-18506.cic-demo-platform.auth0app.com"
      clientId="iEl6LY0JlFQMvjAEAVy8ZqAT3g7ogPjW"
      authorizationParams={{
        redirect_uri: window.location.origin,
        audience: "https://api.rag-health.example.com",
        scope: "openid profile email read:content read:calendar write:calendar read:profile offline_access"
      }}
      useRefreshTokens={true}
      cacheLocation="localstorage"
    >
      <App />
    </Auth0Provider>
  </React.StrictMode>
);
