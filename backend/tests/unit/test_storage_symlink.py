"""存储回归测试（S5）：LocalStorageBackend 的 root 为符号链接时读写删正常；
含 ../ 的越界存储键必须被拒绝（防路径穿越）。"""

import pytest
from app.modules.knowledge.storage import LocalStorageBackend


@pytest.fixture()
def linked_root(tmp_path):
    """建真实目录 + 指向它的符号链接，返回 (真实目录, 链接路径)。"""
    real = tmp_path / "real-store"
    real.mkdir()
    link = tmp_path / "link-store"
    link.symlink_to(real, target_is_directory=True)
    return real, link


def test_symlink_root_save_open_delete(linked_root):
    """用链路路径构造 backend：save/open/delete 全链路正常，实体落在真实目录。"""
    real, link = linked_root
    backend = LocalStorageBackend(root=link)

    data = b"kjxy-storage-regression"
    key = backend.save(data, ".txt")
    # 键形如 ab/abcdef.txt，实体写入真实目录（经符号链接解析）
    assert (real / key).read_bytes() == data
    with backend.open(key) as f:
        assert f.read() == data

    backend.delete(key)
    assert not (real / key).exists()
    # 重复删除静默成功（幂等）
    backend.delete(key)


@pytest.mark.parametrize(
    "bad_key",
    ["../escape.txt", "ab/../../escape.txt", "..", "../.."],
)
def test_traversal_keys_rejected(linked_root, bad_key):
    """../ 越界键：_resolve 与 open 必须拒绝（ValueError），不允许逃逸出根目录。"""
    real, link = linked_root
    backend = LocalStorageBackend(root=link)
    with pytest.raises(ValueError):
        backend._resolve(bad_key)
    with pytest.raises(ValueError):
        backend.open(bad_key)
    # delete 对非法键静默吞掉（设计如此），但绝不能把真实目录外的文件删掉
    backend.delete(bad_key)


def test_illegal_charset_key_rejected(linked_root):
    """非法字符键（绝对路径、空字节等）直接被字符白名单拒绝。"""
    _, link = linked_root
    backend = LocalStorageBackend(root=link)
    for bad in ["/etc/passwd", "a b/c.txt", "x.txt\0"]:
        with pytest.raises(ValueError):
            backend._resolve(bad)
