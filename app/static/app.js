'use strict';
const $ = (id) => document.getElementById(id);
let documents = [];
let maxUploadMB = 5;
let uploading = false;
let chatting = false;
let clearing = false;
let available = false;
let toastTimer;

function notify(text, error = false) {
  const toast = $('toast');
  toast.textContent = text;
  toast.classList.toggle('error', error);
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 10000 : 4500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    headers: { 'X-Requested-With': 'DocumentRAG', ...(options.headers || {}) },
  });
  let data;
  const raw = await response.text();
  try { data = JSON.parse(raw); }
  catch {
    const hint = [502, 503, 504].includes(response.status) ? ' Возможно, Render перезапускает сервис. Проверьте Logs.' : ' Проверьте Logs сервиса.';
    throw new Error(`Сервер вернул ответ не в формате JSON (HTTP ${response.status}).${hint}`);
  }
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : 'Проверьте введённые данные.';
    throw new Error(detail);
  }
  return data;
}

function syncControls() {
  const busy = uploading || chatting || clearing || !available;
  $('file-input').disabled = busy;
  $('drop-zone').classList.toggle('busy', busy);
  $('drop-zone').setAttribute('aria-disabled', String(busy));
  $('url-submit').disabled = busy;
  $('url-input').disabled = busy;
  $('clear-button').disabled = busy || !documents.length;
  $('send-button').disabled = busy || !documents.length || !$('question').value.trim();
  document.querySelectorAll('.delete-doc').forEach(button => { button.disabled = busy; });
}

function renderDocuments() {
  const container = $('document-list');
  container.replaceChildren();
  $('document-count').textContent = documents.length;
  if (!documents.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-library';
    empty.textContent = 'Пока здесь пусто. Добавьте первый документ.';
    container.append(empty);
  }
  for (const doc of documents) {
    const item = document.createElement('div'); item.className = 'document-item';
    const icon = document.createElement('span'); icon.className = 'doc-icon'; icon.textContent = doc.kind.toUpperCase();
    const info = document.createElement('div'); info.className = 'doc-info';
    const name = document.createElement('div'); name.className = 'doc-name'; name.textContent = doc.name; name.title = doc.name;
    const meta = document.createElement('div'); meta.className = 'doc-meta'; meta.textContent = `${doc.chunk_count} фрагм. · готов к вопросам`;
    info.append(name, meta);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'delete-doc'; remove.textContent = '×'; remove.setAttribute('aria-label', `Удалить ${doc.name}`);
    remove.addEventListener('click', () => deleteDocument(doc));
    item.append(icon, info, remove); container.append(item);
  }
  syncControls();
}

async function refreshDocuments() {
  documents = (await api('/api/documents')).documents;
  renderDocuments();
}

async function deleteDocument(doc) {
  if (!confirm(`Удалить «${doc.name}» из базы знаний?`)) return;
  clearing = true; syncControls();
  try {
    await api(`/api/documents/${encodeURIComponent(doc.document_id)}`, { method: 'DELETE' });
    await refreshDocuments(); resetMessages(); notify('Документ удалён.');
  } catch (error) { notify(error.message, true); }
  finally { clearing = false; syncControls(); }
}

async function uploadFiles(files) {
  if (uploading || chatting || clearing || !available) return;
  uploading = true; $('upload-progress').hidden = false; syncControls();
  try {
    for (const file of files) {
      if (!/\.(pdf|txt)$/i.test(file.name)) { notify(`«${file.name}»: нужен PDF или TXT.`, true); continue; }
      if (file.size > maxUploadMB * 1024 * 1024) { notify(`«${file.name}»: превышен лимит ${maxUploadMB} МБ.`, true); continue; }
      $('progress-text').textContent = `Читаем и индексируем: ${file.name}`;
      const body = new FormData(); body.append('file', file);
      try {
        await api('/api/documents/upload', { method: 'POST', body });
        await refreshDocuments(); notify(`«${file.name}» добавлен.`);
      } catch (error) { notify(`${file.name}: ${error.message}`, true); }
    }
  } finally { uploading = false; $('upload-progress').hidden = true; $('file-input').value = ''; syncControls(); }
}

$('file-input').addEventListener('change', event => uploadFiles(Array.from(event.target.files)));
$('drop-zone').addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') && !$('file-input').disabled) { event.preventDefault(); $('file-input').click(); }
});
for (const type of ['dragenter', 'dragover']) $('drop-zone').addEventListener(type, event => { event.preventDefault(); $('drop-zone').classList.add('dragover'); });
for (const type of ['dragleave', 'drop']) $('drop-zone').addEventListener(type, event => { event.preventDefault(); $('drop-zone').classList.remove('dragover'); });
$('drop-zone').addEventListener('drop', event => uploadFiles(Array.from(event.dataTransfer.files)));
$('url-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (uploading || chatting || clearing || !available) return;
  uploading = true; $('upload-progress').hidden = false; $('progress-text').textContent = 'Загружаем страницу и создаём эмбеддинги…'; syncControls();
  try {
    await api('/api/documents/url', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: $('url-input').value.trim() }) });
    $('url-input').value = ''; await refreshDocuments(); notify('Страница добавлена.');
  } catch (error) { notify(error.message, true); }
  finally { uploading = false; $('upload-progress').hidden = true; syncControls(); }
});

function resetMessages() {
  for (const item of $('messages').querySelectorAll('.message')) item.remove();
  $('welcome').hidden = false;
}
$('clear-button').addEventListener('click', () => $('clear-dialog').showModal());
$('cancel-clear').addEventListener('click', () => $('clear-dialog').close());
$('confirm-clear').addEventListener('click', async () => {
  if (clearing) return;
  clearing = true; $('confirm-clear').disabled = true; syncControls();
  try {
    await api('/api/documents', { method: 'DELETE' });
    await refreshDocuments(); resetMessages(); $('clear-dialog').close(); notify('База знаний очищена.');
  } catch (error) { notify(error.message, true); }
  finally { clearing = false; $('confirm-clear').disabled = false; syncControls(); }
});

function addMessage(role, text) {
  $('welcome').hidden = true;
  const article = document.createElement('article'); article.className = `message ${role}`;
  const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.textContent = role === 'user' ? 'Вы' : '✳'; avatar.setAttribute('aria-hidden', 'true');
  const content = document.createElement('div'); content.className = 'message-content';
  const label = document.createElement('div'); label.className = 'message-label'; label.textContent = role === 'user' ? 'Вы' : 'Источник';
  const body = document.createElement('div'); body.className = 'message-text'; body.textContent = text;
  content.append(label, body); article.append(avatar, content); $('messages').append(article);
  scrollBottom(); return { article, content, body };
}
function scrollBottom() { $('messages').scrollTop = $('messages').scrollHeight; }
function addSources(content, sources) {
  const list = document.createElement('div'); list.className = 'source-list';
  for (const source of sources) {
    const card = document.createElement('details'); card.className = 'source-card';
    const summary = document.createElement('summary');
    const location = source.page ? `страница ${source.page}, чанк ${source.chunk}` : `чанк ${source.chunk}`;
    summary.textContent = `[${source.id}] ${source.name} · ${location}`; card.append(summary);
    for (const quote of source.quotes) { const block = document.createElement('blockquote'); block.textContent = quote; card.append(block); }
    if (source.url) {
      try {
        const url = new URL(source.url);
        if (['http:', 'https:'].includes(url.protocol)) {
          const link = document.createElement('a'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Открыть страницу ↗'; card.append(link);
        }
      } catch { /* Invalid source URLs are never rendered as links. */ }
    }
    list.append(card);
  }
  content.append(list);
}
$('question').addEventListener('input', () => {
  $('question').style.height = 'auto'; $('question').style.height = `${Math.min($('question').scrollHeight, 150)}px`;
  $('question-count').textContent = `${$('question').value.length} / 2000`; syncControls();
});
$('question').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$('send-button').disabled) $('chat-form').requestSubmit(); }
});
$('chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  const question = $('question').value.trim();
  if (!question || chatting || uploading || clearing || !documents.length || !available) return;
  chatting = true; addMessage('user', question); $('question').value = ''; $('question').dispatchEvent(new Event('input')); syncControls();
  const message = addMessage('assistant', '');
  const pending = document.createElement('div'); pending.className = 'pending';
  const spinner = document.createElement('span'); spinner.className = 'spinner'; pending.append(spinner, document.createTextNode('Ищем ответ в материалах…')); message.body.append(pending);
  try {
    const result = await api('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) });
    message.body.textContent = result.answer;
    if (!result.found) message.body.classList.add('no-answer');
    addSources(message.content, result.sources);
  } catch (error) {
    message.body.textContent = `Не удалось получить ответ: ${error.message}`;
    notify(error.message, true);
  } finally { chatting = false; syncControls(); scrollBottom(); $('question').focus(); }
});

async function init() {
  syncControls();
  try {
    // Sequential calls ensure the first response establishes a session before other requests.
    const status = await api('/api/status');
    available = status.configured;
    maxUploadMB = status.max_upload_mb;
    $('upload-limit').textContent = `PDF или TXT · до ${maxUploadMB} МБ каждый`;
    $('config-warning').hidden = available;
    await refreshDocuments();
  } catch (error) { notify(error.message, true); }
  syncControls();
}
init();
