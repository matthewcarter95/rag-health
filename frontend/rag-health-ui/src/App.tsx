import React, { useState, useRef, useEffect } from 'react';
import { useAuth0 } from '@auth0/auth0-react';
import './App.css';

const API_URL = 'https://5e3ecqd7qkwygbyik3fmh5qq4u0qlhul.lambda-url.us-east-1.on.aws';
const AUTH0_DOMAIN = 'violet-hookworm-18506.cic-demo-platform.auth0app.com';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  tier?: string;
  intent?: string;
  topic?: string;  // Topic for booking consultations
}

// Sample prompts organized by content tier to demonstrate FGA access control
const SAMPLE_PROMPTS = [
  {
    tier: 'basic',
    label: 'Basic: Microbiome Intro',
    prompt: 'What is the gut microbiome and why is it important?',
    color: '#4CAF50',
  },
  {
    tier: 'premium',
    label: 'Premium: Advanced Probiotics',
    prompt: 'What are the clinical benefits of specific probiotic strains for IBS?',
    color: '#2196F3',
  },
  {
    tier: 'premium',
    label: 'Premium: Gut-Brain Axis',
    prompt: 'How does the gut-brain axis affect mental health and what interventions help?',
    color: '#2196F3',
  },
  {
    tier: 'researcher',
    label: 'Researcher: Clinical Studies',
    prompt: 'What do recent clinical trials say about fecal microbiota transplantation?',
    color: '#9C27B0',
  },
  {
    tier: 'calendar',
    label: 'Calendar: View Events',
    prompt: 'What appointments do I have on my calendar?',
    color: '#FF9800',
  },
];

function App() {
  const { isAuthenticated, isLoading, user, loginWithRedirect, logout, getAccessTokenSilently, getAccessTokenWithPopup } = useAuth0();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [myAccountToken, setMyAccountToken] = useState<string | null>(null);
  const [googleConnected, setGoogleConnected] = useState(false);
  const [calendarConnecting, setCalendarConnecting] = useState(false);
  const [pendingConnection, setPendingConnection] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Store connect_code from URL immediately (before auth check)
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const connectCode = urlParams.get('connect_code');
    if (connectCode) {
      console.log('Found connect_code in URL, storing for later processing');
      sessionStorage.setItem('pending_connect_code', connectCode);
      // Clear URL immediately to avoid reprocessing
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }, []);

  // Check for pending connection after auth completes
  useEffect(() => {
    if (isLoading || !isAuthenticated) return;

    const connectCode = sessionStorage.getItem('pending_connect_code');
    const authSession = sessionStorage.getItem('auth0_connect_session');

    if (connectCode && authSession) {
      console.log('Found pending connection, showing completion button');
      setPendingConnection(true);
    }
  }, [isLoading, isAuthenticated]);

  // Complete the Google connection (called by button click)
  const completeGoogleConnection = async () => {
    const connectCode = sessionStorage.getItem('pending_connect_code');
    const authSession = sessionStorage.getItem('auth0_connect_session');

    if (!connectCode || !authSession) {
      console.error('Missing connect_code or auth_session');
      return;
    }

    setCalendarConnecting(true);
    console.log('Completing Google connection...');

    try {
      // Get MyAccount token via popup (user initiated, so popup should work)
      const token = await getAccessTokenWithPopup({
        authorizationParams: {
          audience: `https://${AUTH0_DOMAIN}/me/`,
          scope: 'openid profile email read:me:connected_accounts create:me:connected_accounts'
        }
      });

      if (!token) {
        console.error('Failed to get token for completing connection');
        return;
      }

      // Complete the connection
      const completeResponse = await fetch(`https://${AUTH0_DOMAIN}/me/v1/connected-accounts/complete`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          auth_session: authSession,
          connect_code: connectCode,
          redirect_uri: window.location.origin,
        }),
      });

      // Clear stored values regardless of outcome
      sessionStorage.removeItem('pending_connect_code');
      sessionStorage.removeItem('auth0_connect_session');
      setPendingConnection(false);

      if (completeResponse.ok) {
        const result = await completeResponse.json();
        console.log('Connection completed successfully:', result);
        setMyAccountToken(token);
        setGoogleConnected(true);
      } else {
        const errorData = await completeResponse.json();
        console.error('Failed to complete connection:', errorData);
        alert(`Failed to complete Google connection: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (error: any) {
      console.error('Error completing connection:', error);
      if (error?.message?.includes('popup')) {
        alert('Please allow popups for this site to complete the Google connection.');
      }
    } finally {
      setCalendarConnecting(false);
    }
  };

  // Check if Google is connected when we have a MyAccount token
  useEffect(() => {
    const checkGoogleConnection = async () => {
      if (!myAccountToken) return;

      try {
        const response = await fetch(`https://${AUTH0_DOMAIN}/me/v1/connected-accounts/accounts`, {
          headers: {
            'Authorization': `Bearer ${myAccountToken}`,
          },
        });

        if (response.ok) {
          const data = await response.json();
          const accounts = data.accounts || data.connected_accounts || data || [];
          const hasGoogle = accounts.some((acc: any) =>
            acc.connection?.toLowerCase().includes('google') ||
            acc.provider?.toLowerCase().includes('google')
          );
          setGoogleConnected(hasGoogle);
          console.log('Google connected status:', hasGoogle, 'Accounts:', accounts);
        }
      } catch (error) {
        console.error('Failed to check Google connection:', error);
      }
    };

    checkGoogleConnection();
  }, [myAccountToken]);

  // Connect calendar via popup for MyAccount audience, then initiate Google connection
  const connectCalendar = async () => {
    setCalendarConnecting(true);
    try {
      // Use popup to get token for MyAccount API without redirecting
      // Need both read and create scopes for Connected Accounts API
      const token = await getAccessTokenWithPopup({
        authorizationParams: {
          audience: `https://${AUTH0_DOMAIN}/me/`,
          scope: 'openid profile email read:me:connected_accounts create:me:connected_accounts'
        }
      });

      if (!token) {
        console.error('No token received from popup');
        return;
      }

      setMyAccountToken(token);
      console.log('MyAccount token obtained, initiating Google connection...');

      // Now initiate the Google connection request
      const connectResponse = await fetch(`https://${AUTH0_DOMAIN}/me/v1/connected-accounts/connect`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          connection: 'google-oauth2',
          redirect_uri: window.location.origin,
          scopes: ['openid', 'email', 'profile', 'https://www.googleapis.com/auth/calendar'],
        }),
      });

      if (!connectResponse.ok) {
        const errorData = await connectResponse.json();
        console.error('Connect request failed:', errorData);
        // If already connected or feature not enabled, show appropriate message
        if (connectResponse.status === 404) {
          alert('Connected Accounts feature is not enabled. Please activate MyAccount API in Auth0 Dashboard under Authentication > APIs.');
        } else {
          alert(`Failed to initiate Google connection: ${errorData.detail || errorData.message || 'Unknown error'}`);
        }
        return;
      }

      const connectData = await connectResponse.json();
      console.log('Connect response:', connectData);

      // Store auth_session for completing the connection after redirect
      if (connectData.auth_session) {
        sessionStorage.setItem('auth0_connect_session', connectData.auth_session);
      }

      // Redirect to Google OAuth
      if (connectData.connect_uri) {
        const connectUrl = new URL(connectData.connect_uri);
        if (connectData.connect_params?.ticket) {
          connectUrl.searchParams.set('ticket', connectData.connect_params.ticket);
        }
        window.location.href = connectUrl.toString();
      }
    } catch (error: any) {
      console.error('Failed to connect calendar:', error?.message || error);
      if (error?.error === 'popup_closed_by_user') {
        console.log('User closed the popup');
      }
    } finally {
      setCalendarConnecting(false);
    }
  };

  const sendMessage = async (messageOverride?: string) => {
    const userMessage = messageOverride || input.trim();
    if (!userMessage || loading) return;

    if (!messageOverride) {
      setInput('');
    }
    const topic = extractTopic(userMessage);
    setMessages(prev => [...prev, { role: 'user', content: userMessage, topic }]);
    setLoading(true);

    try {
      // Get API token (for authorization)
      const apiToken = await getAccessTokenSilently({
        authorizationParams: {
          audience: 'https://api.rag-health.example.com',
          scope: 'openid profile email read:content read:calendar write:calendar'
        }
      });

      // Build request body - include MyAccount token for calendar operations
      const requestBody: Record<string, string> = {
        message: userMessage
      };

      // Include MyAccount token if available (needed for calendar features)
      if (myAccountToken) {
        requestBody.myaccount_token = myAccountToken;
      }

      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${apiToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
      });

      const data = await response.json();

      if (data.error) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `Error: ${data.error}`
        }]);
      } else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.answer,
          tier: data.user_tier,
          intent: data.intent,
          topic: data.intent?.startsWith('calendar') ? undefined : topic  // Only set topic for health queries
        }]);
      }
    } catch (error) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Error: ${error instanceof Error ? error.message : 'Unknown error'}`
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleSamplePrompt = (prompt: string) => {
    sendMessage(prompt);
  };

  // Extract topic from user query for specialist booking
  const extractTopic = (query: string): string => {
    const lowerQuery = query.toLowerCase();
    if (lowerQuery.includes('microbiome') || lowerQuery.includes('gut')) return 'gut microbiome';
    if (lowerQuery.includes('probiotic')) return 'probiotics';
    if (lowerQuery.includes('ibs') || lowerQuery.includes('irritable bowel')) return 'IBS treatment';
    if (lowerQuery.includes('brain') || lowerQuery.includes('mental')) return 'gut-brain axis';
    if (lowerQuery.includes('fecal') || lowerQuery.includes('fmt') || lowerQuery.includes('transplant')) return 'fecal microbiota transplantation';
    if (lowerQuery.includes('nutrition') || lowerQuery.includes('diet')) return 'nutrition';
    if (lowerQuery.includes('digestive') || lowerQuery.includes('digestion')) return 'digestive health';
    return 'gut health';
  };

  const handleBookConsultation = (topic: string) => {
    const message = `Schedule a 30-minute consultation with a ${topic} specialist for next week`;
    sendMessage(message);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  if (isLoading) {
    return (
      <div className="app">
        <div className="loading-container">
          <div className="spinner"></div>
          <p>Loading...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="app">
        <div className="login-container">
          <div className="login-card">
            <h1>RAG Health</h1>
            <p>Your AI-powered gut health assistant</p>
            <p className="subtitle">Get personalized answers about microbiome, probiotics, nutrition, and digestive health.</p>
            <button className="login-button" onClick={() => loginWithRedirect()}>
              Log In to Continue
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <h1>RAG Health</h1>
        </div>
        <div className="header-right">
          <span className="user-info">
            {user?.picture && <img src={user.picture} alt="avatar" className="avatar" />}
            {user?.name || user?.email}
          </span>
          {myAccountToken && googleConnected ? (
            <span className="calendar-status connected" title="Google Calendar connected">
              Google Calendar Connected
            </span>
          ) : pendingConnection ? (
            <button
              className="connect-calendar-btn"
              onClick={completeGoogleConnection}
              disabled={calendarConnecting}
              title="Complete your Google Calendar connection"
              style={{ backgroundColor: '#4ade8033', borderColor: '#4ade80' }}
            >
              {calendarConnecting ? 'Completing...' : 'Complete Google Connection'}
            </button>
          ) : (
            <button
              className="connect-calendar-btn"
              onClick={connectCalendar}
              disabled={calendarConnecting}
              title="Connect your Google Calendar to enable scheduling features"
            >
              {calendarConnecting ? 'Connecting...' : 'Connect Calendar'}
            </button>
          )}
          <button className="logout-button" onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}>
            Logout
          </button>
        </div>
      </header>

      <main className="chat-container">
        <div className="messages">
          {messages.length === 0 && (
            <div className="welcome-message">
              <h2>Welcome to RAG Health!</h2>
              <p>Ask me anything about gut health, or check your calendar. Try these sample prompts to see FGA access control in action:</p>
              <div className="sample-prompts">
                {SAMPLE_PROMPTS.map((item, idx) => (
                  <button
                    key={idx}
                    className="sample-prompt-btn"
                    style={{ borderColor: item.color, color: item.color }}
                    onClick={() => handleSamplePrompt(item.prompt)}
                    disabled={loading || (item.tier === 'calendar' && !myAccountToken)}
                    title={item.tier === 'calendar' && !myAccountToken ? 'Connect Google account to enable' : ''}
                  >
                    <span className="prompt-tier" style={{ backgroundColor: item.color }}>
                      {item.tier}
                    </span>
                    {item.label.split(': ')[1]}
                  </button>
                ))}
              </div>
              <p className="access-note">
                Your access level determines which content you can retrieve.
                Basic users see basic content, Premium users see basic + premium,
                and Researchers see all content including clinical studies.
              </p>
              {!myAccountToken && (
                <div className="calendar-connect-prompt">
                  <p>Want to schedule appointments with specialists?</p>
                  <button
                    className="connect-calendar-btn large"
                    onClick={connectCalendar}
                    disabled={calendarConnecting}
                  >
                    {calendarConnecting ? 'Connecting...' : 'Connect Google Calendar'}
                  </button>
                </div>
              )}
            </div>
          )}
          {messages.map((msg, idx) => (
            <div key={idx} className={`message ${msg.role}`}>
              <div className="message-content">
                {msg.content}
                {msg.tier && <span className="tier-badge">{msg.tier}</span>}
                {msg.intent && msg.intent.startsWith('calendar') && (
                  <span className="intent-badge calendar">{msg.intent.replace('_', ' ')}</span>
                )}
              </div>
              {msg.role === 'assistant' && msg.topic && myAccountToken && !loading && (
                <button
                  className="book-consultation-btn"
                  onClick={() => handleBookConsultation(msg.topic!)}
                  title={`Book a consultation with a ${msg.topic} specialist`}
                >
                  <span className="calendar-icon">+</span>
                  Book Specialist Consultation
                </button>
              )}
            </div>
          ))}
          {loading && (
            <div className="message assistant">
              <div className="message-content loading">
                <span className="dot"></span>
                <span className="dot"></span>
                <span className="dot"></span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="input-area">
          <div className="quick-prompts">
            {SAMPLE_PROMPTS.map((item, idx) => (
              <button
                key={idx}
                className="quick-prompt-btn"
                style={{ borderColor: item.color, color: item.color }}
                onClick={() => handleSamplePrompt(item.prompt)}
                disabled={loading || (item.tier === 'calendar' && !myAccountToken)}
                title={item.tier === 'calendar' && !myAccountToken ? 'Connect Google account to enable' : item.prompt}
              >
                <span className="prompt-tier-small" style={{ backgroundColor: item.color }}>
                  {item.tier}
                </span>
                {item.label.split(': ')[1]}
              </button>
            ))}
          </div>
          <div className="input-container">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Ask about gut health or your calendar..."
              disabled={loading}
              rows={1}
            />
            <button onClick={() => sendMessage()} disabled={loading || !input.trim()}>
              Send
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
