import argparse
import asyncio
import json
import sys

from bot.config import Config, ROOT
from bot.runtime import InstanceLock, AlreadyRunningError, prepare_runtime


def check(config):
    import importlib.metadata
    import imageio_ffmpeg
    import subprocess
    for package in ('discord.py', 'discord-ext-voice-recv', 'davey', 'PyNaCl', 'faster-whisper',
                    'ctranslate2', 'piper-tts', 'vosk', 'webrtcvad-wheels', 'httpx'):
        print(package, importlib.metadata.version(package))
    if config.tts_engine == 'supertonic':
        from bot.services.supertonic_voice import SupertonicVoice
        tts_files = SupertonicVoice.required_files(config.path(config.tts_supertonic_model), config.tts_supertonic_voice)
    else:
        tts_files = [config.path(config.tts_model), config.path(config.tts_model + '.json')]
    for path in [config.path(config.whisper_model) / 'model.bin',
                 config.path(config.wake_model) / 'am/final.mdl', *tts_files]:
        if not path.is_file():
            raise RuntimeError(f'Отсутствует {path.name}. Запустите setup.bat.')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-version'], check=True, capture_output=True)
    print('FFmpeg OK. Local model files OK. Token configured:', bool(config.discord_token))


async def run(config, logger, metrics, voice_smoke=False):
    from bot.bot import DiscordBot
    from bot.voice.compat import install
    install()
    if not config.discord_token or config.discord_token == 'YOUR_DISCORD_BOT_TOKEN':
        raise RuntimeError('Укажите DISCORD_TOKEN в .env или discord_token в config.json.')
    async with DiscordBot(config, logger, metrics) as bot:
        bot.voice_smoke_requested = voice_smoke
        async def watch_stop():
            await bot.wait_until_ready()
            while not bot.is_closed():
                path = ROOT / '.serega.stop'
                if path.exists():
                    path.unlink(missing_ok=True)
                    logger.info('Запрошена штатная остановка Собеседника.')
                    await bot.close()
                    return
                await asyncio.sleep(.5)
        watcher = asyncio.create_task(watch_stop(), name='stop-watcher')
        try:
            await bot.start(config.discord_token, reconnect=True)
        finally:
            # When the watcher initiated close(), let its closing task finish.
            # Cancelling it here also cancels discord.py's shared _closing_task.
            if not bot.is_closed():
                watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        logger.info('Собеседник остановлен, голосовые задачи закрыты.')
        return getattr(bot, 'learning_request', None)


def supervise(args, logger):
    """Voice must fully exit before training starts: native CUDA teardown is process-wide."""
    import subprocess
    import uuid
    from tools.process_tree import run_owned
    from tools.run_learning_cycle import execute
    result = ROOT / 'temp' / ('maintenance-' + uuid.uuid4().hex + '.json')
    result.parent.mkdir(exist_ok=True)
    fallback = result.with_name(result.stem + '-config.json')
    config_path = ROOT / 'config.json'
    known_good = config_path.read_bytes() if config_path.exists() else b'{}'
    use_fallback = False
    resume = args.resume_channel
    with InstanceLock(ROOT / '.serega-supervisor.lock'):
        while True:
            command = [sys.executable, str(ROOT / 'main.py'), '--voice-worker', '--maintenance-result', str(result)]
            if resume:
                command += ['--resume-channel', str(resume)]
            if args.voice_smoke:
                command += ['--voice-smoke']
            if use_fallback:
                fallback.write_bytes(known_good)
                command += ['--runtime-config', str(fallback)]
            result.unlink(missing_ok=True)
            status = run_owned(command, cwd=ROOT)
            if status or not result.is_file():
                fallback.unlink(missing_ok=True)
                return status
            maintenance = json.loads(result.read_text(encoding='utf-8'))
            result.unlink()
            try:
                execute([sys.executable, 'tools/run_learning_cycle.py', '--guild', str(maintenance['guild'])],
                        ROOT / 'logs/learning-maintenance.log', 7200)
            except (OSError, subprocess.SubprocessError) as error:
                logger.warning('Обучение завершилось ошибкой %s; возвращаю голос.', type(error).__name__)
            if (ROOT / '.serega.stop').exists():
                fallback.unlink(missing_ok=True)
                return 0
            try:
                Config.load()
                known_good = config_path.read_bytes() if config_path.exists() else b'{}'
            except (OSError, ValueError):
                use_fallback = True
                logger.warning('Настройки изменены с ошибкой. Возвращаю голос с последними рабочими настройками; правки оставлены на диске.')
            resume = maintenance['channel']


def main():
    parser = argparse.ArgumentParser(description='Собеседник — локальный голосовой Discord-бот')
    parser.add_argument('--check', action='store_true', help='Проверить зависимости и файлы без входа в Discord')
    parser.add_argument('--voice-smoke', action='store_true', help='Проверить подключение и воспроизведение в пустом канале')
    parser.add_argument('--resume-channel', type=int, default=0, help='Однократно вернуться в канал после перезапуска')
    parser.add_argument('--stop', action='store_true', help='Штатно остановить уже запущенного Серёгу')
    parser.add_argument('--voice-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--maintenance-result', help=argparse.SUPPRESS)
    parser.add_argument('--runtime-config', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.stop:
        (ROOT / '.serega.stop').write_text('stop', encoding='ascii')
        print('Запрошена штатная остановка Собеседника.')
        return
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        # Avoid aiodns incompatibilities on Windows; never patch DNS globally.
    prepare_runtime()
    if args.runtime_config:
        from pathlib import Path
        runtime_config = Path(args.runtime_config).resolve()
        if not args.voice_worker or runtime_config.parent != (ROOT / 'temp').resolve():
            raise ValueError('Invalid runtime recovery configuration')
        config = Config.load(runtime_config)
        config.learning_auto_enabled = False
    else:
        config = Config.load()
    if args.resume_channel:
        config.auto_join_channel_id = args.resume_channel
    from bot.logger import setup_logger
    logger, metrics = setup_logger([config.discord_token])
    if args.check:
        check(config)
        return
    if not args.voice_worker:
        return supervise(args, logger)
    with InstanceLock(ROOT / '.learning-maintenance.lock'), InstanceLock(ROOT / '.serega.lock'):
        (ROOT / '.serega.stop').unlink(missing_ok=True)
        check(config)
        logger.info('Запускается Собеседник. Остановка: Ctrl+C.')
        try:
            maintenance = asyncio.run(run(config, logger, metrics, args.voice_smoke))
        except KeyboardInterrupt:
            logger.info('Собеседник остановлен.')
            return 0
        if maintenance and args.maintenance_result:
            from pathlib import Path
            output = Path(args.maintenance_result).resolve()
            if output.parent != (ROOT / 'temp').resolve() or not output.name.startswith('maintenance-'):
                raise ValueError('Invalid maintenance handoff path')
            output.write_text(json.dumps(maintenance), encoding='utf-8')


if __name__ == '__main__':
    try:
        sys.exit(main() or 0)
    except AlreadyRunningError as error:
        print(str(error))
        sys.exit(2)
    except Exception as error:
        # Full trace goes through the redacting logger if configured.
        import logging
        logging.getLogger('serega').exception('Запуск не удался: %s', type(error).__name__)
        sys.exit(1)
