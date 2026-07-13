"""Portable atomic directory rename without replace semantics."""

from __future__ import annotations

import ctypes
import errno
import os
import sys

RENAME_NOREPLACE = 2
RENAME_EXCL = 0x0004


def atomic_rename_noreplace(
    src_dir_fd: int,
    src_name: str,
    dst_dir_fd: int,
    dst_name: str,
) -> None:
    """Rename ``src_name`` into ``dst_name`` without replacing an existing entry."""
    if sys.platform == "linux":
        _renameat2_noreplace(src_dir_fd, src_name, dst_dir_fd, dst_name)
        return
    if sys.platform == "darwin":
        _renameatx_np_excl(src_dir_fd, src_name, dst_dir_fd, dst_name)
        return
    if os.name == "nt":
        try:
            os.rename(
                src_name,
                dst_name,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
        except FileExistsError:
            raise
        except OSError as exc:
            if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(dst_name) from exc
            raise
        return
    msg = "atomic no-replace publication is not available on this platform"
    raise OSError(errno.ENOTSUP, msg)


def _renameat2_noreplace(
    src_dir_fd: int,
    src_name: str,
    dst_dir_fd: int,
    dst_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        msg = "renameat2 is not available"
        raise OSError(errno.ENOTSUP, msg) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        src_dir_fd,
        src_name.encode(),
        dst_dir_fd,
        dst_name.encode(),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return
    err = ctypes.get_errno()
    raise OSError(err, os.strerror(err))


def _renameatx_np_excl(
    src_dir_fd: int,
    src_name: str,
    dst_dir_fd: int,
    dst_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameatx_np = libc.renameatx_np
    except AttributeError as exc:
        msg = "renameatx_np is not available"
        raise OSError(errno.ENOTSUP, msg) from exc
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    result = renameatx_np(
        src_dir_fd,
        src_name.encode(),
        dst_dir_fd,
        dst_name.encode(),
        RENAME_EXCL,
    )
    if result == 0:
        return
    err = ctypes.get_errno()
    raise OSError(err, os.strerror(err))
