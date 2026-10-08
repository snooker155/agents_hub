/**
 * Models the Local tab offers in one click: a Hugging Face repo and either
 * the GGUF `file` to fetch from it (a chat model) or the `package` GET
 * /hf/files names in it (a speech model, see speech_packages in
 * deploy/models/app.py). Any other repo of the same shapes works too; these
 * are the ones checked to exist and run.
 */
export const MODEL_PRESETS = [
  { id: 'qwen25-05b', kind: 'chat', repo: 'Qwen/Qwen2.5-0.5B-Instruct-GGUF', file: 'qwen2.5-0.5b-instruct-q4_k_m.gguf', label: 'Qwen2.5 0.5B (0.5 GB)' },
  { id: 'qwen3-4b', kind: 'chat', repo: 'Qwen/Qwen3-4B-GGUF', file: 'Qwen3-4B-Q4_K_M.gguf', label: 'Qwen3 4B (2.5 GB)' },
  { id: 'gpt-oss-20b', kind: 'chat', repo: 'ggml-org/gpt-oss-20b-GGUF', file: 'gpt-oss-20b-MXFP4.gguf', label: 'gpt-oss 20B (12 GB)' },
  { id: 'whisper-small', kind: 'transcription', repo: 'Systran/faster-whisper-small', package: 'faster-whisper-small', label: 'Whisper small' },
  { id: 'whisper-turbo', kind: 'transcription', repo: 'deepdml/faster-whisper-large-v3-turbo-ct2', package: 'faster-whisper-large-v3-turbo-ct2', label: 'Whisper large-v3 turbo' },
  { id: 'piper-ru', kind: 'speech', repo: 'rhasspy/piper-voices', package: 'piper-ru_RU-irina-medium', label: 'Piper, Russian (Irina)' },
  { id: 'piper-en', kind: 'speech', repo: 'rhasspy/piper-voices', package: 'piper-en_US-lessac-medium', label: 'Piper, English (Lessac)' },
  { id: 'piper-de', kind: 'speech', repo: 'rhasspy/piper-voices', package: 'piper-de_DE-thorsten-medium', label: 'Piper, German (Thorsten)' },
  { id: 'kokoro', kind: 'speech', repo: 'fastrtc/kokoro-onnx', package: 'kokoro-v1.0', label: 'Kokoro 82M' },
  { id: 'supertonic', kind: 'speech', repo: 'Supertone/supertonic-3', package: 'supertonic-3', label: 'Supertonic 3, 31 languages (400 MB)' },
  { id: 'kitten', kind: 'speech', repo: 'KittenML/kitten-tts-nano-0.8-int8', package: 'kitten-tts-nano-0.8-int8', label: 'Kitten TTS nano, English (28 MB)' },
  // Speak in a recorded voice (the Recorded voices card).
  { id: 'chatterbox', kind: 'speech', repo: 'ResembleAI/chatterbox', package: 'chatterbox-multilingual', label: 'Chatterbox, your voice, 23 languages (3.2 GB)' },
  // `engine`: shown only where the runtime lists that engine (Apple silicon).
  { id: 'chatterbox-mlx', kind: 'speech', engine: 'chatterbox_mlx', repo: 'mlx-community/chatterbox-4bit', package: 'chatterbox-4bit-mlx', label: 'Chatterbox MLX, your voice fast on a Mac (1.1 GB)' },
  { id: 'openvoice', kind: 'speech', repo: 'myshell-ai/OpenVoiceV2', package: 'OpenVoiceV2-converter', label: 'OpenVoice, your voice fast (130 MB)' },
];

// The order presets are grouped in.
export const PRESET_KINDS = ['chat', 'transcription', 'speech'];

// The engines the runtime knows, in the order the engines row shows them.
export const ENGINES = [
  { id: 'llama', kind: 'chat' },
  // Apple silicon only: the runtime lists it nowhere else, and the row skips it.
  { id: 'mlx', kind: 'chat' },
  { id: 'whisper', kind: 'transcription' },
  { id: 'piper', kind: 'speech' },
  { id: 'kokoro', kind: 'speech' },
  { id: 'kitten', kind: 'speech' },
  { id: 'supertonic', kind: 'speech' },
  // Torch, in an environment of their own; a gigabyte or two to install.
  { id: 'chatterbox', kind: 'speech' },
  // Apple silicon only: the runtime lists it nowhere else.
  { id: 'chatterbox_mlx', kind: 'speech' },
  { id: 'openvoice', kind: 'speech' },
  // Recorded voice cleanup; a cleanup installs its engine itself. DeepFilterNet
  // is Apple silicon only.
  { id: 'deepfilternet', kind: 'cleanup' },
  { id: 'resemble_enhance', kind: 'cleanup' },
];

export const isSpeechKind = (kind) => kind === 'speech' || kind === 'transcription';

// Engines whose voices are the recordings people made (the Recorded voices card).
export const CLONING_ENGINES = ['chatterbox', 'chatterbox_mlx', 'openvoice'];
