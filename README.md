# Лабораторная работа 3. Технология MapReduce

Вариант: №10 - аналитические вакансии.

## Состав проекта

- `src/` - загрузка данных в RustFS, MapReduce, DuckDB, сверка результатов и профилирование.
- `tests/` - модульные тесты.
- `report/` - итоговый отчёт в DOCX и PDF.
- `results/` - сводка проверки, профилирование и небольшие таблицы бизнес-анализа.
- `docker-compose.yml` - запуск локального RustFS.

Исходный CSV не включен в репозиторий: его размер около 808 MiB. Большие файлы промежуточных результатов также не включены. Они формируются при запуске и загружаются в бакет RustFS `hh-lake`.

## Подготовка

Требуются Python 3.11 и Docker Desktop.

1. Поместить исходный ZIP с датасетом в `data/`.
2. Извлечь CSV:

   ```powershell
   python -m src.prepare_data --archive data/it_vacancies_2024.zip
   ```

3. Создать локальный файл настроек и задать собственный секрет:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Установить зависимости и запустить RustFS:

   ```powershell
   python -m pip install -r requirements.txt
   docker compose up -d rustfs
   ```

5. Загрузить CSV в объектное хранилище:

   ```powershell
   python -m src.upload_data --file data/it_vacancies_2024.csv --bucket hh-lake --key data/it_vacancies_2024.csv
   ```

6. Запустить оба способа расчёта и сверку:

   ```powershell
   python -m src.run_all --sizes 1000,5000,10000,all --chunk-size 5000
   ```

7. Выполнить профилирование:

   ```powershell
   python -m src.profile_runs --trials 3 --chunk-size 5000
   ```

8. Запустить тесты:

   ```powershell
   python -m pytest -q
   ```

## Результаты

Для каждого размера выборки MapReduce и DuckDB сохраняют файлы навыков, пар и троек в RustFS. `results/verification.json` содержит результат построчной сверки. Профилирование выполняется по трём попыткам; сводные данные находятся в `results/profile_runs.csv` и `results/profile_summary.json`.