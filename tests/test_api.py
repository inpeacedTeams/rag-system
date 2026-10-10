import unittest
from dataclasses import replace
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from app import main


class APITests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.documents.return_value = []
        self.llm = Mock()
        self.embedder = Mock()
        self.embedder.prepare_chunks.side_effect = lambda chunks: chunks
        self.patches = [patch.object(main, 'store', self.store), patch.object(main, 'llm', self.llm), patch.object(main, 'embedder', self.embedder),
                        patch.object(main, 'settings', replace(main.settings, session_secret='test-only-secret-' * 4))]
        for item in self.patches:
            item.start()
        with main.rate_lock:
            main.rate_history.clear()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.client.get('/api/status')
        self.headers = {'X-Requested-With': 'DocumentRAG'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()

    def test_home(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Источник', response.text)
        self.assertIn("script-src 'self'", response.headers['content-security-policy'])

    def test_head_home(self):
        response = self.client.head('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')

    def test_empty_chat(self):
        response = self.client.post('/api/chat', json={'question': 'Что в документе?'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['answer'], main.NO_INFORMATION)
        self.embedder.embeddings.assert_not_called()

    def test_missing_csrf_header(self):
        self.assertEqual(self.client.delete('/api/documents').status_code, 403)
        self.store.clear.assert_not_called()

    def test_cross_origin(self):
        response = self.client.delete('/api/documents', headers={**self.headers, 'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        self.store.clear.assert_not_called()

    def test_signed_cookie(self):
        cookie = self.client.cookies.get('rag_session')
        self.assertIsNotNone(main.read_session(cookie))
        self.assertIsNone(main.read_session(cookie + 'tampered'))
        self.assertIsNone(main.read_session('bad.cookie'))
        self.assertIsNone(main.read_session('a' * 32 + '.невалидная'))

    def test_clear_session_isolation(self):
        ws = main.read_session(self.client.cookies.get('rag_session'))
        response = self.client.delete('/api/documents', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.store.clear.assert_called_once_with(ws)
        with TestClient(main.app) as other:
            other.get('/api/status')
            self.assertNotEqual(ws, main.read_session(other.cookies.get('rag_session')))

    def test_upload_real_txt_parsing(self):
        self.embedder.embeddings.return_value = [{'indices': [1], 'values': [1.0]}]
        response = self.client.post('/api/documents/upload', files={'file': ('test.txt', 'Договор действует один год.'.encode(), 'text/plain')}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['document']['chunk_count'], 1)
        self.assertEqual(self.store.add.call_args.args[2][0].text, 'Договор действует один год.')

    def test_invalid_upload(self):
        response = self.client.post('/api/documents/upload', files={'file': ('test.exe', b'bad')}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.store.add.assert_not_called()

    def test_ingestion_busy(self):
        main.ingestion_lock.acquire()
        try:
            response = self.client.post('/api/documents/upload', files={'file': ('a.txt', b'document')}, headers=self.headers)
            self.assertEqual(response.status_code, 429)
        finally:
            main.ingestion_lock.release()

    def test_empty_query_vector(self):
        self.store.documents.return_value = [{'document_id': 'd'}]
        self.embedder.embeddings.return_value = [{'indices': [], 'values': []}]
        response = self.client.post('/api/chat', json={'question': 'и в на'}, headers=self.headers)
        self.assertFalse(response.json()['found'])
        self.store.search.assert_not_called()

    def test_status_limits(self):
        response = self.client.get('/api/status')
        self.assertEqual(response.json()['retrieval_mode'], 'bm25')
        self.assertEqual(response.json()['max_upload_mb'], 5)

    def test_empty_question(self):
        response = self.client.post('/api/chat', json={'question': '   '}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_body_limit(self):
        response = self.client.post('/api/chat', content=b'x' * 40000, headers={**self.headers, 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    def test_answer_and_verification(self):
        self.store.documents.return_value = [{'document_id': 'd'}]
        self.embedder.embeddings.return_value = [{'indices': [1], 'values': [1.0]}]
        self.store.search.return_value = [{'payload': {'document_id': 'd', 'name': 'a.txt', 'text': 'Срок оплаты десять дней.', 'chunk': 1, 'page': None}}]
        self.llm.answer.return_value = {'found': True, 'claims': [{'source_id': 1, 'text': 'Срок оплаты десять дней.', 'quote': 'Срок оплаты десять дней.'}]}
        self.llm.verify.return_value = True
        response = self.client.post('/api/chat', json={'question': 'Какой срок оплаты?'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['found'])
        self.assertEqual(response.json()['sources'][0]['name'], 'a.txt')
        self.llm.verify.assert_called_once()

    def test_verifier_fail_closed(self):
        self.store.documents.return_value = [{'document_id': 'd'}]
        self.embedder.embeddings.return_value = [{'indices': [1], 'values': [1.0]}]
        self.store.search.return_value = [{'payload': {'document_id': 'd', 'name': 'a.txt', 'text': 'Срок оплаты десять дней.', 'chunk': 1}}]
        self.llm.answer.return_value = {'found': True, 'claims': [{'source_id': 1, 'text': 'Срок оплаты сто дней.', 'quote': 'Срок оплаты десять дней.'}]}
        self.llm.verify.return_value = False
        response = self.client.post('/api/chat', json={'question': 'Какой срок оплаты?'}, headers=self.headers)
        self.assertFalse(response.json()['found'])
        self.assertEqual(response.json()['sources'], [])

    def test_rate_limit(self):
        for _ in range(20):
            self.assertEqual(self.client.post('/api/chat', json={'question': 'q'}, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post('/api/chat', json={'question': 'q'}, headers=self.headers).status_code, 429)
