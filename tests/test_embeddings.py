import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from app.config import Settings
from app.documents import Chunk, DocumentError
from app.embeddings import LocalEmbeddings
from app.providers import ProviderError


class CharacterTokenizer:
    """Unit-test tokenizer only; production always uses the actual model tokenizer."""
    def encode(self, text, add_special_tokens=False):
        return SimpleNamespace(ids=list(range(len(text))), offsets=[(i, i + 1) for i in range(len(text))])


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.client = LocalEmbeddings(Settings(embedding_dimensions=3))
        self.client._model = Mock()
        self.client._tokenizer = CharacterTokenizer()

    def test_document_embeddings(self):
        self.client._model.passage_embed.return_value = iter([np.array([1., 2., 3.])])
        self.assertEqual(self.client.embeddings(['text']), [[1., 2., 3.]])
        self.client._model.query_embed.assert_not_called()

    def test_query_embeddings(self):
        self.client._model.query_embed.return_value = iter([np.array([1., 0., 0.])])
        self.assertEqual(self.client.embeddings(['question'], query=True), [[1., 0., 0.]])
        self.client._model.passage_embed.assert_not_called()

    def test_bad_dimensions(self):
        self.client._model.passage_embed.return_value = iter([np.array([1.])])
        with self.assertRaises(ProviderError):
            self.client.embeddings(['text'])

    def test_non_finite_vectors(self):
        self.client._model.passage_embed.return_value = iter([np.array([1., float('nan'), 0.])])
        with self.assertRaises(ProviderError):
            self.client.embeddings(['text'])

    def test_prepare_chunks_preserves_page_and_text(self):
        text = 'я' * 1200
        chunks = self.client.prepare_chunks([Chunk(text, 7, 1)])
        self.assertEqual([c.number for c in chunks], list(range(1, len(chunks) + 1)))
        self.assertTrue(all(c.page == 7 for c in chunks))
        self.assertTrue(all(len(c.text) <= 480 for c in chunks))
        self.assertEqual(sum(len(c.text) for c in chunks) - (len(chunks) - 1) * 60, len(text))

    def test_question_too_long(self):
        with self.assertRaises(DocumentError):
            self.client.embeddings(['x' * 481], query=True)

    def test_empty(self):
        self.assertEqual(self.client.embeddings([]), [])


@unittest.skipUnless(os.getenv('RUN_EMBEDDING_INTEGRATION'), 'RUN_EMBEDDING_INTEGRATION not set')
class RealEmbeddingTests(unittest.TestCase):
    def test_real_multilingual_model(self):
        with tempfile.TemporaryDirectory() as cache:
            client = LocalEmbeddings(Settings(embedding_cache_dir=cache))
            chunks = client.prepare_chunks([Chunk('Срок оплаты составляет десять рабочих дней.', 1, 1)])
            document = np.array(client.embeddings([chunks[0].text])[0])
            question = np.array(client.embeddings([chunks[0].text], query=True)[0])
            self.assertEqual(len(document), 384)
            self.assertTrue(np.isfinite(document).all())
            self.assertGreater(float(np.dot(document, question) / (np.linalg.norm(document) * np.linalg.norm(question))), 0.99)
            long_chunks = client.prepare_chunks([Chunk('сложное слово ' * 500, 2, 1)])
            self.assertTrue(len(long_chunks) > 1)
            self.assertEqual(len(client.embeddings([c.text for c in long_chunks])), len(long_chunks))
