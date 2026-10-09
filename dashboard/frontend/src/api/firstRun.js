/**
 * The first run (common/first_run.py, docs/installation.md, "The first run in the browser"): the install's own
 * setup, once, before the rest of the app opens. Only outside multi mode.
 */
import api from './index';

/** { applies, required, step, language, theme, completed_at } */
export const getFirstRun = () => api.get('/first-run');

/** Save the step (and the language or look), finish, or start again. */
export const firstRunAction = (action, { step, language, theme } = {}) =>
  api.post('/first-run', {
    action,
    ...(step ? { step } : {}),
    ...(language ? { language } : {}),
    ...(theme ? { theme } : {}),
  });

/** What is already set: providers, the default model, runtime, search, demo, voice. */
export const getFirstRunContext = () => api.get('/first-run/context');

/** Each connected provider's model tiers and the voices; asks the providers. */
export const getFirstRunOptions = () => api.get('/first-run/options');

/** One setup operation: choose_model, voice_cloud, voice_local or seed_demo. */
export const firstRunOp = (operation, args = {}) => api.post('/first-run/op', { operation, args });
