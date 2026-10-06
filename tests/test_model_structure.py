"""Model structure from file headers (feature 6, docs/model-structure.md).

The files here are written byte by byte with ``struct`` and ``json``: a GGUF
header with a handful of tensors and no data behind them worth reading, a
single safetensors file and a sharded directory. That pins the parsers to the
formats themselves rather than to a library's reading of them, and keeps the
tests independent of any real model on disk.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from providers import model_structure as ms  # noqa: E402


# ── GGUF writer ──────────────────────────────────────────────────────────────

def _s(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<Q", len(raw)) + raw


def _kv(key: str, vtype: int, payload: bytes) -> bytes:
    return _s(key) + struct.pack("<I", vtype) + payload


HIDDEN, FFN, VOCAB = 256, 512, 1024

# (name, GGUF dims innermost first, type id)
TENSORS = [
    ("token_embd.weight", [HIDDEN, VOCAB], 1),      # F16
    ("blk.0.attn_q.weight", [HIDDEN, HIDDEN], 12),  # Q4_K
    ("blk.0.ffn_up.weight", [HIDDEN, FFN], 12),
    ("blk.1.attn_q.weight", [HIDDEN, HIDDEN], 12),
    ("output_norm.weight", [HIDDEN], 0),            # F32
    ("output.weight", [HIDDEN, VOCAB], 14),         # Q6_K
]


def write_gguf(path: Path, tensors=TENSORS, extra_kv=b"", n_extra=0) -> Path:
    tokens = [f"tok{i}" for i in range(100)]  # longer than the kept array limit
    kvs = [
        _kv("general.architecture", 8, _s("llama")),
        _kv("general.name", 8, _s("Tiny")),
        _kv("general.file_type", 4, struct.pack("<I", 15)),       # Q4_K_M
        _kv("llama.block_count", 4, struct.pack("<I", 2)),
        _kv("llama.embedding_length", 10, struct.pack("<Q", HIDDEN)),
        _kv("llama.attention.head_count", 4, struct.pack("<I", 8)),
        _kv("llama.attention.head_count_kv", 4, struct.pack("<I", 2)),
        _kv("llama.context_length", 4, struct.pack("<I", 4096)),
        _kv("llama.feed_forward_length", 4, struct.pack("<I", FFN)),
        _kv("llama.rope.freq_base", 6, struct.pack("<f", 10000.0)),
        _kv("tokenizer.ggml.add_bos_token", 7, struct.pack("<?", True)),
        _kv("tokenizer.ggml.tokens", 9,
            struct.pack("<IQ", 8, len(tokens)) + b"".join(_s(t) for t in tokens)),
        _kv("tokenizer.ggml.token_type", 9,
            struct.pack("<IQ", 5, 3) + struct.pack("<3i", 1, 2, 3)),
    ]
    body = b"".join(kvs) + extra_kv
    tinfo = b""
    offset = 0
    for name, dims, tid in tensors:
        tinfo += _s(name) + struct.pack("<I", len(dims)) + struct.pack(f"<{len(dims)}Q", *dims)
        tinfo += struct.pack("<IQ", tid, offset)
        offset += 1024
    header = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kvs) + n_extra) + body + tinfo
    header += b"\0" * ((32 - len(header) % 32) % 32)
    path.write_bytes(header + b"\0" * 64)  # a token data section, never read
    return path


def expected_bytes(dims, tid):
    _, block, size = ms.GGML_TYPES[tid]
    n = 1
    for d in dims:
        n *= d
    return (n + block - 1) // block * size


def test_type_table_matches_ggml_block_sizes():
    assert ms.GGML_TYPES[12] == ("Q4_K", 256, 144)
    assert ms.GGML_TYPES[14] == ("Q6_K", 256, 210)
    assert ms.GGML_TYPES[8] == ("Q8_0", 32, 34)
    assert ms.ggml_tensor_bytes("F16", 10) == 20
    assert ms.ggml_tensor_bytes("NOPE", 10) is None


def test_parse_gguf_reads_metadata_and_tensor_sizes(tmp_path):
    parsed = ms.parse_gguf(write_gguf(tmp_path / "tiny.gguf"))
    md = parsed["metadata"]
    assert md["gguf.version"] == 3
    assert md["general.architecture"] == "llama"
    assert md["llama.embedding_length"] == HIDDEN
    assert md["llama.rope.freq_base"] == pytest.approx(10000.0)
    assert md["tokenizer.ggml.add_bos_token"] is True
    # A long array keeps only its length; a short one keeps its items too.
    assert "tokenizer.ggml.tokens" not in md
    assert md["tokenizer.ggml.tokens.length"] == 100
    assert md["tokenizer.ggml.token_type"] == [1, 2, 3]

    by_name = {t["name"]: t for t in parsed["tensors"]}
    assert len(by_name) == 6
    for name, dims, tid in TENSORS:
        t = by_name[name]
        assert t["bytes"] == expected_bytes(dims, tid)
        assert t["shape"] == list(reversed(dims))
    assert by_name["blk.0.attn_q.weight"]["dtype"] == "Q4_K"
    assert by_name["output.weight"]["offset"] == 5 * 1024


def test_split_gguf_is_read_from_every_part(tmp_path):
    first = [t for t in TENSORS if not t[0].startswith("blk.1")]
    second = [t for t in TENSORS if t[0].startswith("blk.1")]
    write_gguf(tmp_path / "big-00001-of-00003.gguf", tensors=first)
    write_gguf(tmp_path / "big-00002-of-00003.gguf", tensors=second)
    # Part 3 is not on disk: the model is still read, and says what is missing.
    for start in ("big-00001-of-00003.gguf", "big-00002-of-00003.gguf"):
        parsed = ms.parse_gguf(tmp_path / start)
        md = parsed["metadata"]
        assert md["general.name"] == "Tiny"  # the first part's metadata, whichever part was named
        assert (md["split.count"], md["split.parts_found"], md["split.missing"]) == (3, 2, [3])
        names = {t["name"]: t["file"] for t in parsed["tensors"]}
        assert names["blk.0.attn_q.weight"] == "big-00001-of-00003.gguf"
        assert names["blk.1.attn_q.weight"] == "big-00002-of-00003.gguf"
        assert len(names) == len(TENSORS)
        assert parsed["file_size"] == sum(
            (tmp_path / f).stat().st_size for f in ("big-00001-of-00003.gguf", "big-00002-of-00003.gguf"))
    structure = ms.structure_from_file(tmp_path / "big-00002-of-00003.gguf")
    assert structure["model"]["layers"] == 2
    assert [b["kind"] for b in structure["blocks"]][:3] == ["embedding", "layer", "layer"]
    assert ms.split_parts(tmp_path / "big-00001-of-00003.gguf") == [
        tmp_path / "big-00001-of-00003.gguf", tmp_path / "big-00002-of-00003.gguf"]


def test_gguf_structure_blocks_roles_and_memory(tmp_path):
    s = ms.structure_from_file(str(write_gguf(tmp_path / "tiny.gguf")))
    assert s["kind"] == "structure" and s["source"] == "gguf"
    assert [b["id"] for b in s["blocks"]] == ["embedding", "layer.0", "layer.1", "norm", "head"]
    m = s["model"]
    assert m["layers"] == 2 and m["hidden_size"] == HIDDEN and m["heads"] == 8
    assert m["kv_heads"] == 2 and m["vocab_size"] == 100 and m["context_length"] == 4096
    assert m["quantization"] == "Q4_K_M" and m["name"] == "Tiny"
    assert "tokenizer.ggml.token_type" not in m["metadata"]  # lists are dropped
    roles = {t["name"]: t["role"] for b in s["blocks"] for t in b["tensors"]}
    assert roles == {"token_embd.weight": "embedding", "blk.0.attn_q.weight": "attn_q",
                     "blk.0.ffn_up.weight": "ffn_up", "blk.1.attn_q.weight": "attn_q",
                     "output_norm.weight": "norm", "output.weight": "output"}
    layer0 = s["blocks"][1]
    assert layer0["quantization"] == "Q4_K" and layer0["index"] == 0
    assert layer0["parameters"] == HIDDEN * HIDDEN + HIDDEN * FFN

    mem = s["memory"]
    q4 = expected_bytes([HIDDEN, HIDDEN], 12) * 2 + expected_bytes([HIDDEN, FFN], 12)
    assert mem["by_dtype"] == {"F16": HIDDEN * VOCAB * 2, "Q4_K": q4, "F32": HIDDEN * 4,
                               "Q6_K": expected_bytes([HIDDEN, VOCAB], 14)}
    assert mem["weights_bytes"] == sum(mem["by_dtype"].values()) == sum(mem["by_kind"].values())
    head_dim = HIDDEN // 8
    assert mem["kv_cache_bytes_per_token"] == 2 * 2 * 2 * head_dim * 2
    assert mem["kv_cache_bytes_at_context"] == mem["kv_cache_bytes_per_token"] * 4096


def test_gguf_without_output_is_tied(tmp_path):
    s = ms.structure_from_file(write_gguf(tmp_path / "t.gguf", tensors=TENSORS[:5]))
    head = s["blocks"][-1]
    assert head["kind"] == "head" and head["tied"] is True and head["bytes"] == 0


def test_gguf_rejects_bad_files(tmp_path):
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"GGUF" + struct.pack("<IQQ", 1, 0, 0))
    with pytest.raises(ms.ModelStructureError):
        ms.parse_gguf(bad)
    trunc = tmp_path / "trunc.gguf"
    trunc.write_bytes(write_gguf(tmp_path / "x.gguf").read_bytes()[:60])
    with pytest.raises(ms.ModelStructureError) as err:
        ms.parse_gguf(trunc)
    assert err.value.status == 422


# ── safetensors ──────────────────────────────────────────────────────────────

def write_safetensors(path: Path, tensors: dict) -> Path:
    header, offset = {"__metadata__": {"format": "pt"}}, 0
    for name, (dtype, shape) in tensors.items():
        n = 1
        for d in shape:
            n *= d
        size = n * {"F32": 4, "BF16": 2, "F16": 2, "U32": 4}[dtype]
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + size]}
        offset += size
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * 16)
    return path


ST_TENSORS = {
    "model.embed_tokens.weight": ("BF16", [1000, 64]),
    "model.layers.0.self_attn.q_proj.weight": ("BF16", [64, 64]),
    "model.layers.0.self_attn.k_proj.weight": ("BF16", [16, 64]),
    "model.layers.0.mlp.down_proj.weight": ("BF16", [64, 128]),
    "model.layers.0.input_layernorm.weight": ("BF16", [64]),
    "model.layers.1.self_attn.o_proj.weight": ("BF16", [64, 64]),
    "model.layers.1.mlp.gate_proj.weight": ("BF16", [128, 64]),
    "model.norm.weight": ("BF16", [64]),
    "lm_head.weight": ("BF16", [1000, 64]),
    "vision_tower.encoder.layers.0.proj.weight": ("F32", [8, 8]),
}


def test_single_safetensors_file(tmp_path):
    s = ms.structure_from_file(write_safetensors(tmp_path / "m.safetensors", ST_TENSORS))
    assert s["source"] == "safetensors"
    ids = [b["id"] for b in s["blocks"]]
    assert ids == ["embedding", "layer.0", "layer.1", "norm", "head", "other.vision_tower"]
    assert s["model"]["layers"] == 2 and s["model"]["hidden_size"] == 64
    roles = {t["name"].split(".")[-2]: t["role"] for t in s["blocks"][1]["tensors"]}
    assert roles == {"q_proj": "attn_q", "k_proj": "attn_k", "down_proj": "ffn_down",
                     "input_layernorm": "norm"}
    assert s["blocks"][4]["bytes"] == 1000 * 64 * 2
    assert s["memory"]["by_dtype"]["F32"] == 8 * 8 * 4


def test_mlx_quantized_weights_are_unpacked(tmp_path):
    """An MLX 4-bit checkpoint packs 8 weights per uint32; the count comes
    from the scales (one per group), a per-module override changes the group,
    and scales and biases are storage, not parameters."""
    d = tmp_path / "mlx"
    d.mkdir()
    write_safetensors(d / "model.safetensors", {
        "model.layers.0.mlp.down_proj.weight": ("U32", [64, 16]),   # 64 x 128 at 4 bits
        "model.layers.0.mlp.down_proj.scales": ("BF16", [64, 2]),
        "model.layers.0.mlp.down_proj.biases": ("BF16", [64, 2]),
        "model.layers.0.mlp.gate.weight": ("U32", [8, 16]),         # 8 x 64 at 8 bits, group 32
        "model.layers.0.mlp.gate.scales": ("BF16", [8, 2]),
        "model.layers.0.input_layernorm.weight": ("BF16", [64]),
    })
    (d / "config.json").write_text(json.dumps({
        "num_hidden_layers": 1, "hidden_size": 64,
        "quantization": {"group_size": 64, "bits": 4,
                         "model.layers.0.mlp.gate": {"group_size": 32, "bits": 8}}}))
    s = ms.structure_from_file(d)
    assert s["model"]["parameters"] == 64 * 128 + 8 * 64 + 64
    assert s["model"]["quantization"] == "MLX 4-bit"
    weights = sum(t["bytes"] for b in s["blocks"] for t in b["tensors"])
    assert weights == (64 * 16 * 4) + 2 * (64 * 2 * 2) + (8 * 16 * 4) + (8 * 2 * 2) + 64 * 2


def test_sharded_safetensors_directory(tmp_path):
    d = tmp_path / "model"
    d.mkdir()
    names = list(ST_TENSORS)
    first = {k: ST_TENSORS[k] for k in names[:5]}
    second = {k: ST_TENSORS[k] for k in names[5:9]}
    write_safetensors(d / "model-00001-of-00002.safetensors", first)
    write_safetensors(d / "model-00002-of-00002.safetensors", second)
    weight_map = {k: "model-00001-of-00002.safetensors" for k in first}
    weight_map.update({k: "model-00002-of-00002.safetensors" for k in second})
    (d / "model.safetensors.index.json").write_text(json.dumps({"weight_map": weight_map}))
    (d / "config.json").write_text(json.dumps({
        "architectures": ["LlamaForCausalLM"], "num_hidden_layers": 2, "hidden_size": 64,
        "num_attention_heads": 4, "num_key_value_heads": 1, "vocab_size": 1000,
        "max_position_embeddings": 2048, "intermediate_size": 128, "torch_dtype": "bfloat16"}))
    s = ms.structure_from_file(d)
    m = s["model"]
    assert m["architecture"] == "LlamaForCausalLM" and m["layers"] == 2
    assert m["hidden_size"] == 64 and m["kv_heads"] == 1 and m["dtype"] == "bfloat16"
    assert m["feed_forward_length"] == 128 and m["context_length"] == 2048
    assert [b["kind"] for b in s["blocks"]] == ["embedding", "layer", "layer", "norm", "head"]
    # 2 layers * 2 (k and v) * 1 kv head * head_dim 16 * 2 bytes
    assert s["memory"]["kv_cache_bytes_per_token"] == 2 * 2 * 1 * 16 * 2


# ── Ollama ───────────────────────────────────────────────────────────────────

MODEL_INFO = {
    "general.architecture": "llama", "general.parameter_count": 8_000_000,
    "llama.block_count": 4, "llama.embedding_length": 128,
    "llama.attention.head_count": 4, "llama.attention.head_count_kv": 2,
    "llama.context_length": 8192, "llama.vocab_size": 1000,
    "tokenizer.ggml.tokens": None,
}
DETAILS = {"family": "llama", "parameter_size": "8.0M", "quantization_level": "Q4_K_M",
           "format": "gguf"}


def test_from_ollama_show_with_tensors():
    show = {"model_info": MODEL_INFO, "details": DETAILS, "modified_at": "2026-09-01T00:00:00Z",
            "tensors": [
                {"name": "token_embd.weight", "type": "Q4_K", "shape": [128, 1000]},
                {"name": "blk.0.attn_k.weight", "type": "Q4_K", "shape": [128, 64]},
                {"name": "blk.1.ffn_down.weight", "type": "Q6_K", "shape": [512, 128]},
                {"name": "output_norm.weight", "type": "F32", "shape": [128]},
            ]}
    s = ms.from_ollama_show("llama3:8b", show)
    assert s["source"] == "ollama" and s["model"]["quantization"] == "Q4_K_M"
    assert s["model"]["modified_at"] == "2026-09-01T00:00:00Z"
    assert [b["id"] for b in s["blocks"]] == ["embedding", "layer.0", "layer.1", "norm", "head"]
    assert s["blocks"][1]["bytes"] == ms.ggml_tensor_bytes("Q4_K", 128 * 64)
    assert s["model"]["layers"] == 4  # from block_count, not the tensors listed
    assert s["model"]["metadata"]["tensors_listed"] is True


def test_from_ollama_show_without_tensors():
    s = ms.from_ollama_show("old:latest", {"model_info": MODEL_INFO, "details": DETAILS})
    layers = [b for b in s["blocks"] if b["kind"] == "layer"]
    assert len(layers) == 4 and all(not b["tensors"] for b in layers)
    assert s["model"]["metadata"]["tensors_listed"] is False
    assert s["model"]["parameters"] == 8_000_000
    assert layers[0]["bytes"] > 0
    assert s["memory"]["weights_bytes"] == sum(b["bytes"] for b in s["blocks"])


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def test_route_ollama(client, monkeypatch):
    from providers import local_models
    calls = []

    def fake_show(name, verbose=False, timeout=30.0):
        calls.append((name, verbose))
        return {"model_info": MODEL_INFO, "details": DETAILS}
    monkeypatch.setattr(local_models, "ollama_show", fake_show)
    r = client.get("/api/models/structure", params={"provider": "ollama", "model": "llama3:8b"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "structure"
    assert calls == [("llama3:8b", True)]

    def missing(name, verbose=False, timeout=30.0):
        raise local_models.LocalModelError(f"Ollama has no model named {name!r}")
    monkeypatch.setattr(local_models, "ollama_show", missing)
    r = client.get("/api/models/structure", params={"provider": "ollama", "model": "nope"})
    assert r.status_code == 404


def test_hub_local_uses_the_managed_runtime(monkeypatch):
    """With AGENTS_HUB_MODELS_URL empty, the runtime the hub runs itself
    answers, through the same lookup the rest of the hub uses."""
    import httpx
    from providers import local_models
    monkeypatch.setattr(local_models, "runtime_settings", lambda: {
        "url": "http://127.0.0.1:8200", "token": "t0k", "timeout": 5.0, "managed": True})
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        return httpx.Response(200, json={"kind": "structure", "model": {"id": "a/b.gguf"}})
    monkeypatch.setattr(httpx, "get", fake_get)
    out = ms.structure_for("hub-local", "a/b.gguf")
    assert out["kind"] == "structure"
    assert seen["url"] == "http://127.0.0.1:8200/models/a%2Fb.gguf/structure"
    assert seen["headers"] == {"Authorization": "Bearer t0k"}

    monkeypatch.setattr(local_models, "runtime_settings", lambda: {
        "url": "", "token": "", "timeout": 5.0, "managed": False})
    with pytest.raises(ms.ModelStructureError) as exc:
        ms.structure_for("hub-local", "a/b.gguf")
    assert exc.value.status == 503


def test_route_card_for_catalog_model(client):
    from providers.catalog import save_catalog_raw
    save_catalog_raw({"openai": {"default": "gpt-x/mini", "models": [
        {"id": "gpt-x/mini", "enabled": True, "input_price": 1.5, "output_price": 6.0,
         "context_window": 128000, "released_at": 1700000000, "price_source": "manual"}]}})
    r = client.get("/api/models/structure", params={"provider": "openai", "model": "gpt-x/mini"})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["kind"] == "card"
    assert card["model"]["default"] is True and card["model"]["context_window"] == 128000
    assert card["model"]["price_source"] == "manual" and card["model"]["input_price"] == 1.5
    r = client.get("/api/models/structure", params={"provider": "openai", "model": "unknown"})
    assert r.status_code == 404


def test_file_route_path_guard(client, tmp_path, monkeypatch):
    from common.config import settings
    if hasattr(settings, "models_dir"):
        monkeypatch.setattr(settings, "models_dir", "")
    monkeypatch.delenv("AGENTS_HUB_MODELS_DIR", raising=False)
    r = client.get("/api/models/structure/file", params={"path": "/etc/passwd"})
    assert r.status_code == 403

    root = tmp_path / "models"
    root.mkdir()
    write_gguf(root / "tiny.gguf")
    outside = write_gguf(tmp_path / "outside.gguf")
    (root / "link.gguf").symlink_to(outside)
    monkeypatch.setenv("AGENTS_HUB_MODELS_DIR", str(root))
    ok = client.get("/api/models/structure/file", params={"path": "tiny.gguf"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["source"] == "gguf"
    for bad in ("../outside.gguf", str(outside), "link.gguf"):
        r = client.get("/api/models/structure/file", params={"path": bad})
        assert r.status_code == 403, bad
    assert client.get("/api/models/structure/file", params={"path": "none.gguf"}).status_code == 404
