/**
 * The first run (common/first_run.py, docs/installation.md, "The first run in the browser"): the install's own
 * setup, once, before the rest of the app opens.
 */
import api from './index';

/** { required, step, language, theme, completed_at } */
export const getFirstRun = () => api.get('/first-run');

/** Save the step (and the language or look), finish, or start again. */
export const firstRunAction = (action, { step, language, theme } = {}) =>
  api.post('/first-run', {
    action,
    ...(step ? { step } : {}),
    ...(language ? { language } : {}),
    ...(theme ? { theme } : {}),
  });

/** What is already set: providers, the default model, search, demo. */
export const getFirstRunContext = () => api.get('/first-run/context');

/** Each connected provider's model tiers; asks the providers. */
export const getFirstRunOptions = () => api.get('/first-run/options');

/** One setup operation: choose_model or seed_demo. */
export const firstRunOp = (operation, args = {}) => api.post('/first-run/op', { operation, args });

/** Connect the first provider: checked with the provider before it is saved. */
export const connectFirstModel = ({ provider, api_key, base_url }) =>
  api.post('/first-run/model', { provider, api_key, base_url });
