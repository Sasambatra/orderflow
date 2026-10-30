# Как оформлено портфолио OrderFlow

При подготовке публикации изучены реальные репозитории и официальная документация GitHub.
Это ориентиры подачи и организации; OrderFlow не является форком этих проектов.

| Ориентир | Наблюдение | Применение в OrderFlow |
| --- | --- | --- |
| [FastAPI Full Stack Template](https://github.com/fastapi/full-stack-fastapi-template) | Стек и быстрый старт видны сразу; подробные инструкции вынесены отдельно | Короткий вход в Docker-запуск и ссылки на runbook/архитектуру |
| [Litestar](https://github.com/litestar-org/litestar) | Главная страница связывает описание, документацию, качество и правила участия | Живой CI badge, contributing/security, навигация и релиз |
| [FastAPI Best Practices](https://github.com/zhanymkanov/fastapi-best-practices) | Технические решения объясняются через причины и конкретные примеры | Таблица проблема → реализация → проверяющий тест |
| [GitHub: README](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) | Посетителю нужны назначение, польза, старт и помощь | Главный README на английском и заметная русская версия |
| [GitHub: status badge](https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge) | Badge отражает реальное состояние workflow | CI badge ветки main; исторические проценты покрытия вынесены в датированный отчёт |
| [GitHub: topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) | Темы описывают стек и назначение | fastapi, postgresql, celery, idempotency, transactional-outbox и другие подходящие темы |
| [GitHub: releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases) | Релиз связывает версию, заметки и скачиваемые артефакты | Версия 0.1.0, changelog, wheel/sdist и контрольные суммы |

Основной акцент — корректность транзакций и восстановление, а не количество файлов или придуманные коммерческие масштабы.
Ограничения mock-оплаты и учебного backend указаны явно. Нагрузочные данные опубликованы вместе с методикой и исходной ревизией.
