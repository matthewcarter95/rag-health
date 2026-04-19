/**
 * Auth0 Action: Enrich User Context
 *
 * Adds custom claims to the access token for FGA authorization:
 * - subscription_tier: basic/premium
 * - roles: array of user roles
 * - fga_user_id: user identifier for FGA tuple matching
 *
 * Trigger: Login / Post Login
 */

exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://rag-health.example.com';

  // Get user metadata (set via Management API or user profile)
  const userMetadata = event.user.user_metadata || {};
  const appMetadata = event.user.app_metadata || {};

  // Determine subscription tier (default to basic)
  const subscriptionTier = appMetadata.subscription_tier || 'basic';

  // Get user roles from app metadata or Auth0 roles
  const roles = appMetadata.roles || [];

  // Add custom claims to access token
  api.accessToken.setCustomClaim(`${namespace}/subscription_tier`, subscriptionTier);
  api.accessToken.setCustomClaim(`${namespace}/roles`, roles);
  api.accessToken.setCustomClaim(`${namespace}/fga_user_id`, event.user.user_id);

  // Add claims to ID token for frontend use
  api.idToken.setCustomClaim(`${namespace}/subscription_tier`, subscriptionTier);
  api.idToken.setCustomClaim(`${namespace}/roles`, roles);
};
