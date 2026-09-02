# Установка Second Brain Agent & Role Pack v1

## Целевой репозиторий

Только:

`MikeMoore1337/second-brain`

Не устанавливать pack в:

`MikeMoore1337/second-brain-vault`

## Установка

Распаковать содержимое этого архива в корень локального клона `second-brain`.

После распаковки ожидается:

```text
second-brain/
├── AGENTS.md
├── ROLE_MANIFEST.json
├── AGENT_MANIFEST.json
├── ai/
│   ├── README.md
│   ├── agents/
│   ├── roles/
│   ├── prompts/
│   └── evals/
├── src/
├── tests/
└── ...
```

`AGENTS.md` в pack уже содержит существующие правила репозитория плюс новую
маршрутизацию ролей/агентов, поэтому он должен заменить текущий корневой файл.

## PowerShell

Если архив распакован, например, в
`D:\Downloads\second-brain-agent-pack-v1`, а репозиторий находится в
`D:\Pet projects\second-brain`:

```powershell
Copy-Item "D:\Downloads\second-brain-agent-pack-v1\*" `
  "D:\Pet projects\second-brain" -Recurse -Force
```

Перед коммитом проверить diff и стандартные проверки проекта.

## Рекомендуемый Git flow

Создать отдельную feature-ветку, скопировать pack, проверить diff и только затем
коммитить. Никаких изменений в `second-brain-vault` для установки pack не
требуется.
