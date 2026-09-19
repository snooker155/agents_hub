/**
 * Theme context and its hook, split out so `ThemeContext.jsx` exports only the
 * provider component (see `stream.js` for the same split).
 */
import { createContext, useContext } from 'react';

export const ThemeContext = createContext();

export const useTheme = () => useContext(ThemeContext);
