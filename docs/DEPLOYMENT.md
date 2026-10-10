# Запуск и деплой

[← README](../README.md) · [Архитектура](ARCHITECTURE.md) · [API и настройки](API.md)

## Локально через Docker

Понадобятся Docker с Compose v2 и ключ Kiro.

````bash
git clone https://github.com/inpeacedTeams/rag-system.git
cd rag-system
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"
````

Заполните KIRO_API_KEY и SESSION_SECRET в `.env`. Compose задаёт QDRANT_URL=http://qdrant:6333 и запускает локальный Qdrant без облачного API-ключа.

````bash
docker compose up --build -d
````

Откройте http://localhost:8000. Для остановки с сохранением базы используйте `docker compose down`. Для обновления после изменения `.env` используйте `docker compose up --build -d --force-recreate app`.

`docker compose down -v` удаляет локальную базу всех сессий необратимо. Это не аналог кнопки сайта, которая очищает только текущий workspace.

## Render Free

Один Web Service запускает только приложение из Dockerfile. Соседний Qdrant из compose.yaml автоматически не поднимается, поэтому для Render используйте Qdrant Cloud или отдельно развёрнутую внешнюю базу.

| Поле Render | Значение |
| :--- | :--- |
| Repository | `inpeacedTeams/rag-system` |
| Branch | `main` |
| Runtime / Language | Docker |
| Compute | Free |
| Root Directory | Пусто |
| Dockerfile Path | `./Dockerfile` |
| Docker Command | Пусто |
| Health Check Path | `/health` |

Dockerfile слушает 0.0.0.0 и порт `${PORT:-8000}`. Render обычно задаёт PORT=10000. При ручном переопределении команды используйте:

````bash
uvicorn app.main:app --host 0.0.0.0 --port 10000 --workers 1
````

### Qdrant Cloud

Откройте https://cloud.qdrant.io/, создайте кластер и дождитесь его готовности. Для первоначального тестирования можно выбрать Free с учётом актуальных условий провайдера.

Из карточки кластера скопируйте **Cluster URL / Endpoint**, а также создайте API-ключ с правами создания коллекций и индексов, чтения, записи и удаления. URL кабинета вида `cloud.qdrant.io/accounts/.../overview` не является адресом базы.

Пример адреса подключения:

````text
https://YOUR_CLUSTER.REGION.cloud.qdrant.io:6333
````

Если панель выдаёт другой полный HTTPS endpoint, используйте его как есть. Не добавляйте `/dashboard` или пути API к QDRANT_URL.

### Environment Variables

````dotenv
KIRO_API_KEY=YOUR_KIRO_KEY
SESSION_SECRET=YOUR_RANDOM_SECRET
CHAT_BASE_URL=https://api.xkiro.com/v1
CHAT_MODEL=qwen/qwen3.8-omni-flash:free
CHAT_RESPONSE_FORMAT=prompt
QDRANT_URL=https://YOUR_CLUSTER.cloud.qdrant.io
QDRANT_API_KEY=YOUR_QDRANT_KEY
QDRANT_COLLECTION=documents_bm25_v1
MIN_RELEVANCE_SCORE=0
MAX_UPLOAD_MB=5
MAX_PDF_PAGES=100
COOKIE_SECURE=true
PORT=10000
````

SESSION_SECRET должен быть случайным и не короче 32 символов. Сохраните его между деплоями: замена секрета сделает старые cookie недействительными. Не передавайте ключи через публичные чаты или README.

После настройки нажмите **Manual Deploy → Deploy latest commit**. Перезапуск старого образа через Restart не заменяет сборку нового коммита. Проверьте `/health`, затем загрузите небольшой TXT.

### Миграция со старых нейросетевых версий

Установите новую QDRANT_COLLECTION=documents_bm25_v1 и MIN_RELEVANCE_SCORE=0. Старые плотные OpenAI/MiniLM-векторы несовместимы с sparse BM25. Документы нужно загрузить заново. Старые коллекции приложение не удаляет автоматически.

Переменные EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, EMBEDDING_CACHE_DIR и EMBEDDING_THREADS больше не используются, их можно удалить. Сохраните существующие ключи Kiro/Qdrant и SESSION_SECRET.

## Диагностика

| Симптом | Что проверить |
| :--- | :--- |
| Render ищет 10000, Uvicorn слушает 8000 | Обновлён ли коммит, не задана ли старая Docker Command |
| Упоминается FastEmbed или скачивание Hugging Face | Развёрнут старый образ, нужен Deploy latest commit |
| Несовместимая коллекция | Используется ли `documents_bm25_v1`, а не старая dense-коллекция |
| Ошибка ключа Kiro | KIRO_API_KEY, доступ к CHAT_MODEL и CHAT_BASE_URL |
| Ошибка векторной базы | HTTPS endpoint кластера, QDRANT_API_KEY и готовность Qdrant |
| HTTP 429 | Квота Kiro, лимиты приложения или уже выполняющийся импорт |
| HTTP 502 / 503 / 504 вместо JSON | Logs и Events Render, перезапуск процесса, доступность сервиса |
| Внутренняя ошибка с кодом | Найдите этот код в Logs |
| TXT отклоняется | Кодировка UTF-8 и непустой текст |
| PDF не содержит текста | Предварительный OCR, отсутствие пароля, целостность файла |
| Ответ не находится | Термины из документа, не только смысловое перефразирование |

Free-тарифы имеют лимиты. Удаление нейросети снижает нагрузку, но 512 МБ не гарантируют обработку произвольного тяжёлого или повреждённого PDF. Импорт выполняется по одному, по умолчанию файл ограничен 5 МБ и PDF 100 страницами. Лимиты можно менять осознанно, учитывая ресурсы.

## Собственный сервер

Compose публикует приложение только на 127.0.0.1:8000. Перед ним можно поставить reverse proxy с HTTPS. Например, при установленном Caddy и настроенном DNS:

````caddyfile
rag.example.com {
    request_body {
        max_size 6MB
    }
    reverse_proxy 127.0.0.1:8000
}
````

Установите COOKIE_SECURE=true и сохраняйте исходный Host через proxy. Для закрытого сайта добавьте авторизацию на proxy. Резервное копирование Qdrant и контроль расходов внешнего API являются задачами инфраструктуры.
