import math
import subprocess
import sys
import unittest
from app.config import Settings
from app.documents import Chunk, DocumentError
from app.embeddings import BM25Embeddings, tokenize, normalize_term, term_id


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.encoder = BM25Embeddings(Settings())

    def test_russian_inflections(self):
        self.assertEqual(normalize_term('оплата'), normalize_term('оплаты'))
        self.assertEqual(normalize_term('договор'), normalize_term('договора'))
        self.assertEqual(normalize_term('оплатить'), normalize_term('оплата'))

    def test_case_and_yo(self):
        self.assertEqual(tokenize('СЧЁТ'), tokenize('счет'))

    def test_stopwords(self):
        self.assertEqual(tokenize('и в на the and'), [])

    def test_numbers(self):
        self.assertIn('10', tokenize('10 дней'))
        self.assertIn('1', tokenize('1 день'))

    def test_query_weights(self):
        vector = self.encoder.embeddings(['оплата оплата договор'], query=True)[0]
        self.assertEqual(len(vector['indices']), 2)
        self.assertEqual(vector['values'], [1.0, 1.0])

    def test_document_weights(self):
        one = self.encoder.embeddings(['договор'])[0]
        repeated = self.encoder.embeddings(['договор договор договор'])[0]
        self.assertGreater(repeated['values'][0], one['values'][0])
        self.assertLess(repeated['values'][0], 3 * one['values'][0])

    def test_length_penalty(self):
        short = self.encoder.embeddings(['оплата'])[0]
        long = self.encoder.embeddings(['оплата ' + 'договор ' * 100])[0]
        identifier = term_id(normalize_term('оплата'))
        weight = long['values'][long['indices'].index(identifier)]
        self.assertGreater(short['values'][0], weight)

    def test_indices_sorted_unique(self):
        vector = self.encoder.embeddings(['договор оплата срок договор'])[0]
        self.assertEqual(vector['indices'], sorted(set(vector['indices'])))
        self.assertEqual(len(vector['indices']), len(vector['values']))
        self.assertTrue(all(0 <= i <= 2**32 - 1 for i in vector['indices']))
        self.assertTrue(all(math.isfinite(v) and v > 0 for v in vector['values']))

    def test_stable_ids_between_processes(self):
        script = 'from app.embeddings import term_id; print(term_id("оплат"))'
        output = subprocess.check_output([sys.executable, '-c', script], text=True)
        self.assertEqual(int(output), term_id('оплат'))

    def test_question_document_overlap(self):
        document = self.encoder.embeddings(['Срок оплаты по договору составляет десять рабочих дней.'])[0]
        question = self.encoder.embeddings(['Какой срок оплаты по договору?'], query=True)[0]
        self.assertEqual(len(set(question['indices']) & set(document['indices'])), 3)

    def test_unrelated_question_no_overlap(self):
        document = self.encoder.embeddings(['Срок оплаты по договору составляет десять рабочих дней.'])[0]
        question = self.encoder.embeddings(['Курс доллара сегодня?'], query=True)[0]
        self.assertFalse(set(question['indices']) & set(document['indices']))

    def test_prepare_preserves_page_and_text(self):
        chunks = self.encoder.prepare_chunks([Chunk('и в на', 1, 1), Chunk('Срок оплаты.', 7, 2)])
        self.assertEqual(chunks, [Chunk('Срок оплаты.', 7, 1)])

    def test_empty_document(self):
        with self.assertRaises(DocumentError):
            self.encoder.prepare_chunks([Chunk('и в на', None, 1)])

    def test_empty_vectors(self):
        self.assertEqual(self.encoder.embeddings([]), [])
        self.assertEqual(self.encoder.embeddings(['и в на'], query=True), [{'indices': [], 'values': []}])

    def test_invalid_configuration(self):
        for config in [Settings(bm25_k1=0), Settings(bm25_b=2), Settings(bm25_avg_length=0), Settings(bm25_b=float('nan'))]:
            with self.assertRaises(ValueError):
                BM25Embeddings(config)

    def test_lightweight_dependencies(self):
        from pathlib import Path
        requirements = (Path(__file__).resolve().parents[1] / 'requirements.txt').read_text()
        for dependency in ['fastembed', 'onnxruntime', 'torch', 'transformers', 'numpy']:
            self.assertNotIn(dependency, requirements)
