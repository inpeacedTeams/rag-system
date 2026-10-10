import io
import json
import unittest
from unittest.mock import patch, Mock
from pypdf import PdfWriter
from app.config import Settings
from app.documents import chunk_pages, extract_file, normalize, DocumentError
from app.rag import build_response, NO_INFORMATION
from app.web import validate_url, public_addresses, fetch_page
from app.store import VectorStore
from app.providers import OpenAIClient, ProviderError


class DocumentTests(unittest.TestCase):
    def test_txt_bom_unicode(self):
        self.assertEqual(extract_file('документ.TXT', b'\xef\xbb\xbf' + 'Тестовый текст'.encode())[0].text, 'Тестовый текст')

    def test_invalid_encoding(self):
        with self.assertRaises(DocumentError):
            extract_file('a.txt', b'\xff')

    def test_empty(self):
        with self.assertRaises(DocumentError):
            extract_file('a.txt', b'  \n')

    def test_unsupported(self):
        with self.assertRaises(DocumentError):
            extract_file('a.docx', b'data')

    def test_fake_pdf(self):
        with self.assertRaises(DocumentError):
            extract_file('a.pdf', b'not a pdf')

    def test_scanned_pdf(self):
        writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO(); writer.write(buffer)
        with self.assertRaisesRegex(DocumentError, 'OCR'):
            extract_file('scan.pdf', buffer.getvalue())

    def test_pdf_page_limit(self):
        writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO(); writer.write(buffer)
        with self.assertRaisesRegex(DocumentError, 'страниц'):
            extract_file('a.pdf', buffer.getvalue(), max_pages=0)

    def test_chunk_pages_and_size(self):
        chunks = chunk_pages([(1, 'первая ' * 800), (2, 'вторая ' * 800)])
        self.assertEqual([c.number for c in chunks], list(range(1, len(chunks) + 1)))
        self.assertEqual({c.page for c in chunks}, {1, 2})
        self.assertTrue(all(len(c.text) <= 1400 for c in chunks))
        self.assertTrue(all(not ('первая' in c.text and 'вторая' in c.text) for c in chunks))

    def test_chunk_progress(self):
        chunks = chunk_pages([(None, 'x' * 5000)])
        self.assertLess(len(chunks), 10)
        self.assertEqual(chunks[-1].text[-1], 'x')

    def test_invalid_chunk_settings(self):
        with self.assertRaises(ValueError):
            chunk_pages([(None, 'a')], size=20, overlap=20)

    def test_normalize(self):
        self.assertEqual(normalize('  a\n b\x00c '), 'a b c')


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.hits = [{'payload': {'document_id': 'doc1', 'name': 'contract.pdf', 'page': 3,
                                 'chunk': 5, 'url': None, 'text': 'Срок оплаты составляет десять рабочих дней.'}}]
        self.claim = {'text': 'Срок оплаты: десять рабочих дней.', 'source_id': 1, 'quote': 'Срок оплаты составляет десять рабочих дней.'}

    def build(self, claim=None):
        return build_response({'found': True, 'claims': [claim or self.claim]}, self.hits)

    def test_valid(self):
        result = self.build()
        self.assertTrue(result['found'])
        self.assertEqual(result['sources'][0]['page'], 3)
        self.assertEqual(result['sources'][0]['chunk'], 5)
        self.assertIn('[1]', result['answer'])

    def test_missing_answer(self):
        result = build_response({'found': False, 'claims': []}, self.hits)
        self.assertEqual(result['answer'], NO_INFORMATION)
        self.assertEqual(result['sources'], [])

    def test_fabricated_quote(self):
        result = self.build({**self.claim, 'quote': 'Срок оплаты составляет сто дней.'})
        self.assertFalse(result['found'])

    def test_fabricated_source(self):
        self.assertFalse(self.build({**self.claim, 'source_id': 4})['found'])

    def test_boolean_not_integer_id(self):
        self.assertFalse(self.build({**self.claim, 'source_id': True})['found'])

    def test_empty_claims(self):
        self.assertFalse(build_response({'found': True, 'claims': []}, self.hits)['found'])

    def test_short_quote(self):
        self.assertFalse(self.build({**self.claim, 'quote': 'Срок'})['found'])

    def test_one_invalid_claim_rejects_all(self):
        result = build_response({'found': True, 'claims': [self.claim, {**self.claim, 'source_id': 99}]}, self.hits)
        self.assertFalse(result['found'])


class URLSecurityTests(unittest.TestCase):
    def test_invalid_urls(self):
        for url in ['file:///etc/passwd', 'ftp://example.org', 'http://a:b@example.org', 'https://example.org:8000', 'http://example.org\n']:
            with self.subTest(url=url), self.assertRaises(DocumentError):
                validate_url(url)

    def test_unicode_path(self):
        self.assertEqual(validate_url('https://example.org/тест')[3], '/%D1%82%D0%B5%D1%81%D1%82')

    def test_private_addresses(self):
        for ip in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '192.168.1.2', '::1', 'fc00::1', '0.0.0.0', '224.0.0.1', '::ffff:8.8.8.8']:
            with self.subTest(ip=ip), patch('app.web.socket.getaddrinfo', return_value=[(2, 1, 6, '', (ip, 80))]), self.assertRaises(DocumentError):
                public_addresses('host', 80)

    @patch('app.web.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))])
    def test_public_address(self, resolver):
        self.assertEqual(public_addresses('example.org', 443), ['8.8.8.8'])

    @patch('app.web.PinnedConnection')
    @patch('app.web.public_addresses', return_value=['8.8.8.8'])
    def test_html_removes_scripts(self, addresses, connection):
        response = Mock(status=200)
        response.getheader.side_effect = lambda key, default=None: {'Content-Type': 'text/html', 'Content-Encoding': 'identity'}.get(key, default)
        response.read.return_value = b'<title>Test</title><main><p>Useful document content.</p><script>malicious script</script></main><nav>menu</nav>'
        connection.return_value.getresponse.return_value = response
        title, url, chunks = fetch_page('https://example.org')
        self.assertEqual(title, 'Test')
        self.assertEqual(chunks[0].text, 'Useful document content.')

    @patch('app.web.PinnedConnection')
    @patch('app.web.public_addresses', side_effect=[['8.8.8.8'], DocumentError('blocked')])
    def test_redirect_is_revalidated(self, addresses, connection):
        response = Mock(status=302)
        response.getheader.return_value = 'http://127.0.0.1/private'
        connection.return_value.getresponse.return_value = response
        with self.assertRaises(DocumentError):
            fetch_page('https://example.org')
        self.assertEqual(addresses.call_count, 2)


class StoreTests(unittest.TestCase):
    def test_workspace_filter(self):
        scope = VectorStore.scope('private-a', 'document-b')
        self.assertEqual(scope['must'][0]['match']['value'], 'private-a')
        self.assertEqual(scope['must'][1]['match']['value'], 'document-b')

    def test_search_is_scoped(self):
        store = VectorStore(Settings())
        with patch.object(store, 'request', return_value={'points': []}) as request:
            store.search('a', [0.1])
            body = request.call_args.args[2]
            self.assertEqual(body['filter'], store.scope('a'))

    def test_clear_is_scoped(self):
        store = VectorStore(Settings())
        with patch.object(store, 'request') as request:
            store.clear('a')
            self.assertEqual(request.call_args.args[2], {'filter': store.scope('a')})

    def test_scroll_paginates(self):
        store = VectorStore(Settings())
        doc = {'document_id': 'd', 'created_at': '2026', 'name': 'test'}
        with patch.object(store, 'request', side_effect=[{'points': [{'payload': doc}], 'next_page_offset': 'next'}, {'points': [{'payload': doc}], 'next_page_offset': None}]) as request:
            self.assertEqual(len(store.documents('a')), 1)
            self.assertEqual(request.call_args.args[2]['offset'], 'next')


class ProviderTests(unittest.TestCase):
    def test_missing_key(self):
        client = OpenAIClient(Settings(openai_api_key=''))
        with self.assertRaises(ProviderError):
            client.embeddings(['hello'])

    def test_embedding_order(self):
        client = OpenAIClient(Settings(embedding_dimensions=2))
        with patch.object(client, 'request', return_value={'data': [{'index': 1, 'embedding': [2, 3]}, {'index': 0, 'embedding': [0, 1]}]}):
            self.assertEqual(client.embeddings(['a', 'b']), [[0, 1], [2, 3]])

    def test_wrong_dimensions(self):
        client = OpenAIClient(Settings(embedding_dimensions=2))
        with patch.object(client, 'request', return_value={'data': [{'index': 0, 'embedding': [0]}]}), self.assertRaises(ProviderError):
            client.embeddings(['a'])

    def test_verifier_rejects(self):
        client = OpenAIClient(Settings())
        with patch.object(client, 'request', return_value={'choices': [{'message': {'content': '{"supported":false}'}}]}):
            self.assertFalse(client.verify('q', []))

    def test_verifier_refusal_rejects(self):
        client = OpenAIClient(Settings())
        with patch.object(client, 'request', return_value={'choices': [{'message': {'refusal': 'no'}}]}):
            self.assertFalse(client.verify('q', []))


if __name__ == '__main__':
    unittest.main()
