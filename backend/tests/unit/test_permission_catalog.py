"""权限目录完整性：中文名、分组覆盖与可授权范围。"""

from app.modules.yards.permissions import (
    ALL_PERMISSIONS,
    GRANTABLE_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSION_LABELS,
    PERMISSIONS,
    PROTECTED_PERMISSIONS,
)


def test_every_permission_has_chinese_label():
    assert set(PERMISSION_LABELS) == ALL_PERMISSIONS
    assert all(label.strip() for label in PERMISSION_LABELS.values())


def test_groups_cover_all_permissions_without_duplicates():
    grouped = [str(p) for group in PERMISSION_GROUPS for p in group["permissions"]]
    assert set(grouped) == ALL_PERMISSIONS
    assert len(grouped) == len(set(grouped))
    assert all(str(group["label"]).strip() for group in PERMISSION_GROUPS)


def test_grantable_excludes_hierarchy_permissions():
    assert "owner:transfer" not in GRANTABLE_PERMISSIONS
    assert "members:manage_admins" not in GRANTABLE_PERMISSIONS
    assert (PROTECTED_PERMISSIONS - GRANTABLE_PERMISSIONS) == {
        "owner:transfer",
        "members:manage_admins",
    }
    assert GRANTABLE_PERMISSIONS <= ALL_PERMISSIONS


def test_role_defaults_are_known_permissions():
    for role, perms in PERMISSIONS.items():
        assert perms <= ALL_PERMISSIONS, role
