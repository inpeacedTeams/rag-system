"""Actual Qdrant integration, opt-in through QDRANT_TEST_URL. No OpenAI calls."""
import os
import time
import unittest
import uuid
from app.config import Settings
from app.documents import Chunk
from app.store import VectorStore
from app.providers import ProviderError


@unittest.skipUnless(os.getenv('QDRANT_TEST_URL'), 'QDRANT_TEST_URL not set')
class QdrantIntegrationTests(unittest.TestCase):
    def test_upsert_search_scroll_delete_isolation(self):
        store = VectorStore(Settings(qdrant_url=os.environ['QDRANT_TEST_URL'], collection='ci_' + uuid.uuid4().hex,
                                     embedding_dimensions=3, min_score=0.1))
        for attempt in range(30):
            try:
                store.ensure_collection()
                break
            except ProviderError:
                if attempt == 29:
                    raise
                time.sleep(1)
        try:
            for workspace in ['a', 'b']:
                doc = {'document_id': str(uuid.uuid4()), 'name': workspace + '.txt', 'kind': 'txt', 'url': None,
                       'created_at': '2026-01-01T00:00:00Z', 'chunk_count': 1}
                store.add(workspace, doc, [Chunk('Тестовый документ ' + workspace, None, 1)], [[1.0, 0.0, 0.0]])
            self.assertEqual(len(store.documents('a')), 1)
            self.assertEqual(len(store.documents('b')), 1)
            results = store.search('a', [1.0, 0.0, 0.0])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['payload']['workspace'], 'a')
            store.clear('a')
            self.assertEqual(store.documents('a'), [])
            self.assertEqual(len(store.documents('b')), 1)
            self.assertEqual(store.search('a', [1.0, 0.0, 0.0]), [])
        finally:
            store.request('DELETE', store.root)
