# Как участвовать в разработке

Second Brain работает с личными знаниями. Для каждого изменения приоритетны безопасность, воспроизводимость и соблюдение границы между публичным приложением и приватным vault.

## Перед началом

Изучите [README](README.md), [AGENTS.md](AGENTS.md), [PRODUCT.md](PRODUCT.md), [архитектуру](docs/architecture/overview.md) и [дизайн-контракт](DESIGN.md). Проверьте существующие [Issues](https://github.com/MikeMoore1337/second-brain/issues).

Не прикладывайте настоящие заметки, токены, скриншоты с личными данными, записи голоса, локальные пути или содержимое `second-brain-vault`. Все примеры и тесты должны быть синтетическими.

## Проверки локально

Нужны Python 3.14 и [uv](https://docs.astral.sh/uv/); для интерфейса Node.js 24 и npm.

```powershell
uv sync --locked
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
```

Для frontend:

```powershell
cd web
npm ci
npm run check
npm run build
npm run qa:pwa
```

Создавайте только собственные временные vault-fixtures по [контракту](docs/architecture/vault-contract-v1.md). Обычные тесты не должны выполнять сетевые операции или обращаться к реальному vault. Специальные `live_smoke` запускаются только отдельно, с явным разрешением.

## Правила PR

1. Создайте ветку от актуального `main`. Одна задача - один сфокусированный PR.
2. Объясните ценность, затронутые границы, возможные регрессии и критерии готовности.
3. Добавьте тесты и обновите документацию, если изменился публичный контракт.
4. Для UI проверьте узкие экраны, клавиатуру, доступность, русский текст и reduced-motion.
5. Убедитесь, что CI прошёл. Merge и production deployment не выполняются без разрешения владельца.

Не смешивайте косметические изменения с изменением прав доступа, production-конфигурации или политик Safe Write. `second-brain-vault` не изменяется из этого репозитория.

См. также [SECURITY.md](SECURITY.md), [SUPPORT.md](SUPPORT.md) и [правила общения](CODE_OF_CONDUCT.md).
