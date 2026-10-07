# Model structure

A read-only view of a local model's structure from its GGUF or safetensors header without loading the weights: architecture, layers, tensors and memory, drawn left to right as a 2D block diagram or a 3D row of slabs, and a card for API models. The 2D view opens on the first blocks at a readable size; the mouse wheel or a pinch zooms around the cursor, dragging the canvas moves along the model, and a click outside every block clears the selection.

The parsing lives in `providers/model_structure.py`, the routes in `dashboard/backend/routes/model_structure.py`. The hub's own model runtime imports `structure_from_file` from the same module, so every source answers in one shape.

## What is read

Only the header of a model file. A GGUF file lists every metadata key and every tensor (name, shape, type, offset) before its data section; a safetensors file starts with an 8 byte length and a JSON header. Neither needs the weights, so a 70B model is described in milliseconds and a few kilobytes of reads. No tensor data is ever opened, and every length read from a header is bounded before it is used, because a header is untrusted input.

The answer is one of two shapes:

- `{"kind": "structure", "source": "gguf" | "safetensors" | "ollama", "model": {...}, "blocks": [...], "memory": {...}}` for a model with a file behind it;
- `{"kind": "card", "model": {...}}` for a model the hub only reaches through an API: context window, prices, release date, enabled, default and price source, taken from the catalog on the Models page.

`model.metadata` keeps at most 200 flat scalar keys from the file; lists and strings longer than 200 characters are dropped.

## GGUF

`parse_gguf(path)` reads the magic `GGUF`, versions 2 and 3, the tensor and key counts, every typed key (all thirteen GGUF value types, arrays included) and the tensor infos. Arrays longer than 64 items, such as the tokenizer's tokens and merges, are skipped without being decoded and keep only their length under `<key>.length`; that length is where the vocabulary size comes from when the architecture does not state it.

Bytes per tensor come from the GGML type table (`GGML_TYPES`: type id to name, elements per block and bytes per block, from ggml's type traits), covering F32, F16, BF16, F64, the integer types, Q4_0 to Q8_1, the K quants, the IQ quants, TQ1_0, TQ2_0 and MXFP4. A type the table does not know gets the gap to the next tensor's offset as an estimate. Shapes are reported outermost first, like PyTorch, although GGUF stores them innermost first. The data section starts at the header's end rounded up to `general.alignment` (32 when absent).

A split model (`-00001-of-0000N.gguf`) is read whole from any of its parts: the metadata from the first, the tensors from every part on disk (each tagged with its `file`), the size as their sum, and `split.count`, `split.parts_found` and `split.missing` in the metadata say what was there.

The quantization shown is the file's `general.file_type` (Q4_K_M, Q5_K_S and so on); the per block quantization is the type that holds most bytes in that block, which is how a Q4_K_M file shows its Q6_K layers.

## safetensors

`parse_safetensors(path)` takes a single `.safetensors` file or a directory. A directory with `model.safetensors.index.json` reads the header of each shard its `weight_map` names (a shard path escaping the directory is refused); a directory without an index reads every `*.safetensors` in it. A `config.json` beside the files is flattened into the metadata and gives the architecture, `num_hidden_layers`, `hidden_size`, `num_attention_heads`, `num_key_value_heads`, `vocab_size`, `max_position_embeddings`, `intermediate_size` and `torch_dtype`. A multimodal config's `text_config` is lifted so the language model's geometry is found. Tensor bytes come from `data_offsets`.

An MLX quantized checkpoint (a `quantization` block in `config.json`) packs several weights into each `U32` element and keeps a `.scales` and `.biases` tensor per module, one entry per group. Its parameter count is the scales' element count times `group_size` (a per-module entry in the block overrides it), the scales and biases count as storage only, and the quantization reads `MLX 4-bit` from the block's `bits`.

## Ollama models

For provider `ollama` the hub asks Ollama's `/api/show` with `verbose`, which answers with the GGUF metadata as `model_info` and every tensor as `{name, type, shape}`. `from_ollama_show` turns that into the same structure, with the quantization from `details.quantization_level` and `modified_at` from the answer.

An older Ollama lists no tensors. The layers are then drawn from `block_count` with no tensors in them, the size is estimated from the parameter count and the quantization's bits per weight and shared out evenly between the layers (after an embedding of vocabulary times hidden size), and `model.metadata.tensors_listed` is `false` so the page can say the proportions are an estimate.

For provider `hub-local` the hub forwards to its runtime, `GET {runtime}/models/{file}/structure` with the runtime's bearer token, and returns its answer unchanged. The runtime is the one `AGENTS_HUB_MODELS_URL` names, else the one the hub runs itself (`runtime_settings()` in providers/local_models.py). The reader is part of the runtime's code stamp, so a changed reader marks the runtime stale and the hub restarts it once nothing is loaded.

## The block graph

Blocks come in a fixed order:

1. `embedding`: `token_embd` in GGUF; `embed_tokens`, `wte`, `word_embeddings` and position embeddings in safetensors.
2. One `layer` block per layer index, `blk.N.` in GGUF and `model.layers.N.` or `transformer.h.N.` in safetensors, with `index` set to N.
3. `norm`: the final norm (`output_norm`, `model.norm`, `ln_f`).
4. `head`: the output matrix (`output.weight`, `lm_head`). A model without one reuses the embedding for its logits; its head block then has `"tied": true`, no tensors and zero bytes, so the weights are counted once.
5. `other`: everything outside the language model, one block per prefix, such as `vision_tower`, `multi_modal_projector` or a projector file's `v` and `mm` tensors.

Every tensor carries a `role` read from its name: `attn_q`, `attn_k`, `attn_v`, `attn_o`, `attn_qkv`, `ffn_gate`, `ffn_up`, `ffn_down`, `norm`, `embedding`, `output`, `expert` (mixture of experts weights and their routers), or null when the name says nothing recognisable.

## Memory

`memory.weights_bytes` is the sum of the tensor sizes, which is what the weights take on disk and, loaded without further conversion, roughly in memory. `by_dtype` and `by_kind` split the same total by tensor type and by block kind.

The KV cache estimate assumes an f16 cache: per token it is layers times 2 (keys and values) times KV heads times head dimension times 2 bytes, and `kv_cache_bytes_at_context` multiplies that by the context length. The head dimension is `attention.key_length` or `head_dim` when the model states it, else hidden size divided by heads. Either figure is null when the geometry is incomplete. Sliding window attention, a quantized cache or MLA compression make the real cache smaller than this.

## Routes

- `GET /api/models/structure?provider=&model=`: the structure of an Ollama or hub-local model, or the card of a catalog model. The model is a query parameter because ids carry slashes and colons. 404 when Ollama or the catalog has no such model, 502 when a server cannot be reached, 503 when `hub-local` is asked for and no runtime is running.
- `GET /api/models/structure/file?path=`: the structure of a file or safetensors directory on the host, for an operator. The path must lie under `models_dir` (when that setting exists) or `AGENTS_HUB_MODELS_DIR`; without either the route answers 403, and a path that escapes the directory, including through a symlink, is refused with 403. A relative path is taken relative to that directory.

## Gotchas

- A split GGUF with parts missing on disk still parses; the missing parts' tensors are simply absent, and `split.missing` names them.
- GGUF from before version 2 is refused rather than guessed at.
- Byte sizes of the IQ and TQ types follow ggml's block structs; a type added upstream after this table was written falls back to an offset based estimate.
- A safetensors checkpoint that ties its head to the embedding and still stores `lm_head` shows both; the tie is only detected when the head tensor is absent.
- Layer blocks from Ollama without tensors are evenly sized estimates, not measurements.
