import React, { useState, useRef, useEffect } from 'react';
import { useAuth0 } from '@auth0/auth0-react';
import './App.css';

const API_URL = 'https://5e3ecqd7qkwygbyik3fmh5qq4u0qlhul.lambda-url.us-east-1.on.aws';

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

// Helper to extract refresh token from Auth0 SDK's localStorage cache
const getRefreshTokenFromCache = (): string | null => {
  try {
    // Auth0 SPA SDK stores tokens with a specific key pattern in localStorage
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith('@@auth0spajs@@')) {
        const data = localStorage.getItem(key);
        if (data) {
          const parsed = JSON.parse(data);
          // The cache entry has a body with refresh_token
          if (parsed?.body?.refresh_token) {
            return parsed.body.refresh_token;
          }
        }
      }
    }
  } catch (e) {
    console.error('Error reading refresh token from cache:', e);
  }
  return null;
};

function App() {
  const { isAuthenticated, isLoading, user, loginWithRedirect, logout, getAccessTokenSilently } = useAuth0();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Extract and store refresh token when authenticated
  useEffect(() => {
    if (isAuthenticated && !isLoading) {
      const token = getRefreshTokenFromCache();
      if (token) {
        console.log('Refresh token found in cache');
        setRefreshToken(token);
      } else {
        console.log('No refresh token found in cache - user may need to re-login');
      }
    }
  }, [isAuthenticated, isLoading]);

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

      // Build request body - include refresh token for Token Vault calendar access
      const requestBody: Record<string, string> = {
        message: userMessage
      };

      // Include refresh token for Token Vault (Google Calendar access via token exchange)
      if (refreshToken) {
        requestBody.refresh_token = refreshToken;
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
          {refreshToken ? (
            <span className="calendar-status connected" title="Calendar access available via Token Vault">
              Calendar Ready
            </span>
          ) : (
            <span className="calendar-status" title="Login with Google to enable calendar features">
              No Calendar Access
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
                    disabled={loading || (item.tier === 'calendar' && !refreshToken)}
                    title={item.tier === 'calendar' && !refreshToken ? 'Login with Google to enable calendar' : ''}
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
              {!refreshToken && (
                <div className="calendar-connect-prompt">
                  <p>Login with Google to enable calendar features via Token Vault.</p>
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
              {msg.role === 'assistant' && msg.topic && refreshToken && !loading && (
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
                disabled={loading || (item.tier === 'calendar' && !refreshToken)}
                title={item.tier === 'calendar' && !refreshToken ? 'Login with Google to enable calendar' : item.prompt}
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
