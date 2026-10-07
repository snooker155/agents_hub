"""
Read-only structure of a model, parsed from its file header without loading a
single weight. Feature 6 of the third plan; see docs/model-structure.md.

Three sources feed one shape:

- a GGUF file, whose header lists every metadata key and every tensor (name,
  shape, GGML type, offset) before the data section;
- safetensors, a single file or a sharded directory, where each file starts
  with an 8 byte length and a JSON header, and ``config.json`` carries the
  architecture;
- Ollama's verbose ``/api/show`` answer, which is the GGUF header Ollama has
  already read for us.

Each parser returns ``{"metadata": {...}, "tensors": [...]}`` and
``to_structure`` turns that into the block graph the Models page draws:
embedding, one block per layer, the final norm, the output head and whatever
is left (vision towers, projectors) as ``other`` blocks. Memory is summed
from tensor sizes, so it is what the weights take on disk and roughly in RAM,
plus a KV cache estimate from the attention geometry.

A model the hub only reaches through an API has no file to read: it gets a
card built from the catalog instead (``structure_for``).

Everything here reads headers only, with ``struct`` and ``json``. A header is
still untrusted input, so every length read from it is bounded before it is
used to read or seek.
"""
from __future__ import annotations

import json
import os
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Optional, Tuple, Union


class ModelStructureError(Exception):
    """A structure could not be produced. ``status`` is the HTTP status the
    route answers with; the message is safe to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── GGML types ────────────────────────────────────────────────────────────────

# type id -> (name, elements per block, bytes per block), from ggml's
# type_traits table (ggml/src/ggml.c). Ids 4 and 5 (Q4_2, Q4_3) and 31 to 33
# (the Q4_0_4_4 family) were removed upstream and are absent on purpose. The
# block sizes follow the block_* structs in ggml-common.h; the IQ and TQ
# figures were checked against those structs by hand, and an unknown id falls
# back to an estimate proportional to the data section (see _fill_unknown_bytes).
GGML_TYPES: Dict[int, Tuple[str, int, int]] = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    2: ("Q4_0", 32, 18),
    3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24),
    8: ("Q8_0", 32, 34),
    9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84),
    11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210),
    15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66),
    17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18),
    21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82),
    23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1),
    25: ("I16", 1, 2),
    26: ("I32", 1, 4),
    27: ("I64", 1, 8),
    28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2),
    34: ("TQ1_0", 256, 54),
    35: ("TQ2_0", 256, 66),
    39: ("MXFP4", 32, 17),
}
_GGML_BY_NAME: Dict[str, Tuple[int, int]] = {n: (b, s) for n, b, s in GGML_TYPES.values()}

# general.file_type (llama_ftype) -> the quantization name people know it by.
GGUF_FILE_TYPES: Dict[int, str] = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1",
    10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S",
    15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS",
    20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S",
    25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M",
    30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0",
    38: "MXFP4_MOE",
}

# safetensors dtype -> bytes per element. F4 style packed types are rounded
# per tensor in _safetensors_bytes, which prefers the data_offsets anyway.
SAFETENSORS_DTYPES: Dict[str, float] = {
    "F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4, "I16": 2,
    "I8": 1, "U64": 8, "U32": 4, "U16": 2, "U8": 1, "BOOL": 1,
    "F8_E4M3": 1, "F8_E5M2": 1, "F8_E8M0": 1, "F6_E2M3": 0.75,
    "F6_E3M2": 0.75, "F4": 0.5,
}


def ggml_tensor_bytes(type_name: str, n_elements: int) -> Optional[int]:
    """Bytes a tensor of this GGML type takes, or None for an unknown type."""
    info = _GGML_BY_NAME.get(str(type_name).upper())
    if info is None:
        return None
    block, size = info
    return (n_elements + block - 1) // block * size


def _prod(shape: Iterable[int]) -> int:
    n = 1
    for d in shape:
        n *= int(d)
    return n


# ── GGUF ──────────────────────────────────────────────────────────────────────

GGUF_MAGIC = b"GGUF"
# Arrays longer than this keep only their length: tokenizer vocabularies and
# merges run to hundreds of thousands of strings and nobody reads them here.
_ARRAY_KEEP = 64
# Bounds against a corrupt or hostile header, well above anything real.
_MAX_STRING = 1 << 24
_MAX_COUNT = 1 << 32
_MAX_DIMS = 8

# GGUF value type -> struct format for the fixed size ones.
_GGUF_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f",
                 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_GGUF_STRING, _GGUF_ARRAY = 8, 9


class _Reader:
    """Little-endian reads over a file object that fail loudly on a short read."""

    def __init__(self, f: BinaryIO, size: int):
        self.f = f
        self.size = size

    def read(self, n: int) -> bytes:
        data = self.f.read(n)
        if len(data) != n:
            raise ModelStructureError("GGUF header is truncated", 422)
        return data

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def u32(self) -> int:
        return self.unpack("<I")

    def u64(self) -> int:
        return self.unpack("<Q")

    def string(self) -> str:
        n = self.u64()
        if n > _MAX_STRING:
            raise ModelStructureError(f"GGUF string of {n} bytes is not plausible", 422)
        return self.read(n).decode("utf-8", errors="replace")

    def skip(self, n: int) -> None:
        if n < 0 or self.f.tell() + n > self.size:
            raise ModelStructureError("GGUF header is truncated", 422)
        self.f.seek(n, os.SEEK_CUR)

    def value(self, vtype: int) -> Any:
        if vtype in _GGUF_SCALARS:
            return self.unpack(_GGUF_SCALARS[vtype])
        if vtype == _GGUF_STRING:
            return self.string()
        if vtype == _GGUF_ARRAY:
            return self.array()[0]
        raise ModelStructureError(f"GGUF value type {vtype} is unknown", 422)

    def skip_value(self, vtype: int) -> None:
        if vtype in _GGUF_SCALARS:
            self.skip(struct.calcsize(_GGUF_SCALARS[vtype]))
        elif vtype == _GGUF_STRING:
            n = self.u64()
            if n > _MAX_STRING:
                raise ModelStructureError(f"GGUF string of {n} bytes is not plausible", 422)
            self.skip(n)
        elif vtype == _GGUF_ARRAY:
            etype, n = self.u32(), self.u64()
            self._skip_elements(etype, n)
        else:
            raise ModelStructureError(f"GGUF value type {vtype} is unknown", 422)

    def _skip_elements(self, etype: int, n: int) -> None:
        if n > _MAX_COUNT:
            raise ModelStructureError(f"GGUF array of {n} items is not plausible", 422)
        if etype in _GGUF_SCALARS:
            # Fixed size elements: one seek past the lot.
            self.skip(n * struct.calcsize(_GGUF_SCALARS[etype]))
            return
        for _ in range(n):
            self.skip_value(etype)

    def array(self) -> Tuple[Optional[list], int]:
        """(items or None when too long to keep, length)."""
        etype, n = self.u32(), self.u64()
        if n > _ARRAY_KEEP:
            self._skip_elements(etype, n)
            return None, n
        return [self.value(etype) for _ in range(n)], n


#: llama.cpp's split naming, <stem>-00001-of-00003.gguf: the first part
#: carries the model's metadata, every part lists its own tensors.
SPLIT_RE = re.compile(r"^(?P<stem>.+)-(?P<idx>\d{5})-of-(?P<n>\d{5})\.gguf$", re.IGNORECASE)


def split_parts(path: Path) -> List[Path]:
    """The parts of a split GGUF in order, first part first, missing ones
    left out; ``[path]`` for a plain file."""
    m = SPLIT_RE.match(path.name)
    if not m:
        return [path]
    stem, n = m.group("stem"), int(m.group("n"))
    found = [path.with_name(f"{stem}-{i:05d}-of-{n:05d}.gguf") for i in range(1, n + 1)]
    return [q for q in found if q.is_file()] or [path]


def parse_gguf(path: Union[str, Path]) -> Dict[str, Any]:
    """The metadata and tensor list of a GGUF file, from its header alone.

    Tensors carry ``name``, ``shape``, ``dtype`` (the GGML type name),
    ``n_elements``, ``bytes`` and ``offset`` (relative to the data section).
    Arrays keep their length under ``<key>.length``; long ones keep only that.

    A split model (``-00001-of-0000N``) is read whole from any of its parts:
    the metadata comes from the first part, the tensors from every part that
    is on disk (each tagged with its ``file``), ``file_size`` is their sum,
    and ``split.count``, ``split.parts_found`` and ``split.missing`` say what
    was there.
    """
    p = Path(path)
    m = SPLIT_RE.match(p.name)
    if m is None:
        return _parse_gguf_one(p)
    first = p.with_name(f"{m.group('stem')}-00001-of-{m.group('n')}.gguf")
    parts = split_parts(first if first.is_file() else p)
    head = parts[0]
    parsed = _parse_gguf_one(head)
    tensors: List[Dict[str, Any]] = []
    size = 0
    for part in parts:
        one = parsed if part == head else _parse_gguf_one(part)
        for t in one["tensors"]:
            t["file"] = part.name
        tensors.extend(one["tensors"])
        size += one["file_size"]
    n = int(m.group("n"))
    present = {int(SPLIT_RE.match(q.name).group("idx")) for q in parts}  # type: ignore[union-attr]
    parsed["metadata"]["split.count"] = n
    parsed["metadata"]["split.parts_found"] = len(parts)
    parsed["metadata"]["split.missing"] = [i for i in range(1, n + 1) if i not in present]
    parsed["tensors"] = tensors
    parsed["file_size"] = size
    parsed["files"] = [q.name for q in parts]
    return parsed


def _parse_gguf_one(p: Path) -> Dict[str, Any]:
    """One GGUF file's header, split or not."""
    try:
        size = p.stat().st_size
        f = p.open("rb")
    except OSError as exc:
        raise ModelStructureError(f"cannot open {p.name}: {exc.strerror or exc}", 404)
    with f:
        r = _Reader(f, size)
        if r.read(4) != GGUF_MAGIC:
            raise ModelStructureError(f"{p.name} is not a GGUF file", 422)
        version = r.u32()
        if version not in (2, 3):
            raise ModelStructureError(f"GGUF version {version} is not supported (2 and 3 are)", 422)
        n_tensors, n_kv = r.u64(), r.u64()
        if n_tensors > 1_000_000 or n_kv > 1_000_000:
            raise ModelStructureError("GGUF header counts are not plausible", 422)

        metadata: Dict[str, Any] = {"gguf.version": version}
        for _ in range(n_kv):
            key = r.string()
            vtype = r.u32()
            if vtype == _GGUF_ARRAY:
                items, n = r.array()
                metadata[f"{key}.length"] = n
                if items is not None:
                    metadata[key] = items
            else:
                metadata[key] = r.value(vtype)

        tensors: List[Dict[str, Any]] = []
        for _ in range(n_tensors):
            name = r.string()
            n_dims = r.u32()
            if n_dims > _MAX_DIMS:
                raise ModelStructureError(f"tensor {name} has {n_dims} dimensions", 422)
            # GGUF lists dimensions innermost first (ne[0] is the row length);
            # reversed they read like the PyTorch shape of the same weight.
            dims = [r.u64() for _ in range(n_dims)]
            type_id = r.u32()
            offset = r.u64()
            shape = list(reversed(dims))
            n_el = _prod(dims)
            tname = GGML_TYPES.get(type_id, (f"TYPE_{type_id}", 0, 0))[0]
            tensors.append({"name": name, "shape": shape, "dtype": tname,
                            "n_elements": n_el, "bytes": ggml_tensor_bytes(tname, n_el),
                            "offset": offset})
        header_end = f.tell()

    align = metadata.get("general.alignment")
    align = align if isinstance(align, int) and align > 0 else 32
    data_start = (header_end + align - 1) // align * align
    _fill_unknown_bytes(tensors, size - data_start)
    return {"metadata": metadata, "tensors": tensors, "file_size": size}


def _fill_unknown_bytes(tensors: List[Dict[str, Any]], data_size: int) -> None:
    """A tensor of a type missing from GGML_TYPES gets the gap to the next
    tensor's offset, or its share of what the known tensors leave of the data
    section. Either way an estimate, never a read of the data."""
    if all(t["bytes"] is not None for t in tensors):
        return
    by_offset = sorted(tensors, key=lambda t: t["offset"])
    for i, t in enumerate(by_offset):
        if t["bytes"] is not None:
            continue
        end = by_offset[i + 1]["offset"] if i + 1 < len(by_offset) else data_size
        t["bytes"] = max(0, int(end) - int(t["offset"]))


# ── safetensors ───────────────────────────────────────────────────────────────

_MAX_ST_HEADER = 100 * 1024 * 1024


def _read_safetensors_header(path: Path) -> Tuple[Dict[str, Any], int]:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            raw = f.read(8)
            if len(raw) != 8:
                raise ModelStructureError(f"{path.name} is too short for safetensors", 422)
            n = struct.unpack("<Q", raw)[0]
            if n > _MAX_ST_HEADER or n + 8 > size:
                raise ModelStructureError(f"{path.name} has an implausible header length", 422)
            header = json.loads(f.read(n).decode("utf-8"))
    except OSError as exc:
        raise ModelStructureError(f"cannot open {path.name}: {exc.strerror or exc}", 404)
    except (ValueError, UnicodeDecodeError):
        raise ModelStructureError(f"{path.name} has a header that is not JSON", 422)
    if not isinstance(header, dict):
        raise ModelStructureError(f"{path.name} has a header that is not an object", 422)
    return header, size


def _safetensors_bytes(info: Dict[str, Any], n_el: int) -> int:
    offs = info.get("data_offsets")
    if isinstance(offs, list) and len(offs) == 2:
        try:
            return max(0, int(offs[1]) - int(offs[0]))
        except (TypeError, ValueError):
            pass
    per = SAFETENSORS_DTYPES.get(str(info.get("dtype")).upper(), 2)
    return int(n_el * per + 0.999)


def _tensors_from_header(header: Dict[str, Any], shard: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for name, info in header.items():
        if name == "__metadata__" or not isinstance(info, dict):
            continue
        shape = [int(d) for d in info.get("shape") or []]
        n_el = _prod(shape)
        t = {"name": name, "shape": shape, "dtype": str(info.get("dtype") or "?").upper(),
             "n_elements": n_el, "bytes": _safetensors_bytes(info, n_el),
             "offset": (info.get("data_offsets") or [0])[0]}
        if shard:
            t["file"] = shard
        out.append(t)
    return out


def _flatten(prefix: str, value: Any, out: Dict[str, Any]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        out[prefix] = value


def parse_safetensors(path: Union[str, Path]) -> Dict[str, Any]:
    """The tensors of a safetensors file or sharded directory, plus the flat
    ``config.json`` as metadata when there is one beside them."""
    p = Path(path)
    metadata: Dict[str, Any] = {}
    tensors: List[Dict[str, Any]] = []
    file_size = 0
    folder = p if p.is_dir() else p.parent

    config = folder / "config.json"
    cfg: Any = None
    if config.is_file():
        try:
            cfg = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = None
        if isinstance(cfg, dict):
            # Multimodal configs nest the language model under text_config;
            # lift it so the geometry keys are found where they usually are.
            text_cfg = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
            _flatten("", {**cfg, **{k: v for k, v in text_cfg.items() if k not in cfg}}, metadata)

    if p.is_dir():
        index = p / "model.safetensors.index.json"
        if index.is_file():
            try:
                weight_map = json.loads(index.read_text(encoding="utf-8")).get("weight_map") or {}
            except (OSError, ValueError, AttributeError):
                raise ModelStructureError("model.safetensors.index.json is not valid JSON", 422)
            shards = sorted({str(v) for v in weight_map.values()})
        else:
            shards = sorted(x.name for x in p.glob("*.safetensors"))
        if not shards:
            raise ModelStructureError(f"{p.name} holds no safetensors files", 404)
        for shard in shards:
            sp = (p / shard).resolve()
            if not sp.is_relative_to(p.resolve()):
                raise ModelStructureError(f"shard {shard} lies outside the model directory", 422)
            header, size = _read_safetensors_header(sp)
            file_size += size
            tensors.extend(_tensors_from_header(header, shard if len(shards) > 1 else None))
            _merge_st_metadata(header, metadata)
    else:
        header, file_size = _read_safetensors_header(p)
        tensors = _tensors_from_header(header, None)
        _merge_st_metadata(header, metadata)

    quant = cfg.get("quantization") if isinstance(cfg, dict) else None
    quantization = _unpack_mlx_quantized(tensors, quant) if isinstance(quant, dict) else None
    return {"metadata": metadata, "tensors": tensors, "file_size": file_size,
            "quantization": quantization}


def _unpack_mlx_quantized(tensors: List[Dict[str, Any]], quant: Dict[str, Any]) -> Optional[str]:
    """MLX quantized checkpoints pack several weights into each uint32 and
    keep a ``.scales`` (and ``.biases``) tensor with one entry per group of
    ``group_size`` weights. Count the weights the scales cover, and count the
    scales and biases as storage only, the way a GGUF block's scales are."""
    default_group = _as_int(quant.get("group_size")) or 64
    by_name = {t["name"]: t for t in tensors}
    found = False
    for t in tensors:
        name = t["name"]
        if not name.endswith(".weight") or t["dtype"] != "U32":
            continue
        module = name[: -len(".weight")]
        scales = by_name.get(module + ".scales")
        if scales is None:
            continue
        override = quant.get(module)
        group = (_as_int(override.get("group_size")) if isinstance(override, dict) else None) or default_group
        t["n_elements"] = _prod(scales["shape"]) * group
        for extra in (scales, by_name.get(module + ".biases")):
            if extra is not None:
                extra["n_elements"] = 0
        found = True
    bits = _as_int(quant.get("bits"))
    return f"MLX {bits}-bit" if found and bits else None


def _merge_st_metadata(header: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    extra = header.get("__metadata__")
    if isinstance(extra, dict):
        for k, v in extra.items():
            metadata.setdefault(f"safetensors.{k}", v)


# ── Classifying tensors ───────────────────────────────────────────────────────

# Prefixes that wrap the language model in safetensors checkpoints. Anything
# under another prefix (vision_tower., multi_modal_projector., audio_tower.)
# is not the text stack and lands in an ``other`` block.
_LM_PREFIX = re.compile(
    r"^(?:model\.language_model\.|language_model\.model\.|language_model\.|"
    r"model\.decoder\.|model\.|transformer\.|gpt_neox\.|backbone\.)")
_GGUF_LAYER = re.compile(r"^blk\.(\d+)\.")
_ST_LAYER = re.compile(r"^(?:layers|h|blocks|layer)\.(\d+)\.")

_EMBED_NAMES = re.compile(r"^(?:token_embd|embed_tokens|wte|word_embeddings|embed_in|"
                          r"position_embd|wpe|embed_positions|token_types)\b")
_NORM_NAMES = re.compile(r"^(?:output_norm|norm|ln_f|final_layer_norm|final_layernorm|norm_f)\.")
_HEAD_NAMES = re.compile(r"^(?:output|lm_head|embed_out)\.")


def _lm_name(name: str, source: str) -> Optional[str]:
    """The name inside the language model, or None when it lies outside it."""
    if source != "safetensors":
        # GGUF names are flat; a vision projector file uses v. and mm.
        return None if re.match(r"^(?:v|mm|a|enc|dec)\.", name) else name
    if name.startswith(("lm_head.", "embed_out.")):
        return name
    m = _LM_PREFIX.match(name)
    rest = name[m.end():] if m else name
    if re.match(r"^(?:vision|visual|audio|multi_modal|mm_|image)", rest):
        return None
    return rest


def tensor_role(name: str) -> Optional[str]:
    """A short classification of a tensor from its name alone."""
    n = name.lower()
    if re.search(r"(_exps|_shexp|\.experts\.|shared_expert|ffn_gate_inp|block_sparse_moe\.gate\.|"
                 r"mlp\.gate\.|router)", n):
        return "expert"
    if re.search(r"(norm|\bln_|layernorm|\.ln\d)", n):
        return "norm"
    if re.search(r"(attn_qkv|qkv_proj|query_key_value|c_attn|wqkv)", n):
        return "attn_qkv"
    if re.search(r"(attn_q\b|attn_q\.|q_proj|\.query\.|\.wq\.)", n):
        return "attn_q"
    if re.search(r"(attn_k\b|attn_k\.|k_proj|\.key\.|\.wk\.)", n):
        return "attn_k"
    if re.search(r"(attn_v\b|attn_v\.|v_proj|\.value\.|\.wv\.)", n):
        return "attn_v"
    if re.search(r"(attn_output|o_proj|out_proj|attn\.c_proj|attention\.dense|\.wo\.)", n):
        return "attn_o"
    if re.search(r"(ffn_gate|gate_proj|\.w1\.)", n):
        return "ffn_gate"
    if re.search(r"(ffn_up|up_proj|\.w3\.|c_fc|\.fc1\.|dense_h_to_4h)", n):
        return "ffn_up"
    if re.search(r"(ffn_down|down_proj|\.w2\.|mlp\.c_proj|\.fc2\.|dense_4h_to_h)", n):
        return "ffn_down"
    if re.search(r"(token_embd|embed_tokens|\bwte\b|\bwpe\b|word_embeddings|embed_in|position_embd)", n):
        return "embedding"
    if re.match(r"^(output\.|lm_head\.|embed_out\.)", n):
        return "output"
    return None


def _other_prefix(name: str) -> str:
    parts = name.split(".")
    if len(parts) > 2 and parts[0] in ("model", "v", "mm"):
        return ".".join(parts[:2])
    return parts[0]


# ── Unified structure ─────────────────────────────────────────────────────────

_MAX_METADATA_KEYS = 200
_MAX_METADATA_STRING = 200


def _scalar_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in metadata.items():
        if len(out) >= _MAX_METADATA_KEYS:
            break
        if isinstance(v, bool) or isinstance(v, (int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str) and len(v) <= _MAX_METADATA_STRING:
            out[k] = v
    return out


def _first(metadata: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = metadata.get(k)
        if v is not None and v != "":
            return v
    return None


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, list):
        # Some architectures give a per layer value (head counts in OpenELM);
        # the largest one is what sizes the cache.
        nums = [x for x in v if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return int(max(nums)) if nums else None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _dominant(items: Iterable[Tuple[str, int]]) -> Optional[str]:
    c: Counter = Counter()
    for dtype, b in items:
        c[dtype] += b or 0
    return c.most_common(1)[0][0] if c else None


def _geometry(metadata: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Architecture, layer count and attention geometry from the metadata."""
    if source == "safetensors":
        arch = metadata.get("architectures")
        arch = arch[0] if isinstance(arch, list) and arch else _first(metadata, "model_type")
        g = {
            "architecture": arch,
            "layers": _first(metadata, "num_hidden_layers", "n_layer", "num_layers"),
            "hidden_size": _first(metadata, "hidden_size", "n_embd", "d_model"),
            "heads": _first(metadata, "num_attention_heads", "n_head"),
            "kv_heads": _first(metadata, "num_key_value_heads", "multi_query_group_num"),
            "vocab_size": _first(metadata, "vocab_size"),
            "context_length": _first(metadata, "max_position_embeddings", "n_positions",
                                     "max_sequence_length", "seq_length"),
            "feed_forward_length": _first(metadata, "intermediate_size", "n_inner", "ffn_dim"),
            "head_dim": _first(metadata, "head_dim"),
            "parameters": None,
        }
    else:
        arch = metadata.get("general.architecture") or ""
        a = f"{arch}."
        g = {
            "architecture": arch or None,
            "layers": _first(metadata, a + "block_count"),
            "hidden_size": _first(metadata, a + "embedding_length"),
            "heads": _first(metadata, a + "attention.head_count"),
            "kv_heads": _first(metadata, a + "attention.head_count_kv"),
            "vocab_size": _first(metadata, a + "vocab_size", "tokenizer.ggml.tokens.length"),
            "context_length": _first(metadata, a + "context_length"),
            "feed_forward_length": _first(metadata, a + "feed_forward_length"),
            "head_dim": _first(metadata, a + "attention.key_length"),
            "parameters": _first(metadata, "general.parameter_count"),
        }
    return {k: (v if k == "architecture" else _as_int(v)) for k, v in g.items()}


def _block(bid: str, kind: str, label: str, index: Optional[int],
           tensors: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": bid, "kind": kind, "label": label, "index": index,
        "parameters": sum(t["parameters"] for t in tensors),
        "bytes": sum(t["bytes"] for t in tensors),
        "quantization": _dominant((t["dtype"], t["bytes"]) for t in tensors),
        "tensors": tensors,
    }


def to_structure(parsed: Dict[str, Any], *, source: str, provider: str, model_id: str,
                 name: Optional[str] = None, file: Optional[str] = None,
                 file_size: Optional[int] = None, modified_at: Optional[str] = None,
                 quantization: Optional[str] = None) -> Dict[str, Any]:
    """The unified structure (see the module docstring) from a parse result."""
    metadata = parsed.get("metadata") or {}
    raw = parsed.get("tensors") or []
    layer_re = _GGUF_LAYER if source != "safetensors" else _ST_LAYER

    embedding: List[dict] = []
    norm: List[dict] = []
    head: List[dict] = []
    layers: Dict[int, List[dict]] = defaultdict(list)
    others: Dict[str, List[dict]] = defaultdict(list)

    for t in raw:
        tname = str(t.get("name") or "")
        shape = [int(d) for d in t.get("shape") or []]
        n_el = int(t.get("n_elements") if t.get("n_elements") is not None else _prod(shape))
        entry = {"name": tname, "shape": shape, "dtype": str(t.get("dtype") or "?"),
                 "parameters": n_el, "bytes": int(t.get("bytes") or 0), "role": tensor_role(tname)}
        inner = _lm_name(tname, source)
        if inner is None:
            others[_other_prefix(tname)].append(entry)
            continue
        m = layer_re.match(inner)
        if m:
            layers[int(m.group(1))].append(entry)
        elif _EMBED_NAMES.match(inner):
            embedding.append(entry)
        elif _NORM_NAMES.match(inner):
            norm.append(entry)
        elif _HEAD_NAMES.match(inner) or _HEAD_NAMES.match(tname):
            head.append(entry)
        else:
            others[_other_prefix(tname)].append(entry)

    blocks: List[Dict[str, Any]] = []
    if embedding:
        blocks.append(_block("embedding", "embedding", "Embedding", None, embedding))
    for i in sorted(layers):
        blocks.append(_block(f"layer.{i}", "layer", f"Layer {i}", i, layers[i]))
    if norm:
        blocks.append(_block("norm", "norm", "Final norm", None, norm))
    if head:
        blocks.append(_block("head", "head", "Output head", None, head))
    elif embedding:
        # No separate output matrix: the logits reuse the embedding, which is
        # counted once, in the embedding block.
        hb = _block("head", "head", "Output head (tied to the embedding)", None, [])
        hb["tied"] = True
        blocks.append(hb)
    for prefix in sorted(others):
        blocks.append(_block(f"other.{prefix}", "other", prefix, None, others[prefix]))

    geo = _geometry(metadata, source)
    all_tensors = [t for b in blocks for t in b["tensors"]]
    n_layers = geo["layers"] or len(layers)
    hidden = geo["hidden_size"]
    heads = geo["heads"]
    kv_heads = geo["kv_heads"] or heads
    if not hidden and embedding:
        hidden = min(embedding[0]["shape"]) if embedding[0]["shape"] else None
    vocab = geo["vocab_size"]
    if not vocab and embedding and embedding[0]["shape"]:
        vocab = max(embedding[0]["shape"])
    parameters = sum(t["parameters"] for t in all_tensors) or geo["parameters"] or 0

    if not quantization and source != "safetensors":
        ft = _as_int(metadata.get("general.file_type"))
        quantization = GGUF_FILE_TYPES.get(ft) if ft is not None else None
    dominant = _dominant((t["dtype"], t["bytes"]) for t in all_tensors)
    dtype = metadata.get("torch_dtype") if source == "safetensors" else dominant
    quantization = quantization or parsed.get("quantization")
    if not quantization and source != "safetensors":
        quantization = dominant

    by_dtype: Dict[str, int] = defaultdict(int)
    by_kind: Dict[str, int] = defaultdict(int)
    for b in blocks:
        by_kind[b["kind"]] += b["bytes"]
        for t in b["tensors"]:
            by_dtype[t["dtype"]] += t["bytes"]

    head_dim = geo["head_dim"] or (hidden // heads if hidden and heads else None)
    kv_per_token = (n_layers * 2 * kv_heads * head_dim * 2
                    if n_layers and kv_heads and head_dim else None)
    ctx = geo["context_length"]
    kv_total = kv_per_token * ctx if kv_per_token and ctx else None

    return {
        "kind": "structure",
        "source": source,
        "model": {
            "id": model_id, "provider": provider, "name": name or model_id,
            "file": file, "file_size_bytes": file_size,
            "architecture": geo["architecture"] or "unknown",
            "parameters": int(parameters), "layers": int(n_layers or 0),
            "hidden_size": int(hidden or 0), "heads": int(heads or 0),
            "kv_heads": kv_heads, "vocab_size": vocab,
            "context_length": ctx, "feed_forward_length": geo["feed_forward_length"],
            "quantization": quantization, "dtype": dtype,
            "modified_at": modified_at,
            "metadata": _scalar_metadata(metadata),
        },
        "blocks": blocks,
        "memory": {
            "weights_bytes": sum(b["bytes"] for b in blocks),
            "by_dtype": dict(by_dtype), "by_kind": dict(by_kind),
            "kv_cache_bytes_per_token": kv_per_token,
            "kv_cache_bytes_at_context": kv_total,
        },
    }


# ── Entry points ──────────────────────────────────────────────────────────────

def structure_from_file(path: Union[str, Path], *, provider: str = "file",
                        model_id: Optional[str] = None) -> Dict[str, Any]:
    """The structure of a GGUF file, a safetensors file or a directory holding
    safetensors shards, chosen by extension or directory content."""
    p = Path(path)
    if not p.exists():
        raise ModelStructureError(f"{p.name} does not exist", 404)
    if p.is_dir():
        if (p / "model.safetensors.index.json").is_file() or any(p.glob("*.safetensors")):
            source = "safetensors"
            parsed = parse_safetensors(p)
        else:
            ggufs = sorted(p.glob("*.gguf"))
            if not ggufs:
                raise ModelStructureError(f"{p.name} holds no GGUF or safetensors files", 404)
            # A split GGUF (-00001-of-0000N) is read whole from its first
            # part; parse_gguf gathers the other parts itself.
            p = ggufs[0]
            source, parsed = "gguf", parse_gguf(p)
    elif p.suffix.lower() == ".safetensors":
        source, parsed = "safetensors", parse_safetensors(p)
    else:
        with p.open("rb") as f:
            magic = f.read(4)
        if magic != GGUF_MAGIC:
            raise ModelStructureError(f"{p.name} is neither GGUF nor safetensors", 422)
        source, parsed = "gguf", parse_gguf(p)
    try:
        mtime = p.stat().st_mtime
        from datetime import datetime, timezone
        modified = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        modified = None
    name = parsed["metadata"].get("general.name") if source == "gguf" else None
    return to_structure(parsed, source=source, provider=provider,
                        model_id=model_id or p.name, name=name or p.name,
                        file=str(path), file_size=parsed.get("file_size"),
                        modified_at=modified)


# Rough bits per weight by quantization, used only to size an older Ollama's
# layers when it does not list tensors.
_BITS = {"F32": 32, "F16": 16, "BF16": 16, "Q8_0": 8.5, "Q6_K": 6.6, "Q5": 5.5,
         "Q4": 4.6, "Q3": 3.5, "Q2": 2.6, "IQ4": 4.3, "IQ3": 3.3, "IQ2": 2.3, "IQ1": 1.6}


def _bits_for(quant: Optional[str]) -> float:
    q = (quant or "").upper()
    for key in sorted(_BITS, key=len, reverse=True):
        if q.startswith(key):
            return _BITS[key]
    return 4.6


def _parse_param_size(text: Any) -> Optional[int]:
    m = re.match(r"^\s*([\d.]+)\s*([KMBT]?)", str(text or ""), re.I)
    if not m:
        return None
    mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)


def from_ollama_show(name: str, show: Dict[str, Any]) -> Dict[str, Any]:
    """The structure of an Ollama model from its verbose ``/api/show`` answer."""
    info = dict(show.get("model_info") or {})
    details = show.get("details") or {}
    quant = details.get("quantization_level") or None
    listed = show.get("tensors")
    if listed:
        tensors = []
        for t in listed:
            shape = [int(d) for d in t.get("shape") or []]
            n_el = _prod(shape)
            dtype = str(t.get("type") or "?").upper()
            tensors.append({"name": t.get("name") or "", "shape": shape, "dtype": dtype,
                            "n_elements": n_el, "bytes": ggml_tensor_bytes(dtype, n_el) or 0})
        s = to_structure({"metadata": info, "tensors": tensors}, source="ollama",
                         provider="ollama", model_id=name, name=name,
                         modified_at=show.get("modified_at"), quantization=quant)
        s["model"]["metadata"]["tensors_listed"] = True
        return s

    # An older Ollama lists no tensors: draw the layers from block_count and
    # share the estimated size out between them, so the diagram still has the
    # right shape and plausible proportions.
    s = to_structure({"metadata": info, "tensors": []}, source="ollama", provider="ollama",
                     model_id=name, name=name, modified_at=show.get("modified_at"),
                     quantization=quant)
    m = s["model"]
    params = (_as_int(info.get("general.parameter_count"))
              or _parse_param_size(details.get("parameter_size")) or 0)
    bits = _bits_for(quant)
    total_bytes = _as_int(show.get("size")) or int(params * bits / 8)
    embed_params = (m["vocab_size"] or 0) * (m["hidden_size"] or 0)
    if embed_params >= params:
        embed_params = 0
    embed_bytes = int(embed_params * bits / 8) if params else 0
    n = m["layers"]
    blocks: List[Dict[str, Any]] = []
    if embed_params:
        blocks.append({"id": "embedding", "kind": "embedding", "label": "Embedding", "index": None,
                       "parameters": embed_params, "bytes": embed_bytes,
                       "quantization": quant, "tensors": []})
    for i in range(n):
        blocks.append({"id": f"layer.{i}", "kind": "layer", "label": f"Layer {i}", "index": i,
                       "parameters": (params - embed_params) // n if n else 0,
                       "bytes": (total_bytes - embed_bytes) // n if n else 0,
                       "quantization": quant, "tensors": []})
    s["blocks"] = blocks
    m["parameters"] = params
    m["metadata"]["tensors_listed"] = False
    mem = s["memory"]
    mem["weights_bytes"] = sum(b["bytes"] for b in blocks)
    mem["by_kind"] = {}
    for b in blocks:
        mem["by_kind"][b["kind"]] = mem["by_kind"].get(b["kind"], 0) + b["bytes"]
    mem["by_dtype"] = {quant or "unknown": mem["weights_bytes"]} if blocks else {}
    return s


def model_card(provider: str, model: str, catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The catalog entry of a model the hub only reaches through an API."""
    if catalog is None:
        from providers.catalog import load_catalog_raw
        catalog = load_catalog_raw() or {}
    entry = catalog.get(provider) if isinstance(catalog, dict) else None
    if not isinstance(entry, dict):
        raise ModelStructureError(f"provider {provider!r} is not in the catalog", 404)
    for m in entry.get("models") or []:
        if isinstance(m, dict) and str(m.get("id") or "") == model:
            return {"kind": "card", "model": {
                "id": model, "provider": provider,
                "context_window": int(m.get("context_window") or 0),
                "input_price": float(m.get("input_price") or 0.0),
                "output_price": float(m.get("output_price") or 0.0),
                "cached_input_price": (None if m.get("cached_input_price") is None
                                       else float(m.get("cached_input_price"))),
                "released_at": int(m.get("released_at") or 0),
                "enabled": bool(m.get("enabled", False)),
                "default": (entry.get("default") or "") == model,
                "price_source": "manual" if m.get("price_source") == "manual" else "auto",
            }}
    raise ModelStructureError(f"model {model!r} is not in the {provider} catalog", 404)


def structure_for(provider: str, model: str, *,
                  catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The structure (or card) of a model named the way the rest of the hub
    names it: a provider id and a model id."""
    provider = (provider or "").strip()
    model = (model or "").strip()
    if not provider or not model:
        raise ModelStructureError("provider and model are required", 400)

    if provider == "ollama":
        from providers import local_models
        try:
            show = local_models.ollama_show(model, verbose=True)
        except local_models.LocalModelError as exc:
            msg = str(exc)
            raise ModelStructureError(msg, 404 if "no model named" in msg else 502)
        return from_ollama_show(model, show)

    if provider == "hub-local":
        return _from_runtime(model)

    return model_card(provider, model, catalog)


def _from_runtime(model: str) -> Dict[str, Any]:
    """Ask the hub's own model runtime, which parses its files with this same
    module and answers in the same shape."""
    import httpx
    from urllib.parse import quote
    from providers.local_models import runtime_settings
    # The same lookup the rest of the hub uses: AGENTS_HUB_MODELS_URL when
    # set, else the runtime the hub runs itself on this host.
    cfg = runtime_settings()
    base = str(cfg["url"] or "").rstrip("/")
    if not base:
        raise ModelStructureError("the hub model runtime is not running", 503)
    token = str(cfg["token"] or "")
    timeout = float(cfg["timeout"] or 30)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{base}/models/{quote(model, safe='')}/structure"
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ModelStructureError(f"the hub model runtime is unreachable: {type(exc).__name__}", 502)
    if resp.status_code == 404:
        raise ModelStructureError(f"the hub model runtime has no model {model!r}", 404)
    if resp.status_code != 200:
        raise ModelStructureError(f"the hub model runtime answered HTTP {resp.status_code}", 502)
    try:
        return resp.json()
    except ValueError:
        raise ModelStructureError("the hub model runtime answered with something that is not JSON", 502)


__all__ = [
    "GGML_TYPES", "GGUF_FILE_TYPES", "ModelStructureError", "from_ollama_show",
    "ggml_tensor_bytes", "model_card", "parse_gguf", "parse_safetensors",
    "structure_for", "structure_from_file", "tensor_role", "to_structure",
]
