  // First get a MyAccount token                                                                                          
  const auth0 = await window.__auth0Client__;                                                                             
                                                                                                                          
  // Get MyAccount token                                                                                                  
  const myAccountToken = await auth0.getAccessTokenSilently({                                                             
    authorizationParams: {                                                                                                
      audience: 'https://violet-hookworm-18506.cic-demo-platform.auth0app.com/me/',                                       
      scope: 'openid profile email read:me:connected_accounts'                                                            
    }                                                                                                                     
  });                                                                                                                     
                                                                                                                          
  // List connected accounts                                                                                              
  const accountsResp = await                                                                                              
  fetch('https://violet-hookworm-18506.cic-demo-platform.auth0app.com/me/v1/connected-accounts/accounts', {               
    headers: { 'Authorization': `Bearer ${myAccountToken}` }                                                              
  });                                                                                                                     
  const accounts = await accountsResp.json();                                                                             
  console.log('Accounts:', accounts);                                                                                     
                                                                                                                          
  // If Google account exists, try to get its token                                                                       
  const googleAccount = accounts.find(a => a.connection?.includes('google'));                                             
  if (googleAccount) {                                                                                                    
    const tokenResp = await fetch(`https://violet-hookworm-18506.cic-demo-platform.auth0app.com/me/v1/connected-accounts/a
  ccounts/${googleAccount.id}/token`, {                                                                                   
      headers: { 'Authorization': `Bearer ${myAccountToken}` }                                                            
    });                                                                                                                   
    const tokenData = await tokenResp.json();                                                                             
    console.log('Token response:', tokenData);                                                                            
  }       
