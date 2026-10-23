# Уведомления

Основа проекта — [ajr-dev/discospeech](https://github.com/ajr-dev/discospeech), MIT,
copyright (c) 2025 AJR. Исходное уведомление сохранено в LICENSE.

Адаптация Supertonic использует upstream helper; MIT-уведомление и ссылка находятся
в bot/vendor/SUPERTONIC_LICENSE и bot/vendor/README.md.

Модели и сторонние зависимости скачиваются отдельно и сохраняют собственные лицензии.
Supertonic 3: OpenRAIL-M; голос Piper Denis: условия карточки модели; Qwen3 и его
модификации: условия соответствующего репозитория модели. Эти веса не входят в выпуск.

Рекламный ролик использует нарисованную программно графику, локальную синтезированную
речь и оригинальный синтезированный звуковой пульс. Личные записи пользователей,
видео Discord-участников и коммерческая музыка в него не включены.
# Windows desktop application

The optional desktop application uses Qt for Python (PySide6 Essentials and
Shiboken6), under LGPLv3/GPLv3 or commercial terms. Distributed Qt libraries are
dynamic, replaceable files in the application directory. Their license texts
are included in `_internal/licenses`. Qt and PySide sources and corresponding
release tags are available at https://code.qt.io/pyside/pyside-setup.git/ and
https://code.qt.io/qt/ . Sobesednik does not modify these libraries.

psutil is BSD-3-Clause. PyInstaller uses GPLv2-or-later with its exception for
bundled applications. The generated installer uses unmodified Inno Setup by
Jordan Russell and Martijn Laan: https://jrsoftware.org/ . Their respective
notices are preserved. Python runtime is downloaded from python.org and keeps
its PSF license. Model licenses remain separate from the application license.
