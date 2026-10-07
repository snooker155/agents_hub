"""
OpenVoice's tone color converter, the part of it speech_worker.py's
``openvoice`` engine runs: it takes speech another model read and gives it the
timbre of a voice sample, in one pass, without reading text itself.

Taken from OpenVoice (https://github.com/myshell-ai/OpenVoice, MIT licence,
Copyright 2024 MyShell.ai): ``openvoice/models.py`` (PosteriorEncoder,
Generator, ReferenceEncoder, ResidualCouplingBlock and the converter half of
SynthesizerTrn), ``openvoice/modules.py`` (WN, ResBlock1, ResBlock2, Flip,
ResidualCouplingLayer), ``openvoice/commons.py`` and ``spectrogram_torch``
from ``openvoice/mel_processing.py``. The layers keep their names and
arguments, so OpenVoice's own checkpoints (myshell-ai/OpenVoiceV2,
``converter/checkpoint.pth`` with its ``config.json``) load into them as they
are. Left out: text encoding and duration prediction (the converter has
none), the watermark (wavmark) and the reference splitter, which needs
Whisper and a VAD; speech_worker.py cuts the sample into pieces itself.

The MIT licence of the original:

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the
    following conditions: The above copyright notice and this permission
    notice shall be included in all copies or substantial portions of the
    Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, Optional

import torch
from torch import nn
from torch.nn import Conv1d, ConvTranspose1d
from torch.nn import functional as F
from torch.nn.utils import remove_weight_norm, weight_norm

LRELU_SLOPE = 0.1

# torch keeps the old weight_norm working and warns about it on every layer;
# the checkpoints store weight_g and weight_v, which only it reads.
warnings.filterwarnings("ignore", message=".*weight_norm.*", category=FutureWarning)


# ── commons.py ───────────────────────────────────────────────────────────────

def init_weights(m: nn.Module, mean: float = 0.0, std: float = 0.01) -> None:
    if m.__class__.__name__.find("Conv") != -1:
        m.weight.data.normal_(mean, std)


def get_padding(kernel_size: int, dilation: int = 1) -> int:
    return int((kernel_size * dilation - dilation) / 2)


@torch.jit.script
def fused_add_tanh_sigmoid_multiply(input_a, input_b, n_channels):
    n_channels_int = n_channels[0]
    in_act = input_a + input_b
    t_act = torch.tanh(in_act[:, :n_channels_int, :])
    s_act = torch.sigmoid(in_act[:, n_channels_int:, :])
    return t_act * s_act


def sequence_mask(length: torch.Tensor, max_length: Optional[int] = None) -> torch.Tensor:
    if max_length is None:
        max_length = length.max()
    x = torch.arange(max_length, dtype=length.dtype, device=length.device)
    return x.unsqueeze(0) < length.unsqueeze(1)


# ── mel_processing.py ────────────────────────────────────────────────────────

_hann_window: Dict[str, torch.Tensor] = {}


def spectrogram_torch(y: torch.Tensor, n_fft: int, sampling_rate: int, hop_size: int, win_size: int,
                      center: bool = False) -> torch.Tensor:
    key = f"{win_size}_{y.dtype}_{y.device}"
    if key not in _hann_window:
        _hann_window[key] = torch.hann_window(win_size).to(dtype=y.dtype, device=y.device)
    y = F.pad(y.unsqueeze(1), (int((n_fft - hop_size) / 2), int((n_fft - hop_size) / 2)), mode="reflect")
    y = y.squeeze(1)
    spec = torch.stft(y, n_fft, hop_length=hop_size, win_length=win_size, window=_hann_window[key],
                      center=center, pad_mode="reflect", normalized=False, onesided=True,
                      return_complex=True)
    return torch.sqrt(torch.view_as_real(spec).pow(2).sum(-1) + 1e-6)


# ── modules.py ───────────────────────────────────────────────────────────────

class WN(nn.Module):
    def __init__(self, hidden_channels, kernel_size, dilation_rate, n_layers, gin_channels=0, p_dropout=0):
        super().__init__()
        assert kernel_size % 2 == 1
        self.hidden_channels = hidden_channels
        self.kernel_size = (kernel_size,)
        self.dilation_rate = dilation_rate
        self.n_layers = n_layers
        self.gin_channels = gin_channels
        self.p_dropout = p_dropout
        self.in_layers = nn.ModuleList()
        self.res_skip_layers = nn.ModuleList()
        self.drop = nn.Dropout(p_dropout)
        if gin_channels != 0:
            cond_layer = nn.Conv1d(gin_channels, 2 * hidden_channels * n_layers, 1)
            self.cond_layer = weight_norm(cond_layer, name="weight")
        for i in range(n_layers):
            dilation = dilation_rate ** i
            padding = int((kernel_size * dilation - dilation) / 2)
            in_layer = nn.Conv1d(hidden_channels, 2 * hidden_channels, kernel_size, dilation=dilation,
                                 padding=padding)
            self.in_layers.append(weight_norm(in_layer, name="weight"))
            # The last one has no residual half.
            res_skip_channels = 2 * hidden_channels if i < n_layers - 1 else hidden_channels
            res_skip_layer = nn.Conv1d(hidden_channels, res_skip_channels, 1)
            self.res_skip_layers.append(weight_norm(res_skip_layer, name="weight"))

    def forward(self, x, x_mask, g=None, **kwargs):
        output = torch.zeros_like(x)
        n_channels_tensor = torch.IntTensor([self.hidden_channels])
        if g is not None:
            g = self.cond_layer(g)
        for i in range(self.n_layers):
            x_in = self.in_layers[i](x)
            if g is not None:
                cond_offset = i * 2 * self.hidden_channels
                g_l = g[:, cond_offset:cond_offset + 2 * self.hidden_channels, :]
            else:
                g_l = torch.zeros_like(x_in)
            acts = fused_add_tanh_sigmoid_multiply(x_in, g_l, n_channels_tensor)
            acts = self.drop(acts)
            res_skip_acts = self.res_skip_layers[i](acts)
            if i < self.n_layers - 1:
                x = (x + res_skip_acts[:, :self.hidden_channels, :]) * x_mask
                output = output + res_skip_acts[:, self.hidden_channels:, :]
            else:
                output = output + res_skip_acts
        return output * x_mask


class ResBlock1(nn.Module):
    def __init__(self, channels, kernel_size=3, dilation=(1, 3, 5)):
        super().__init__()
        self.convs1 = nn.ModuleList([
            weight_norm(Conv1d(channels, channels, kernel_size, 1, dilation=d, padding=get_padding(kernel_size, d)))
            for d in dilation[:3]])
        self.convs1.apply(init_weights)
        self.convs2 = nn.ModuleList([
            weight_norm(Conv1d(channels, channels, kernel_size, 1, dilation=1, padding=get_padding(kernel_size, 1)))
            for _ in range(3)])
        self.convs2.apply(init_weights)

    def forward(self, x, x_mask=None):
        for c1, c2 in zip(self.convs1, self.convs2):
            xt = F.leaky_relu(x, LRELU_SLOPE)
            if x_mask is not None:
                xt = xt * x_mask
            xt = c1(xt)
            xt = F.leaky_relu(xt, LRELU_SLOPE)
            if x_mask is not None:
                xt = xt * x_mask
            xt = c2(xt)
            x = xt + x
        if x_mask is not None:
            x = x * x_mask
        return x

    def remove_weight_norm(self):
        for layer in [*self.convs1, *self.convs2]:
            remove_weight_norm(layer)


class ResBlock2(nn.Module):
    def __init__(self, channels, kernel_size=3, dilation=(1, 3)):
        super().__init__()
        self.convs = nn.ModuleList([
            weight_norm(Conv1d(channels, channels, kernel_size, 1, dilation=d, padding=get_padding(kernel_size, d)))
            for d in dilation[:2]])
        self.convs.apply(init_weights)

    def forward(self, x, x_mask=None):
        for c in self.convs:
            xt = F.leaky_relu(x, LRELU_SLOPE)
            if x_mask is not None:
                xt = xt * x_mask
            x = c(xt) + x
        if x_mask is not None:
            x = x * x_mask
        return x

    def remove_weight_norm(self):
        for layer in self.convs:
            remove_weight_norm(layer)


class Flip(nn.Module):
    def forward(self, x, *args, reverse=False, **kwargs):
        x = torch.flip(x, [1])
        if not reverse:
            return x, torch.zeros(x.size(0)).to(dtype=x.dtype, device=x.device)
        return x


class ResidualCouplingLayer(nn.Module):
    def __init__(self, channels, hidden_channels, kernel_size, dilation_rate, n_layers, p_dropout=0,
                 gin_channels=0, mean_only=False):
        assert channels % 2 == 0, "channels should be divisible by 2"
        super().__init__()
        self.channels = channels
        self.hidden_channels = hidden_channels
        self.kernel_size = kernel_size
        self.dilation_rate = dilation_rate
        self.n_layers = n_layers
        self.half_channels = channels // 2
        self.mean_only = mean_only
        self.pre = nn.Conv1d(self.half_channels, hidden_channels, 1)
        self.enc = WN(hidden_channels, kernel_size, dilation_rate, n_layers, p_dropout=p_dropout,
                      gin_channels=gin_channels)
        self.post = nn.Conv1d(hidden_channels, self.half_channels * (2 - mean_only), 1)
        self.post.weight.data.zero_()
        self.post.bias.data.zero_()

    def forward(self, x, x_mask, g=None, reverse=False):
        x0, x1 = torch.split(x, [self.half_channels] * 2, 1)
        h = self.pre(x0) * x_mask
        h = self.enc(h, x_mask, g=g)
        stats = self.post(h) * x_mask
        if not self.mean_only:
            m, logs = torch.split(stats, [self.half_channels] * 2, 1)
        else:
            m = stats
            logs = torch.zeros_like(m)
        if not reverse:
            x1 = m + x1 * torch.exp(logs) * x_mask
            return torch.cat([x0, x1], 1), torch.sum(logs, [1, 2])
        x1 = (x1 - m) * torch.exp(-logs) * x_mask
        return torch.cat([x0, x1], 1)


# ── models.py ────────────────────────────────────────────────────────────────

class PosteriorEncoder(nn.Module):
    def __init__(self, in_channels, out_channels, hidden_channels, kernel_size, dilation_rate, n_layers,
                 gin_channels=0):
        super().__init__()
        self.out_channels = out_channels
        self.pre = nn.Conv1d(in_channels, hidden_channels, 1)
        self.enc = WN(hidden_channels, kernel_size, dilation_rate, n_layers, gin_channels=gin_channels)
        self.proj = nn.Conv1d(hidden_channels, out_channels * 2, 1)

    def forward(self, x, x_lengths, g=None, tau=1.0):
        x_mask = torch.unsqueeze(sequence_mask(x_lengths, x.size(2)), 1).to(x.dtype)
        x = self.pre(x) * x_mask
        x = self.enc(x, x_mask, g=g)
        stats = self.proj(x) * x_mask
        m, logs = torch.split(stats, self.out_channels, dim=1)
        z = (m + torch.randn_like(m) * tau * torch.exp(logs)) * x_mask
        return z, m, logs, x_mask


class Generator(nn.Module):
    def __init__(self, initial_channel, resblock, resblock_kernel_sizes, resblock_dilation_sizes,
                 upsample_rates, upsample_initial_channel, upsample_kernel_sizes, gin_channels=0):
        super().__init__()
        self.num_kernels = len(resblock_kernel_sizes)
        self.num_upsamples = len(upsample_rates)
        self.conv_pre = Conv1d(initial_channel, upsample_initial_channel, 7, 1, padding=3)
        block = ResBlock1 if resblock == "1" else ResBlock2
        self.ups = nn.ModuleList()
        for i, (u, k) in enumerate(zip(upsample_rates, upsample_kernel_sizes)):
            self.ups.append(weight_norm(ConvTranspose1d(upsample_initial_channel // (2 ** i),
                                                        upsample_initial_channel // (2 ** (i + 1)),
                                                        k, u, padding=(k - u) // 2)))
        self.resblocks = nn.ModuleList()
        ch = upsample_initial_channel
        for i in range(len(self.ups)):
            ch = upsample_initial_channel // (2 ** (i + 1))
            for k, d in zip(resblock_kernel_sizes, resblock_dilation_sizes):
                self.resblocks.append(block(ch, k, d))
        self.conv_post = Conv1d(ch, 1, 7, 1, padding=3, bias=False)
        self.ups.apply(init_weights)
        if gin_channels != 0:
            self.cond = nn.Conv1d(gin_channels, upsample_initial_channel, 1)

    def forward(self, x, g=None):
        x = self.conv_pre(x)
        if g is not None:
            x = x + self.cond(g)
        for i in range(self.num_upsamples):
            x = F.leaky_relu(x, LRELU_SLOPE)
            x = self.ups[i](x)
            xs = None
            for j in range(self.num_kernels):
                out = self.resblocks[i * self.num_kernels + j](x)
                xs = out if xs is None else xs + out
            x = xs / self.num_kernels
        x = F.leaky_relu(x)
        x = self.conv_post(x)
        return torch.tanh(x)


class ReferenceEncoder(nn.Module):
    """Spectrogram frames [N, Ty, n_freqs] to one speaker embedding [N, gin]."""

    def __init__(self, spec_channels, gin_channels=0, layernorm=True):
        super().__init__()
        self.spec_channels = spec_channels
        ref_enc_filters = [32, 32, 64, 64, 128, 128]
        K = len(ref_enc_filters)
        filters = [1] + ref_enc_filters
        self.convs = nn.ModuleList([
            weight_norm(nn.Conv2d(in_channels=filters[i], out_channels=filters[i + 1], kernel_size=(3, 3),
                                  stride=(2, 2), padding=(1, 1)))
            for i in range(K)])
        out_channels = self.calculate_channels(spec_channels, 3, 2, 1, K)
        self.gru = nn.GRU(input_size=ref_enc_filters[-1] * out_channels, hidden_size=256 // 2, batch_first=True)
        self.proj = nn.Linear(128, gin_channels)
        self.layernorm = nn.LayerNorm(self.spec_channels) if layernorm else None

    def forward(self, inputs, mask=None):
        N = inputs.size(0)
        out = inputs.view(N, 1, -1, self.spec_channels)
        if self.layernorm is not None:
            out = self.layernorm(out)
        for conv in self.convs:
            out = F.relu(conv(out))
        out = out.transpose(1, 2)
        T = out.size(1)
        N = out.size(0)
        out = out.contiguous().view(N, T, -1)
        self.gru.flatten_parameters()
        _, out = self.gru(out)
        return self.proj(out.squeeze(0))

    @staticmethod
    def calculate_channels(L, kernel_size, stride, pad, n_convs):
        for _ in range(n_convs):
            L = (L - kernel_size + 2 * pad) // stride + 1
        return L


class ResidualCouplingBlock(nn.Module):
    def __init__(self, channels, hidden_channels, kernel_size, dilation_rate, n_layers, n_flows=4,
                 gin_channels=0):
        super().__init__()
        self.flows = nn.ModuleList()
        for _ in range(n_flows):
            self.flows.append(ResidualCouplingLayer(channels, hidden_channels, kernel_size, dilation_rate,
                                                    n_layers, gin_channels=gin_channels, mean_only=True))
            self.flows.append(Flip())

    def forward(self, x, x_mask, g=None, reverse=False):
        if not reverse:
            for flow in self.flows:
                x, _ = flow(x, x_mask, g=g, reverse=reverse)
        else:
            for flow in reversed(self.flows):
                x = flow(x, x_mask, g=g, reverse=reverse)
        return x


class ToneColorModel(nn.Module):
    """SynthesizerTrn with ``n_speakers=0``: the converter, nothing else."""

    def __init__(self, spec_channels, inter_channels, hidden_channels, resblock, resblock_kernel_sizes,
                 resblock_dilation_sizes, upsample_rates, upsample_initial_channel, upsample_kernel_sizes,
                 gin_channels=256, zero_g=False, **_: Any):
        super().__init__()
        self.dec = Generator(inter_channels, resblock, resblock_kernel_sizes, resblock_dilation_sizes,
                             upsample_rates, upsample_initial_channel, upsample_kernel_sizes,
                             gin_channels=gin_channels)
        self.enc_q = PosteriorEncoder(spec_channels, inter_channels, hidden_channels, 5, 1, 16,
                                      gin_channels=gin_channels)
        self.flow = ResidualCouplingBlock(inter_channels, hidden_channels, 5, 1, 4, gin_channels=gin_channels)
        self.ref_enc = ReferenceEncoder(spec_channels, gin_channels)
        self.zero_g = zero_g

    def voice_conversion(self, y, y_lengths, sid_src, sid_tgt, tau=1.0):
        g_src, g_tgt = sid_src, sid_tgt
        z, _, _, y_mask = self.enc_q(y, y_lengths, g=torch.zeros_like(g_src) if self.zero_g else g_src, tau=tau)
        z_p = self.flow(z, y_mask, g=g_src)
        z_hat = self.flow(z_p, y_mask, g=g_tgt, reverse=True)
        o_hat = self.dec(z_hat * y_mask, g=torch.zeros_like(g_tgt) if self.zero_g else g_tgt)
        return o_hat, y_mask, (z, z_p, z_hat)


class ToneColorConverter:
    """A converter directory (``config.json`` and ``checkpoint.pth``) loaded
    on ``device``. Audio in and out is float32 mono at :attr:`rate`."""

    def __init__(self, path: Path, device: str = "cpu") -> None:
        cfg = json.loads((Path(path) / "config.json").read_text(encoding="utf-8"))
        data = cfg["data"]
        self.rate = int(data["sampling_rate"])
        self.n_fft = int(data["filter_length"])
        self.hop = int(data["hop_length"])
        self.win = int(data["win_length"])
        self.device = device
        self.model = ToneColorModel(self.n_fft // 2 + 1, **cfg["model"]).to(device).eval()
        ckpt = torch.load(Path(path) / "checkpoint.pth", map_location="cpu", weights_only=True)
        missing, unexpected = self.model.load_state_dict(ckpt["model"], strict=False)
        # The checkpoint also carries layers of the full model (a text
        # encoder in v1); only the converter's own must all be there.
        if missing:
            raise RuntimeError(f"the converter checkpoint lacks {len(missing)} weights, e.g. {missing[:3]}")
        self.version = str(cfg.get("_version_") or "v1")

    def _spec(self, audio: Any) -> torch.Tensor:
        y = torch.as_tensor(audio, dtype=torch.float32, device=self.device).unsqueeze(0)
        return spectrogram_torch(y, self.n_fft, self.rate, self.hop, self.win, center=False)

    @torch.no_grad()
    def embed(self, audio: Any) -> torch.Tensor:
        """The tone color of ``audio``: [1, gin, 1]."""
        return self.model.ref_enc(self._spec(audio).transpose(1, 2)).unsqueeze(-1)

    @torch.no_grad()
    def convert(self, audio: Any, src: torch.Tensor, tgt: torch.Tensor, tau: float = 0.3) -> Any:
        spec = self._spec(audio)
        lengths = torch.LongTensor([spec.size(-1)]).to(self.device)
        out = self.model.voice_conversion(spec, lengths, sid_src=src.to(self.device), sid_tgt=tgt.to(self.device),
                                          tau=tau)[0]
        return out[0, 0].float().cpu().numpy()
