// Feature 6: the read-only structure of one model (dashboard/backend/routes/
// model_structure.py). A local model answers with `kind: "structure"`, parsed
// from its GGUF or safetensors header or from Ollama's show output; an API
// model answers with `kind: "card"`, the catalogue facts the hub keeps on it.
import api from './index';

export const getModelStructure = (provider, model) =>
  api.get('/models/structure', { params: { provider, model } });
