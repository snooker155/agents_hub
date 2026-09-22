/**
 * Optional-feature flags from `GET /api/health` (`features: {...}`), read once
 * at app start. Kept apart from `FeaturesContext.jsx` so that file exports
 * nothing but the provider component and Fast Refresh can hot-swap it (the
 * same split as `stream.js` / `StreamContext.jsx`).
 *
 * A feature is *shown* unless the backend says otherwise: an old backend that
 * does not report `features` at all, or a health call that never answers, must
 * not make pages disappear.
 */
import { createContext, useContext } from 'react';

export const DEFAULT_FEATURES = { playground: true };

export const FeaturesContext = createContext(DEFAULT_FEATURES);

export const useFeatures = () => useContext(FeaturesContext) || DEFAULT_FEATURES;
