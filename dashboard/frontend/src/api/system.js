/**
 * Self diagnostics (doctor): a structured self check, distinct from the plain
 * up/down snapshot at GET /api/health. Each entry carries its own status, a
 * human summary, an optional detail map, and a doc anchor to read more.
 */
import api from './index';

export const getDoctor = () => api.get('/health/doctor');
