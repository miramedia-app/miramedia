import shutil
from logging import Logger
from pathlib import Path

from miramedia.config import MiraMediaConfig


def run_filesystem_checks(config: MiraMediaConfig, log: Logger) -> None:
    log.info("Creating directories if they don't exist...")
    config.misc.show_directory.mkdir(parents=True, exist_ok=True)
    config.misc.movie_directory.mkdir(parents=True, exist_ok=True)
    config.misc.torrent_directory.mkdir(parents=True, exist_ok=True)
    config.misc.effective_completed_path.mkdir(parents=True, exist_ok=True)
    config.misc.image_directory.mkdir(parents=True, exist_ok=True)
    log.info("Conducting filesystem tests...")
    test_dir = config.misc.show_directory / Path(".miramedia_test_dir")
    test_dir.mkdir(parents=True, exist_ok=True)
    test_dir.rmdir()
    log.info(f"Successfully created test dir in Show directory at: {test_dir}")
    test_dir = config.misc.movie_directory / Path(".miramedia_test_dir")
    test_dir.mkdir(parents=True, exist_ok=True)
    test_dir.rmdir()
    log.info(f"Successfully created test dir in Movie directory at: {test_dir}")
    test_dir = config.misc.image_directory / Path(".miramedia_test_dir")
    test_dir.touch()
    test_dir.unlink()
    log.info(f"Successfully created test file in Image directory at: {test_dir}")
    test_dir = config.misc.show_directory / Path(".miramedia_test_dir")
    test_dir.mkdir(parents=True, exist_ok=True)
    torrent_dir = config.misc.effective_completed_path / Path(".miramedia_test_dir")
    torrent_dir.mkdir(parents=True, exist_ok=True)
    test_torrent_file = torrent_dir / Path(".miramedia.test.torrent")
    test_torrent_file.touch()
    test_hardlink = test_dir / Path(".miramedia.test.hardlink")
    try:
        test_hardlink.hardlink_to(test_torrent_file)
        if not test_hardlink.samefile(test_torrent_file):
            log.critical("Hardlink creation failed!")
        log.info("Successfully created test hardlink in Show directory")
    except OSError:
        log.exception("Hardlink creation failed, falling back to copying files")
        shutil.copy(src=test_torrent_file, dst=test_hardlink)
    finally:
        test_hardlink.unlink(missing_ok=True)
        test_torrent_file.unlink(missing_ok=True)
        for leftover in (torrent_dir, test_dir):
            try:
                leftover.rmdir()
            except OSError:
                log.warning("Could not remove preflight test dir %s", leftover)
