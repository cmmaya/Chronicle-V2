"""BU135: text transcript file parser."""
import pytest

from src.transcription.text_import import (
    MAX_TRANSCRIPT_BYTES,
    TranscriptImportError,
    read_transcript_paragraphs,
)


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize('data', [
    'Reunión de diseño\n\nAcción: revisar'.encode('utf-8'),
    'Reunión de diseño\n\nAcción: revisar'.encode('utf-8-sig'),
    'Reunión de diseño\n\nAcción: revisar'.encode('cp1252'),
], ids=['utf8', 'utf8-bom', 'cp1252'])
def test_encodings_decode(tmp_path, data):
    path = _write(tmp_path, 't.txt', data)
    assert read_transcript_paragraphs(path) == ['Reunión de diseño', 'Acción: revisar']


def test_paragraph_splitting(tmp_path):
    text = 'Ana: hola  \r\nLuis: hey\r\n\r\n\r\n  \r\nAna: next topic\r\n\r\n'
    path = _write(tmp_path, 't.md', text.encode('utf-8'))
    assert read_transcript_paragraphs(path) == ['Ana: hola\nLuis: hey', 'Ana: next topic']


@pytest.mark.parametrize('name,data', [
    ('empty.txt', b''),
    ('blank.txt', b'  \r\n\n\t\n'),
    ('binary.txt', b'abc\x00def'),
    ('bad.txt', b'\x81\x8d\x8f'),
    ('big.txt', b'a' * (MAX_TRANSCRIPT_BYTES + 1)),
    ('doc.pdf', b'%PDF-1.4 text'),
], ids=['empty', 'blank', 'binary', 'undecodable', 'oversized', 'pdf'])
def test_rejections(tmp_path, name, data):
    path = _write(tmp_path, name, data)
    with pytest.raises(TranscriptImportError, match=name):
        read_transcript_paragraphs(path)


def test_missing_file(tmp_path):
    with pytest.raises(TranscriptImportError, match="doesn't exist"):
        read_transcript_paragraphs(tmp_path / 'nope.txt')
