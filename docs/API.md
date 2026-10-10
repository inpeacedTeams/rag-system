# API и конфигурация

[← README](../README.md) · [Архитектура](ARCHITECTURE.md) · [Деплой](DEPLOYMENT.md)

Интерактивные схемы доступны по `/api/docs`. Все изменяющие запросы требуют `X-Requested-With: DocumentRAG`. Cookie сессии создаётся первым запросом; CLI-клиент должен сохранять её между вызовами.

## Маршруты

| Метод | Путь | Назначение |
| :--- | :--- | :--- |
| GET / HEAD | `/` | Сайт / проверка доступности |
| GET | `/health` | Готовность процесса после startup |
| GET | `/api/status` | Наличие настройки ключа, лимиты, retrieval_mode |
| GET | `/api/documents` | Документы текущей сессии |
| POST | `/api/documents/upload` | Multipart, поле `file` |
| POST | `/api/documents/url` | JSON с полем `url` |
| POST | `/api/chat` | JSON с полем `question` |
| DELETE | `/api/documents/{document_id}` | Удаление одного документа текущей сессии |
| DELETE | `/api/documents` | Очистка текущей базы |

Поле configured в `/api/status` означает наличие ключа в настройках, не проверенную валидность ключа. `/health` отражает готовность процесса после startup и не является полноценным end-to-end тестом Kiro и Qdrant.

## Пример запросов

````bash
curl -c cookies.txt http://localhost:8000/api/status
curl -b cookies.txt -H 'X-Requested-With: DocumentRAG' \
  -F 'file=@document.txt' http://localhost:8000/api/documents/upload
curl -b cookies.txt -H 'X-Requested-With: DocumentRAG' \
  -H 'Content-Type: application/json' \
  -d '{"question":"Какой срок оплаты по договору?"}' \
  http://localhost:8000/api/chat
````

Пример формы успешного ответа:

````json
{
  "answer": "Срок оплаты составляет десять рабочих дней. [1]",
  "sources": [
    {
      "id": 1,
      "document_id": "document-identifier",
      "name": "document.txt",
      "page": null,
      "chunk": 1,
      "url": null,
      "quotes": ["Срок оплаты по договору составляет десять рабочих дней."]
    }
  ],
  "found": true
}
````

Пример отказа:

````json
{
  "answer": "В загруженных документах нет этой информации",
  "sources": [],
  "found": false
}
````

Идентификатор источника соответствует номеру evidence и может иметь пропуски, если использована только часть фрагментов. Для PDF page содержит номер страницы, для TXT/URL используется null. Чат не передаёт историю предыдущих вопросов.

## Настройки

| Переменная | По умолчанию | Назначение |
| :--- | :--- | :--- |
| KIRO_API_KEY | Пусто | Ключ Kiro, обязательно для ответов |
| SESSION_SECRET | Пусто | Случайный секрет минимум 32 символа, обязательно |
| CHAT_BASE_URL | `https://api.xkiro.com/v1` | Endpoint совместимого chat API |
| CHAT_MODEL | `qwen/qwen3.8-omni-flash:free` | Модель ответа и проверки |
| CHAT_RESPONSE_FORMAT | `prompt` | `prompt`, `json_object` или `json_schema` |
| QDRANT_URL | `http://qdrant:6333` | Адрес векторной базы |
| QDRANT_API_KEY | Пусто | Ключ при использовании защищённой базы |
| QDRANT_COLLECTION | `documents_bm25_v1` | Коллекция с sparse-вектором bm25 |
| MIN_RELEVANCE_SCORE | `0` | Порог BM25 score |
| BM25_K1 | `1.2` | Насыщение частот термов |
| BM25_B | `0.75` | Сила нормализации длины |
| BM25_AVG_LENGTH | `220` | Фиксированная средняя длина чанка в термах |
| MAX_UPLOAD_MB | `5` | Максимальный размер файла |
| MAX_PDF_PAGES | `100` | Максимальное число страниц |
| COOKIE_SECURE | `false` | `true` для HTTPS |
| PORT | `8000` без переменной | Docker-порт, Render задаёт свой |

CHAT_RESPONSE_FORMAT=prompt отправляет обычные model, messages и max_tokens. Схема JSON описывается в системном сообщении и проверяется на сервере. Режимы json_object/json_schema используйте только при поддержке выбранного API. Цельный JSON и один JSON code fence допустимы, произвольный текст вокруг JSON не допускается.

Для совместимости OPENAI_BASE_URL принимается, но CHAT_BASE_URL имеет приоритет. OPENAI_API_KEY не используется при адресе api.xkiro.com, там необходим KIRO_API_KEY.

## Ошибки

| HTTP | Типичная причина |
| :--- | :--- |
| 400 | Неподдерживаемый документ, кодировка, пустой текст, недопустимый URL |
| 403 | Нет обязательного заголовка или запрос с чужого Origin |
| 413 | Превышен лимит тела/файла |
| 422 | Неверная структура параметров запроса |
| 429 | Rate limit или занят слот импорта |
| 500 | Внутренняя ошибка с кодом для Logs |
| 503 | Недоступность/ошибка Kiro или Qdrant |

Ошибка провайдера не подменяется found=false. Если инфраструктура Render вернула не-JSON ответ, интерфейс показывает HTTP-статус и предлагает проверить Logs. Во время startup коллекция проверяется на совместимость и создаются payload-индексы workspace/document_id.
