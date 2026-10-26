"""admin doctor 体检端点测试：结构完整、修复提示齐备、绝不泄露密钥值。"""

from app.core.config import settings
from app.core.security import create_access_token


def _hdr(roles=("platform_admin",), user_id="u-doctor"):
    token = create_access_token(user_id=user_id, yard_id=None, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


class TestAdminDoctor:
    def test_requires_platform_admin(self, client):
        resp = client.get("/api/v1/admin/doctor", headers=_hdr(roles=("yard_admin",)))
        assert resp.status_code == 403

    def test_anonymous_rejected(self, client):
        resp = client.get("/api/v1/admin/doctor")
        assert resp.status_code in (401, 403)

    def test_doctor_shape_and_fix_hints(self, client):
        resp = client.get("/api/v1/admin/doctor", headers=_hdr())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["overall"] in ("ok", "warn", "fail")
        names = {c["name"] for c in data["checks"]}
        # 关键检查项必须在场（失败即教学：让运维一眼看到体检面）
        for expected in (
            "database",
            "alembic_version",
            "redis",
            "worker_heartbeat",
            "llm_provider",
            "signup_enabled",
        ):
            assert expected in names
        for check in data["checks"]:
            assert check["detail"], f"{check['name']} 缺 detail"
            # 非 ok 项必须给可照做的下一条动作
            if check["status"] != "ok":
                assert check["fix_hint"], f"{check['name']} 非 ok 却无 fix_hint"

    def test_doctor_never_leaks_secret_values(self, client, monkeypatch):
        # 设置高辨识度的“密钥”，响应任何位置都不允许出现其值（安全红线）
        monkeypatch.setattr(settings, "llm_api_key", "sk-DOCTOR-MUST-NOT-LEAK-9f3a", raising=False)
        resp = client.get("/api/v1/admin/doctor", headers=_hdr())
        assert resp.status_code == 200
        assert "sk-DOCTOR-MUST-NOT-LEAK-9f3a" not in resp.text
