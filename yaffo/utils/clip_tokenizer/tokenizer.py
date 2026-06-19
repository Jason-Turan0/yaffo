"""Vendored CLIP byte-pair tokenizer (OpenAI CLIP / open_clip).

Self-contained so the label classifier needs no torch/transformers/ftfy/regex —
it pairs with the ViT-B-32 ONNX text encoder, producing the exact token ids that
model was trained on. The byte-level BPE merges come from the canonical
``bpe_simple_vocab_16e6.txt.gz`` shipped beside this file.

The original implementation matches words with a ``\\p{L}/\\p{N}`` pattern via the
third-party ``regex`` module; we use stdlib ``re`` on the already-lowercased text,
which is byte-for-byte identical for the ASCII English the label vocabulary uses.
"""
import gzip
import html
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

_BPE_PATH = Path(__file__).resolve().parent / "bpe_simple_vocab_16e6.txt.gz"

CONTEXT_LENGTH = 77


@lru_cache()
def _bytes_to_unicode() -> dict[int, str]:
    """Reversible map from the 256 byte values to printable unicode chars (CLIP's
    byte-level BPE alphabet), so every byte is a usable BPE symbol."""
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(2 ** 8):
        if b not in bs:
            bs.append(b)
            cs.append(2 ** 8 + n)
            n += 1
    return dict(zip(bs, (chr(c) for c in cs)))


def _get_pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    return {(word[i], word[i + 1]) for i in range(len(word) - 1)}


def _basic_clean(text: str) -> str:
    return html.unescape(html.unescape(text)).strip()


def _whitespace_clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class SimpleTokenizer:
    def __init__(self, bpe_path: Path = _BPE_PATH):
        self.byte_encoder = _bytes_to_unicode()
        merges = gzip.open(bpe_path).read().decode("utf-8").split("\n")
        merges = merges[1 : 49152 - 256 - 2 + 1]
        merges = [tuple(merge.split()) for merge in merges]
        vocab = list(_bytes_to_unicode().values())
        vocab = vocab + [v + "</w>" for v in vocab]
        for merge in merges:
            vocab.append("".join(merge))
        vocab.extend(["<|startoftext|>", "<|endoftext|>"])
        self.encoder = dict(zip(vocab, range(len(vocab))))
        self.bpe_ranks = dict(zip(merges, range(len(merges))))
        self.cache = {
            "<|startoftext|>": "<|startoftext|>",
            "<|endoftext|>": "<|endoftext|>",
        }
        # Operates on lowercased text, so only [a-z] (not \p{L}) is needed.
        self.pat = re.compile(
            r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[a-z]+|[0-9]|[^\sa-z0-9]+"
        )
        self.sot_token = self.encoder["<|startoftext|>"]
        self.eot_token = self.encoder["<|endoftext|>"]

    def _bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _get_pairs(word)
        if not pairs:
            return token + "</w>"
        while True:
            bigram = min(pairs, key=lambda pair: self.bpe_ranks.get(pair, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new_word: list[str] = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new_word.extend(word[i:])
                    break
                new_word.extend(word[i:j])
                i = j
                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new_word.append(first + second)
                    i += 2
                else:
                    new_word.append(word[i])
                    i += 1
            word = tuple(new_word)
            if len(word) == 1:
                break
            pairs = _get_pairs(word)
        result = " ".join(word)
        self.cache[token] = result
        return result

    def encode(self, text: str) -> list[int]:
        bpe_tokens: list[int] = []
        text = _whitespace_clean(_basic_clean(text)).lower()
        for token in re.findall(self.pat, text):
            token = "".join(self.byte_encoder[b] for b in token.encode("utf-8"))
            bpe_tokens.extend(self.encoder[bpe] for bpe in self._bpe(token).split(" "))
        return bpe_tokens


_tokenizer: SimpleTokenizer | None = None


def _get_tokenizer() -> SimpleTokenizer:
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = SimpleTokenizer()
    return _tokenizer


def tokenize(texts: list[str], context_length: int = CONTEXT_LENGTH) -> np.ndarray:
    """Tokenize texts to an (N, context_length) int32 array, framed by the
    start/end tokens and zero-padded — the shape the ViT-B-32 text encoder wants.
    Over-long sequences are truncated with the end token preserved."""
    tok = _get_tokenizer()
    result = np.zeros((len(texts), context_length), dtype=np.int32)
    for i, text in enumerate(texts):
        tokens = [tok.sot_token, *tok.encode(text), tok.eot_token]
        if len(tokens) > context_length:
            tokens = tokens[:context_length]
            tokens[-1] = tok.eot_token
        result[i, : len(tokens)] = tokens
    return result
