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
  topic?: string;
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
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Get MyAccount token on load and check Google connection
  useEffect(() => {
    if (!isAuthenticated || isLoading) return;

    const getMyAccountToken = async () => {
      let token: string | null = null;

      // Try silent token first, fall back to popup if needed
      try {
        token = await getAccessTokenSilently({
          authorizationParams: {
            audience: `https://${AUTH0_DOMAIN}/me/`,
            scope: 'openid profile email read:me:connected_accounts'
          }
        });
        console.log('MyAccount token obtained silently');
      } catch (silentError) {
        console.log('Silent token failed, will try popup on user action:', silentError);
        // Don't block - user can still use the app, calendar will prompt if needed
      }

      if (token) {
        setMyAccountToken(token);

        // Check if Google is connected via Connected Accounts API
        try {
          const response = await fetch(`https://${AUTH0_DOMAIN}/me/v1/connected-accounts/accounts`, {
            headers: { 'Authorization': `Bearer ${token}` }
          });

          console.log('Connected accounts response status:', response.status);

          if (response.ok) {
            const data = await response.json();
            const accounts = Array.isArray(data) ? data : (data.accounts || []);
            console.log('Connected accounts:', accounts);

            const googleAccount = accounts.find((acc: any) =>
              acc.connection?.toLowerCase().includes('google') ||
              acc.provider?.toLowerCase().includes('google')
            );

            if (googleAccount) {
              console.log('Google account found:', googleAccount);
              setGoogleConnected(true);

              // Test the token endpoint
              if (googleAccount.id) {
                console.log('Testing token endpoint for account:', googleAccount.id);
                const tokenResp = await fetch(
                  `https://${AUTH0_DOMAIN}/me/v1/connected-accounts/accounts/${googleAccount.id}/token`,
                  { headers: { 'Authorization': `Bearer ${token}` } }
                );
                console.log('Token endpoint status:', tokenResp.status);
                const tokenData = await tokenResp.json();
                console.log('Token endpoint response:', tokenData);
              }
            } else {
              console.log('No Google account in connected accounts, but user logged in with Google');
              setGoogleConnected(true);
            }
          } else {
            console.log('Connected accounts API not available');
            setGoogleConnected(true);
          }
        } catch (apiError) {
          console.error('Connected accounts API error:', apiError);
          setGoogleConnected(true);
        }
      } else {
        // No MyAccount token, but user logged in with Google so assume calendar is available
        console.log('No MyAccount token, but user authenticated with Google');
        setGoogleConnected(true);
      }
    };

    getMyAccountToken();
  }, [isAuthenticated, isLoading, getAccessTokenSilently]);

  // Function to get MyAccount token via popup (for calendar operations)
  const ensureMyAccountToken = async (): Promise<string | null> => {
    if (myAccountToken) return myAccountToken;

    try {
      const token = await getAccessTokenWithPopup({
        authorizationParams: {
          audience: `https://${AUTH0_DOMAIN}/me/`,
          scope: 'openid profile email read:me:connected_accounts'
        }
      });
      if (token) {
        setMyAccountToken(token);
        return token;
      }
    } catch (error) {
      console.error('Failed to get MyAccount token via popup:', error);
    }
    return null;
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
      const apiToken = await getAccessTokenSilently({
        authorizationParams: {
          audience: 'https://api.rag-health.example.com',
          scope: 'openid profile email read:content read:calendar write:calendar'
        }
      });

      const requestBody: Record<string, string> = {
        message: userMessage
      };

      // For calendar requests, ensure we have the MyAccount token
      const isCalendarRequest = ['calendar', 'schedule', 'appointment', 'event', 'meeting'].some(
        keyword => userMessage.toLowerCase().includes(keyword)
      );

      let tokenToSend = myAccountToken;
      if (isCalendarRequest && !tokenToSend) {
        tokenToSend = await ensureMyAccountToken();
      }

      if (tokenToSend) {
        requestBody.myaccount_token = tokenToSend;
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
          topic: data.intent?.startsWith('calendar') ? undefined : topic
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
          {googleConnected ? (
            <span className="calendar-status connected" title="Google Calendar available">
              Calendar Ready
            </span>
          ) : (
            <span className="calendar-status" title="Calendar not available">
              No Calendar
            </span>
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
                    disabled={loading || (item.tier === 'calendar' && !googleConnected)}
                    title={item.tier === 'calendar' && !googleConnected ? 'Connect Google Calendar first' : ''}
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
              {!googleConnected && (
                <div className="calendar-connect-prompt">
                  <p>Log in with Google to enable calendar features.</p>
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
              {msg.role === 'assistant' && msg.topic && googleConnected && !loading && (
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
                disabled={loading || (item.tier === 'calendar' && !googleConnected)}
                title={item.tier === 'calendar' && !googleConnected ? 'Connect Google Calendar first' : item.prompt}
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
