/**
 * The guided setup (common/setup_guide.py, docs/assistant.md "Guided setup"):
 * the step-by-step progress the assistant leads a person through after
 * install, and the one call that connects the hub's first model from the
 * welcome window (routes/setup_guide.py).
 */
import api from './index';

/** The person's guide: every step, what is next, and any work in progress. */
export const getSetupGuide = ({ tourDone } = {}) =>
  api.get('/setup-guide', { params: tourDone === undefined ? {} : { tour_done: tourDone } });

/** Start, skip, mark or end the guide; returns the guide, same shape as above. */
export const setupGuideAction = (action, { step, mode, tourDone } = {}) =>
  api.post('/setup-guide', {
    action,
    ...(step ? { step } : {}),
    ...(mode ? { mode } : {}),
    ...(tourDone === undefined ? {} : { tour_done: tourDone }),
  });

/** The welcome window's own step: connect a provider, no card and no agent involved. */
export const connectFirstModel = ({ provider, api_key, base_url }) =>
  api.post('/setup-guide/model', { provider, api_key, base_url });
