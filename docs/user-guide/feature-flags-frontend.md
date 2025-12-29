# Feature Flags Frontend Integration

Feature Flags are a powerful way to control application behavior in the frontend without requiring a new deployment. This guide explains how to integrate the backend Feature Flags API with your frontend application.

## API Endpoint

The primary endpoint for retrieving feature flags is:
`GET /api/v1/feature-flags`

This endpoint returns all active flags for the current tenant associated with the authenticated user.

### Response format

```json
{
  "flags": {
    "dark_mode": true,
    "beta_features": false,
    "max_projects": 5
  }
}
```

## Integration Strategy

### 1. Global State / Context

The recommended approach is to fetch the feature flags during app initialization and store them in a global state (e.g., React Context, Vuex/Pinia, or Redux).

#### React Example

```tsx
// FeatureFlagContext.tsx
import React, { createContext, useContext, useEffect, useState } from 'react';

const FeatureFlagContext = createContext<Record<string, any>>({});

export const FeatureFlagProvider = ({ children }) => {
  const [flags, setFlags] = useState({});

  useEffect(() => {
    fetch('/api/v1/feature-flags')
      .then(res => res.json())
      .then(data => setFlags(data.flags));
  }, []);

  return (
    <FeatureFlagContext.Provider value={flags}>
      {children}
    </FeatureFlagContext.Provider>
  );
};

export const useFeatureFlag = (name: string, defaultValue: any = false) => {
  const flags = useContext(FeatureFlagContext);
  return flags[name] ?? defaultValue;
};
```

### 2. Guarding Components

Use the `useFeatureFlag` hook to conditionally render components.

```tsx
const AnalyticsDashboard = () => {
  const isEnabled = useFeatureFlag('advanced_analytics');

  if (!isEnabled) {
    return <UpgradeBanner message="Advanced analytics is available on the Pro plan." />;
  }

  return <div>{/* Analytics charts */}</div>;
};
```

### 3. Guarding Routes

If you use a router (like React Router or Vue Router), you can create a protected route component.

```tsx
const FeatureGate = ({ feature, children }) => {
  const isEnabled = useFeatureFlag(feature);
  
  if (!isEnabled) {
    return <Navigate to="/dashboard" replace />;
  }
  
  return children;
};

// Usage
<Route 
  path="/beta-feature" 
  element={
    <FeatureGate feature="beta_features">
      <BetaComponent />
    </FeatureGate>
  } 
/>
```

## Best Practices

1. **Default Values**: Always provide sensible default values in case the API is unreachable or the flag is missing.
2. **Caching**: Consider caching the flags in `localStorage` or `sessionStorage` to avoid flicker during page reloads, but ensure you re-validate them frequently.
3. **Sensitive Logic**: Remember that frontend checks are for UX purposes. **Always enforce feature access on the backend** as well, as frontend checks can be bypassed by knowledgeable users.
4. **Cleanup**: Periodically review your code and remove flags (and the corresponding `if` statements) once a feature is permanently rolled out.
