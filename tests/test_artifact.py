import struct

import pytest

import artifact
from artifact import TagEntry, TagSet, encode, decode


def sample_tagset() -> TagSet:
    return TagSet(
        threshold=25,
        tags=(
            TagEntry("1girl", 0, 100, False),
            TagEntry("blue_hair", 0, 90, False),
            TagEntry("hatsune_miku", 4, 80, True),
        ),
        aliases=("blu_hair", "miku"),
        alias_target=(1, 2),
    )


def test_roundtrip_preserves_every_field():
    original = sample_tagset()
    assert decode(encode(original)).to_tagset() == original


def test_header_layout_is_stable():
    data = encode(sample_tagset())
    assert data[:4] == b"DTA1"
    assert len(data) >= artifact.HEADER_SIZE == 64
    _, version, flags = struct.unpack_from("<4sHH", data, 0)
    assert version == 1
    assert flags & 1


def test_section_offsets_are_4_byte_aligned():
    data = encode(sample_tagset())
    off_names = struct.unpack_from("<I", data, 20)[0]
    assert off_names == 64


def test_deprecated_flag_is_per_tag():
    decoded = decode(encode(sample_tagset()))
    assert [decoded.deprecated(i) for i in range(decoded.n_tags)] == [False, False, True]


def test_encode_rejects_unsorted_tags():
    bad = TagSet(threshold=0, tags=(TagEntry("b", 0, 1, False), TagEntry("a", 0, 1, False)), aliases=(), alias_target=())
    with pytest.raises(ValueError, match="sorted"):
        encode(bad)


def test_encode_rejects_alias_target_out_of_range():
    bad = TagSet(threshold=0, tags=(TagEntry("a", 0, 1, False),), aliases=("b",), alias_target=(1,))
    with pytest.raises(ValueError, match="out of range"):
        encode(bad)


def test_decode_rejects_bad_magic():
    data = bytearray(encode(sample_tagset()))
    data[0:4] = b"XXXX"
    with pytest.raises(ValueError, match="magic"):
        decode(bytes(data))


def test_decode_rejects_unaligned_section():
    data = bytearray(encode(sample_tagset()))
    struct.pack_into("<I", data, 20, 65)
    with pytest.raises(ValueError, match="aligned"):
        decode(bytes(data))


def test_decode_rejects_truncated_buffer():
    data = encode(sample_tagset())
    with pytest.raises(ValueError):
        decode(data[: artifact.HEADER_SIZE + 1])


def test_decode_rejects_unsorted_names():
    data = bytearray(encode(sample_tagset()))
    off_names = struct.unpack_from("<I", data, 20)[0]
    data[off_names:off_names + 5] = b"zzzzz"
    with pytest.raises(ValueError, match="sorted"):
        decode(bytes(data))
