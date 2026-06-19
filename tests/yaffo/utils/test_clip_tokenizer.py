"""The vendored CLIP tokenizer must reproduce OpenAI CLIP's exact token ids — the
ViT-B-32 text encoder was trained on them. These pin a few known encodings and the
framing/padding contract.
"""
import numpy as np
import pytest

from yaffo.utils.clip_tokenizer import CONTEXT_LENGTH, tokenize

pytestmark = pytest.mark.unit

SOT, EOT = 49406, 49407


def test_canonical_token_ids():
    # Reference ids from OpenAI CLIP's tokenizer for "a photo of a dog".
    row = tokenize(["a photo of a dog"])[0]
    assert row[:7].tolist() == [SOT, 320, 1125, 539, 320, 1929, EOT]


def test_shape_and_padding():
    out = tokenize(["dog", "a cat on a mat"])
    assert out.shape == (2, CONTEXT_LENGTH)
    assert out.dtype == np.int32
    # first row is sot, dog, eot, then zero padding
    assert out[0, 0] == SOT and out[0, 2] == EOT
    assert out[0, 3:].sum() == 0


def test_framed_by_sot_eot():
    row = tokenize(["beach"])[0]
    assert row[0] == SOT
    assert EOT in row.tolist()


def test_long_text_truncated_with_eot_preserved():
    row = tokenize([" ".join(["word"] * 200)])[0]
    assert len(row) == CONTEXT_LENGTH
    assert row[0] == SOT
    assert row[-1] == EOT  # truncation keeps the end token
